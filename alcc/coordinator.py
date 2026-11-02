"""Bridge to installed K1–K8 Python CLI; never overrides STOP gates."""
from __future__ import annotations
import subprocess, threading, os, uuid, json, time, hashlib
from collections import deque
from pathlib import Path
from .core import run_command, require_good, now, audit_event
from .ollama_lifecycle import OllamaLifecycle

READ_COMMANDS={'status','assess','verify','deployment-status','rollback-preview','rollback-probe','live-status','logs','trace','research','plan','probe'}
CONTROL_COMMANDS={'deploy','rollback','activate','restore-albot'}
CONSOLE_LOG_PART_BYTES = 8*1024*1024  # keep 4 recent 8-MiB parts per run

class Coordinator:
    def __init__(self, settings):
        self.settings=settings; self.proc=None; self.lock=threading.RLock()
        self.lines=deque(maxlen=2000); self.line_index=0; self.run_id=None; self.exit_code=None
        self.log_path=None; self.audit=settings.data/'audit.jsonl'
        self.ollama=OllamaLifecycle()
        self._shutdown_requested_for=None
        self._current_mode=None
    @property
    def root(self): return Path(self.settings.values['coordinator_dir'])
    def check(self):
        if not (self.root/'newbot_autonomy.py').is_file(): raise ValueError('Koordinator-Quellcode nicht gefunden')
        return self.root
    def stops(self):
        root=self.root/'data'/'newbot-autonomy'
        return {'stop.request':(root/'stop.request').exists(), 'session.stop':(root/'session.stop').exists()}
    def status(self):
        r=self.execute('status')
        return {'output':r['stdout'] or r['stderr'],'stop_markers':self.stops(),'process_running':self.is_running()}
    def execute(self,command):
        if command not in READ_COMMANDS and command!='stop': raise ValueError('Befehl nicht als Lese-/Stopaktion freigegeben')
        r=run_command([self.settings.values['python_exe'],'-B','-m','newbot_autonomy',command],cwd=self.check(),timeout=60)
        if r['exit_code']!=0 and command not in ('verify',):require_good(r,'Koordinator '+command)
        audit_event(self.audit,'coordinator_command',command=command,exit_code=r['exit_code'])
        return r
    def advanced(self,command,confirmation):
        if command not in CONTROL_COMMANDS:raise ValueError('Fortgeschrittener Befehl nicht freigegeben')
        if confirmation!='ADVANCED '+command:raise ValueError('Explizite Befehl-Freigabe fehlt')
        self.check()
        if self.is_running():raise ValueError('Ein vom Control Center gestarteter Koordinator läuft bereits')
        # Never clear stop gates. The installed CLI enforces its own eligibility.
        r=run_command([self.settings.values['python_exe'],'-B','-m','newbot_autonomy',command],
                      cwd=self.root,timeout=150,max_output=1_000_000)
        audit_event(self.audit,'coordinator_advanced',command=command,exit_code=r['exit_code'])
        return {'exit_code':r['exit_code'],'output':r['stdout']+'\n'+r['stderr'],
                'stop_markers':self.stops()}
    def is_running(self):return self.proc is not None and self.proc.poll() is None
    def start_one_batch(self, confirm, mode="work"):
        if confirm!='START ONE BATCH':raise ValueError('Explizite Startbestätigung erforderlich')
        if mode not in ('work','start','interactive'):raise ValueError('Unbekannter Koordinatormodus')
        self.check()
        if any(self.stops().values()): raise ValueError('STOP-Marker gesetzt. Es wird keine automatische Wiederaufnahme durchgeführt.')
        with self.lock:
            if self.is_running():raise ValueError('Koordinator läuft bereits im Control Center')
            # --once deliberately limits the UI-started work to a single assessed batch.
            mode_args={
                'work':['work','--open-features','--once','--session-minutes','30','--poll-seconds','30'],
                'start':['start','--open-features','--cycles','1','--repairs','2','--minutes','30'],
                'interactive':['interactive','--open-features','--once','--session-minutes','30']
            }
            args=[self.settings.values['python_exe'],'-u','-B','-m','newbot_autonomy']+mode_args[mode]
            self._reset_console()
            self.proc=subprocess.Popen(args,cwd=str(self.root),stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace',
                bufsize=1,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
            audit_event(self.audit,'coordinator_start',mode=mode,pid=self.proc.pid)
            threading.Thread(target=self._collect,args=(self.proc,),daemon=True).start()
            return {'started':True,'pid':self.proc.pid,'stop_markers':self.stops()}
    def _verify_manual_compatibility(self):
        # Fail closed: an unpatched CLI silently limits an order to four hours.
        required={
            'autonomy_interactive.py': 'F70AC9602933006A70E460D69EFE506C6B1D1CE841B158EF0604E81A6672D728',
            'autonomy_session.py': '583AAA52CA4442F8DF425EBB8A2C6415BCB8542391D525EE7E31B3E969E3DAD7',
        }
        for name,expected in required.items():
            p=self.root/name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest().upper()!=expected:
                raise ValueError('Unbegrenzter Bedienerauftrag erfordert den geprüften Koordinator-Kompatibilitätspatch: '+name)

    def _start_guided(self, *, confirm, expected, task=None):
        """Supervised 30-minute mode using the coordinator's existing two-choice CLI.

        A manual task is fed only as console INPUT (never as Python or shell code).
        The official autonomy_interactive creates and binds the operator order.
        """
        if confirm != expected:
            raise ValueError('Explizite Startbestätigung fehlt')
        if task is not None:
            if type(task) is not str:
                raise ValueError('Auftrag muss Text sein')
            task=task.strip()
            if not 1 <= len(task) <= 4000:
                raise ValueError('Auftrag muss 1 bis 4000 Zeichen enthalten')
            # The interactive CLI reads exactly ONE line. Never allow a user task
            # to inject a second menu choice or multiple interactive commands.
            if any(ord(ch)<32 and ch not in ('\t','\n','\r') for ch in task):
                raise ValueError('Auftrag enthält ungültige Steuerzeichen')
            task=' '.join(task.splitlines()).strip()
            if not task:
                raise ValueError('Auftrag darf nicht leer sein')
        self.check()
        if not (self.root/'autonomy_interactive.py').is_file() or not (self.root/'autonomy_orders.py').is_file():
            raise ValueError('Installierter Koordinator unterstützt keine geführten Bedieneraufträge')
        with self.lock:
            if self.is_running():
                raise ValueError('Koordinator läuft bereits im Control Center')
            if any(self.stops().values()):
                raise ValueError('STOP-Marker gesetzt. Keine automatische Wiederaufnahme oder Auftragsausführung.')
            if task is not None:self._verify_manual_compatibility()
            args=[self.settings.values['python_exe'],'-u','-B','-m','newbot_autonomy']
            # Both modes have no UI/session deadline. The official CLI enforces
            # independent task budgets and durable stop/admission gates.
            if task is None:
                args+=['work','--open-features','--repairs','-1',
                       '--session-minutes','0','--poll-seconds','30']
                selection=None
            else:
                args+=['interactive','--open-features','--repairs','-1','--session-minutes','0','--poll-seconds','30']
                selection='2\n'+task+'\n'
            env=os.environ.copy();env['PYTHONUTF8']='1';env['PYTHONUNBUFFERED']='1'
            self._reset_console()
            # Start a dedicated server only when no Ollama endpoint is already present.
            # Never take ownership of an already-running system Ollama service.
            self.ollama.ensure_ready()
            try:
                p=subprocess.Popen(args,cwd=str(self.root),stdin=subprocess.DEVNULL if task is None else subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                    bufsize=1,env=env,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
            except Exception:
                # A just-started Ollama instance must not leak if Python cannot launch.
                self.ollama.shutdown()
                raise
            self.proc=p
            self._current_mode='automatic' if task is None else 'manual'
            if selection is not None:
                try:
                    p.stdin.write(selection)
                    p.stdin.flush()
                    p.stdin.close()
                except (OSError,BrokenPipeError):
                    # Early process exit is captured by _collect, never retry in a loop.
                    pass
            audit_event(self.audit,'coordinator_guided_start',
                        mode='automatic' if task is None else 'manual',pid=p.pid,
                        task_sha256=__import__('hashlib').sha256(task.encode('utf8')).hexdigest() if task else None)
            threading.Thread(target=self._collect,args=(p,),daemon=True).start()
            if task is None:
                threading.Thread(target=self._watch_automatic,args=(p,),daemon=True).start()
            return {'started':True,'pid':p.pid,'mode':'automatic' if task is None else 'manual',
                    'session_minutes':0, 'unlimited_session':True,'stop_markers':self.stops()}

    def start_automatic(self,confirm):
        return self._start_guided(confirm=confirm,expected='START AUTO SESSION')

    def start_manual(self,task,confirm):
        return self._start_guided(confirm=confirm,expected='START MANUAL TASK',task=task)

    def _reset_console(self):
        """Called under the coordinator lock before starting a new process."""
        self.lines=deque(maxlen=2000)
        self.line_index=0
        self.exit_code=None
        self.run_id=uuid.uuid4().hex
        folder=self.settings.data/'coordinator-console'
        folder.mkdir(parents=True,exist_ok=True)
        self.log_path=folder/('run-'+self.run_id+'.log')

    def _append_console(self, line, log_file=None):
        # Preserve terminal UTF-8 text verbatim, but keep the GUI buffer bounded.
        line=line.rstrip('\r\n')
        with self.lock:
            self.line_index+=1
            self.lines.append((self.line_index,line))
        if log_file is not None:
            log_file.write(line+'\n')
            log_file.flush()

    def _collect(self,p):
        """Consume combined stdout/stderr continuously (no terminal pipe deadlock)."""
        log_file=None
        try:
            try:
                log_file=self.log_path.open('a',encoding='utf-8',buffering=1)
            except OSError:
                # The live console must still work if local log storage fails.
                pass
            self._append_console('[Control Center] Konsolenausgabe gestartet.',log_file)
            if p.stdout is not None:
                for line in p.stdout:
                    try:
                        self._append_console(line,log_file)
                        if log_file is not None and log_file.tell()>=CONSOLE_LOG_PART_BYTES:
                            log_file.close()
                            # File retention bounded to this process run; no global wipe.
                            base=self.log_path
                            oldest=base.with_name(base.name+'.3')
                            oldest.unlink(missing_ok=True)
                            for i in (2,1):
                                source=base.with_name(base.name+('.'+str(i) if i>1 else ''))
                                dest=base.with_name(base.name+'.'+str(i+1))
                                if source.exists():source.replace(dest)
                            # Move .log to .1 (unless already moved by above loop).
                            if base.exists():base.replace(base.with_name(base.name+'.1'))
                            log_file=base.open('w',encoding='utf-8',buffering=1)
                    except OSError:
                        # Disk full/permission changed: continue draining stdout.
                        if log_file is not None:
                            try:log_file.close()
                            except OSError:pass
                        log_file=None
                        self._append_console('[Control Center] WARNUNG: Logdatei nicht beschreibbar.')
            code=p.wait()
            with self.lock:self.exit_code=code
            self._append_console('PROCESS_EXIT='+str(code),log_file)
            # Always release coordinator model memory when this session ends.
            # The server is also exited if and only if it was started by this UI.
            with self.lock:
                if self.proc is p:
                    try:
                        report=self.ollama.shutdown()
                    except Exception as exc:
                        report={'unloaded':False,'server_terminated':False,'message':'Aufräumfehler: '+type(exc).__name__}
                    self._append_console('[Ollama] '+report['message'],log_file)
                    audit_event(self.audit,'ollama_session_cleanup',**report)
        finally:
            if log_file is not None:
                try:log_file.close()
                except OSError:pass
    def _watch_automatic(self,p):
        """Detect durable stops and a running supervisor's fatal safety hold.

        Does NOT treat transient recovery_wait as a safety hold and never clears
        an operator stop. The official stop CLI lets the worker clean up itself.
        """
        state_path=self.root/'data'/'newbot-autonomy'/'session.json'
        while p.poll() is None:
            reason=None
            try:
                if any(self.stops().values()):
                    reason='operator_or_safety_stop_marker'
                elif state_path.is_file():
                    obj=json.loads(state_path.read_text(encoding='utf-8'))
                    if obj.get('pid')==p.pid and obj.get('phase') in ('safety_hold','needs_attention'):
                        reason='session_'+str(obj['phase'])
            except (OSError,ValueError,TypeError):
                pass
            if reason:
                with self.lock:
                    if self._shutdown_requested_for==p.pid:break
                    self._shutdown_requested_for=p.pid
                self._append_console('[Control Center] Schutzstopp erkannt: '+reason+'. Koordinator wird kontrolliert beendet.')
                audit_event(self.audit,'automatic_safety_shutdown',reason=reason,pid=p.pid)
                # No direct process-tree kill: the CLI owns child/worker cleanup.
                # If a marker is already present, only wait for regular cleanup.
                if reason!='operator_or_safety_stop_marker':
                    try:
                        self.execute('stop')
                    except Exception as exc:
                        self._append_console('[Control Center] WARNUNG: Stop-CLI fehlgeschlagen: '+type(exc).__name__)
                break
            time.sleep(1.5)

    def stop(self):
        # The official CLI creates both STOP files. Never discard or erase either marker.
        r=self.execute('stop')
        return {'stop_requested':True,'cli_output':r['stdout'],'process_running':self.is_running()}
    def running_logs(self,since=0):
        if type(since) is not int or since<0:raise ValueError('Ungültige Konsolenposition')
        with self.lock:
            run=self.run_id
            # A missing cursor should display only a bounded console tail.
            entries=list(self.lines)
            if since==0:
                entries=entries[-250:]
            else:
                entries=[item for item in entries if item[0]>since]
            truncated=bool(entries and entries[0][0]>since+1 and since>0)
            return {'running':self.is_running(), 'lines':[line for _,line in entries],
                    'sequence':self.line_index,'run_id':run,'truncated':truncated,
                    'exit_code':self.exit_code, 'log_path':str(self.log_path) if self.log_path else None,
                    'stop_markers':self.stops()}

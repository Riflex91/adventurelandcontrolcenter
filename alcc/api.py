"""pywebview API bridge: explicitly named actions, no arbitrary command executor."""
from __future__ import annotations
import ast, json, os, re, subprocess, threading, webbrowser
from datetime import datetime, timezone
from pathlib import Path
from .core import Settings, run_command, require_good, now, digest, audit_event, safe_member, load_json
from .workspaces import Workspaces
from .coordinator import Coordinator
from .remote import RaspberryPi
from .context import ProjectContext, RULES
from .diagnostics import local_diagnostics
from .bot_sync import BotSync


def put_clipboard(value):
    """Uses native Windows Unicode clipboard; avoids command injection via PowerShell."""
    if os.name != 'nt': return False
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32;user=ctypes.windll.user32
    kernel.GlobalAlloc.argtypes=[wintypes.UINT,ctypes.c_size_t];kernel.GlobalAlloc.restype=wintypes.HGLOBAL
    kernel.GlobalLock.argtypes=[wintypes.HGLOBAL];kernel.GlobalLock.restype=ctypes.c_void_p
    kernel.GlobalUnlock.argtypes=[wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes=[wintypes.HGLOBAL]
    user.SetClipboardData.argtypes=[wintypes.UINT,wintypes.HANDLE];user.SetClipboardData.restype=wintypes.HANDLE
    b=value.encode('utf-16-le')+b'\x00\x00'
    mem=kernel.GlobalAlloc(0x0002,len(b))
    if not mem:return False
    ptr=kernel.GlobalLock(mem)
    if not ptr:kernel.GlobalFree(mem);return False
    ctypes.memmove(ptr,b,len(b));kernel.GlobalUnlock(mem)
    if not user.OpenClipboard(None):kernel.GlobalFree(mem);return False
    try:
        if not user.EmptyClipboard():kernel.GlobalFree(mem);return False
        if not user.SetClipboardData(13,mem):kernel.GlobalFree(mem);return False
        return True
    finally:user.CloseClipboard()

class ControlAPI:
    def __init__(self, settings=None, webview_module=None):
        self.settings=settings or Settings()
        self.ws=Workspaces(self.settings)
        self.coord=Coordinator(self.settings)
        self.pi=RaspberryPi(self.settings,self.ws)
        self.bot_sync=BotSync(self.settings,self.ws,self.pi)
        self.ctx=ProjectContext(self.settings,self.ws,self.coord)
        self.webview=webview_module
        self.chat=None;self.pending=None
        self.logs=self.settings.data/'audit.jsonl'
        self._one_click_lock=threading.Lock()
        self._logs_export_lock=threading.Lock()
    def bootstrap(self):
        return {'settings':self.settings.values,'project':self.ctx.read(),
                'workspaces':self.ws.list_workspaces(),'stop_markers':self.coord.stops(),
                'coordinator_running':self.coord.is_running(),
                'capabilities':{'webview':bool(self.webview),'ssh':'Windows OpenSSH',
                                'embedded_login':'abhängig von ChatGPT-/WebView2-Unterstützung'}}
    def save_settings(self,data):return self.settings.save(data)
    def environment_check(self):return local_diagnostics(self.settings,self.coord)
    def set_chat_visible(self,visible):
        # Qt host provides a native browser panel; legacy pywebview returns a safe hint.
        if self.webview and hasattr(self.webview, 'set_visible'):
            return self.webview.set_visible(bool(visible))
        return {'integrated':False,'visible':False,'reason':'Use the integrated Qt launcher'}
    def coord_action(self,command):
        if command=='status':return self.coord.status()
        r=self.coord.execute(command);return {'exit_code':r['exit_code'],'output':r['stdout'] or r['stderr'], 'stop_markers':self.coord.stops()}
    def coord_start(self,confirm,mode="work"):return self.coord.start_one_batch(confirm,mode)
    def coord_auto(self,confirm):return self.coord.start_automatic(confirm)
    def coord_manual(self,task,confirm):return self.coord.start_manual(task,confirm)
    def coord_advanced(self,command,confirm):return self.coord.advanced(command,confirm)
    def coord_stop(self):return self.coord.stop()
    def coord_output(self):return self.coord.running_logs()
    def pi_status(self):return self.pi.status()
    def sync_bot(self,force=False):return self.bot_sync.sync(force)
    def sync_cache(self):return self.bot_sync.cached()
    def export_logs_desktop(self):
        from .log_export import export_logs
        return export_logs(self)
    def pi_logs(self):return self.pi.logs()
    def pi_pull(self):return self.pi.get_snapshot()
    def list_workspaces(self):return self.ws.list_workspaces()
    def list_files(self,name):return self.ws.candidate_file_list(name)
    def read_file(self,name,relative):return self.ws.read_candidate(name,relative)
    def save_file(self,name,relative,text,expected_sha256):
        result=self.ws.save_candidate(name,relative,text,expected_sha256)
        audit_event(self.logs,'file_edit',workspace=name,path=relative,new_sha256=result['sha256'])
        return result
    def file_diff(self,name,relative):return {'diff':self.ws.diff(name,relative)}
    def project_update(self,fields):return self.ctx.update(fields)
    def project_note(self,kind,value):return self.ctx.note(kind,value)
    def project_prompt(self,name=''):
        if name and self.bot_sync.record_path.exists(): self.bot_sync.assert_current(name)
        return {'prompt':self.ctx.build(name)}
    def prepare_chat_context(self,workspace=''):
        """Persist a factual context snapshot for the embedded ChatGPT browser.

        No automatic injection into ChatGPT.com: the user pastes the copied
        context explicitly. The browser has no local Python/SSH bridge.
        """
        prompt=self.ctx.build(workspace)
        path=self.settings.data/'chat-context-current.md'
        temp=path.with_suffix('.md.tmp')
        temp.write_bytes(prompt.encode('utf-8'))
        temp.replace(path)
        copied=put_clipboard(prompt)
        return {'path':str(path),'copied':copied,'workspace':workspace,
                'sha256':digest(prompt.encode('utf-8')),
                'paste_required':True,
                'message':'Projektkontext vorbereitet. Einmal in ChatGPT einfügen (Strg+V).'
                }
    def open_chat(self):
        url=self.settings.values['chat_url']
        if self.webview:
            try:
                if hasattr(self.webview,'ensure_chat'):
                    self.chat=self.webview.ensure_chat(url)
                    return {'opened':'embedded','url':url,'note':'ChatGPT im Hauptfenster eingebettet.'}
                self.chat=self.webview.create_window('ChatGPT – AdventureLand NewBot',url=url,width=1170,height=850,
                                                      min_size=(600,450),text_select=True)
                return {'opened':'embedded','url':url,'note':'ChatGPT-WebView-Anmeldung kann eingeschränkt sein.'}
            except Exception as exc: return {'opened':'failed','note':str(exc),'url':url}
        webbrowser.open(url)
        return {'opened':'external','url':url}
    def open_chat_browser(self):
        webbrowser.open(self.settings.values['chat_url']);return {'opened':'external'}
    def new_chat(self,workspace=''):
        # Main UI sends no workspace name. Accept only a very recent, verified
        # AutoSync record. The dashboard refreshes that record via SSH BEFORE
        # asking this GUI-thread method to open the embedded browser tab.
        # Avoid running SSH/blocking the whole Qt main window here.
        if not workspace:
            record=load_json(self.bot_sync.record_path,{}) or {}
            workspace=record.get('workspace','')
            try:
                last=datetime.fromisoformat(record['checked_at'])
                seconds=(datetime.now(timezone.utc)-last).total_seconds()
            except (KeyError,TypeError,ValueError):
                seconds=float('inf')
            if not (0<=seconds<75) or not workspace:
                raise ValueError('Pi-Basis nicht frisch bestätigt. Zuerst automatisch synchronisieren.')
            self.bot_sync.assert_current(workspace)
        if workspace and self.bot_sync.record_path.exists():
            self.bot_sync.assert_current(workspace)
        # Never claim ChatGPT conversation contents were extracted.
        handoff=self.ctx.handoff(workspace)
        copied=put_clipboard(handoff['prompt'])
        old=self.chat
        if self.webview:
            try: new=self.webview.create_window('ChatGPT – Neuer Bot-Chat',url=self.settings.values['chat_url'],
                                                 width=1170,height=850,min_size=(600,450),text_select=True)
            except Exception as exc:
                return handoff|{'opened':False,'clipboard_copied':copied,'error':str(exc)}
            self.chat=new
            self.pending={'old':old,'new':new,'handoff_id':handoff['handoff_id']}
            return handoff|{'opened':True,'clipboard_copied':copied,
                            'requires_confirmation':True,
                            'message':'Übergabe im neuen Chat einfügen und dort prüfen. Danach im Control Center „Übergabe bestätigt“ wählen.'}
        else:
            webbrowser.open(self.settings.values['chat_url'])
            self.pending={'old':None,'new':None,'handoff_id':handoff['handoff_id']}
            return handoff|{'opened':True,'clipboard_copied':copied,'requires_confirmation':True,
                            'message':'Externer Browser: Alten Chat bitte selbst schließen, nachdem die Übergabe geprüft ist.'}
    def confirm_new_chat(self,hid):
        if not self.pending or self.pending['handoff_id']!=hid:raise ValueError('Kein passender Chatwechsel offen')
        r=self.ctx.confirm(hid)
        old=self.pending.get('old');self.pending=None
        # Closing old window is not deleting a ChatGPT account chat.
        if old:
            try:old.destroy()
            except Exception:r['old_window_closed']=False
            else:r['old_window_closed']=True
        else:r['old_window_closed']=False
        return r
    def validate_workspace(self,name):return self.ws.plan(name)
    def test_candidate(self,name,mode,confirm):
        if confirm!='TEST '+name:raise ValueError('Bewusste Testfreigabe erforderlich')
        if mode not in ('syntax','node','python','npm'):raise ValueError('Unbekannter Testmodus')
        ws=self.ws.location(name)/'candidate'
        if not ws.is_dir():raise ValueError('Kandidat fehlt')
        files=self.ws.candidate_file_list(name)
        output=[];failed=0;ran=0
        # Static syntax validation does not execute candidate code.
        for f in files:
            rel=f['path']; p=safe_member(ws,rel,deploy=True)
            if p.suffix.lower()=='.json':
                try:json.loads(p.read_text('utf8'))
                except Exception as e:failed+=1;output.append(rel+': JSON: '+str(e))
                else:output.append(rel+': JSON OK');ran+=1
            elif p.suffix.lower()=='.py':
                try:ast.parse(p.read_text('utf8'),filename=rel)
                except Exception as e:failed+=1;output.append(rel+': Python: '+str(e))
                else:output.append(rel+': Python OK');ran+=1
            elif p.suffix.lower() in ('.js','.cjs','.mjs'):
                r=run_command([self.settings.values['node_exe'],'--check',str(p)],cwd=ws,timeout=15,max_output=90000)
                if r['exit_code']!=0: failed+=1;output.append(rel+': '+r['stderr'][-800:])
                else:output.append(rel+': JS OK');ran+=1
        if ran==0 and failed==0: failed=1;output.append('Keine Dateien mit statischer Syntaxprüfung vorhanden')
        if failed==0 and mode!='syntax':
            # Executes local candidate test code. Always requires caller's explicit TEST phrase.
            cmd={'node':[self.settings.values['node_exe'],'--test'],
                 'python':[self.settings.values['python_exe'],'-B','-m','unittest','discover','-p','test*.py'],
                 'npm':['npm','test']}[mode]
            try:
                r=run_command(cmd,cwd=ws,timeout=150,max_output=1_000_000)
                detail=r['stdout']+'\n'+r['stderr']
                output.append(detail[-30000:]); failed+=int(r['exit_code']!=0)
                # A zero-test runner cannot qualify as tested.
                test_count=0
                if mode=='node':
                    matches=re.findall(r'(?m)^# tests (\d+)\s*$',detail)
                    test_count=int(matches[-1]) if matches else 0
                elif mode=='python':
                    matches=re.findall(r'Ran (\d+) tests?',detail)
                    test_count=int(matches[-1]) if matches else 0
                elif mode=='npm':
                    test_count=1 if r['exit_code']==0 and 'Missing script' not in detail else 0
                if test_count<1:failed+=1;output.append('Keine nachgewiesenen Funktionstests ausgeführt')
                else: ran+=test_count
            except Exception as exc:failed+=1;output.append(str(exc))
        result='\n'.join(output)[-55000:]
        receipt=self.ws.test_receipt(name,mode,0 if failed==0 else 1,result)
        receipt['tests_executed']=ran
        receipt['test_mode']=mode
        # Syntax alone intentionally cannot satisfy the deploy gate.
        receipt['succeeded']=failed==0 and mode!='syntax' and ran>0
        from .core import atomic_json
        atomic_json(self.ws.location(name)/'test-receipt.json',receipt)
        audit_event(self.logs,'candidate_test',workspace=name,mode=mode,ok=receipt['succeeded'])
        return {'passed':failed==0,'eligible_for_deploy':receipt['succeeded'],'mode':mode,'checked':ran,
                'output':result,'receipt':receipt}
    def one_click_release(self,name):
        from .one_click import run_one_click
        return run_one_click(self,name)
    def deploy_preview(self,name):return self.pi.preview(name)
    def deploy_apply(self,name,plan_sha256,confirmation):return self.pi.deploy(name,plan_sha256,confirmation)
    def deploy_rollback(self,name,confirmation):return self.pi.rollback(name,confirmation)
    def open_data_dir(self):
        if os.name=='nt':os.startfile(str(self.settings.data))
        return {'path':str(self.settings.data)}

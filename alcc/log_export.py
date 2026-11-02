"""Read-only one-click AdventureLand diagnostic bundle, written to Windows Desktop.

No remote commands change files, service state or coordinator status. The SSH output
is bounded, verified as a tar stream and repackaged as an uploadable ZIP.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import threading
import zipfile

MAX_STREAM = 65 * 1024 * 1024
MAX_MEMBER = 3 * 1024 * 1024
MAX_TOTAL = 55 * 1024 * 1024
MAX_FILES = 145

# Fixed program-authored remote command; user values are passed only through
# the existing RaspberryPi._vars() validator and quoted SERVICE argument.
REMOTE_BUNDLE = r'''check_current
python3 - "$CURRENT" "$SERVICE" <<'ALCC_LOGS_PY'
import hashlib, io, json, pathlib, re, subprocess, sys, tarfile, time
release = pathlib.Path(sys.argv[1])
service = sys.argv[2]
logdir = pathlib.Path('/mnt/adventureland-ssd/logs/characters/newbot')
base = pathlib.Path('/mnt/adventureland-ssd/logs/characters')
MAX_LOG = 2 * 1024 * 1024
MAX_SOURCE = 700 * 1024
MAX_ALL = 45 * 1024 * 1024
used = 0
entries = []
skipped = []
def add(name, content, limit):
    global used
    if not isinstance(content, bytes): return
    if len(content)>limit: content=content[-limit:]
    if used+len(content)>MAX_ALL:
        skipped.append(name+': total cap');return
    used+=len(content)
    entries.append((name,content))
def tail(path, cap):
    with path.open('rb') as f:
        f.seek(0,2)
        pos=f.tell()
        f.seek(max(0,pos-cap))
        return f.read(cap)
if logdir.is_dir():
    paths = [p for p in logdir.iterdir() if p.is_file() and not p.is_symlink() and
             re.fullmatch(r'[A-Za-z0-9_-]+\.jsonl(?:\.[0-9]+)?',p.name)]
    paths.sort(key=lambda p:(p.name.split('.jsonl')[1] != '', -p.stat().st_mtime))
else:
    paths=[];skipped.append('newbot log directory absent')
# Reserve the diagnostic client log and the newest log for every character.
client=base/'headless-client.log'
if client.is_file() and not client.is_symlink():
    try:add('logs/headless-client.log',tail(client,MAX_LOG),MAX_LOG)
    except (OSError,ValueError): skipped.append('headless-client.log unreadable')
# Gather compact failure evidence from all considered log tails before the cap.
error_events=[]
error_counter_last={}
for p in paths[:65]:
    try:
        blob=tail(p,MAX_LOG)
        for raw in blob.splitlines()[1:]:
            try: evt=json.loads(raw)
            except (ValueError,UnicodeDecodeError):continue
            if not isinstance(evt,dict):continue
            if evt.get('type')=='action_error':
                fields=('character','ts','action','reason','error_name','error_fingerprint','correlation_id','family','skill')
                error_events.append({k:evt[k] for k in fields if k in evt})
            if evt.get('type')=='heartbeat' and isinstance(evt.get('counts'),dict):
                who=evt.get('character');count=evt['counts'].get('unexpected_errors')
                if isinstance(who,str) and isinstance(count,int) and count>=0:
                    old=error_counter_last.get(who)
                    if old is None or evt.get('ts',0)>old[0]:error_counter_last[who]=(evt.get('ts',0),count)
    except (OSError,ValueError):pass
error_events.sort(key=lambda e:e.get('ts',0))
add('meta/action-errors.json',json.dumps({'schema':'alcc.error-diagnostics.v1',
    'action_errors':error_events[-100:],'unexpected_error_counts':{k:v[1] for k,v in error_counter_last.items()},
    'note':'Only captured error events, not proof of absence. Counter changes may outlive rotated detail logs.'},
    ensure_ascii=False).encode('utf8'),MAX_LOG)
try:
    j=subprocess.run(['journalctl','-u',service,'--no-pager','-n','1200','-o','short-iso'],
      capture_output=True,timeout=12,check=False)
    if j.returncode==0:add('logs/systemd-service.log',j.stdout,MAX_LOG)
    else:skipped.append('journalctl not readable')
except (OSError,subprocess.TimeoutExpired):skipped.append('journalctl unavailable')
files = [release/'bot.js',release/'policy.json',release/'release.json']
src = release/'modules'/'src'
if src.is_dir() and not src.is_symlink():
    files += sorted(p for p in src.iterdir() if p.is_file() and not p.is_symlink() and
                    p.suffix in ('.js','.json','.cjs','.mjs'))[:100]
for p in files:
    try:
        if not p.is_file() or p.is_symlink() or p.stat().st_size>MAX_SOURCE:
            skipped.append('source skipped: '+p.name);continue
        rel=p.relative_to(release).as_posix()
        add('bot-source/'+rel,p.read_bytes(),MAX_SOURCE)
    except (OSError,ValueError):skipped.append('source unreadable: '+p.name)
for p in paths[:65]:
    try:add('logs/characters/newbot/'+p.name,tail(p,MAX_LOG),MAX_LOG)
    except (OSError,ValueError):skipped.append(p.name+': unreadable')
meta={'schema':'alcc.log-bundle.v1','generated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
      'active_release':str(release),'service':service,
      'included':[{'path':n,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)} for n,b in entries],
      'skipped':skipped, 'note':'Latest tail per file; not full historical logs. Sensitive data redacted locally.'}
add('meta/manifest.json',json.dumps(meta,indent=2).encode('utf-8'),MAX_LOG)
if not any(name.startswith('logs/') for name,_ in entries):
    raise SystemExit('NO_LOGS_READABLE')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
    for name,blob in entries:
        hdr=tarfile.TarInfo(name);hdr.size=len(blob);hdr.mode=0o644
        hdr.mtime=int(time.time());archive.addfile(hdr,io.BytesIO(blob))
ALCC_LOGS_PY
'''

# These heuristics protect common mistakes; users should still review the bundle.
_SECRET_PATTERNS = [
    re.compile(r'-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?-----END [^-\n]*PRIVATE KEY-----'),
    re.compile(r'(?i)(\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|cookie)\s*[:=]\s*["\']?)([^\s,"\'\r\n;]+)'),
    re.compile(r'(?i)(["\'](?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|cookie)["\']\s*:\s*["\'])([^"\']+)'),
    re.compile(r'(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]{12,})'),
    re.compile(r'\beyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{8,}\b'),
]


def redact(blob: bytes) -> bytes:
    text=blob.decode('utf-8','replace')
    text=re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}', 'Bearer [REDACTED]', text)
    for i,p in enumerate(_SECRET_PATTERNS):
        text=p.sub((lambda m: m.group(1)+'[REDACTED]') if i in (1,2,3) else '[REDACTED]',text)
    return text.encode('utf-8')


_SOURCE_LITERAL_SECRET = re.compile(
    rb"(?i)(?:\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\b|['\"](?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)['\"])\s*[:=]\s*['\"][A-Za-z0-9_./+\-]{16,}['\"]"
)

def source_contains_secret(content: bytes) -> bool:
    return (b'-----BEGIN PRIVATE KEY-----' in content or
            b'-----BEGIN OPENSSH PRIVATE KEY-----' in content or
            bool(_SOURCE_LITERAL_SECRET.search(content)))


def desktop_folder() -> Path:
    """Resolve the actual Windows Desktop, including OneDrive redirection."""
    if os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID
            guid=UUID('{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}')
            raw=guid.bytes_le
            class GUID(ctypes.Structure):
                _fields_=[('Data1',ctypes.c_ulong),('Data2',ctypes.c_ushort),('Data3',ctypes.c_ushort),('Data4',ctypes.c_ubyte*8)]
            g=GUID.from_buffer_copy(raw)
            result=ctypes.c_wchar_p()
            rc=ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g),0,None,ctypes.byref(result))
            if rc==0 and result.value:
                path=Path(result.value)
                ctypes.windll.ole32.CoTaskMemFree(result)
                if path.is_dir():return path
        except (OSError,AttributeError,ValueError):pass
    candidates=[Path.home()/'Desktop']
    for name in ('OneDrive','OneDriveCommercial','OneDriveConsumer'):
        if os.environ.get(name):candidates.append(Path(os.environ[name])/'Desktop')
    for p in candidates:
        if p.is_dir():return p
    raise RuntimeError('Windows-Desktop nicht gefunden; bitte den Desktop-Ordner prüfen')


def receive_remote_tar(pi, destination: Path) -> None:
    """SSH binary streaming with hard bounds and no remote mutation."""
    destination.parent.mkdir(parents=True,exist_ok=True)
    errors=[]
    proc=subprocess.Popen(pi.args_ssh(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
    def reader():
        try:
            with destination.open('wb') as f:
                total=0
                while True:
                    chunk=proc.stdout.read(65536)
                    if not chunk:break
                    total+=len(chunk)
                    if total>MAX_STREAM:
                        errors.append('SSH-Logstream zu groß');proc.kill();break
                    f.write(chunk)
        except Exception as exc:
            errors.append('Stream: '+str(exc));proc.kill()
    err_chunks=[]
    def err_reader():
        try:
            total=0
            while True:
                chunk=proc.stderr.read(4096)
                if not chunk:break
                total+=len(chunk)
                if total<24000:err_chunks.append(chunk)
        except OSError:pass
    t1=threading.Thread(target=reader,daemon=True)
    t2=threading.Thread(target=err_reader,daemon=True)
    t1.start();t2.start()
    try:
        proc.stdin.write(pi.script(pi._vars()+REMOTE_BUNDLE))
        proc.stdin.close()
        proc.wait(timeout=95)
        t1.join(timeout=10);t2.join(timeout=5)
        if t1.is_alive() or t2.is_alive():raise RuntimeError('SSH-Logstream konnte nicht beendet werden')
        if proc.returncode or errors:
            stderr=b''.join(err_chunks).decode('utf8','replace')[-1200:]
            raise RuntimeError('Logexport fehlgeschlagen: '+(' / '.join(errors) or stderr or str(proc.returncode)))
    except BaseException:
        proc.kill()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:pass
        raise
    finally:
        for s in (proc.stdin,proc.stdout,proc.stderr):
            if s and not s.closed:s.close()


def create_zip(tar_path: Path, destination: Path) -> dict:
    """No extraction; validate whitelist, file types and aggregate size first."""
    seen=set();total=0;files=[]
    allowed=lambda n: n.startswith(('logs/','bot-source/','meta/'))
    with tarfile.open(tar_path,mode='r:') as tf:
        members=tf.getmembers()
        if len(members)>MAX_FILES:raise ValueError('Zu viele Logdateien')
        for item in members:
            name=item.name
            parts=PurePosixPath(name).parts
            if (not item.isfile() or len(parts)<2 or not allowed(name) or
                any(part in ('.','..') or part.startswith('.') for part in parts) or
                '\\' in name or ':' in name or name in seen or
                item.size<0 or item.size>MAX_MEMBER):
                raise ValueError('Unerlaubtes Archivmitglied: '+name[:120])
            seen.add(name);total+=item.size
            if total>MAX_TOTAL:raise ValueError('Gesamtdatenmenge zu groß')
            files.append(item)
        if not any(x.name.startswith('logs/') for x in files):
            raise ValueError('Keine aktuellen Logs gefunden')
        if not any(x.name=='meta/manifest.json' for x in files):
            raise ValueError('Release-Metadaten fehlen')
        # A source digest mismatch is a hard failure, never a deployable partial ZIP.
        manifest_item=next((x for x in files if x.name=='meta/manifest.json'),None)
        manifest_data=json.loads(tf.extractfile(manifest_item).read())
        expected={x['path']:x['sha256'].lower() for x in manifest_data.get('included',[]) if
                  isinstance(x,dict) and isinstance(x.get('path'),str) and isinstance(x.get('sha256'),str)}
        for item in files:
            if item.name.startswith('bot-source/'):
                if item.name not in expected:
                    raise ValueError('Quellcode nicht im Pi-Manifest: '+item.name)
                raw=tf.extractfile(item).read()
                if hashlib.sha256(raw).hexdigest()!=expected[item.name]:
                    raise ValueError('Pi-Quellcode SHA256 stimmt nicht: '+item.name)
        destination.parent.mkdir(parents=True,exist_ok=True)
        created=False
        try:
            with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                created=True
                export_details=[]
                for item in files:
                    src=tf.extractfile(item)
                    if src is None:raise ValueError('Fehlerhafter Tar-Eintrag')
                    content=src.read(MAX_MEMBER+1)
                    if len(content)!=item.size:raise ValueError('Fehlerhafte Dateigröße')
                    # Source must stay byte-exact: never run text redaction on code.
                    # Refuse export instead of silently damaging or leaking secrets.
                    if item.name.startswith('bot-source/'):
                        if source_contains_secret(content):
                            raise ValueError('Private key in source: export abgebrochen')
                        safe_content=content
                    else:
                        safe_content=redact(content)
                    z.writestr(item.name,safe_content)
                    export_details.append({'file':item.name,'sha256_in_zip':hashlib.sha256(safe_content).hexdigest(),
                                           'sha256_before_redaction':hashlib.sha256(content).hexdigest(),
                                           'bytes_in_zip':len(safe_content),'mode':'unchanged_source' if item.name.startswith('bot-source/') else 'redacted_diagnostics'})
                z.writestr('meta/export-integrity.json',json.dumps(
                    {'schema':'alcc.export-integrity.v1','files':export_details},
                    ensure_ascii=False,indent=2))
                instructions=(
                    'AdventureLand - aktuelle Logs und Bot-Quellen\n'
                    'Dieses ZIP wurde rein lesend vom Raspberry Pi erstellt.\n'
                    'Inhalt: logs/, bot-source/ und meta/manifest.json.\n'
                    'Logs enthalten nur die jüngsten maximal 2 MiB je Datei, keine vollständige Historie.\n'
                    'Logs werden maskiert, Quellcode bleibt bytegenau unverändert.\n'
                    'Quellcode kann trotzdem vertrauliche Daten enthalten: Vor dem Hochladen kontrollieren.\n'
                    'WICHTIG: Bitte das ZIP vor einer Weitergabe auf sensible Inhalte überprüfen.\n'
                    'Weder der Pi noch seine Dienste wurden verändert.\n'
                )
                z.writestr('BITTE_LESEN.txt',instructions)
        except BaseException:
            if created:destination.unlink(missing_ok=True)
            raise
    return {'path':str(destination),'files':len(files),'bytes':destination.stat().st_size}


def export_logs(api, *, desktop: Path | None = None) -> dict:
    """Called only on explicit UI button click."""
    if not api._logs_export_lock.acquire(blocking=False):
        raise RuntimeError('Ein Logexport läuft bereits')
    try:
        target=desktop or desktop_folder()
        stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
        base=f'AdventureLand-Logs-und-Bot-{stamp}'
        with tempfile.TemporaryDirectory(prefix='alcc-logexport-') as td:
            tar=Path(td)/'remote.tar'
            receive_remote_tar(api.pi,tar)
            # Exclusive destination creation means existing ZIPs are never overwritten.
            for i in range(100):
                dst=target/(base+(f'-{i}' if i else '')+'.zip')
                try:
                    result=create_zip(tar,dst)
                    break
                except FileExistsError:
                    if i==99:raise
            return result
    finally:
        api._logs_export_lock.release()

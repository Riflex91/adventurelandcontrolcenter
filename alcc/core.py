"""Small, dependency-free core for AdventureLand Control Center."""
from __future__ import annotations
import json, os, re, hashlib, subprocess, shutil, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_NAME = 'AdventureLandControlCenter'
ALLOWED_REMOTE_PATH = re.compile(r'^[a-zA-Z0-9_./-]{1,180}$')
ALLOWED_ACCOUNT = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$')
ALLOWED_HOST = re.compile(r'^[a-zA-Z0-9.:-]{1,255}$')
EXCLUDED_NAMES = {'.env', '.git', 'node_modules', '__pycache__', '.ssh', 'id_rsa', 'id_ed25519', '.npmrc', '.pypirc', 'localStorage.json', 'secrets.json', 'credentials.json'}
EDITABLE_EXT = {'.js', '.mjs', '.cjs', '.json', '.py', '.md', '.yaml', '.yml', '.ts', '.tsx', '.css', '.html', '.txt'}
DEPLOY_EXT = {'.js', '.mjs', '.cjs', '.json', '.py', '.yaml', '.yml', '.ts', '.tsx', '.css', '.html'}
MAX_EDIT_BYTES = 512 * 1024
MAX_SNAPSHOT_BYTES = 120 * 1024 * 1024
MAX_UNPACKED_BYTES = 250 * 1024 * 1024


def now(): return datetime.now(timezone.utc).isoformat()
def digest(blob: bytes) -> str: return hashlib.sha256(blob).hexdigest()
def filehash(p: Path) -> str: return digest(p.read_bytes())

def atomic_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = path.with_name(path.name + '.tmp')
    t.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    t.replace(path)


def load_json(path: Path, default=None):
    try: return json.loads(path.read_text('utf-8'))
    except FileNotFoundError: return default


def app_data_dir():
    base = Path(os.environ.get('APPDATA') or (Path.home() / '.config'))
    return base / APP_NAME


def verify_remote_config(s: dict):
    host = str(s.get('ssh_host', ''))
    user = str(s.get('ssh_user', ''))
    base = str(s.get('remote_root', ''))
    service = str(s.get('service', ''))
    if not ALLOWED_HOST.fullmatch(host) or not ALLOWED_ACCOUNT.fullmatch(user): raise ValueError('Ungültige SSH-Adresse oder Benutzername')
    if not ALLOWED_REMOTE_PATH.fullmatch(base) or base.startswith('/') or '..' in base.split('/') or base.startswith('-'):
        raise ValueError('Remote-Root muss ein relativer Pfad innerhalb des Pi-Home-Verzeichnisses sein')
    if not re.fullmatch(r'[A-Za-z0-9@_.-]{1,120}\.service', service): raise ValueError('Ungültiger systemd-Dienst')
    key = str(s.get('ssh_key', '')).strip()
    if key and (not Path(key).is_file() or not Path(key).is_absolute()): raise ValueError('SSH-Key muss eine vorhandene absolute Datei sein')
    return s


class Settings:
    def __init__(self, root=None):
        self.data = Path(root) if root else app_data_dir()
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / 'settings.json'
        h = Path.home()
        self.values = {
            'coordinator_dir': str(h / 'Projects' / 'adventureland-coordinator'),
            'bot_repo_dir': str(h / 'Projects' / 'adventureland-newbot-offline'),
            'workspace_dir': str(self.data / 'workspaces'),
            'ssh_host': '192.168.178.134', 'ssh_user': 'riflex', 'ssh_key': '',
            'remote_root': 'adventureland-headless/CODE/newbot-coordinator',
            'service': 'adventureland-headless.service',
            'chat_url': 'https://chatgpt.com/',
            'python_exe': shutil.which('python') or 'python',
            'node_exe': shutil.which('node') or 'node',
        }
        saved = load_json(self.path, {}) or {}
        self.values.update(saved)
        # Migrate the historic prototype default, not explicitly customized hosts.
        if self.values['ssh_host'] == '100.76.36.122':
            self.values['ssh_host'] = '192.168.178.134'
        # Earlier releases accidentally used $HOME/CODE/... instead of the
        # verified $HOME/adventureland-headless/CODE/... layout. Migrate only
        # that exact historic default; keep explicitly configured alternatives.
        if self.values['remote_root'] == 'CODE/newbot-coordinator':
            self.values['remote_root'] = 'adventureland-headless/CODE/newbot-coordinator'
    def save(self, new):
        safe_keys = set(self.values)
        if set(new) - safe_keys: raise ValueError('Unbekannte Konfigurationsschlüssel')
        combined = self.values | {k:str(v).strip() for k,v in new.items()}
        verify_remote_config(combined)
        url = combined['chat_url']
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('chatgpt.com', 'www.chatgpt.com'):
            raise ValueError('ChatGPT-URL muss https://chatgpt.com/... verwenden')
        for field in ('coordinator_dir','bot_repo_dir','workspace_dir'):
            if not Path(combined[field]).is_absolute(): raise ValueError(f'{field}: absoluter Pfad erforderlich')
        self.values = combined
        atomic_json(self.path, combined)
        return {'saved': True, 'data_dir': str(self.data)}


class ExecResult:
    def __init__(self, process): self.process = process


def run_command(args, *, cwd=None, stdin=None, timeout=40, max_output=3*1024*1024, env=None):
    """Never run a shell; do not accept caller-provided executable code implicitly."""
    if not args or not isinstance(args,(tuple,list)) or not all(isinstance(a,str) for a in args): raise ValueError('Command arguments required')
    if stdin is not None and not isinstance(stdin,bytes): raise TypeError('stdin must be bytes')
    try:
        p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=str(cwd) if cwd else None, timeout=timeout, env=env, check=False,
                           creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
    except FileNotFoundError as exc: raise RuntimeError(f'Programm nicht gefunden: {args[0]}') from exc
    except subprocess.TimeoutExpired: raise RuntimeError(f'Zeitüberschreitung nach {timeout}s') from None
    if len(p.stdout) > max_output or len(p.stderr) > max_output: raise RuntimeError('Ausgabe überschreitet Sicherheitsgrenze')
    return {'exit_code':p.returncode, 'stdout':p.stdout.decode('utf-8','replace'), 'stderr':p.stderr.decode('utf-8','replace')}


def require_good(result, label):
    if result['exit_code'] != 0:
        err = (result['stderr'] or result['stdout'])[-2400:]
        raise RuntimeError(f'{label} fehlgeschlagen ({result["exit_code"]}): {err}')
    return result


def valid_relative(name, *, deploy=False):
    if not isinstance(name,str) or not name or '\\' in name or name.startswith('/') or ':' in name or '\x00' in name: raise ValueError('Ungültiger Dateiname')
    parts = name.split('/')
    if any(p in ('', '.', '..') or p.lower() in EXCLUDED_NAMES or p.startswith('.') for p in parts): raise ValueError('Geschützter Dateipfad')
    if not ALLOWED_REMOTE_PATH.fullmatch(name): raise ValueError('Dateiname enthält nicht erlaubte Zeichen')
    if Path(name).suffix.lower() not in (DEPLOY_EXT if deploy else EDITABLE_EXT): raise ValueError('Dateityp nicht erlaubt')
    return name


def safe_member(root: Path, name: str, *, deploy=False):
    valid_relative(name, deploy=deploy)
    root = root.resolve()
    p = root.joinpath(*name.split('/'))
    if not p.resolve().is_relative_to(root): raise ValueError('Symlink-/Pfad-Ausbruch blockiert')
    for current in [p, *p.parents]:
        if current == root: break
        if current.is_symlink(): raise ValueError('Symlink nicht zugelassen')
    return p


def audit_event(log_path: Path, event: str, **details):
    log_path.parent.mkdir(parents=True,exist_ok=True)
    # Do not log SSH keys, user-auth tokens, or raw chat transcripts.
    record = {'ts':now(),'event':event,'details':details}
    with log_path.open('a',encoding='utf8') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')

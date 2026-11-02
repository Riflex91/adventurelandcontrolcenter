"""Portable Windows launcher for pinned GitHub Release updates.

Never edits a running program, bot, SSH credentials, or Raspberry Pi.
Keep this bootstrap executable separate from versioned application directories.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

REPO = 'Riflex91/adventurelandcontrolcenter'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'AdventureLandControlCenter-windows-x64.zip'
CHECKSUM = ASSET + '.sha256'
MAX_ARCHIVE = 350 * 1024 * 1024
MAX_UNPACKED = 850 * 1024 * 1024
MAX_FILES = 14000
VERSION_RE = re.compile(r'^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$')


def install_root():
    return Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'AdventureLandControlCenter'


def request_bytes(url: str, *, max_bytes: int, opener=urllib.request.urlopen) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent':'AdventureLandControlCenter-Updater/1', 'Accept':'application/vnd.github+json'})
    with opener(req, timeout=15) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError('Download ist größer als das erlaubte Limit')
    return payload


def release_assets(release: dict) -> tuple[str, str, str]:
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not VERSION_RE.fullmatch(tag) or release.get('draft') or release.get('prerelease'):
        raise ValueError('Keine gültige stabile Release-Version')
    assets = {a.get('name'): a.get('browser_download_url') for a in release.get('assets', []) if isinstance(a, dict)}
    urls = []
    for name in (ASSET, CHECKSUM):
        url = assets.get(name)
        prefix = f'https://github.com/{REPO}/releases/download/{tag}/'
        if not isinstance(url, str) or not url.startswith(prefix):
            raise ValueError('Erwartete GitHub Release-Datei fehlt')
        urls.append(url)
    return tag, urls[0], urls[1]


def check_digest(archive: bytes, checksum: bytes):
    text = checksum.decode('ascii').strip()
    match = re.fullmatch(r'([0-9a-fA-F]{64})(?:\s+\*?' + re.escape(ASSET) + r')?', text)
    if not match or hashlib.sha256(archive).hexdigest().lower() != match.group(1).lower():
        raise ValueError('SHA-256 der heruntergeladenen Anwendung stimmt nicht überein')


def unpack_safe(archive_path: Path, dest: Path):
    """Reject traversal, links, duplicate paths, enormous archives and zip bombs."""
    dest.mkdir(parents=True, exist_ok=False)
    seen = set()
    total = 0
    with zipfile.ZipFile(archive_path) as z:
        members = z.infolist()
        if len(members) > MAX_FILES:
            raise ValueError('Zu viele Dateien im Update')
        for m in members:
            name = m.filename
            if not name or '\\' in name or '\x00' in name or name.startswith('/') or ':' in name:
                raise ValueError('Ungültiger Dateipfad im Update')
            parts = Path(name).parts
            if any(x in ('..', '.') for x in parts):
                raise ValueError('Pfadausbruch im Update')
            # unix mode 0o120000 is a symlink
            if ((m.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Symbolische Links im Update sind unzulässig')
            key = name.rstrip('/').casefold()
            if key in seen:
                raise ValueError('Doppelter Dateiname im Update')
            seen.add(key)
            total += m.file_size
            if total > MAX_UNPACKED:
                raise ValueError('Update ist entpackt zu groß')
        for m in members:
            target = dest.joinpath(*Path(m.filename).parts)
            if m.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as fin, target.open('xb') as fout:
                shutil.copyfileobj(fin, fout)
    if not (dest / 'AdventureLandControlCenter' / 'AdventureLandControlCenter.exe').is_file():
        raise ValueError('Startprogramm fehlt im GitHub-Release')


def find_latest_installed(root: Path) -> Path | None:
    versions = root / 'versions'
    if not versions.is_dir():
        return None
    candidates = [p for p in versions.iterdir()
                  if p.is_dir() and VERSION_RE.fullmatch(p.name)
                  and (p / 'AdventureLandControlCenter' / 'AdventureLandControlCenter.exe').is_file()]
    if not candidates:
        return None
    def version_key(p):
        match = re.match(r'v(\d+)\.(\d+)\.(\d+)', p.name)
        return tuple(map(int, match.groups()))
    return max(candidates, key=version_key)


def install_release(root: Path, tag: str, archive: bytes) -> Path:
    versions = root / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    dest = versions / tag
    if dest.is_dir() and (dest/'AdventureLandControlCenter'/'AdventureLandControlCenter.exe').is_file():
        return dest
    with tempfile.TemporaryDirectory(prefix='alcc-download-', dir=root) as temp:
        temp = Path(temp)
        archive_file = temp / ASSET
        archive_file.write_bytes(archive)
        stage = temp / 'staged'
        unpack_safe(archive_file, stage)
        if dest.exists():
            # Never silently replace a corrupt or partial preexisting release.
            raise ValueError(f'Unvollständiges Release-Verzeichnis existiert: {dest}')
        stage.replace(dest)
    return dest


def notify_error(text: str):
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, 'AdventureLand Control Center', 0x10)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def start_application(version_dir: Path):
    exe = version_dir / 'AdventureLandControlCenter' / 'AdventureLandControlCenter.exe'
    if not exe.is_file():
        raise FileNotFoundError(exe)
    subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)


def main() -> int:
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    current = find_latest_installed(root)
    try:
        release = json.loads(request_bytes(API, max_bytes=1024*1024))
        tag, zip_url, sha_url = release_assets(release)
        dest = root / 'versions' / tag
        # Never downgrade a newer installed release based on a stale API cache.
        if current is not None:
            def nums(value):
                m = re.match(r'v(\d+)\.(\d+)\.(\d+)', value)
                return tuple(map(int, m.groups()))
            if nums(current.name) > nums(tag):
                start_application(current)
                return 0
        if not (dest/'AdventureLandControlCenter'/'AdventureLandControlCenter.exe').is_file():
            data = request_bytes(zip_url, max_bytes=MAX_ARCHIVE)
            digest = request_bytes(sha_url, max_bytes=512)
            check_digest(data, digest)
            current = install_release(root, tag, data)
        else:
            current = dest
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        if current is None:
            notify_error('Update/Start fehlgeschlagen: ' + str(exc))
            return 2
        # Use last known-good version when offline or invalid remote release.
        print('Update nicht verfügbar, starte installierte Version: ' + str(exc), file=sys.stderr)
    start_application(current)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

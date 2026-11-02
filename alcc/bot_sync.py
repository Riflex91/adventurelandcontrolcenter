"""Read-only release-aware Pi snapshot synchronization. Never deploy or overwrite edits."""
from __future__ import annotations
import threading
from pathlib import Path
from .core import atomic_json, load_json, filehash, now


class BotSync:
    """The remote 'current' symlink is authoritative for release *identity*.

    It does not prove the release directory's contents were never modified in place.
    First sync always fetches a new snapshot: old test workspaces are not trusted.
    """
    def __init__(self, settings, workspaces, pi):
        self.settings, self.ws, self.pi = settings, workspaces, pi
        self.record_path = settings.data / 'last-verified-pi-snapshot.json'
        self.lock = threading.Lock()

    def _valid_record(self, record, release):
        if not isinstance(record, dict) or record.get('schema') != 'alcc.sync.v1':
            return False
        if (record.get('host') != self.settings.values['ssh_host'] or
            record.get('user') != self.settings.values['ssh_user'] or
            record.get('remote_root') != self.settings.values['remote_root'] or
            record.get('release') != release):
            return False
        name = record.get('workspace')
        if not isinstance(name, str): return False
        try:
            loc = self.ws.location(name)
            manifest = load_json(loc/'manifest.json',{})
            if (manifest.get('schema') != 'alcc.workspace.v1' or
                manifest.get('source') != 'ssh_tar_snapshot' or
                manifest.get('base_release') != release):
                return False
            hashes = manifest.get('base_hashes')
            if not isinstance(hashes,dict) or not hashes: return False
            baseline = loc/'baseline'
            for rel,sha in hashes.items():
                from .core import safe_member
                p = safe_member(baseline,rel,deploy=True)
                if not p.is_file() or filehash(p) != sha: return False
            # A missing or tampered candidate cannot silently become the current editor.
            self.ws.plan(name)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    def cached(self):
        record = load_json(self.record_path,{}) or {}
        return {'verified_now':False,'status':'not_checked',
                'last_verified_release':record.get('release'),
                'last_workspace':record.get('workspace'),
                'last_check':record.get('checked_at'),
                'note':'Cache ist kein aktueller SSH-Nachweis.'}

    def sync(self, force=False):
        if not isinstance(force,bool): raise ValueError('force muss bool sein')
        if not self.lock.acquire(blocking=False):
            return {'verified_now':False,'status':'in_progress',
                    'note':'Eine Synchronisierung läuft bereits.'}
        try:
            record = load_json(self.record_path,{}) or {}
            status = self.pi.status()  # SSH, read only; fail closed if unavailable.
            release = status.get('release')
            if not isinstance(release,str) or '/releases/' not in release:
                raise ValueError('Pi hat keinen gültigen aktiven Release gemeldet')
            old_release=record.get('release')
            if force or not self._valid_record(record,release):
                snap=self.pi.get_snapshot()  # checks release again before reading tar
                if snap['base_release']!=release:
                    raise ValueError('Pi-Release änderte sich während des Downloads; keine Übernahme')
                workspace=snap['name']
                created=True
            else:
                workspace=record['workspace']
                created=False
            # Do not remove an old workspace or mutate any existing candidate.
            current={'schema':'alcc.sync.v1','host':self.settings.values['ssh_host'],
                     'user':self.settings.values['ssh_user'],
                     'remote_root':self.settings.values['remote_root'],
                     'release':release,'workspace':workspace,'checked_at':now()}
            atomic_json(self.record_path,current)
            candidate_changes=self.ws.plan(workspace)['changed']
            return {'verified_now':True,'status':'current','release':release,
                    'workspace':workspace,'created':created,'previous_release':old_release,
                    'release_changed':bool(old_release and release!=old_release),
                    'candidate_changed_count':len(candidate_changes),
                    'service':status.get('service','unknown'),
                    'checked_at':current['checked_at'],
                    'note':'Vergleich anhand des aktuellen Release-Symlinks; keine Prüfung auf Änderungen innerhalb desselben Release-Verzeichnisses.'}
        finally:
            self.lock.release()

    def assert_current(self, name):
        """Only a successfully synchronized, selected workspace may feed chat handoffs."""
        record = load_json(self.record_path,{}) or {}
        if not name or record.get('workspace')!=name or not self._valid_record(record,record.get('release')):
            raise ValueError('Arbeitsbereich ist nicht als aktuelle Pi-Basis bestätigt. Erst synchronisieren.')
        return record

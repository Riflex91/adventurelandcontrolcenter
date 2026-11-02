"""Versioned bot snapshots and safe local candidate editing."""
from __future__ import annotations
import difflib, json, shutil, tarfile, io, os
from datetime import datetime, timezone
from pathlib import Path
from .core import (digest, filehash, now, atomic_json, load_json, valid_relative, safe_member,
                   MAX_EDIT_BYTES, MAX_SNAPSHOT_BYTES, MAX_UNPACKED_BYTES, DEPLOY_EXT)

class Workspaces:
    def __init__(self, settings): self.settings=settings
    @property
    def root(self):
        p=Path(self.settings.values['workspace_dir']); p.mkdir(parents=True,exist_ok=True);return p
    def location(self, name):
        if not isinstance(name,str) or not name or not name.replace('-','').replace('_','').isalnum() or len(name)>80:
            raise ValueError('Ungültiger Arbeitsbereich')
        p=self.root/name
        if p.is_symlink() or not p.resolve().is_relative_to(self.root.resolve()): raise ValueError('Ungültiger Arbeitsbereichspfad')
        return p
    def list_workspaces(self):
        out=[]
        for p in sorted(self.root.iterdir(),reverse=True):
            if p.is_dir() and not p.is_symlink() and (p/'manifest.json').exists():
                m=load_json(p/'manifest.json',{})
                out.append({'name':p.name,'created':m.get('created'),'base_release':m.get('base_release'),'file_count':len(m.get('base_hashes',{}))})
        return out
    def create_from_tar(self, archive:bytes, remote_ref:str):
        if len(archive)>MAX_SNAPSHOT_BYTES: raise ValueError('Bot-Archiv zu groß')
        if not remote_ref or not remote_ref.startswith('/') or '/releases/' not in remote_ref:
            raise ValueError('Release-Pfad nicht bestätigt')
        ts=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        name=f'bot-{ts}-{digest(os.urandom(12))[:6]}'
        ws=self.location(name)
        ws.mkdir()
        base=ws/'baseline'; candidate=ws/'candidate'
        base.mkdir();candidate.mkdir()
        hashes={}; total=0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
                members=tar.getmembers()
                if len(members)>5000: raise ValueError('Zu viele Archivdateien')
                for info in members:
                    name=info.name.removeprefix('./').rstrip('/')
                    if not name: continue
                    if info.isdir(): continue
                    if not info.isfile(): raise ValueError('Archiv enthält Symlinks oder Spezialdateien')
                    # All deployed source is restricted to an explicit allowlist.
                    try: rel=valid_relative(name, deploy=True)
                    except ValueError: continue  # Exclude non-source and hidden metadata from snapshots
                    if info.size>MAX_EDIT_BYTES: raise ValueError(f'Datei zu groß: {rel}')
                    total+=info.size
                    if total>MAX_UNPACKED_BYTES: raise ValueError('Archiv entpackt zu groß')
                    f=tar.extractfile(info)
                    if f is None: raise ValueError('Archivdatei fehlt')
                    blob=f.read(MAX_EDIT_BYTES+1)
                    if len(blob)!=info.size: raise ValueError('Archivgröße inkonsistent')
                    dst=safe_member(base,rel,deploy=True);dst.parent.mkdir(parents=True,exist_ok=True)
                    dst.write_bytes(blob);hashes[rel]=digest(blob)
            if not hashes: raise ValueError('Keine erlaubten Quelldateien im Archiv')
            shutil.copytree(base,candidate,dirs_exist_ok=True)
            atomic_json(ws/'manifest.json',{
                'schema':'alcc.workspace.v1','created':now(), 'base_release':remote_ref,
                'base_hashes':hashes, 'source':'ssh_tar_snapshot', 'deploy_history':[]
            })
            return {'name':ws.name,'created':True,'files':len(hashes),'base_release':remote_ref}
        except Exception:
            shutil.rmtree(ws,ignore_errors=True)
            raise
    def candidate_file_list(self,name):
        ws=self.location(name); base=ws/'candidate'
        out=[]
        if not base.exists(): raise ValueError('Arbeitsbereich nicht vorhanden')
        for p in sorted(base.rglob('*')):
            if p.is_symlink(): raise ValueError('Symlink im Kandidaten erkannt')
            if not p.is_file(): continue
            rel=p.relative_to(base).as_posix()
            try: valid_relative(rel,deploy=True)
            except ValueError: continue
            out.append({'path':rel,'bytes':p.stat().st_size,'sha256':filehash(p)})
        return out
    def read_candidate(self,name,rel):
        p=safe_member(self.location(name)/'candidate',rel)
        if not p.is_file(): raise FileNotFoundError(rel)
        if p.stat().st_size>MAX_EDIT_BYTES: raise ValueError('Datei zu groß')
        try: content=p.read_text('utf8')
        except UnicodeError: raise ValueError('Datei ist nicht UTF-8')
        return {'path':rel,'text':content,'sha256':filehash(p)}
    def save_candidate(self,name,rel,text,expected_sha256):
        if not isinstance(text,str) or len(text.encode('utf8'))>MAX_EDIT_BYTES: raise ValueError('Datei zu groß')
        p=safe_member(self.location(name)/'candidate',rel,deploy=True)
        if p.exists() and p.is_file():
            if not expected_sha256 or filehash(p)!=expected_sha256: raise ValueError('Datei zwischenzeitlich geändert; neu laden')
            old=p.read_bytes()
        elif expected_sha256 not in ('',None): raise ValueError('Datei existiert nicht mehr')
        else: old=None
        if old is not None:
            hist=self.location(name)/'history';hist.mkdir(exist_ok=True)
            (hist/(digest(rel.encode())+'-'+digest(old)[:16]+'.bak')).write_bytes(old)
        p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_name(p.name+'.alcc-tmp'); tmp.write_text(text,encoding='utf8',newline='')
        tmp.replace(p)
        return {'saved':True,'sha256':filehash(p)}
    def plan(self,name):
        ws=self.location(name); m=load_json(ws/'manifest.json')
        if not isinstance(m,dict) or m.get('schema')!='alcc.workspace.v1': raise ValueError('Ungültiges Manifest')
        base_hashes=m['base_hashes']
        current={i['path']:i['sha256'] for i in self.candidate_file_list(name)}
        changed=sorted(p for p in current if current[p]!=base_hashes.get(p))
        deleted=sorted(set(base_hashes)-set(current))
        if deleted: # dangerous deletions never deployed silently
            raise ValueError('Gelöschte Dateien gefunden; Deployment verweigert: '+', '.join(deleted[:10]))
        manifest={
            'workspace':name,'base_release':m['base_release'],'changed':changed,
            'base_hashes':base_hashes,'candidate_hashes':{p:current[p] for p in changed},
            'candidate_file_count':len(current),'test_receipt':load_json(ws/'test-receipt.json'),
        }
        manifest['plan_sha256']=digest(json.dumps({k:v for k,v in manifest.items() if k!='test_receipt'},sort_keys=True).encode())
        return manifest
    def diff(self,name,rel):
        ws=self.location(name)
        left=safe_member(ws/'baseline',rel,deploy=True)
        right=safe_member(ws/'candidate',rel,deploy=True)
        if not right.is_file(): raise FileNotFoundError(rel)
        a=left.read_text('utf8').splitlines(keepends=True) if left.exists() else []
        b=right.read_text('utf8').splitlines(keepends=True)
        return ''.join(difflib.unified_diff(a,b,fromfile='basis/'+rel,tofile='kandidat/'+rel))[:200000]
    def test_receipt(self,name,command,exit_code,output):
        ws=self.location(name); plan=self.plan(name)
        payload={'schema':'alcc.test.v1','time':now(), 'plan_sha256':plan['plan_sha256'],
                 'command':command,'exit_code':exit_code,'output_sha256':digest(output.encode('utf8')),
                 'succeeded':exit_code==0}
        atomic_json(ws/'test-receipt.json',payload)
        return payload
    def add_deploy_receipt(self,name,receipt):
        ws=self.location(name);m=load_json(ws/'manifest.json');m.setdefault('deploy_history',[]).append(receipt)
        atomic_json(ws/'manifest.json',m)

"""OpenSSH-only, staged and rollback-capable Raspberry-Pi deploys.
Remote commands are fixed bash templates with validated data (no user shell).
"""
from __future__ import annotations
import io, json, re, shlex, tarfile, time, os
from pathlib import Path
from .core import (run_command,require_good,verify_remote_config,digest,filehash,
                   safe_member,now,audit_event)

class RaspberryPi:
    def __init__(self, settings, workspaces):
        self.settings=settings;self.workspaces=workspaces
        self.audit=settings.data/'audit.jsonl'

    @property
    def s(self): return verify_remote_config(self.settings.values)
    def args_ssh(self):
        s=self.s
        a=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
           '-o','ConnectTimeout=10','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2']
        if s['ssh_key']: a+=['-i',s['ssh_key']]
        return a+['--',s['ssh_user']+'@'+s['ssh_host'],'bash -s']
    def args_scp(self):
        s=self.s
        a=['scp','-B','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10']
        if s['ssh_key']: a+=['-i',s['ssh_key']]
        return a
    def script(self, body):
        # Input is only bash source authored by the program, not from chats.
        return ('set -euo pipefail\n'+body+'\n').encode('utf8')
    def _vars(self):
        s=self.s
        return ('ROOT="$HOME"/'+shlex.quote(s['remote_root'])+'\n'
                'ROOT="$(readlink -m "$ROOT")"\n'
                'SERVICE='+shlex.quote(s['service'])+'\n'
                'test -d "$ROOT/releases" || { echo "NO_RELEASES_DIR" >&2; exit 11; }\n'
                'check_current() {\n'
                ' test -L "$ROOT/current" || { echo "CURRENT_NOT_SYMLINK" >&2; return 1; }\n'
                ' CURRENT="$(readlink -f "$ROOT/current")"\n'
                ' case "$CURRENT" in "$ROOT"/releases/*) ;; *) echo "UNEXPECTED_CURRENT_PATH" >&2; return 1;; esac\n'
                ' test -d "$CURRENT"\n'
                '}\n')
    def call(self,body,timeout=30,max_output=2_000_000):
        r=run_command(self.args_ssh(),stdin=self.script(self._vars()+body),timeout=timeout,max_output=max_output)
        return require_good(r,'SSH')
    def status(self):
        r=self.call('check_current\nprintf "RELEASE=%s\\n" "$CURRENT"\n'
                    'if systemctl is-active --quiet "$SERVICE"; then echo SERVICE=active; else echo SERVICE=inactive; fi\n',20)
        vals=dict(line.split('=',1) for line in r['stdout'].splitlines() if '=' in line)
        return {'reachable':True,'release':vals.get('RELEASE'),'service':vals.get('SERVICE','unknown'),
                'host':self.s['ssh_host'],'status_checked':now()}
    def logs(self):
        r=self.call('if test -r /mnt/adventureland-ssd/logs/characters/headless-client.log; then\n'
                    ' tail -n 130 /mnt/adventureland-ssd/logs/characters/headless-client.log\n'
                    'else journalctl -u "$SERVICE" -n 130 --no-pager -o cat; fi\n',25)
        return {'output':r['stdout'][-90000:]}
    def get_snapshot(self):
        status=self.status(); base=status['release']
        # cap stdout bytes *while* executing to avoid unbounded memory on compromised remote.
        body=(self._vars()+ 'check_current\n'
              'test "$CURRENT" = '+shlex.quote(base)+' || { echo "RELEASE_CHANGED" >&2; exit 13; }\n'
              'tar -C "$CURRENT" -czf - --exclude=.env --exclude=secrets.json '
              '--exclude=credentials.json --exclude=localStorage.json --exclude=.git '
              '--exclude=node_modules --exclude=.ssh --exclude=__pycache__ '
              "--exclude=\'*.pem\' --exclude=\'*.key\' --exclude=logs -- .\n")
        import subprocess
        proc=subprocess.Popen(self.args_ssh(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        try:
            # Archive generation is bounded by timeout and maximum size.
            import threading
            errors=[]
            def put():
                try:
                    proc.stdin.write(self.script(body)); proc.stdin.close()
                except (BrokenPipeError, OSError, ValueError) as exc: errors.append(str(exc))
            thread=threading.Thread(target=put,daemon=True);thread.start()
            import selectors
            # Windows anonymous pipes are not select()-compatible; cap by reader thread.
            collected=bytearray();problem=[]
            def reader():
                try:
                    while True:
                        part=proc.stdout.read(65536)
                        if not part: break
                        if len(collected)+len(part)>120*1024*1024:
                            problem.append('SNAPSHOT_TOO_LARGE');proc.kill();break
                        collected.extend(part)
                except Exception as exc: problem.append(str(exc))
            rt=threading.Thread(target=reader,daemon=True);rt.start()
            try: err=proc.stderr.read(16000)
            except Exception: err=b''
            proc.wait(timeout=90);rt.join(timeout=10);thread.join(timeout=3)
            if proc.returncode!=0 or problem: raise RuntimeError(f'Snapshot fehlgeschlagen: {problem or err.decode("utf8","replace")[-1000:]}')
            result=self.workspaces.create_from_tar(bytes(collected),base)
            audit_event(self.audit,'snapshot',workspace=result['name'],release=base)
            return result
        except Exception:
            proc.kill();proc.wait(timeout=5)
            raise
        finally:
            if proc.stdin and not proc.stdin.closed: proc.stdin.close()
            if proc.stdout: proc.stdout.close()
            if proc.stderr: proc.stderr.close()

    def _package(self,name,plan):
        ws=self.workspaces.location(name)
        out=ws/'candidate-upload.tar.gz'
        with tarfile.open(out,'w:gz') as z:
            for rel in plan['changed']:
                p=safe_member(ws/'candidate',rel,deploy=True)
                if not p.is_file() or filehash(p)!=plan['candidate_hashes'][rel]:
                    raise ValueError(f'Kandidat seit der Planung verändert: {rel}')
                info=tarfile.TarInfo(rel);info.mode=0o644;info.size=p.stat().st_size;info.mtime=int(time.time())
                with p.open('rb') as f:z.addfile(info,f)
        return out
    def preview(self,name):
        plan=self.workspaces.plan(name)
        s=self.status()
        plan['remote_current_release']=s['release']
        plan['remote_base_matches']=s['release']==plan['base_release']
        receipt=plan['test_receipt']
        plan['tests_current_and_passed']=bool(receipt and receipt.get('succeeded') and receipt.get('plan_sha256')==plan['plan_sha256'])
        plan['deploy_eligible']=bool(plan['changed'] and plan['remote_base_matches'] and plan['tests_current_and_passed'])
        return plan
    def deploy(self,name,expected_plan_hash,confirm):
        if confirm!='DEPLOY '+name: raise ValueError('Deployment-Bestätigung stimmt nicht überein')
        plan=self.preview(name)
        if not plan['deploy_eligible'] or plan['plan_sha256']!=expected_plan_hash: raise ValueError('Release, Änderungen oder Testnachweis nicht mehr aktuell')
        if len(plan['changed'])>200:raise ValueError('Zu viele veränderte Dateien')
        identity='alcc-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime())+'-'+digest(os.urandom(12))[:8]
        script_names=' '.join(shlex.quote(p) for p in plan['changed'])
        base=plan['base_release']
        upload=self._package(name,plan)
        remote_incoming=f'./{self.s["remote_root"]}/.alcc-incoming/{identity}.tar.gz'
        # First a strictly limited directory, no production changes yet.
        self.call('mkdir -p "$ROOT/.alcc-incoming"\n',20)
        target=self.s['ssh_user']+'@'+self.s['ssh_host']+':'+remote_incoming
        try:
            require_good(run_command(self.args_scp()+[str(upload),target],timeout=90),'SCP')
            checks='\n'.join('printf "%s  %s\\n" '+shlex.quote(plan['candidate_hashes'][p])+' '+shlex.quote(p) for p in plan['changed'])
            # Checkout always derives from the *currently running* release; no unrelated version switch.
            script=('check_current\n'
                'test "$CURRENT" = '+shlex.quote(base)+' || { echo STALE_RELEASE >&2; exit 20; }\n'
                'id='+shlex.quote(identity)+'\n'
                'incoming="$ROOT/.alcc-incoming/$id.tar.gz"\n'
                'stage="$ROOT/releases/.$id.stage"; release="$ROOT/releases/$id"\n'
                'test -f "$incoming"; test ! -e "$stage"; test ! -e "$release"\n'
                'mkdir "$stage"\n'
                'trap \'rm -rf "$stage"\' EXIT\n'
                'cp -a "$CURRENT/." "$stage/"\n'
                'cd "$stage"\n'
                'for rel in '+script_names+'; do\n'
                '  check="$stage"\n'
                '  IFS=/ read -ra parts <<< "$rel"\n'
                '  for part in "${parts[@]}"; do check="$check/$part"; test ! -L "$check" || { echo LINK_TRAVERSAL >&2; exit 21; }; done\n'
                'done\n'
                'tar -xzf "$incoming" --no-same-owner --no-same-permissions\n'
                'printf "%s\\n" '+shlex.quote('\n'.join(plan['candidate_hashes'][p]+'  '+p for p in plan['changed']))+' | sha256sum -c - >/dev/null\n'
                # Syntax check for *.js while not running any bot code.
                'for rel in '+script_names+'; do case "$rel" in *.js|*.mjs|*.cjs) node --check "$rel" >/dev/null ;; esac; done\n'
                'mv "$stage" "$release"\n'
                'trap - EXIT\n'
                'link="$ROOT/.alcc-link-$id"\n'
                'ln -s "$release" "$link"\n'
                'mv -Tf "$link" "$ROOT/current"\n'
                'restart_failed=0\n'
                'sudo -n systemctl restart "$SERVICE" || restart_failed=1\n'
                'sleep 3\n'
                'systemctl is-active --quiet "$SERVICE" || restart_failed=1\n'
                'test "$(readlink -f "$ROOT/current")" = "$release" || restart_failed=1\n'
                'if test "$restart_failed" != 0; then\n'
                '  ln -s '+shlex.quote(base)+' "$ROOT/.alcc-revert-$id"\n'
                '  mv -Tf "$ROOT/.alcc-revert-$id" "$ROOT/current"\n'
                '  sudo -n systemctl restart "$SERVICE" || true\n'
                '  echo DEPLOY_ROLLED_BACK >&2; exit 22\n'
                'fi\n'
                'printf "RELEASE=%s\\nPREVIOUS=%s\\n" "$release" '+shlex.quote(base)+'\n')
            r=self.call(script,timeout=150)
            vals=dict(line.split('=',1) for line in r['stdout'].splitlines() if '=' in line)
            if vals.get('RELEASE')!='$ROOT/releases/'+identity and not vals.get('RELEASE','').endswith('/releases/'+identity):
                raise RuntimeError('Remote receipt missing or invalid; check deployment status manually')
            receipt={'schema':'alcc.deploy.v1','ts':now(),'workspace':name,'plan_sha256':plan['plan_sha256'],
                     'new_release':vals['RELEASE'],'previous_release':base,'service':'active'}
            self.workspaces.add_deploy_receipt(name,receipt)
            audit_event(self.audit,'deploy',workspace=name,release=vals['RELEASE'],previous=base)
            return receipt
        finally:
            upload.unlink(missing_ok=True)
            try:self.call('rm -f "$ROOT/.alcc-incoming/'+identity+'.tar.gz"\n',timeout=15)
            except Exception:pass

    def rollback(self,name,confirm):
        m=self.workspaces.location(name)/'manifest.json'
        import json
        data=json.loads(m.read_text('utf8'))
        history=data.get('deploy_history') or []
        if not history:raise ValueError('Kein lokaler Deploymentbeleg')
        last=history[-1]; expected='ROLLBACK '+name
        if confirm!=expected:raise ValueError('Rollback-Bestätigung stimmt nicht')
        current=self.status()['release']; new=last['new_release'];old=last['previous_release']
        if current!=new:raise ValueError('Aktiver Release unterscheidet sich vom letzten Deployment')
        script=('check_current\n'
                'test "$CURRENT" = '+shlex.quote(new)+' || exit 42\n'
                'old='+shlex.quote(old)+'\n'
                'test -d "$old" || exit 43\n'
                'case "$old" in "$ROOT"/releases/*) ;; *) exit 44;; esac\n'
                'ln -s "$old" "$ROOT/.alcc-rollback-link-$$"\n'
                'mv -Tf "$ROOT/.alcc-rollback-link-$$" "$ROOT/current"\n'
                'if ! sudo -n systemctl restart "$SERVICE" || ! systemctl is-active --quiet "$SERVICE"; then\n'
                ' ln -s '+shlex.quote(new)+' "$ROOT/.alcc-rollback-undo-$$"\n'
                ' mv -Tf "$ROOT/.alcc-rollback-undo-$$" "$ROOT/current"\n'
                ' sudo -n systemctl restart "$SERVICE" || true\n'
                ' echo ROLLBACK_RESTART_FAILURE >&2; exit 45\n'
                'fi\n'
                'echo "ROLLED_BACK_TO=$old"\n')
        r=self.call(script,timeout=90)
        audit_event(self.audit,'rollback',workspace=name,from_release=new,to_release=old)
        return {'rolled_back':True,'previous_release':old,'output':r['stdout'].strip()}

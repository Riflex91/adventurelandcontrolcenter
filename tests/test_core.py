import io,json,tarfile,tempfile,unittest,os,subprocess
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings,valid_relative,safe_member,digest,atomic_json,verify_remote_config
from alcc.workspaces import Workspaces
from alcc.coordinator import Coordinator
from alcc.context import ProjectContext
from alcc.api import ControlAPI


def make_tar(files):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as t:
        for name,data in files.items():
            x=tarfile.TarInfo(name);x.size=len(data)
            t.addfile(x,io.BytesIO(data))
    return out.getvalue()

class TestSystem(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.s=Settings(self.root/'app')
        self.s.values['workspace_dir']=str(self.root/'work')
        self.s.values['coordinator_dir']=str(self.root/'coordinator')
        (self.root/'coordinator'/'data'/'newbot-autonomy').mkdir(parents=True)
        self.ws=Workspaces(self.s);self.c=Coordinator(self.s)
        self.ctx=ProjectContext(self.s,self.ws,self.c)
        self.tar=make_tar({'CODE/newbot.start.js':b'const a=1;\n','src/a.json':b'{"a":1}\n'})
        self.ref='/home/riflex/CODE/newbot-coordinator/releases/7b556a2a'
    def fresh(self):return self.ws.create_from_tar(self.tar,self.ref)['name']
    def test_workspace_clean(self):
        name=self.fresh();p=self.ws.plan(name)
        self.assertEqual(p['changed'],[])
        self.assertEqual(p['base_release'],self.ref)
    def test_editor_revision_and_diff(self):
        name=self.fresh();old=self.ws.read_candidate(name,'CODE/newbot.start.js')
        r=self.ws.save_candidate(name,'CODE/newbot.start.js','const a=2;\n',old['sha256'])
        self.assertNotEqual(old['sha256'],r['sha256'])
        with self.assertRaises(ValueError):self.ws.save_candidate(name,'CODE/newbot.start.js','oops',old['sha256'])
        self.assertIn('-const a=1;',self.ws.diff(name,'CODE/newbot.start.js'))
        self.assertEqual(self.ws.plan(name)['changed'],['CODE/newbot.start.js'])
    def test_modified_candidate_hash_bumps_plan(self):
        name=self.fresh();old=self.ws.plan(name)['plan_sha256']
        p=self.ws.read_candidate(name,'CODE/newbot.start.js')
        self.ws.save_candidate(name,'CODE/newbot.start.js','const a=2;',p['sha256'])
        self.assertNotEqual(old,self.ws.plan(name)['plan_sha256'])
    def test_deleted_candidate_blocks(self):
        name=self.fresh();(self.ws.location(name)/'candidate'/'CODE'/'newbot.start.js').unlink()
        with self.assertRaisesRegex(ValueError,'Gelöschte'):self.ws.plan(name)
    def test_reject_path_traversal(self):
        for name in ('../x.js','/etc/evil.js','sub/../../evil.js','C:/Windows/evil.js','src\\foo.js','a/.env','a/.ssh/key.js','a/.git/x.js',''): 
            with self.subTest(name=name):
                with self.assertRaises(ValueError):valid_relative(name,deploy=True)
    def test_reject_symlink(self):
        name=self.fresh();candidate=self.ws.location(name)/'candidate'
        target=self.root/'elsewhere';target.mkdir()
        link=candidate/'link'
        try:
            link.symlink_to(target,target_is_directory=True)
        except OSError as exc:
            # Creating Windows symbolic links requires Developer Mode or elevated
            # privileges on some installations. A directory junction exercises the
            # same path-escape protection without administrator privileges.
            if os.name != 'nt' or getattr(exc,'winerror',None) not in (5,1314):
                raise
            result=subprocess.run(['cmd.exe','/d','/c',
                                   'mklink /J "{}" "{}"'.format(link,target)],
                                  capture_output=True,text=True,encoding='utf-8',errors='replace')
            if result.returncode != 0:
                self.skipTest('Windows denied both symlink and directory junction creation')
        try:
            with self.assertRaises(ValueError):
                safe_member(candidate,'link/e.js',deploy=True)
        finally:
            if link.is_symlink(): link.unlink()
            elif os.name == 'nt' and link.is_dir(): link.rmdir()

    def test_handoff_persists_exact_utf8_bytes(self):
        # CRLF/LF must round-trip unchanged on every OS, including Windows.
        self.ctx.update({'session_notes':'Erkenntnis 1\nErkenntnis 2\r\nÜbergabe'})
        handoff=self.ctx.handoff()
        data=Path(handoff['file']).read_bytes()
        record=json.loads((self.ctx.handoffs/(handoff['handoff_id']+'.json')).read_text(encoding='utf8'))
        self.assertEqual(data,handoff['prompt'].encode('utf8'))
        self.assertEqual(digest(data),record['prompt_sha256'])
        self.assertTrue(self.ctx.confirm(handoff['handoff_id'])['confirmed'])
    def test_tar_hardlink_block(self):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w:gz') as t:
            x=tarfile.TarInfo('danger.js');x.type=tarfile.SYMTYPE;x.linkname='/etc/passwd';t.addfile(x)
        with self.assertRaisesRegex(ValueError,'Symlinks'):self.ws.create_from_tar(out.getvalue(),self.ref)
    def test_tar_excludes_hidden_files(self):
        name=self.ws.create_from_tar(make_tar({'.env':b'SECRET','a.js':b'1'}),self.ref)['name']
        self.assertFalse((self.ws.location(name)/'candidate'/'.env').exists())
        self.assertTrue((self.ws.location(name)/'candidate'/'a.js').exists())
    def test_source_not_changed(self):
        name=self.fresh();self.ws.save_candidate(name,'CODE/newbot.start.js','2',self.ws.read_candidate(name,'CODE/newbot.start.js')['sha256'])
        self.assertEqual((self.ws.location(name)/'baseline'/'CODE'/'newbot.start.js').read_text(),'const a=1;\n')
    def test_stop_blocks_execution(self):
        (self.root/'coordinator'/'newbot_autonomy.py').write_text('print("noop")')
        (self.root/'coordinator'/'data'/'newbot-autonomy'/'stop.request').touch()
        with self.assertRaisesRegex(ValueError,'STOP-Marker'):self.c.start_one_batch('START ONE BATCH')
    def test_stop_without_keyword_fails(self):
        (self.root/'coordinator'/'newbot_autonomy.py').write_text('print("noop")')
        with self.assertRaises(ValueError):self.c.start_one_batch('start')
    def test_context_handoff_persistent_and_no_crawl(self):
        name=self.fresh()
        self.ctx.update({'goal':'MP fix','session_notes':'Befund 42'})
        self.ctx.note('decision','Kein Pi-Deploy ohne Tests')
        h=self.ctx.handoff(name)
        self.assertIn('Befund 42',h['prompt'])
        self.assertIn('stop.request',h['prompt'])
        self.assertTrue(Path(h['file']).is_file())
        r=self.ctx.confirm(h['handoff_id']);self.assertTrue(r['confirmed'])
        self.assertTrue(json.loads((self.ctx.handoffs/(h['handoff_id']+'.json')).read_text())['confirmed'])
    def test_corrupt_handoff_rejected(self):
        h=self.ctx.handoff();Path(h['file']).write_text('altered')
        with self.assertRaisesRegex(ValueError,'verändert'):self.ctx.confirm(h['handoff_id'])
    def test_url_restricts_offsite(self):
        with self.assertRaisesRegex(ValueError,'ChatGPT-URL'):self.s.save({'chat_url':'https://evil.example/login'})
    def test_remote_configuration(self):
        with self.assertRaises(ValueError):self.s.save({'remote_root':'../../etc'})
        with self.assertRaises(ValueError):self.s.save({'ssh_user':'riflex; sudo rm -rf /'})
    def test_snapshot_release_ref(self):
        with self.assertRaises(ValueError):self.ws.create_from_tar(self.tar,'/tmp/other')
    def test_test_receipt_invalidated_by_edit(self):
        name=self.fresh(); f=self.ws.read_candidate(name,'CODE/newbot.start.js')
        self.ws.save_candidate(name,'CODE/newbot.start.js','const b=2;',f['sha256'])
        p=self.ws.plan(name); self.ws.test_receipt(name,'node',0,'OK')
        self.assertEqual(self.ws.plan(name)['test_receipt']['plan_sha256'],p['plan_sha256'])
        f=self.ws.read_candidate(name,'CODE/newbot.start.js')
        self.ws.save_candidate(name,'CODE/newbot.start.js','const b=3;',f['sha256'])
        self.assertNotEqual(self.ws.plan(name)['test_receipt']['plan_sha256'],self.ws.plan(name)['plan_sha256'])
    def test_api_exposes_functions(self):
        x=ControlAPI(settings=self.s)
        b=x.bootstrap();self.assertIn('settings',b)
        self.assertTrue(callable(x.pi_status))
    def test_no_shell_start(self):
        from alcc.core import run_command
        with self.assertRaises(ValueError):run_command('echo hi')
    def test_context_gates_remain_present(self):
        (self.root/'coordinator'/'data'/'newbot-autonomy'/'session.stop').touch()
        prompt=self.ctx.build()
        self.assertIn('"session.stop": true',prompt)

if __name__=='__main__':unittest.main()

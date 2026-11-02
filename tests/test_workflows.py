import io,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings, filehash, load_json
from alcc.api import ControlAPI

class FakeWindow:
    def __init__(self): self.destroyed=False
    def destroy(self): self.destroyed=True
class FakeBrowser:
    def __init__(self):self.windows=[]
    def create_window(self,*a,**kw):
        w=FakeWindow();self.windows.append(w);return w

class Workflows(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name);self.s=Settings(self.root/'data')
        self.s.values['coordinator_dir']=str(self.root/'coordinator')
        self.s.values['workspace_dir']=str(self.root/'workspaces')
        (self.root/'coordinator'/'data'/'newbot-autonomy').mkdir(parents=True)
        self.browser=FakeBrowser()
        self.api=ControlAPI(self.s,self.browser)
        b=io.BytesIO()
        with tarfile.open(fileobj=b,mode='w:gz') as tar:
            x=b'let x=1;'
            info=tarfile.TarInfo('src/main.js');info.size=len(x)
            tar.addfile(info,io.BytesIO(x))
        self.name=self.api.ws.create_from_tar(b.getvalue(),'/home/riflex/CODE/newbot-coordinator/releases/test')['name']
    def change(self):
        f=self.api.read_file(self.name,'src/main.js')
        return self.api.save_file(self.name,'src/main.js','let x=2;',f['sha256'])
    def test_new_chat_does_not_destroy_old_before_confirm(self):
        first=self.api.open_chat();self.assertEqual(first['opened'],'embedded')
        old=self.browser.windows[0]
        h=self.api.new_chat(self.name)
        self.assertTrue(h['opened']);self.assertFalse(old.destroyed)
        self.assertIn('Projektzustand',h['prompt'])
        self.assertTrue(Path(h['file']).exists())
        self.api.confirm_new_chat(h['handoff_id']);self.assertTrue(old.destroyed)
        self.assertFalse(self.browser.windows[1].destroyed)
    def test_chat_confirmation_must_match_pending(self):
        self.api.new_chat(self.name)
        with self.assertRaises(ValueError): self.api.confirm_new_chat('wrong')
    def test_syntax_only_cannot_deploy(self):
        self.change();r=self.api.test_candidate(self.name,'syntax','TEST '+self.name)
        self.assertTrue(r['passed']);self.assertFalse(r['eligible_for_deploy'])
        with patch.object(self.api.pi,'status',return_value={'release':'/home/riflex/CODE/newbot-coordinator/releases/test','service':'active'}):
            p=self.api.deploy_preview(self.name)
        self.assertTrue(p['remote_base_matches']);self.assertFalse(p['deploy_eligible'])
    def test_release_mismatch_blocks(self):
        self.change();self.api.test_candidate(self.name,'syntax','TEST '+self.name)
        with patch.object(self.api.pi,'status',return_value={'release':'/other/releases/something'}):
            p=self.api.deploy_preview(self.name)
        self.assertFalse(p['remote_base_matches']);self.assertFalse(p['deploy_eligible'])
    def test_deploy_confirmation_must_match(self):
        with self.assertRaises(ValueError):self.api.deploy_apply(self.name,'fake','yes')
    def test_modified_file_after_receipt_invalidates(self):
        self.change();self.api.test_candidate(self.name,'syntax','TEST '+self.name)
        plan=self.api.validate_workspace(self.name)
        f=self.api.read_file(self.name,'src/main.js')
        self.api.save_file(self.name,'src/main.js','let x=3;',f['sha256'])
        self.assertNotEqual(plan['plan_sha256'],self.api.validate_workspace(self.name)['plan_sha256'])
    def test_no_chat_data_scraping(self):
        self.assertNotIn('previous_messages',self.api.ctx.build(self.name))
        h=self.api.new_chat(self.name)
        self.assertIn('Keine Sitzungsnotizen gespeichert',h['prompt'])
    def test_operator_stop_unchanged_by_snapshot_and_context(self):
        stop=self.root/'coordinator'/'data'/'newbot-autonomy'/'stop.request';stop.touch()
        self.api.project_prompt(self.name)
        self.api.list_files(self.name)
        self.assertTrue(stop.exists())

if __name__=='__main__':unittest.main()

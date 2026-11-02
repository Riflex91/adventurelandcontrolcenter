"""Offline synchronization tests: never connect to the actual Pi."""
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alcc.api import ControlAPI
from alcc.core import Settings, load_json

ROOT=Path(__file__).resolve().parents[1]
R1='/home/riflex/adventureland-headless/CODE/newbot-coordinator/releases/release-one'
R2='/home/riflex/adventureland-headless/CODE/newbot-coordinator/releases/release-two'


def tar_bytes(text):
    b=io.BytesIO()
    with tarfile.open(fileobj=b,mode='w:gz') as f:
        blob=text.encode()
        i=tarfile.TarInfo('src/main.js');i.size=len(blob)
        f.addfile(i,io.BytesIO(blob))
    return b.getvalue()


class AutoSyncTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.s=Settings(self.root/'app')
        self.s.values['workspace_dir']=str(self.root/'work')
        self.s.values['coordinator_dir']=str(self.root/'coord')
        self.api=ControlAPI(self.s)
        self.remote=R1
        self.source='let amount=1;'
        self.downloads=0
        self.status_patch=patch.object(self.api.pi,'status',side_effect=self._status)
        self.get_patch=patch.object(self.api.pi,'get_snapshot',side_effect=self._download)
        self.status_patch.start();self.get_patch.start()
        self.addCleanup(self.status_patch.stop);self.addCleanup(self.get_patch.stop)

    def _status(self):
        return {'release':self.remote,'service':'active'}

    def _download(self):
        self.downloads+=1
        return self.api.ws.create_from_tar(tar_bytes(self.source),self.remote)

    def test_first_sync_discards_test_workspace_and_downloads_real(self):
        fake=self.api.ws.create_from_tar(tar_bytes('TEST'),R1)
        first=self.api.sync_bot()
        self.assertTrue(first['verified_now'])
        self.assertNotEqual(first['workspace'],fake['name'])
        self.assertEqual(self.downloads,1)
        self.assertEqual(self.api.read_file(first['workspace'],'src/main.js')['text'],self.source)

    def test_same_release_reuses_edited_candidate_without_overwrite(self):
        first=self.api.sync_bot()
        name=first['workspace'];file=self.api.read_file(name,'src/main.js')
        self.api.save_file(name,'src/main.js','let amount=99;',file['sha256'])
        again=self.api.sync_bot()
        self.assertEqual(again['workspace'],name)
        self.assertFalse(again['created']);self.assertEqual(self.downloads,1)
        self.assertEqual(again['candidate_changed_count'],1)
        self.assertEqual(self.api.read_file(name,'src/main.js')['text'],'let amount=99;')

    def test_new_release_new_snapshot_old_edits_preserved(self):
        old=self.api.sync_bot()['workspace']
        file=self.api.read_file(old,'src/main.js')
        self.api.save_file(old,'src/main.js','old edits',file['sha256'])
        self.remote=R2;self.source='let amount=2;'
        updated=self.api.sync_bot()
        self.assertTrue(updated['release_changed'])
        self.assertTrue(updated['created'])
        self.assertNotEqual(old,updated['workspace'])
        self.assertEqual(self.api.read_file(old,'src/main.js')['text'],'old edits')
        self.assertEqual(self.api.read_file(updated['workspace'],'src/main.js')['text'],'let amount=2;')
        self.assertEqual(self.downloads,2)
        with self.assertRaisesRegex(ValueError,'nicht als aktuelle Pi-Basis'):
            self.api.project_prompt(old)
        self.assertEqual(self.api.read_file(updated['workspace'],'src/main.js')['text'],'let amount=2;')

    def test_network_failure_does_not_claim_new_validation(self):
        old=self.api.sync_bot()['workspace']
        with patch.object(self.api.pi,'status',side_effect=RuntimeError('SSH offline')):
            with self.assertRaisesRegex(RuntimeError,'SSH offline'):self.api.sync_bot()
        self.assertEqual(load_json(self.api.bot_sync.record_path)['workspace'],old)
        self.assertFalse(self.api.sync_cache()['verified_now'])
        self.assertEqual(self.downloads,1)

    def test_baseline_tamper_redownloads_without_overwrite(self):
        old=self.api.sync_bot()['workspace']
        (self.api.ws.location(old)/'baseline'/'src'/'main.js').write_text('tampered')
        new=self.api.sync_bot()
        self.assertNotEqual(new['workspace'],old)
        self.assertEqual(self.downloads,2)

    def test_failed_download_does_not_advance_record(self):
        original=self.api.sync_bot()['workspace'];self.remote=R2
        with patch.object(self.api.pi,'get_snapshot',side_effect=RuntimeError('download failed')):
            with self.assertRaisesRegex(RuntimeError,'download failed'):self.api.sync_bot()
        self.assertEqual(load_json(self.api.bot_sync.record_path)['workspace'],original)

    def test_no_remote_changes_or_writes_from_sync(self):
        self.api.sync_bot()
        self.assertFalse(self.api.coord.stops().get('stop.request',False))
        self.assertFalse(self.api.coord.stops().get('session.stop',False))
        self.assertEqual(self.downloads,1)

    def test_type_errors_and_workspace_selection(self):
        with self.assertRaises(ValueError):self.api.sync_bot('False')
        current=self.api.sync_bot()['workspace']
        self.api.bot_sync.assert_current(current)
        with self.assertRaises(ValueError):self.api.bot_sync.assert_current('old-test')

    def test_ui_wires_auto_refresh_and_sync_guard(self):
        html=(ROOT/'assets'/'index.html').read_text('utf8')
        bridge=(ROOT/'alcc'/'qt_desktop.py').read_text('utf8')
        self.assertIn('setInterval(()=>{if(window.pywebview?.api)syncBot(true,false)',html)
        self.assertIn("await call('sync_bot',false)",html)
        self.assertIn('if(!synced?.verified_now)',html)
        self.assertIn("'sync_bot','sync_cache'",bridge)
        self.assertIn('copyCurrentBotContext()',html)
        self.assertIn('Aktuellen Bot-Kontext für ChatGPT vorbereiten',html)
        self.assertNotIn("call('deploy_apply'",html[html.index('async function syncBot'):html.index('async function piStatus')])


if __name__=='__main__':unittest.main()

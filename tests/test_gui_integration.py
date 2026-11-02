"""Static and backend contract tests for Windows-integrated two-view UI.
The actual Windows Qt rendering/login is a separate end-to-end check.
"""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings
from alcc.api import ControlAPI
from alcc.diagnostics import local_diagnostics

ROOT=Path(__file__).resolve().parents[1]


class IntegratedGUIContract(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data=Path(tmp.name)
        self.settings=Settings(self.data/'app')

    def test_192_pi_is_default(self):
        self.assertEqual(self.settings.values['ssh_host'],'192.168.178.134')

    def test_correct_pi_release_root_is_default(self):
        self.assertEqual(self.settings.values['remote_root'],
                         'adventureland-headless/CODE/newbot-coordinator')

    def test_old_release_root_migrates_in_memory_only(self):
        legacy={'remote_root':'CODE/newbot-coordinator', 'ssh_host':'192.168.178.134'}
        self.settings.path.write_text(json.dumps(legacy),encoding='utf8')
        new=Settings(self.settings.data)
        self.assertEqual(new.values['remote_root'],
                         'adventureland-headless/CODE/newbot-coordinator')
        self.assertEqual(json.loads(self.settings.path.read_text(encoding='utf8'))['remote_root'],
                         'CODE/newbot-coordinator')

    def test_custom_release_root_not_overwritten(self):
        self.settings.path.write_text(json.dumps({'remote_root':'custom/another-root'}),encoding='utf8')
        self.assertEqual(Settings(self.settings.data).values['remote_root'],'custom/another-root')

    def test_remote_root_used_for_all_pi_operations(self):
        from alcc.remote import RaspberryPi
        from alcc.workspaces import Workspaces
        pi=RaspberryPi(self.settings,Workspaces(self.settings))
        bash=pi._vars()
        self.assertIn('adventureland-headless/CODE/newbot-coordinator',bash)
        self.assertIn('NO_RELEASES_DIR',bash)
        self.assertIn('check_current()',bash)

    def test_remote_current_link_guard_contract(self):
        """Portable guard: remote script requires real symlink and confines release to releases/."""
        from alcc.remote import RaspberryPi
        from alcc.workspaces import Workspaces
        script=RaspberryPi(self.settings,Workspaces(self.settings))._vars()
        self.assertIn('test -L "$ROOT/current"',script)
        self.assertIn('CURRENT="$(readlink -f "$ROOT/current")"',script)
        self.assertIn('case "$CURRENT" in "$ROOT"/releases/*)',script)
        self.assertIn('test -d "$CURRENT"',script)

    @unittest.skipIf(__import__('os').name == 'nt',
                     'Linux symlink semantics cannot be tested via Windows local filesystem without extra privileges')
    def test_remote_script_follows_expected_current_symlink(self):
        import os, subprocess
        from alcc.remote import RaspberryPi
        from alcc.workspaces import Workspaces
        home=self.data/'fake-pi-home'
        releases=home/'adventureland-headless/CODE/newbot-coordinator/releases'
        release=releases/'commit-123';release.mkdir(parents=True)
        root=releases.parent
        (root/'current').symlink_to(release,target_is_directory=True)
        pi=RaspberryPi(self.settings,Workspaces(self.settings))
        cmd=pi._vars()+'\ncheck_current\nprintf "RELEASE=%s\\n" "$CURRENT"\n'
        env={**os.environ,'HOME':str(home)}
        result=subprocess.run(['bash','-euo','pipefail','-c',cmd],capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'RELEASE='+str(release))

    def test_connection_failure_handled_as_ui_status(self):
        html=(ROOT/'assets/index.html').read_text('utf8')
        self.assertIn("e.message.includes('NO_RELEASES_DIR')",html)
        self.assertIn('return {reachable:false,error:e.message}',html)

    def test_old_pi_default_migrates_without_touching_saved_settings(self):
        original={'ssh_host':'100.76.36.122','ssh_user':'riflex'}
        self.settings.path.write_text(json.dumps(original),encoding='utf8')
        next_settings=Settings(self.settings.data)
        self.assertEqual(next_settings.values['ssh_host'],'192.168.178.134')
        self.assertEqual(json.loads(self.settings.path.read_text())['ssh_host'],'100.76.36.122')

    def test_custom_pi_is_preserved(self):
        self.settings.path.write_text(json.dumps({'ssh_host':'192.168.1.12'}))
        self.assertEqual(Settings(self.settings.data).values['ssh_host'],'192.168.1.12')

    def test_environment_only_reads(self):
        api=ControlAPI(settings=self.settings)
        before=set(self.settings.data.rglob('*'))
        d=api.environment_check()
        after=set(self.settings.data.rglob('*'))
        self.assertEqual(d['mode'],'read_only')
        self.assertFalse(d['raspberry_pi']['connectivity_checked'])
        self.assertEqual(d['raspberry_pi']['ssh_target'],'riflex@192.168.178.134')
        self.assertEqual(before,after)

    def test_no_start_required_to_check_coordinator_path(self):
        root=self.data/'coord';root.mkdir()
        (root/'newbot_autonomy.py').write_text('x=1')
        self.settings.values['coordinator_dir']=str(root)
        d=ControlAPI(settings=self.settings).environment_check()
        self.assertTrue(d['coordinator']['found'])
        self.assertFalse(d['coordinator']['control_center_process_running'])

    def test_gui_does_not_embed_chatgpt_as_iframe(self):
        html=(ROOT/'assets/index.html').read_text('utf8')
        self.assertIn("call('set_chat_visible',tab==='chat')",html)
        self.assertIn('new QWebChannel(',html)
        self.assertNotIn('<iframe',html.lower())

    def test_native_chat_is_not_exposed_to_local_api_bridge(self):
        source=(ROOT/'alcc/qt_desktop.py').read_text('utf8')
        self.assertIn('QSplitter(',source)
        self.assertIn('QWebEngineView(',source)
        self.assertIn('self.dashboard.page().setWebChannel(self.channel)',source)
        self.assertNotIn('view.page().setWebChannel(self.channel)',source)

    def test_chat_switch_contract(self):
        api=ControlAPI(settings=self.settings)
        self.assertEqual(api.set_chat_visible(True)['integrated'],False)

    def test_old_stop_marker_never_cleared_by_diagnostics(self):
        self.settings.values['coordinator_dir']=str(self.data/'sandboxed-coordinator')
        stop=Path(self.settings.values['coordinator_dir'])/'data'/'newbot-autonomy'/'stop.request'
        stop.parent.mkdir(parents=True,exist_ok=True)
        stop.touch()
        ControlAPI(settings=self.settings).environment_check()
        self.assertTrue(stop.exists())

if __name__=='__main__':unittest.main()

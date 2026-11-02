"""Offline regression tests: never use SSH and never deploy to the Pi."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from alcc.one_click import run_one_click, _test_modes

class OneClickTests(unittest.TestCase):
    def make_api(self, mode='normal'):
        root=tempfile.TemporaryDirectory();self.addCleanup(root.cleanup)
        candidate=Path(root.name)/'candidate';candidate.mkdir()
        (candidate/'bot.js').write_text('hello',encoding='utf8')
        (candidate/'test').mkdir()
        (candidate/'test'/'potions.test.cjs').write_text('test',encoding='utf8')
        ws=Mock()
        ws.plan.return_value={'changed':['bot.js'],'base_release':'/releases/current'}
        ws.location.return_value=Path(root.name)
        ws.candidate_file_list.return_value=[{'path':'bot.js'}, {'path':'test/potions.test.cjs'}]
        pi=Mock()
        pi.status.return_value={'release':'/releases/current','service':'active'}
        pi.preview.return_value={'deploy_eligible':True,'plan_sha256':'sha','changed':['bot.js']}
        pi.deploy.return_value={'new_release':'/releases/next','previous_release':'/releases/current'}
        api=SimpleNamespace(ws=ws,pi=pi,coord=Mock(),logs=Path(root.name)/'log',
                             _one_click_lock=threading.Lock())
        api.coord.stops.return_value={'session.stop':True, 'stop.request':True}
        api.test_candidate=Mock(side_effect=lambda name,mode,confirm:{'passed':True,'eligible_for_deploy':mode!='syntax',
                                                     'checked':2,'output':'OK'})
        return api
    def test_single_action_runs_checks_and_deploys(self):
        api=self.make_api()
        got=run_one_click(api,'workspace')
        self.assertTrue(got['deployed'])
        self.assertEqual(got['new_release'],'/releases/next')
        self.assertEqual([x.args[1] for x in api.test_candidate.call_args_list],['syntax','node'])
        api.pi.deploy.assert_called_once_with('workspace','sha','DEPLOY workspace')
        self.assertEqual(got['stop_markers']['session.stop'],True)
    def test_no_deploy_for_syntax_failure(self):
        api=self.make_api()
        api.test_candidate.side_effect=None
        api.test_candidate.return_value={'passed':False,'eligible_for_deploy':False,'checked':1,'output':'bad'}
        r=run_one_click(api,'workspace');self.assertFalse(r['ok'])
        api.pi.deploy.assert_not_called()
    def test_no_deploy_for_node_failure(self):
        api=self.make_api()
        api.test_candidate.side_effect=[{'passed':True,'eligible_for_deploy':False,'checked':1,'output':'OK'},
                                        {'passed':False,'eligible_for_deploy':False,'checked':1,'output':'bad'}]
        r=run_one_click(api,'workspace');self.assertFalse(r['ok'])
        api.pi.deploy.assert_not_called()
    def test_rejects_changed_remote_release_before_testing(self):
        api=self.make_api();api.pi.status.return_value={'release':'/releases/other','service':'active'}
        r=run_one_click(api,'workspace');self.assertFalse(r['ok'])
        api.test_candidate.assert_not_called();api.pi.deploy.assert_not_called()
    def test_rejects_inactive_service(self):
        api=self.make_api();api.pi.status.return_value={'release':'/releases/current','service':'inactive'}
        r=run_one_click(api,'workspace');self.assertFalse(r['ok'])
        api.pi.deploy.assert_not_called()
    def test_rejects_no_changes(self):
        api=self.make_api();api.ws.plan.return_value={'changed':[],'base_release':'/releases/current'}
        r=run_one_click(api,'workspace');self.assertFalse(r['ok']);api.pi.deploy.assert_not_called()
    def test_rejects_no_function_tests(self):
        api=self.make_api();api.ws.candidate_file_list.return_value=[{'path':'bot.js'}]
        r=run_one_click(api,'workspace');self.assertFalse(r['ok']);api.pi.deploy.assert_not_called()
    def test_rejects_unapproved_preview(self):
        api=self.make_api();api.pi.preview.return_value={'deploy_eligible':False,'plan_sha256':'sha'}
        r=run_one_click(api,'workspace');self.assertFalse(r['ok']);api.pi.deploy.assert_not_called()
    def test_no_parallel_deployment(self):
        api=self.make_api();api._one_click_lock.acquire()
        try:r=run_one_click(api,'workspace')
        finally:api._one_click_lock.release()
        self.assertEqual(r['stage'],'busy');api.pi.deploy.assert_not_called()
    def test_renders_deploy_error_not_success(self):
        api=self.make_api();api.pi.deploy.side_effect=RuntimeError('DEPLOY_ROLLED_BACK')
        r=run_one_click(api,'workspace')
        self.assertFalse(r['ok']);self.assertFalse(r['deployed']);self.assertEqual(r['stage'],'deploy')
    def test_has_python_and_node_tests(self):
        api=self.make_api();api.ws.candidate_file_list.return_value+=[{'path':'tests/test_regression.py'}]
        self.assertEqual(_test_modes(api,'workspace'),['python','node'])

if __name__=='__main__':unittest.main()

"""No network, process, or Pi mutations. GUI and coordinator behavior contracts."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from alcc.core import Settings
from alcc.coordinator import Coordinator
from alcc.ollama_lifecycle import OllamaLifecycle, MODEL

ROOT=Path(__file__).resolve().parents[1]

class MonitoredProcess:
    pid=44521
    def __init__(self):self.alive=True
    def poll(self):return None if self.alive else 0

class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=Settings(Path(self.tmp.name)/'settings')
        self.coordinator_root=Path(self.tmp.name)/'coord'
        (self.coordinator_root/'data/newbot-autonomy').mkdir(parents=True)
        (self.coordinator_root/'newbot_autonomy.py').write_text('# stub',encoding='utf8')
        self.settings.values['coordinator_dir']=str(self.coordinator_root)
        self.coord=Coordinator(self.settings)
        self.coord._reset_console()

    def test_auto_supervisor_safety_hold_requests_official_stop(self):
        p=MonitoredProcess()
        (self.coordinator_root/'data/newbot-autonomy/session.json').write_text(json.dumps({'pid':p.pid,'phase':'safety_hold'}))
        def fake_execute(command):
            self.assertEqual(command,'stop')
            p.alive=False
            return {'exit_code':0}
        with patch.object(self.coord,'execute',side_effect=fake_execute) as action:
            self.coord._watch_automatic(p)
        action.assert_called_once_with('stop')
        self.assertTrue(any('Schutzstopp erkannt' in x for x in self.coord.running_logs()['lines']))

    def test_stop_marker_requires_no_duplicate_cli_stop(self):
        p=MonitoredProcess()
        (self.coordinator_root/'data/newbot-autonomy/stop.request').touch()
        with patch.object(self.coord,'execute') as action:
            self.coord._watch_automatic(p)
        action.assert_not_called()
        self.assertTrue((self.coordinator_root/'data/newbot-autonomy/stop.request').exists())

    def test_stale_safety_hold_pid_is_ignored(self):
        p=MonitoredProcess(); checks=0
        (self.coordinator_root/'data/newbot-autonomy/session.json').write_text(json.dumps({'pid':p.pid-1,'phase':'safety_hold'}))
        def polled():
            nonlocal checks
            checks+=1
            return None if checks < 2 else 0
        p.poll=polled
        with patch('alcc.coordinator.time.sleep'),patch.object(self.coord,'execute') as action:
            self.coord._watch_automatic(p)
        action.assert_not_called()

    def test_transient_recovery_is_not_fatal(self):
        p=MonitoredProcess(); calls=0
        (self.coordinator_root/'data/newbot-autonomy/session.json').write_text(json.dumps({'pid':p.pid,'phase':'recovery_wait'}))
        def polled():
            nonlocal calls
            calls+=1
            return None if calls < 2 else 0
        p.poll=polled
        with patch('alcc.coordinator.time.sleep'),patch.object(self.coord,'execute') as action:
            self.coord._watch_automatic(p)
        action.assert_not_called()

    def test_unload_only_model_does_not_kill_external_server(self):
        manager=OllamaLifecycle()
        with patch.object(manager,'_request',return_value=b'{}') as req:
            report=manager.shutdown()
        self.assertTrue(report['unloaded']);self.assertFalse(report['server_terminated'])
        req.assert_called_once_with('/api/generate',{'model':MODEL,'keep_alive':0,'stream':False},timeout=12)

    def test_managed_server_terminated_on_cleanup(self):
        manager=OllamaLifecycle()
        p=Mock();p.poll.return_value=None;manager.owned_server=p
        with patch.object(manager,'_request',return_value=b'{}'):
            result=manager.shutdown()
        p.terminate.assert_called_once_with();p.wait.assert_called_once_with(timeout=8)
        self.assertTrue(result['server_terminated']);self.assertIsNone(manager.owned_server)

    def test_session_deadline_vs_work_budget_contract(self):
        text=(ROOT/'assets/index.html').read_text('utf8')
        self.assertIn('ohne Sitzungszeitlimit bis zum Abschluss',text)
        self.assertNotIn('maximal 30 Minuten bearbeiten lassen',text)
        self.assertIn('entlädt das Koordinator-Modell',text)
        self.assertIn('Unbegrenzter Bedienerauftrag', (ROOT/'alcc/coordinator.py').read_text('utf8'))

if __name__ == '__main__':unittest.main()

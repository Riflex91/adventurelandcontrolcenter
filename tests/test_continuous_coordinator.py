"""Dauerbetrieb / Live-Terminal: strictly offline test contracts."""
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings
from alcc.api import ControlAPI
from alcc.coordinator import Coordinator

ROOT=Path(__file__).resolve().parents[1]

class OutputProcess:
    def __init__(self, lines):
        self.pid=123
        self.stdout=io.StringIO(lines)
        self.stdin=io.StringIO()
        self.code=0
    def poll(self):return self.code
    def wait(self):return self.code

class ContinuousCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=Settings(Path(self.temp.name)/'app')
        self.root=Path(self.temp.name)/'coordinator'
        (self.root/'data/newbot-autonomy').mkdir(parents=True)
        for name in ('newbot_autonomy.py','autonomy_interactive.py','autonomy_orders.py'):
            (self.root/name).write_text('pass',encoding='utf8')
        self.settings.values['coordinator_dir']=str(self.root)
        self.coord=Coordinator(self.settings)

    def test_live_lines_incremental_and_persisted(self):
        self.coord._reset_console()
        fake=OutputProcess('Zeile eins\nWARN: 日本語\n')
        self.coord.proc=fake
        self.coord._collect(fake)
        first=self.coord.running_logs()
        self.assertFalse(first['running'])
        self.assertEqual(first['exit_code'],0)
        self.assertTrue(any('日本語' in x for x in first['lines']))
        self.assertTrue(any(x.startswith('PROCESS_EXIT=0') for x in first['lines']))
        self.assertTrue(Path(first['log_path']).is_file())
        self.assertIn('WARN: 日本語',Path(first['log_path']).read_text('utf8'))
        same=self.coord.running_logs(first['sequence'])
        self.assertEqual(same['lines'],[])
        self.coord._append_console('Neue Meldung')
        newer=self.coord.running_logs(first['sequence'])
        self.assertEqual(newer['lines'],['Neue Meldung'])

    def test_console_ring_bounded_and_monotonic(self):
        self.coord._reset_console()
        for i in range(2405):self.coord._append_console('line '+str(i))
        result=self.coord.running_logs(1)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['lines']),2000)
        self.assertEqual(result['sequence'],2405)
        self.assertEqual(result['lines'][-1],'line 2404')
        self.assertEqual(len(self.coord.running_logs()['lines']),250)

    def test_reject_malformed_console_cursor(self):
        for cursor in (-1,'12',True):
            with self.assertRaises(ValueError):self.coord.running_logs(cursor)

    def test_stops_prevent_autostart_without_raising_process(self):
        (self.root/'data/newbot-autonomy/stop.request').touch()
        with patch('alcc.coordinator.subprocess.Popen') as popen:
            with self.assertRaisesRegex(ValueError,'STOP-Marker'):
                self.coord.start_automatic('START AUTO SESSION')
            popen.assert_not_called()

    def test_interface_live_panel_and_polling(self):
        html=(ROOT/'assets/index.html').read_text('utf8')
        self.assertIn('Koordinator / KI – Live-Konsole',html)
        self.assertIn('id="coord-output"',html)
        self.assertIn('coord-autoscroll',html)
        self.assertIn('refreshCoordLogs()},1200)',html)
        self.assertIn('OHNE Sitzungszeitlimit',html)
        self.assertNotIn('maximal 30 Minuten automatisch',html)
        self.assertIn('Automatisch weiterarbeiten',html)
        self.assertIn('Manuelle Aufgabe erteilen',html)
        self.assertNotIn('id="coord-mode"',html)

    def test_new_console_run_resets_sequence(self):
        self.coord._reset_console()
        self.coord._append_console('A')
        a=self.coord.running_logs()
        self.coord._reset_console()
        b=self.coord.running_logs()
        self.assertNotEqual(a['run_id'],b['run_id'])
        self.assertEqual(b['sequence'],0)
        self.assertEqual(b['lines'],[])

if __name__=='__main__':unittest.main()

class LogRotationTests(unittest.TestCase):
    def test_log_retention_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            settings=Settings(Path(root)/'app')
            settings.values['coordinator_dir']=str(Path(root)/'fake')
            coord=Coordinator(settings)
            coord._reset_console()
            proc=OutputProcess(''.join('line '+str(i)+'-abc\n' for i in range(200)))
            coord.proc=proc
            with patch('alcc.coordinator.CONSOLE_LOG_PART_BYTES',140):
                coord._collect(proc)
            files=list(coord.log_path.parent.glob(coord.log_path.name+'*'))
            self.assertLessEqual(len(files),4)
            self.assertGreaterEqual(len(files),2)
            self.assertEqual(coord.running_logs()['exit_code'],0)
            self.assertIn('PROCESS_EXIT=0',coord.running_logs()['lines'])

"""K1-K8 stop gates and user-directed task input; offline only."""
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings
from alcc.api import ControlAPI
from alcc.coordinator import Coordinator

ROOT=Path(__file__).resolve().parents[1]

class FakeInput(io.StringIO):
    def close(self):
        self.was_closed=True   # keep buffer inspectable after simulated close

class FakeProcess:
    def __init__(self):
        self.stdin=FakeInput()
        self.stdout=io.StringIO()
        self.pid=4567
    def poll(self):return None
    def wait(self):return 0

class SimpleCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.settings=Settings(self.root/'app')
        self.coord_root=self.root/'coordinator'
        (self.coord_root/'data/newbot-autonomy').mkdir(parents=True)
        for name in ('newbot_autonomy.py','autonomy_interactive.py','autonomy_orders.py'):
            (self.coord_root/name).write_text('# placeholder',encoding='utf8')
        self.settings.values['coordinator_dir']=str(self.coord_root)
        self.c=Coordinator(self.settings)

    def test_manual_missing_confirmation_never_starts(self):
        with patch('alcc.coordinator.subprocess.Popen') as start:
            with self.assertRaisesRegex(ValueError,'Startbestätigung'):
                self.c.start_manual('fix mp potions','wrong')
            start.assert_not_called()

    def test_automatic_missing_confirmation_never_starts(self):
        with patch('alcc.coordinator.subprocess.Popen') as start:
            with self.assertRaises(ValueError): self.c.start_automatic('wrong')
            start.assert_not_called()

    def test_stop_blocks_both_modes(self):
        for marker in ('stop.request','session.stop'):
            stop=self.coord_root/'data/newbot-autonomy'/marker
            stop.touch()
            with patch('alcc.coordinator.subprocess.Popen') as start:
                with self.assertRaisesRegex(ValueError,'STOP-Marker'):
                    self.c.start_automatic('START AUTO SESSION')
                with self.assertRaisesRegex(ValueError,'STOP-Marker'):
                    self.c.start_manual('fix mp potions','START MANUAL TASK')
                start.assert_not_called()
            stop.unlink()

    def test_bad_manual_text_cannot_inject_extra_menu_lines(self):
        for value in ('',' '*20, 'x'*4001,'foo\0bar'):
            with patch('alcc.coordinator.subprocess.Popen') as start:
                with self.assertRaises(ValueError):
                    self.c.start_manual(value,'START MANUAL TASK')
                start.assert_not_called()

    def test_manual_task_uses_real_existing_interactive_mode(self):
        # Hash validation has dedicated tests; mock it only in the process contract.
        fp=FakeProcess()
        with patch('alcc.coordinator.subprocess.Popen',return_value=fp) as start, \
             patch('alcc.coordinator.threading.Thread') as thread, \
             patch.object(self.c.ollama,'ensure_ready'), \
             patch.object(self.c,'_verify_manual_compatibility'):
            r=self.c.start_manual('Prüfe MP-Tränke\nund Ranger','START MANUAL TASK')
        self.assertTrue(r['started']);self.assertEqual(r['mode'],'manual');self.assertEqual(r['session_minutes'],0)
        argv=start.call_args.args[0]
        self.assertEqual(argv[2:5],['-B','-m','newbot_autonomy'])
        self.assertEqual(argv[5],'interactive')
        self.assertIn('--open-features',argv)
        self.assertEqual(argv[argv.index('--session-minutes')+1],'0')
        self.assertEqual(fp.stdin.getvalue(),'2\nPrüfe MP-Tränke und Ranger\n')
        self.assertEqual(start.call_args.kwargs['env']['PYTHONUTF8'],'1')
        self.assertEqual(start.call_args.kwargs['cwd'],str(self.coord_root))
        self.assertNotIn('Prüfe', ' '.join(argv))  # task not passed as executable arguments
        thread.assert_called_once()

    def test_unpatched_manual_coordinator_is_refused(self):
        with patch('alcc.coordinator.subprocess.Popen') as p:
            with self.assertRaisesRegex(ValueError,'Kompatibilitätspatch'):
                self.c.start_manual('Fertigstellen','START MANUAL TASK')
            p.assert_not_called()

    def test_auto_runs_without_global_deadline_or_single_batch(self):
        fp=FakeProcess()
        with patch('alcc.coordinator.subprocess.Popen',return_value=fp) as start, \
             patch('alcc.coordinator.threading.Thread'), \
             patch.object(self.c.ollama,'ensure_ready'):
            r=self.c.start_automatic('START AUTO SESSION')
        self.assertEqual(r['mode'],'automatic')
        self.assertEqual(r['session_minutes'],0)
        self.assertTrue(r['unlimited_session'])
        argv=start.call_args.args[0]
        self.assertEqual(argv[5],'work')
        self.assertIn('--open-features',argv)
        self.assertEqual(argv[argv.index('--session-minutes')+1],'0')
        self.assertEqual(argv[argv.index('--repairs')+1],'-1')
        self.assertNotIn('--once',argv)
        self.assertNotIn('--managed-session',argv)
        self.assertEqual(start.call_args.kwargs['stdin'],__import__('subprocess').DEVNULL)
        self.assertEqual(start.call_args.kwargs['env']['PYTHONUNBUFFERED'],'1')

    def test_cli_module_contract_required(self):
        (self.coord_root/'autonomy_orders.py').unlink()
        with patch('alcc.coordinator.subprocess.Popen') as start:
            with self.assertRaisesRegex(ValueError,'Bedieneraufträge'):
                self.c.start_manual('fix','START MANUAL TASK')
            start.assert_not_called()

    def test_qt_bridge_has_only_named_methods(self):
        qt=(ROOT/'alcc/qt_desktop.py').read_text('utf8')
        self.assertIn("'coord_auto','coord_manual'",qt)
        self.assertNotIn('eval(',qt)

    def test_interface_minimal_and_no_dropdown(self):
        html=(ROOT/'assets/index.html').read_text('utf8')
        co=html[html.index('<section class="tab" id="tab-coordinator">'):html.index('<section class="tab" id="tab-editor">')]
        ch=html[html.index('<section class="tab" id="tab-chat">'):html.index('<section class="tab" id="tab-coordinator">')]
        self.assertIn('Automatisch weiterarbeiten',co)
        self.assertIn('Manuelle Aufgabe erteilen',co)
        self.assertIn('coord-task',co)
        self.assertIn('coord-gates',co)
        self.assertNotIn('coord-mode',co)
        self.assertNotIn('coord-advanced',co)
        self.assertNotIn('Assess',co)
        self.assertNotIn('id="chat-workspace"',ch)
        self.assertIn('aktuell synchronisierte Bot',ch)
        self.assertIn("call('new_chat')",html)
        self.assertNotIn("call('new_chat',synced.workspace)",html)
        self.assertIn("call('coord_manual',task,'START MANUAL TASK')",html)
        self.assertIn("call('coord_auto','START AUTO SESSION')",html)

    def test_new_chat_defaults_to_verified_sync(self):
        api=ControlAPI(settings=self.settings)
        fake=io.BytesIO()
        with tarfile.open(fileobj=fake,mode='w:gz') as tar:
            payload=b'let x=1;'
            info=tarfile.TarInfo('main.js');info.size=len(payload)
            tar.addfile(info,io.BytesIO(payload))
        release='/home/riflex/adventureland-headless/CODE/newbot-coordinator/releases/release-new'
        with patch.object(api.pi,'status',return_value={'release':release,'service':'active'}), \
             patch.object(api.pi,'get_snapshot',side_effect=lambda: api.ws.create_from_tar(fake.getvalue(),release)), \
             patch('alcc.api.webbrowser.open',return_value=True):
            api.sync_bot()
            handoff=api.new_chat()
        self.assertTrue(handoff['opened'])
        current=api.bot_sync.cached()['last_workspace']
        self.assertTrue(current)
        self.assertIn('Manuell bearbeiteter Arbeitsbereich: '+current,handoff['prompt'])
        self.assertIn(release,handoff['prompt'])

    def test_no_network_no_handoff_and_no_new_chat(self):
        api=ControlAPI(settings=self.settings)
        with patch.object(api.pi,'status',side_effect=RuntimeError('SSH down')), \
             patch('alcc.api.webbrowser.open') as browser:
            with self.assertRaisesRegex(ValueError,'nicht frisch bestätigt'):api.new_chat()
            browser.assert_not_called()
        self.assertEqual(list(api.ctx.handoffs.glob('*.md')),[])

if __name__=='__main__':unittest.main()

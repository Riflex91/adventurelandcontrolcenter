import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alcc.core import Settings
from alcc.api import ControlAPI

ROOT=Path(__file__).resolve().parents[1]

class ContextStartTests(unittest.TestCase):
    def test_auto_handoff_persists_and_needs_user_paste(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(Path(folder)/'data')
            api=ControlAPI(settings=settings)
            api.ctx.update({'goal':'mana potions only','notes':'current task','session_notes':'verified observations'})
            with patch('alcc.api.put_clipboard',return_value=True):
                handoff=api.prepare_chat_context()
            self.assertTrue(handoff['paste_required'])
            self.assertTrue(handoff['copied'])
            data=Path(handoff['path']).read_bytes()
            self.assertIn(b'mana potions only',data)
            self.assertEqual(handoff['sha256'],hashlib.sha256(data).hexdigest())
            self.assertNotIn('password=',data.decode('utf-8'))

    def test_chat_page_does_not_get_privileged_bridge(self):
        qt=(ROOT/'alcc/qt_desktop.py').read_text()
        self.assertIn('self.chat_host.set_visible(True)',qt)
        self.assertIn('prepare_chat_context',qt)
        self.assertNotIn('self.current.view.page().setWebChannel',qt)

if __name__=='__main__':unittest.main()

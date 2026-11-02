from pathlib import Path
import tempfile
import unittest
from alcc.chat_resume import clean_chat_url, read_chat_url, store_chat_url

class LastChatTests(unittest.TestCase):
    def test_persist_and_resume_same_conversation(self):
        with tempfile.TemporaryDirectory() as d:
            link='https://chatgpt.com/c/abc-123?session_secret=bad#fragment'
            self.assertTrue(store_chat_url(Path(d),link))
            self.assertEqual(read_chat_url(Path(d)),'https://chatgpt.com/c/abc-123')

    def test_rejects_unsafe_urls(self):
        with tempfile.TemporaryDirectory() as d:
            for link in ('http://chatgpt.com/c/a','https://evil.invalid/c/a','https://chatgpt.com/',
                         'https://chatgpt.com/c/../bad','https://chatgpt.com@evil.invalid/c/a'):
                self.assertFalse(store_chat_url(Path(d),link),link)
            self.assertIsNone(read_chat_url(Path(d)))

    def test_gpt_project_path(self):
        self.assertEqual(clean_chat_url('https://chatgpt.com/g/g-p-abc/c/123'),
                         'https://chatgpt.com/g/g-p-abc/c/123')

if __name__=='__main__':unittest.main()

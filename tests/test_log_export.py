"""Offline tests for the one-click diagnostic ZIP; never access a real Pi."""
from __future__ import annotations
import io
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch
from alcc.log_export import (
    REMOTE_BUNDLE, MAX_STREAM, create_zip, export_logs, redact, desktop_folder
)


def sample_tar(path: Path, extra=None):
    items=[('logs/characters/newbot/My_Ranger1.jsonl',b'{"type":"action_result","skill":"use_mp","token":"abc12345678"}\n'),
           ('logs/systemd-service.log',b'Bot active\n'),
           ('bot-source/bot.js',b'const foo = 42;\n'),
           ('meta/manifest.json',json.dumps({'active_release':'test_release','included':[{'path':'bot-source/bot.js','sha256':hashlib.sha256(b'const foo = 42;\n').hexdigest()}]}).encode())]
    if extra is not None:items=extra
    with tarfile.open(path,'w') as tf:
        for name,data in items:
            info=tarfile.TarInfo(name);info.size=len(data);tf.addfile(info,io.BytesIO(data))


class LogExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.tar=self.root/'in.tar';sample_tar(self.tar)

    def test_remote_bundle_is_read_only_and_restricted(self):
        self.assertIn("/mnt/adventureland-ssd/logs/characters/newbot",REMOTE_BUNDLE)
        self.assertIn("release/'bot.js'",REMOTE_BUNDLE)
        self.assertIn('check_current',REMOTE_BUNDLE)
        for banned in ('sudo ', 'systemctl restart','rm -','mkdir ','scp ','mv ','touch '):
            self.assertNotIn(banned,REMOTE_BUNDLE)

    def test_zip_contains_logs_code_release_manifest(self):
        p=self.root/'bundle.zip'
        r=create_zip(self.tar,p)
        self.assertEqual(r['files'],4)
        with zipfile.ZipFile(p) as z:
            self.assertIn('bot-source/bot.js',z.namelist())
            self.assertIn('logs/systemd-service.log',z.namelist())
            self.assertIn('meta/manifest.json',z.namelist())
            self.assertIn('BITTE_LESEN.txt',z.namelist())
            self.assertNotIn(b'abc12345678',z.read('logs/characters/newbot/My_Ranger1.jsonl'))
            self.assertIn(b'"skill":"use_mp"',z.read('logs/characters/newbot/My_Ranger1.jsonl'))

    def test_redacts_authorization_and_secrets(self):
        raw=b'api_key = Secret123\nAuthorization: Bearer abcdefghijklmnopqrstuv\n{"password":"hello"}\n'
        sanitized=redact(raw)
        for token in (b'Secret123',b'abcdefghijklmnopqrstuv',b'hello'):
            self.assertNotIn(token,sanitized)


    def test_source_is_byte_exact_even_when_it_looks_like_a_secret(self):
        script=b"function x(){ const token = x; const password = 'not-a-secret'; return token; }\n"
        manifest={'included':[{'path':'bot-source/bot.js','sha256':hashlib.sha256(script).hexdigest()}]}
        sample_tar(self.tar,[('logs/t.log',b'password=REAL_SECRET\n'),
            ('bot-source/bot.js',script),('meta/manifest.json',json.dumps(manifest).encode())])
        p=self.root/'bundle.zip';create_zip(self.tar,p)
        with zipfile.ZipFile(p) as z:
            self.assertEqual(z.read('bot-source/bot.js'),script)
            self.assertNotIn(b'REAL_SECRET',z.read('logs/t.log'))
            detail=json.loads(z.read('meta/export-integrity.json'))
            check=next(x for x in detail['files'] if x['file']=='bot-source/bot.js')
            self.assertEqual(check['mode'],'unchanged_source')
            self.assertEqual(check['sha256_in_zip'],hashlib.sha256(script).hexdigest())

    def test_known_source_secret_refuses_export_instead_of_corruption(self):
        script=b"const api_key = 'abcdefghijklmnopqrstuvwxyz01234';"
        manifest={'included':[{'path':'bot-source/bot.js','sha256':hashlib.sha256(script).hexdigest()}]}
        sample_tar(self.tar,[('logs/l.log',b'ok'),('bot-source/bot.js',script),
            ('meta/manifest.json',json.dumps(manifest).encode())])
        with self.assertRaisesRegex(ValueError,'Private key'):
            create_zip(self.tar,self.root/'blocked.zip')
        self.assertFalse((self.root/'blocked.zip').exists())

    def test_source_manifest_mismatch_is_rejected(self):
        sample_tar(self.tar,[('logs/t.log',b'ok'),('bot-source/bot.js',b'const a=1;'),
            ('meta/manifest.json',json.dumps({'included':[{'path':'bot-source/bot.js','sha256':'0'*64}]}).encode())])
        with self.assertRaisesRegex(ValueError,'SHA256'):
            create_zip(self.tar,self.root/'bad.zip')
        self.assertFalse((self.root/'bad.zip').exists())

    def test_remote_bundling_reserves_client_log_and_errors(self):
        self.assertLess(REMOTE_BUNDLE.index("add('logs/headless-client.log'"),
                        REMOTE_BUNDLE.index("add('logs/characters/newbot/'"))
        self.assertIn("add('meta/action-errors.json'",REMOTE_BUNDLE)
        self.assertIn('unexpected_error_counts',REMOTE_BUNDLE)

    def test_rejects_path_traversal(self):
        sample_tar(self.tar,[('../outside',b'x')])
        with self.assertRaisesRegex(ValueError,'Unerlaubtes Archivmitglied'):
            create_zip(self.tar,self.root/'bad.zip')
        self.assertFalse((self.root/'bad.zip').exists())

    def test_rejects_symlink_tar_entry(self):
        with tarfile.open(self.tar,'w') as tf:
            i=tarfile.TarInfo('logs/evil');i.type=tarfile.SYMTYPE;i.linkname='/etc/shadow';tf.addfile(i)
        with self.assertRaisesRegex(ValueError,'Unerlaubtes Archivmitglied'):
            create_zip(self.tar,self.root/'bad.zip')

    def test_rejects_duplicate_archive_names(self):
        sample_tar(self.tar,[('logs/a',b'x'),('logs/a',b'y')])
        with self.assertRaisesRegex(ValueError,'Unerlaubtes Archivmitglied'):
            create_zip(self.tar,self.root/'bad.zip')

    def test_rejects_missing_logs(self):
        sample_tar(self.tar,[('meta/manifest.json',b'{}')])
        with self.assertRaisesRegex(ValueError,'Keine aktuellen Logs'):
            create_zip(self.tar,self.root/'bad.zip')

    def test_rejects_large_item(self):
        sample_tar(self.tar,[('logs/big.log',b'A'*(3*1024*1024+1))])
        with self.assertRaisesRegex(ValueError,'Unerlaubtes Archivmitglied'):
            create_zip(self.tar,self.root/'bad.zip')

    def test_does_not_overwrite_existing_zip(self):
        p=self.root/'bundle.zip'
        create_zip(self.tar,p)
        old=p.read_bytes()
        with self.assertRaises(FileExistsError):create_zip(self.tar,p)
        self.assertEqual(old,p.read_bytes())

    def test_one_click_api_export_and_lock(self):
        class API:
            pi=object()
            _logs_export_lock=threading.Lock()
        def fake_recv(pi, dest):dest.write_bytes(self.tar.read_bytes())
        with patch('alcc.log_export.receive_remote_tar',side_effect=fake_recv):
            result=export_logs(API(),desktop=self.root)
            self.assertTrue(Path(result['path']).is_file())
            self.assertEqual(result['files'],4)
        API._logs_export_lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError,'läuft bereits'):
                export_logs(API(),desktop=self.root)
        finally:API._logs_export_lock.release()

    def test_ui_and_bridge_connected(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'assets/index.html').read_text('utf8')
        api=(root/'alcc/api.py').read_text('utf8')
        qt=(root/'alcc/qt_desktop.py').read_text('utf8')
        self.assertIn('Aktuelle Logs herunterladen',html)
        self.assertIn("call('export_logs_desktop')",html)
        self.assertIn('def export_logs_desktop(self)',api)
        self.assertIn("'export_logs_desktop'",qt)

if __name__ == '__main__':unittest.main()

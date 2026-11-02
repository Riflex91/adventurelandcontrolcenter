import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
import update_launcher as u


def make_zip(entries):
    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


class UpdateTests(unittest.TestCase):
    def test_only_release_from_expected_repo(self):
        tag = 'v1.3.0'
        p = f'https://github.com/{u.REPO}/releases/download/{tag}/'
        r = {'tag_name':tag, 'assets':[
            {'name':u.ASSET,'browser_download_url':p+u.ASSET},
            {'name':u.CHECKSUM,'browser_download_url':p+u.CHECKSUM}]}
        self.assertEqual(u.release_assets(r)[0], tag)
        r['assets'][0]['browser_download_url'] = 'https://evil.invalid/bad.zip'
        with self.assertRaises(ValueError):u.release_assets(r)

    def test_no_prerelease(self):
        with self.assertRaises(ValueError):u.release_assets({'tag_name':'v1.3.0', 'prerelease':True})

    def test_checksum(self):
        blob = b'hello'
        u.check_digest(blob, hashlib.sha256(blob).hexdigest().encode())
        with self.assertRaises(ValueError):u.check_digest(blob, b'0'*64)

    def test_archive_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'a.zip';f.write_bytes(make_zip({'../escape.txt':'oops'}))
            with self.assertRaises(ValueError):u.unpack_safe(f,Path(d)/'output')
            self.assertFalse((Path(d)/'escape.txt').exists())

    def test_install_to_versioned_dir(self):
        with tempfile.TemporaryDirectory() as d:
            data=make_zip({'AdventureLandControlCenter/AdventureLandControlCenter.exe':b'exe',
                           'AdventureLandControlCenter/_internal/example.dat':b'bytes'})
            installed=u.install_release(Path(d),'v1.3.0',data)
            self.assertEqual(installed,(Path(d)/'versions'/'v1.3.0'))
            self.assertEqual(u.find_latest_installed(Path(d)),installed)
            self.assertEqual(u.install_release(Path(d),'v1.3.0',data),installed)

    def test_start_only_installed_exe(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):u.start_application(Path(d))

if __name__ == '__main__':unittest.main()

import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from app.documents import office_binary
from scripts import launch, serve_desktop


class Desktop(unittest.TestCase):
    def test_existing_dependencies_do_not_require_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'requirements.txt').write_text('fastapi==0.141.1\n')
            python = root/'.venv'/('Scripts/python.exe' if launch.sys.platform == 'win32' else 'bin/python')
            python.parent.mkdir(parents=True);python.touch()
            (root/'.venv/.certicoex-dependencies').write_text(hashlib.sha256((root/'requirements.txt').read_bytes()).hexdigest())
            with patch.object(launch,'ROOT',root), patch.object(launch.subprocess,'run') as install, patch.object(launch.subprocess,'call',return_value=0) as serve:
                self.assertEqual(launch.main(),0)
                install.assert_not_called()
                self.assertIn('serve_desktop.py',serve.call_args.args[0][-1])

    def test_opens_browser_only_after_health_check(self):
        with patch.object(serve_desktop,'urlopen',return_value=BytesIO(b'{"status":"ok"}')), patch.object(serve_desktop.webbrowser,'open') as open_browser:
            serve_desktop.open_when_ready('http://127.0.0.1:8000')
            open_browser.assert_called_once_with('http://127.0.0.1:8000')

    def test_windows_libreoffice_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            office = Path(folder)/'LibreOffice/program/soffice.exe'
            office.parent.mkdir(parents=True);office.touch()
            original=Path.is_file
            def exists(path):
                return original(path) if path == office else False
            with patch('app.documents.shutil.which',return_value=None), patch.dict('os.environ',{'PROGRAMFILES':folder,'CERTICOEX_LIBREOFFICE':''}), patch.object(Path,'is_file',exists):
                self.assertEqual(office_binary(),str(office))


if __name__ == '__main__':
    unittest.main()

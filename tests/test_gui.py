import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
import time
import unittest
from PySide6.QtWidgets import QApplication
from ncmstudio.gui import MainWindow, STYLE
from test_core import make_fixture


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def test_scan_select_convert_and_invalidate(self):
        with tempfile.TemporaryDirectory(prefix='ncm-gui-test-') as directory:
            root = Path(directory)
            source = root / 'input'
            source.mkdir()
            make_fixture(source, '歌曲.ncm')
            make_fixture(source, '有损.ncm', codec='MP3')
            window = MainWindow(str(source), str(root / 'output'), root / 'settings.json')

            def wait_worker():
                deadline = time.monotonic() + 20
                while window.worker is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertIsNone(window.worker)

            window.start_scan()
            wait_worker()
            self.assertEqual(window.table.rowCount(), 2)
            self.assertEqual(len(window.selected()), 1)
            window.select_all(False)
            self.assertFalse(window.convert_button.isEnabled())
            window.select_all(True)
            window.start_conversion()
            wait_worker()
            self.assertTrue((root / 'output' / 'Music' / '歌曲.flac').exists())
            self.assertIn('成功 1', window.status.text())
            window.source.setText(str(root))
            self.assertEqual(window.table.rowCount(), 0)
            self.assertFalse(window.convert_button.isEnabled())
            window.close()

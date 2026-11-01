"""Portable bootstrap checks without network traffic or a browser download."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portable


class PortableTests(unittest.TestCase):
    def test_source_directory_is_project(self):
        self.assertEqual(portable.app_folder(), Path(portable.__file__).resolve().parent)

    def test_frozen_directory_and_browser_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(Path(tmp) / "AliBulkFinder.exe")), \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(portable.app_folder(), Path(tmp).resolve())
            portable.configure_browser()
            self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], str(Path(tmp).resolve() / "browsers"))

    def test_no_download_for_source_run(self):
        with patch("portable._install_dialog") as dialog:
            self.assertTrue(portable.ensure_browser())
            dialog.assert_not_called()

    def test_installed_browser_skips_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", str(Path(tmp) / "AliBulkFinder.exe")), \
             patch("portable.browser_ready", return_value=True), \
             patch("portable._install_dialog") as dialog:
            self.assertTrue(portable.ensure_browser())
            dialog.assert_not_called()
            self.assertTrue((Path(tmp) / "browsers").exists())

    def test_install_command_never_invokes_python_exe(self):
        with patch("playwright._impl._driver.compute_driver_executable", return_value=("node.exe", "cli.js")):
            self.assertEqual(portable.playwright_install_command(),
                             ["node.exe", "cli.js", "install", "chromium", "--no-shell"])


if __name__ == "__main__":
    unittest.main()

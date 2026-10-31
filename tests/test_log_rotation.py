"""Ротация debug и console: лимиты размера, сохранение и перезапуски."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import debuglog
import diagnostic_runner


class RotationTests(unittest.TestCase):
    def test_debug_log_rollover_keeps_three_backups(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'alibulkfinder_debug.log'
            with patch.object(debuglog, 'LOG_PATH', path), \
                 patch.object(debuglog, 'MAX_LOG_BYTES', 1024):
                for i in range(100):
                    debuglog.record(f'номер {i} ' + 'ъ' * 45)
                self.assertTrue(path.exists())
                self.assertTrue((Path(str(path) + '.1')).exists())
                self.assertTrue((Path(str(path) + '.3')).exists())
                self.assertFalse(Path(str(path) + '.4').exists())
                self.assertLessEqual(sum(p.stat().st_size for p in Path(d).iterdir()), 4*1024+700)
                self.assertIn('номер 99', path.read_text(encoding='utf-8'))
            # Закрыть обработчик до удаления временной папки (важно на Windows).
            # Запись по другому пути заставляет debuglog закрыть старый обработчик.
            debuglog.record('переключение пути')

    def test_debug_survives_restart_and_writes_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'alibulkfinder_debug.log'
            with patch.object(debuglog, 'LOG_PATH', path), \
                 patch.object(debuglog, 'MAX_LOG_BYTES', 1024):
                try:
                    raise ValueError('проверочный сбой')
                except ValueError as exc:
                    debuglog.record('тест ошибки', exc)
                self.assertIn('ValueError: проверочный сбой', path.read_text(encoding='utf-8'))
            debuglog.record('путь восстановлен')

    def test_console_launcher_rotates_real_child_stdout_stderr(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            (directory / 'app.py').write_text(
                'import sys\n'
                'for i in range(120):\n'
                ' print("console output %03d " % i + "X" * 50, flush=True)\n'
                ' print("stderr output %03d " % i + "Y" * 50, file=sys.stderr, flush=True)\n',
                encoding='utf-8')
            log = directory / 'alibulkfinder_console.log'
            with patch.object(diagnostic_runner, 'ROOT', directory), \
                 patch.object(diagnostic_runner, 'CONSOLE_LOG_PATH', log), \
                 patch.object(diagnostic_runner, 'MAX_LOG_BYTES', 1024):
                self.assertEqual(diagnostic_runner.main(), 0)
                self.assertEqual(diagnostic_runner.main(), 0)  # повторный запуск
            self.assertTrue(log.exists())
            self.assertTrue(Path(str(log)+'.1').exists())
            self.assertTrue(Path(str(log)+'.3').exists())
            self.assertFalse(Path(str(log)+'.4').exists())
            self.assertLessEqual(sum(p.stat().st_size for p in directory.glob('alibulkfinder_console.log*')), 4*1024+300)
            self.assertIn('EXIT code=0', log.read_text(encoding='utf-8'))

    def test_previously_unlimited_log_is_trimmed_immediately(self):
        from log_rotation import create_rotating_logger, close_logger
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'alibulkfinder_console.log'
            path.write_text('старые данные\n' * 500 + 'последняя запись\n', encoding='utf-8')
            self.assertGreater(path.stat().st_size, 1024)
            logger = create_rotating_logger('alibulkfinder.test.legacy', path,
                                            max_bytes=1024, backups=3)
            try:
                self.assertLessEqual(path.stat().st_size, 1024)
                self.assertIn('последняя запись', path.read_text(encoding='utf-8'))
                logger.info('новый запуск')
            finally:
                close_logger(logger)

    def test_console_splits_very_long_lines(self):
        from io import StringIO
        import logging
        parts = []
        class Capture(logging.Handler):
            def emit(self, record): parts.append(record.getMessage())
        logger = logging.getLogger('alibulkfinder.test.capture')
        logger.setLevel(logging.INFO)
        logger.addHandler(Capture())
        try:
            diagnostic_runner.log_output(StringIO('A'*5000+'\n'), logger, part_limit=400)
        finally:
            logger.handlers.clear()
        self.assertEqual(''.join(parts), 'A'*5000)
        self.assertLessEqual(max(map(len, parts)), 400)


if __name__ == '__main__':
    unittest.main()

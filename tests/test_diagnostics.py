import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from provider import LiveProvider
from request_guard import RequestGuard

class SessionStub:
    def __init__(self, result): self.result=result
    async def call_tool(self, name, args): return self.result

class DiagnosticsTests(unittest.TestCase):
    def test_invalid_mcp_text_is_explained(self):
        with tempfile.TemporaryDirectory() as temp, patch('debuglog.LOG_PATH', Path(temp)/'debug.log'):
            p = LiveProvider("Санкт-Петербург", guard=RequestGuard(Path(temp)))
            p.session = SessionStub(SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(text="<html>Access Denied</html>")]))
            with self.assertRaisesRegex(RuntimeError, r"не является JSON.*<html>"):
                asyncio.run(p.search('ретрактор'))
    def test_valid_mcp_json(self):
        with tempfile.TemporaryDirectory() as temp:
            p = LiveProvider("Санкт-Петербург", guard=RequestGuard(Path(temp)))
            p.session = SessionStub(SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(text='{"items":[]}')]))
            self.assertEqual(asyncio.run(p.search('ретрактор')), {"items": []})
    def test_error_flag_remains_readable(self):
        with tempfile.TemporaryDirectory() as temp, patch('debuglog.LOG_PATH', Path(temp)/'debug.log'):
            p = LiveProvider("Санкт-Петербург", guard=RequestGuard(Path(temp)))
            p.session = SessionStub(SimpleNamespace(isError=True, structuredContent=None, content=[SimpleNamespace(text='aliexpress.ru answered HTTP 403')]))
            with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                asyncio.run(p.search('ретрактор'))

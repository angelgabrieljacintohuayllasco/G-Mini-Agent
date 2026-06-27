"""Regression tests for the audit-fix pass (junio 2026).

Cubre:
- #1: `_open_browser_action` ya no usa shell; pasa la URL/query tal cual a webbrowser.open
  (sin interpretación de metacaracteres de shell → no command injection).
- #4: `_safe_int` no revienta con valores no numéricos de los args del modelo.

Run from repo root:
    PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_audit_fixes
"""

import asyncio
import unittest
from unittest import mock

from backend.core.computer_use_agent import ComputerUseAgent, _safe_int


class SafeIntTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_safe_int("5", 0), 5)
        self.assertEqual(_safe_int(7, 0), 7)

    def test_invalid_returns_default(self):
        self.assertEqual(_safe_int("2s", 3), 3)   # el modelo manda "2 segundos"
        self.assertEqual(_safe_int(None, 1), 1)
        self.assertEqual(_safe_int({}, 9), 9)


class OpenBrowserNoShellTest(unittest.TestCase):
    def _agent(self) -> ComputerUseAgent:
        # Bypass __init__ (requiere vision/automation pesados); _open_browser_action
        # solo usa webbrowser + args, no toca self.
        return object.__new__(ComputerUseAgent)

    def test_navigate_passes_url_verbatim(self):
        agent = self._agent()
        malicious = 'x" & calc & "'   # rompería un shell; debe ir literal a webbrowser.open
        with mock.patch("backend.core.computer_use_agent.webbrowser.open") as m:
            asyncio.run(agent._open_browser_action("navigate", {"url": malicious}))
        m.assert_called_once_with(malicious)

    def test_search_url_encoded(self):
        agent = self._agent()
        with mock.patch("backend.core.computer_use_agent.webbrowser.open") as m:
            asyncio.run(agent._open_browser_action("search", {"query": "a & b"}))
        called_url = m.call_args.args[0]
        self.assertTrue(called_url.startswith("https://www.google.com/search?q="))
        self.assertNotIn(" ", called_url)        # query escapada
        self.assertNotIn(" & ", called_url)

    def test_no_subprocess_import(self):
        # El módulo ya no debe depender de subprocess para abrir el navegador.
        import backend.core.computer_use_agent as cua
        self.assertFalse(hasattr(cua, "subprocess"))


if __name__ == "__main__":
    unittest.main()

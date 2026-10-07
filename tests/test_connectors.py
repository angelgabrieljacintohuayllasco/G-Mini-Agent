"""Conectores: registro, validación, anti-SSRF, lectura completa y nuevos conectores (sin red)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from backend import connectors
from backend.connectors import base
from backend.connectors.base import ConnectorError
from backend.connectors.regional import BcrpExchangeConnector, HolidaysConnector, parse_bcrp_period


def test_registry_ids_are_unique_and_indexed():
    ids = [c.id for c in connectors.all_connectors()]
    assert len(ids) == len(set(ids)) >= 9
    index = connectors.build_prompt_index()
    assert "bcrp: " in index and "range(start*, end*, kind)" in index and "connector_call" in index


async def test_call_validates_connector_action_and_params():
    with pytest.raises(ConnectorError, match="No existe el conector"):
        await connectors.call("nada", "x")
    with pytest.raises(ConnectorError, match="no tiene la acción"):
        await connectors.call("bcrp", "borrar")
    with pytest.raises(ConnectorError, match="obligatorio 'start'"):
        await connectors.call("bcrp", "range", {"end": "2026-10-01"})
    with pytest.raises(ConnectorError, match="se esperaba integer"):
        await connectors.call("holidays", "list", {"year": "dos mil"})


async def test_call_has_a_timeout(monkeypatch):
    async def slow(**kwargs):
        await asyncio.sleep(10)

    connector = connectors.get_connector("packages")
    action = connector.get_action("pypi")
    monkeypatch.setattr(connectors, "CALL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(type(connector), "actions", lambda self: [base.ConnectorAction("pypi", "x", slow, action.params)])
    with pytest.raises(ConnectorError, match="no respondió"):
        await connectors.call("packages", "pypi", {"name": "fastapi"})


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/api/config", "http://10.0.0.5/", "http://[::1]/", "http://169.254.169.254/latest/meta-data",
    "file:///C:/Windows/win.ini", "ftp://8.8.8.8/",
])
async def test_private_and_non_http_urls_are_blocked(url):
    with pytest.raises(ConnectorError):
        await base.ensure_public_url(url)


async def test_public_ip_is_allowed():
    assert await base.ensure_public_url("https://8.8.8.8/dns") == "https://8.8.8.8/dns"


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class _FakeResp:
    def __init__(self, chunks, status=200, headers=None):
        self.content = _FakeContent(chunks)
        self.status = status
        self.headers = headers or {}
        self.charset = "utf-8"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_read_body_reads_every_chunk_and_enforces_the_limit():
    chunks = [b'{"a": "' + b"x" * 70_000, b'", "b": 1}']
    assert (await base.read_body(_FakeResp(chunks))).endswith(b'"b": 1}')
    with pytest.raises(ConnectorError, match="demasiado grande"):
        await base.read_body(_FakeResp([b"x" * 10, b"y" * 10]), limit=15)


async def test_redirect_to_a_private_address_is_blocked(monkeypatch):
    from backend.connectors import info

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            return _FakeResp([], status=302, headers={"Location": "http://127.0.0.1:8765/api/config"})

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    with pytest.raises(ConnectorError, match="locales o privadas"):
        await info.fetch_public_text(info.WebReaderConnector(), "https://8.8.8.8/")


# ── BCRP y feriados ─────────────────────────────────────────────────────

def test_bcrp_dates():
    assert parse_bcrp_period("07.Oct.26") == date(2026, 10, 7)
    assert parse_bcrp_period("28.Set.26") == date(2026, 9, 28)
    with pytest.raises(ConnectorError):
        parse_bcrp_period("mañana")


async def test_bcrp_today_skips_unpublished_days(monkeypatch):
    async def fake_get_json(self, url, **kwargs):
        assert "PD04639PD-PD04640PD" in url
        return {"periods": [
            {"name": "05.Oct.26", "values": ["3.423", "3.435"]},
            {"name": "06.Oct.26", "values": ["3.431", "3.437"]},
            {"name": "07.Oct.26", "values": ["n.d.", "n.d."]},
        ]}

    monkeypatch.setattr(BcrpExchangeConnector, "get_json", fake_get_json)
    result = await BcrpExchangeConnector().today()
    assert result["fecha"] == "2026-10-06" and result["venta"] == 3.437 and "SUNAT" in result["tipo"]


async def test_next_holidays_cross_into_next_year(monkeypatch):
    calls = []

    async def fake_get_json(self, url, **kwargs):
        calls.append(url)
        year = int(url.split("/")[-2])
        return [{"date": f"{year}-12-25", "localName": "Navidad", "global": True},
                {"date": f"{year}-01-01", "localName": "Año Nuevo", "global": True}]

    class _Today(date):
        @classmethod
        def today(cls):
            return date(2026, 12, 26)

    from backend.connectors import regional

    monkeypatch.setattr(regional, "date", _Today)
    monkeypatch.setattr(HolidaysConnector, "get_json", fake_get_json)
    monkeypatch.setattr(HolidaysConnector, "setting", lambda self, key, default=None: "PE")
    result = await HolidaysConnector().next(count=2)
    assert [h["fecha"] for h in result["proximos"]] == ["2027-01-01", "2027-12-25"]
    assert any("/2027/PE" in url for url in calls)
    assert result["proximos"][0]["faltan_dias"] > 0


@pytest.mark.parametrize("repo", ["../etc", "owner", "a/b/c", "owner repo"])
async def test_github_rejects_bad_repo_names(repo):
    with pytest.raises(ConnectorError):
        await connectors.call("github", "repo", {"repo": repo})


@pytest.mark.parametrize("name", ["../../x", "a b", "", "x?y=1"])
async def test_packages_reject_bad_names(name):
    with pytest.raises(ConnectorError):
        await connectors.call("packages", "npm", {"name": name})


# ── Policy y planner ────────────────────────────────────────────────────

def test_policy_marks_read_connectors_as_low_risk():
    from backend.core.planner import Action
    from backend.core.policy import PolicyEngine

    engine = PolicyEngine.__new__(PolicyEngine)
    ok = engine._classify(Action(type="connector_call", params={"connector": "bcrp", "action": "today"}))
    unknown = engine._classify(Action(type="connector_call", params={"connector": "x", "action": "y"}))
    assert ok["severity"] == "low" and unknown["severity"] == "medium"


async def test_planner_connector_call(monkeypatch):
    from backend.core.planner import Action, ActionPlanner

    async def fake_call(cid, action, params):
        return {"compra": 3.43, "venta": 3.44}

    monkeypatch.setattr(connectors, "call", fake_call)
    planner = ActionPlanner.__new__(ActionPlanner)
    result = await planner._execute_single(
        Action(type="connector_call", params={"connector": "bcrp", "action": "today", "params": {}})
    )
    assert result["success"] and result["data"]["venta"] == 3.44
    bad = await planner._execute_single(Action(type="connector_call", params={"connector": "bcrp", "params": "x"}))
    assert not bad["success"]

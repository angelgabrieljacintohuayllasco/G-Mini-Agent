"""Autenticación del núcleo: Host, token de sesión, emparejamiento y WebSockets."""

from __future__ import annotations

import pytest
import socketio
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.security import local_auth

TOKEN = "t" * 43


@pytest.fixture()
def auth_home(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(local_auth, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(local_auth, "SESSION_TOKEN_FILE", runtime / "session_token")
    monkeypatch.setattr(local_auth, "DEVICES_FILE", runtime / "devices.json")
    monkeypatch.setenv("GMINI_SESSION_TOKEN", TOKEN)
    monkeypatch.delenv("GMINI_BIND_HOST", raising=False)
    local_auth.reset_session_token_for_tests()
    local_auth._pairing_codes.clear()
    local_auth._claim_attempts.clear()
    yield runtime
    local_auth.reset_session_token_for_tests()


@pytest.fixture()
def client(auth_home):
    from backend.main import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8765")


def test_session_token_comes_from_electron_env_and_is_written(auth_home):
    assert local_auth.get_session_token() == TOKEN
    assert (auth_home / "session_token").read_text(encoding="utf-8") == TOKEN


def test_foreign_host_is_rejected_even_with_token(client):
    resp = client.get("/api/config/app", headers={"Host": "evil.example:8765", "X-GMini-Token": TOKEN})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "invalid_host"


def test_api_requires_token(client):
    assert client.get("/api/config/app").status_code == 401
    assert client.get("/api/config/app", headers={"X-GMini-Token": "malo"}).status_code == 401
    assert client.get("/api/config/app", headers={"X-GMini-Token": TOKEN}).status_code == 200
    assert client.get("/api/config/app", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_text_plain_csrf_post_is_blocked(client):
    resp = client.put(
        "/api/config",
        content='{"section": "agent", "key": "autonomy_level", "value": "libre"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 401


def test_public_routes_skip_token(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/media/no-existe.png").status_code != 401


def test_config_section_redacts_secrets(client, monkeypatch):
    from backend.config import config

    original = config.get("server")
    monkeypatch.setitem(config.data, "server", {**(original or {}), "webhook_secret": "s3cr3t", "api_auth_token": "abc"})
    data = client.get("/api/config/server", headers={"X-GMini-Token": TOKEN}).json()["data"]["server"]
    assert data["webhook_secret"] == "***"
    assert data["api_auth_token"] == "***"
    assert client.get("/api/config/security", headers={"X-GMini-Token": TOKEN}).status_code == 403


def test_pairing_flow_issues_working_device_token(client, auth_home):
    code = local_auth.create_pairing_code(label="Companion", device_type="esp32")["code"]
    with pytest.raises(PermissionError):
        local_auth.claim_pairing_code("000000" if code != "000000" else "111111", device_name="x", client_ip="1.1.1.1")
    token, record = local_auth.claim_pairing_code(code, device_name="Companion sala", client_ip="1.1.1.1")
    assert token.startswith("gm_dev_")
    assert "admin" not in record.scopes
    assert "gm_dev_" not in (auth_home / "devices.json").read_text(encoding="utf-8")
    assert client.get("/api/config/app", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    with pytest.raises(PermissionError):
        local_auth.claim_pairing_code(code, device_name="otra vez", client_ip="1.1.1.2")
    assert local_auth.revoke_device(record.id) is True
    assert client.get("/api/config/app", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_pairing_claims_are_rate_limited(auth_home):
    local_auth.create_pairing_code()
    for _ in range(5):
        with pytest.raises(PermissionError, match="invalid_code"):
            local_auth.claim_pairing_code("999999", device_name="x", client_ip="9.9.9.9")
    with pytest.raises(PermissionError, match="rate_limited"):
        local_auth.claim_pairing_code("999999", device_name="x", client_ip="9.9.9.9")


def test_socketio_connect_requires_token_and_local_host(auth_home):
    from backend.api.websocket_handler import _authorize_socket

    environ = {"HTTP_HOST": "127.0.0.1:8765", "QUERY_STRING": ""}
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        _authorize_socket(environ, None)
    _authorize_socket(environ, {"token": TOKEN})
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        _authorize_socket({"HTTP_HOST": "evil.example:8765"}, {"token": TOKEN})


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_extension_ws_rejects_web_origins(client, origin):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/extension", headers={"Origin": origin}) as ws:
            ws.receive_text()
    assert exc.value.code == 1008


def test_origin_helper():
    assert local_auth.browser_origin_is_untrusted("https://evil.example")
    assert local_auth.browser_origin_is_untrusted("null")
    assert not local_auth.browser_origin_is_untrusted(None)
    assert not local_auth.browser_origin_is_untrusted("file://")
    assert not local_auth.browser_origin_is_untrusted("chrome-extension://abc", allow_extensions=True)
    assert local_auth.browser_origin_is_untrusted("chrome-extension://abc", allow_extensions=False)

"""Tests for the lightai reverse proxy that serves QLC+'s /stage 3D visualizer from the same
port as the booth console (lightai.api.stage_proxy). Each test builds a bare FastAPI app with
only the proxy routes (add_stage_routes), so the proxy is exercised on its own, against a fake
QLC+ (fakeqlc.FakeQlcHttp for the HTTP side, fakeqlc.FakeQlc for the WebSocket side) rather than
the rest of the lightai API."""

from types import SimpleNamespace

import pytest
from fakeqlc import FakeQlc, FakeQlcHttp, free_port
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lightai.api.stage_proxy import add_stage_routes
from lightai.config import Config


def make_app(qlc_url: str, tmp_path, token=None):
    cfg = Config(data_dir=tmp_path / "data")
    cfg.qlc_url = qlc_url  # not "auto": a single fixed candidate, no https/http dual-probe
    app = FastAPI()
    proxy = add_stage_routes(app, lambda: SimpleNamespace(cfg=cfg), token)
    return app, proxy, cfg


def test_http_proxy_against_fake_upstream(tmp_path):
    fake = FakeQlcHttp({
        "/stage": (200, "text/html", b"<html>stage</html>"),
        "/stage.css": (200, "text/css", b"body{color:red}"),
        "/stage-app.js": (200, "text/javascript", b"console.log(1)"),
        "/stage-lib/index.json": (200, "application/json", b'{"ok":true}'),
        "/three/build/three.module.js": (200, "text/javascript", b"export default {};"),
        "/gobos/x.png": (200, "image/png", b"\x89PNG\r\n"),
    }).start()
    try:
        app, _, _ = make_app(fake.url + "/qlcplusWS", tmp_path)
        c = TestClient(app)
        r = c.get("/stage")
        assert r.status_code == 200 and r.text == "<html>stage</html>"
        assert r.headers["content-type"] == "text/html"
        assert c.get("/stage.css").content == b"body{color:red}"
        assert c.get("/stage-app.js").content == b"console.log(1)"
        assert c.get("/stage-lib/index.json").json() == {"ok": True}
        assert c.get("/three/build/three.module.js").status_code == 200
        assert c.get("/gobos/x.png").status_code == 200
        # a path that doesn't start with stage/three/gobos never reaches the fake at all
        assert c.get("/not-a-stage-thing").status_code == 404
        assert not any(p == "/not-a-stage-thing" for p, _ in fake.requests)
        # a stage-shaped path the fake doesn't have: 404 passed through from upstream
        assert c.get("/stage-nope.js").status_code == 404
    finally:
        fake.stop()


def test_no_stage_route_gives_friendly_404(tmp_path):
    fake = FakeQlcHttp({}).start()  # a stock QLC+ build: no /stage at all
    try:
        app, _, _ = make_app(fake.url + "/qlcplusWS", tmp_path)
        c = TestClient(app)
        r = c.get("/stage")
        assert r.status_code == 404 and "no 3d stage" in r.text.lower()
    finally:
        fake.stop()


def test_upstream_down_gives_502(tmp_path):
    port = free_port()  # freed immediately after; nothing listens here
    app, _, cfg = make_app(f"http://127.0.0.1:{port}/qlcplusWS", tmp_path)
    c = TestClient(app)
    r = c.get("/stage")
    assert r.status_code == 502 and "isn't running" in r.text and str(port) in r.text
    assert c.get("/stage.css").status_code == 502  # sub-resources fail the same friendly way


def test_ws_bridge_round_trip(tmp_path):
    fake = FakeQlc(fork=True).start()
    try:
        app, _, _ = make_app(fake.url, tmp_path)
        c = TestClient(app)
        with c.websocket_connect("/qlcplusWS") as ws:
            ws.send_text("QLC+API|getFunctionsNumber")
            assert ws.receive_text() == "QLC+API|getFunctionsNumber|0"
            ws.send_text("QLC+API|getWidgetsNumber")
            assert ws.receive_text() == "QLC+API|getWidgetsNumber|0"
        assert "QLC+API|getFunctionsNumber" in fake.log
        assert "QLC+API|getWidgetsNumber" in fake.log
    finally:
        fake.stop()


def test_token_enforced_on_stage_http(tmp_path):
    fake = FakeQlcHttp({
        "/stage": (200, "text/html", b"<html>stage</html>"),
        "/stage.css": (200, "text/css", b"body{}"),
    }).start()
    try:
        app, _, _ = make_app(fake.url + "/qlcplusWS", tmp_path, token="s3cret")
        c = TestClient(app)
        assert c.get("/stage").status_code == 401
        assert c.get("/stage", params={"token": "wrong"}).status_code == 401
        assert c.get("/stage", params={"token": "s3cret"}).status_code == 200
        # sub-resources are plain GETs with no way to add a header; not gated
        assert c.get("/stage.css").status_code == 200
    finally:
        fake.stop()


def test_token_enforced_on_ws(tmp_path):
    fake = FakeQlc(fork=True).start()
    try:
        app, _, _ = make_app(fake.url, tmp_path, token="s3cret")
        c = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/qlcplusWS"):
                pass
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/qlcplusWS?token=wrong"):
                pass
        with c.websocket_connect("/qlcplusWS?token=s3cret") as ws:
            ws.send_text("QLC+API|getFunctionsNumber")
            assert ws.receive_text() == "QLC+API|getFunctionsNumber|0"
    finally:
        fake.stop()


def test_routable_rejects_dot_segments():
    from lightai.api.stage_proxy import _routable
    assert _routable("stage") and _routable("stage-lib/props/index.json") and _routable("three/build/three.module.min.js")
    assert not _routable("stage/../control.html")
    assert not _routable("stage-lib/../../secret")
    assert not _routable("gobos\\..\\x")
    assert not _routable("plan")

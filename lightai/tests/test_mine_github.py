"""GitHub show mining against a mocked GitHub API (no token or network needed)."""

import json

import httpx

from conftest import BLANK


def test_mine_github_downloads_workspaces(cfg, monkeypatch):
    import lightai.mine as mine

    body = BLANK.read_bytes()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer TEST"
        if request.url.path == "/search/code":
            if request.url.params.get("page") == "1":
                return httpx.Response(200, json={"items": [
                    {"url": "https://api.github.com/repos/a/b/contents/show.qxw?ref=x", "html_url": "https://github.com/a/b/blob/x/show.qxw",
                     "path": "show.qxw", "repository": {"full_name": "a/b"}},
                    {"url": "https://api.github.com/repos/c/d/contents/notes.qxw?ref=y", "html_url": "https://github.com/c/d/blob/y/notes.qxw",
                     "path": "notes.qxw", "repository": {"full_name": "c/d"}},
                ]})
            return httpx.Response(200, json={"items": []})
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, content=body if request.url.path.endswith("/show.qxw") else b"not a workspace")
        if request.url.path.endswith("show.qxw"):
            return httpx.Response(200, json={"download_url": "https://raw.githubusercontent.com/a/b/x/show.qxw"})
        if request.url.path.endswith("notes.qxw"):
            return httpx.Response(200, json={"download_url": "https://raw.githubusercontent.com/c/d/y/notes.qxw"})
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(mine.time, "sleep", lambda s: None)
    assert mine.mine_github(cfg, "TEST", 10) == 0
    raw = list((cfg.data_dir / "mined" / "raw").glob("gh__*.qxw"))
    assert len(raw) == 1 and raw[0].read_bytes() == body, "only real workspaces are kept"
    manifest = [json.loads(l) for l in (cfg.data_dir / "mined" / "manifest.jsonl").read_text().splitlines()]
    assert manifest[0]["repo"] == "a/b" and manifest[0]["kind"] == "github"
    assert mine.mine_github(cfg, None, 10) == 2, "no token -> instructions, not a crash"

"""Google OAuth 자격 증명·auth youtube 명령 테스트. 토큰 엔드포인트는 responses 로 막는다."""

from __future__ import annotations

import argparse
import json
import os
import stat
from urllib.parse import parse_qs

import httplib2
import pytest
import responses
from googleapiclient.errors import HttpError

from studio import config, paths
from studio.cliutil import group
from studio.config import MissingSecret
from studio.publish import google_auth

TOKEN = "1//0gABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CHANNEL_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(config, "_env_loaded", True)     # 저장소 .env 를 읽지 않게
    config.load_config.cache_clear()
    google_auth.clear_cache()
    yield tmp_path
    google_auth.clear_cache()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "GOCSPX-secretvalue1234")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", TOKEN)


def _form(body) -> dict[str, list[str]]:
    return parse_qs(body.decode("utf-8") if isinstance(body, bytes) else body)


def _token_ok(rsps, scope: str | None = None):
    body = {"access_token": "ya29.test-access", "expires_in": 3599, "token_type": "Bearer"}
    if scope:
        body["scope"] = scope
    rsps.add(responses.POST, google_auth.TOKEN_URI, json=body, status=200)


# ---------------------------------------------------------------- credentials()

def test_credentials_refresh_with_env(root, env):
    with responses.RequestsMock() as rsps:
        _token_ok(rsps, " ".join(google_auth.DEFAULT_SCOPES))
        creds = google_auth.credentials()
        assert creds.token == "ya29.test-access" and creds.valid
        sent = _form(rsps.calls[0].request.body)
        assert sent["grant_type"] == ["refresh_token"]
        assert sent["refresh_token"] == [TOKEN]
        assert sent["client_id"] == ["123-abc.apps.googleusercontent.com"]
        assert set(sent["scope"][0].split()) == set(google_auth.DEFAULT_SCOPES)
        # 같은 범위는 재사용(네트워크 1회)
        assert google_auth.credentials() is creds
        assert len(rsps.calls) == 1


def test_default_scopes_cover_upload_comments_analytics():
    s = set(google_auth.DEFAULT_SCOPES)
    assert {
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    } == s


def test_custom_scopes_are_sent(root, env):
    with responses.RequestsMock() as rsps:
        _token_ok(rsps)
        google_auth.credentials([google_auth.SCOPE_ANALYTICS, google_auth.SCOPE_MONETARY])
        sent = _form(rsps.calls[0].request.body)
        assert set(sent["scope"][0].split()) == {google_auth.SCOPE_ANALYTICS, google_auth.SCOPE_MONETARY}


def test_invalid_grant_explains_testing_mode(root, env):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, google_auth.TOKEN_URI, status=400,
                 json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        with pytest.raises(google_auth.GoogleAuthError) as ei:
            google_auth.credentials()
    msg = str(ei.value)
    assert "Testing" in msg and "7일" in msg and "auth youtube" in msg
    assert TOKEN not in msg
    assert isinstance(ei.value, MissingSecret)      # 진입점이 '[설정 필요]' 로 안내


def test_invalid_client_message(root, env):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, google_auth.TOKEN_URI, status=401,
                 json={"error": "invalid_client", "error_description": "The OAuth client was not found."})
        with pytest.raises(google_auth.GoogleAuthError) as ei:
            google_auth.credentials()
    assert "GOOGLE_CLIENT_ID" in str(ei.value) and "데스크톱 앱" in str(ei.value)


def test_missing_secrets(root):
    with pytest.raises(MissingSecret) as ei:
        google_auth.credentials()
    assert "YOUTUBE_REFRESH_TOKEN" in str(ei.value) and "auth youtube" in str(ei.value)


def test_token_file_fallback(root):
    google_auth._write_secret_json(google_auth.token_file_path(), {
        "client_id": "file-id", "client_secret": "file-secret", "refresh_token": "1//file-token",
    })
    with responses.RequestsMock() as rsps:
        _token_ok(rsps)
        google_auth.credentials()
        sent = _form(rsps.calls[0].request.body)
        assert sent["refresh_token"] == ["1//file-token"] and sent["client_id"] == ["file-id"]


def test_api_error_parsing():
    resp = httplib2.Response({"status": "403"})
    content = json.dumps({"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}).encode()
    info = google_auth.api_error(HttpError(resp, content))
    assert (info.status, info.reason, info.message) == (403, "quotaExceeded", "quota")
    info = google_auth.api_error(HttpError(httplib2.Response({"status": "502"}), b"<html>bad gateway</html>"))
    assert info.status == 502 and info.reason == ""


def test_mask():
    assert google_auth.mask(TOKEN) == "1//0******cdef"
    assert google_auth.mask("short") == "*****"
    assert google_auth.mask(None) == "(없음)"


# ---------------------------------------------------------------- auth youtube

class FakeCreds:
    refresh_token = TOKEN
    scopes = list(google_auth.DEFAULT_SCOPES)
    granted_scopes = list(google_auth.DEFAULT_SCOPES)


class FakeFlow:
    last: "FakeFlow | None" = None

    def __init__(self, cfg, scopes):
        self.cfg, self.scopes, self.kwargs = cfg, scopes, {}
        FakeFlow.last = self

    def run_local_server(self, **kwargs):
        self.kwargs = kwargs
        return FakeCreds()


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    google_auth.register_auth(group(sub, "auth"))
    args = parser.parse_args(["auth", "youtube", *argv])
    return args.func(args)


@pytest.fixture
def fake_flow(monkeypatch):
    from google_auth_oauthlib import flow

    monkeypatch.setattr(flow.InstalledAppFlow, "from_client_config", classmethod(lambda cls, cfg, scopes: FakeFlow(cfg, scopes)))
    monkeypatch.setattr(google_auth, "_channel_info", lambda creds: ("UCtestchannel", "테스트 채널"))
    FakeFlow.last = None
    return FakeFlow


def test_auth_saves_token_and_masks_output(root, env, fake_flow, capsys):
    assert _run(["--no-browser", "--port", "9999"]) == 0
    kw = fake_flow.last.kwargs
    assert kw["open_browser"] is False and kw["port"] == 9999
    assert kw["prompt"] == "consent" and kw["access_type"] == "offline"
    assert set(fake_flow.last.scopes) == set(google_auth.DEFAULT_SCOPES)

    p = root / ".secrets" / "youtube_token.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["refresh_token"] == TOKEN and data["client_id"] == "123-abc.apps.googleusercontent.com"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

    out = capsys.readouterr().out
    assert TOKEN not in out and "GOCSPX-secretvalue1234" not in out
    assert google_auth.mask(TOKEN) in out
    assert "YOUTUBE_REFRESH_TOKEN" in out and "UCtestchannel" in out
    assert "Testing" in out and "gh secret set YOUTUBE_REFRESH_TOKEN" in out
    assert "ssh -L 9999:localhost:9999" in out


def test_auth_show_prints_token(root, env, fake_flow, capsys):
    assert _run(["--show", "--monetary"]) == 0
    assert google_auth.SCOPE_MONETARY in fake_flow.last.scopes
    out = capsys.readouterr().out
    assert TOKEN in out
    assert "동의 화면에서 일부 권한이 빠졌습니다" in out      # monetary 를 요청했지만 가짜 creds 에 없음


def test_auth_dry_run_does_not_open_flow(root, env, fake_flow, capsys):
    assert _run(["--dry-run"]) == 0
    assert fake_flow.last is None
    assert not (root / ".secrets" / "youtube_token.json").exists()
    assert "dry-run" in capsys.readouterr().out


def test_auth_rejects_web_client_json(root, tmp_path, fake_flow):
    p = tmp_path / "client.json"
    p.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}), encoding="utf-8")
    with pytest.raises(MissingSecret) as ei:
        _run(["--client-secrets", str(p)])
    assert "데스크톱 앱" in str(ei.value)


def test_auth_uses_client_json_file(root, tmp_path, fake_flow):
    p = tmp_path / "client.json"
    p.write_text(json.dumps({"installed": {"client_id": "file-cid", "client_secret": "file-sec",
                                           "auth_uri": google_auth.AUTH_URI, "token_uri": google_auth.TOKEN_URI}}), encoding="utf-8")
    assert _run(["--client-secrets", str(p)]) == 0
    assert fake_flow.last.cfg["installed"]["client_id"] == "file-cid"
    saved = json.loads((root / ".secrets" / "youtube_token.json").read_text(encoding="utf-8"))
    assert saved["client_id"] == "file-cid"


def test_auth_without_refresh_token_fails(root, env, fake_flow, monkeypatch, capsys):
    monkeypatch.setattr(FakeCreds, "refresh_token", None)
    assert _run([]) == 1
    assert "myaccount.google.com/permissions" in capsys.readouterr().err

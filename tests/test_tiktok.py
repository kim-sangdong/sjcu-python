"""틱톡 발행기·토큰 회전·`auth tiktok` 테스트. 네트워크는 responses 로 흉내 낸다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from studio import config, paths
from studio.config import Config
from studio.models import PublishTarget, RenderedAsset, VideoScript, read_model
from studio.publish import captions, tiktok
from studio.publish.base import PublishContext, PublishError
from studio.publish.meta_auth import fingerprint
from studio.publish.tiktok import (
    TT_CREATOR_INFO_URL,
    TT_INBOX_INIT_URL,
    TT_STATUS_URL,
    TT_TOKEN_URL,
    TT_VIDEO_INIT_URL,
    TikTokPublisher,
    access_token,
    chunk_plan,
    truncate_utf16,
)
from studio.tokens import FileTokenStore

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W42"
UPLOAD = "https://open-upload.tiktokapis.com/upload/?upload_id=u1&upload_token=secret"
PUB = f"https://ref.supabase.co/storage/v1/object/public/media/{WEEK}"
OK = {"error": {"code": "ok", "message": "", "log_id": "L1"}}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    for k in ("STUDIO_DRY_RUN", "STUDIO_TOKEN_BACKEND", "TIKTOK_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck123")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs456-secret")
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", "rft.initial.0001")
    monkeypatch.setattr(tiktok, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


@pytest.fixture
def cached_token(root):
    """24시간 캐시가 살아 있는 상태(토큰 엔드포인트를 부르지 않음)."""
    exp = datetime.now(UTC) + timedelta(hours=20)
    FileTokenStore().set("tiktok", {"access_token": "act.cached", "access_expires_at": exp.isoformat(),
                                    "refresh_token": "rft.initial.0001", "seed": fingerprint("rft.initial.0001")})


def _cfg(audited: bool = False, **extra) -> Config:
    return Config.model_validate({
        "app": {"name": "복습노트"},
        "platforms": {"tiktok": {"audited": audited, "poll_timeout_seconds": 0, "poll_interval_seconds": 0, **extra}},
    })


def _video(data: bytes = b"\x00" * 4096) -> str:
    rel = f"out/weeks/{WEEK}/2026W42-S01/video.mp4"
    p = paths.ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return rel


def _ctx(cfg: Config, *, remote_id: str | None = None, dry: bool = False, write_video: bool = True) -> PublishContext:
    script = read_model(FIX / "video_short.json", VideoScript)
    rel = _video() if write_video else f"out/weeks/{WEEK}/{script.id}/video.mp4"
    asset = RenderedAsset(asset_id=script.id, kind="short", duration_seconds=42.0, files={"video": rel},
                          remote_urls={"video": f"{PUB}/{script.id}/video.mp4"})
    t = PublishTarget(asset_id=script.id, asset_kind="short", platform="tiktok", publish_at=datetime.now(paths.KST),
                      remote_id=remote_id)
    return PublishContext(week=WEEK, target=t, asset=asset, content=script, cfg=cfg, dry_run=dry)


def _creator(options=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"), **extra) -> dict:
    return {"data": {"creator_username": "study_daily", "creator_nickname": "공부채널", "privacy_level_options": list(options),
                     "comment_disabled": False, "duet_disabled": False, "stitch_disabled": True,
                     "max_video_post_duration_sec": 600, **extra}, **OK}


def _json(call) -> dict:
    return json.loads(call.request.body)


def _flow(rsps, status_payload: dict) -> None:
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator())
    rsps.add(responses.POST, TT_VIDEO_INIT_URL, json={"data": {"publish_id": "p_pub.1", "upload_url": UPLOAD}, **OK})
    rsps.add(responses.PUT, UPLOAD.split("?")[0], status=201)
    rsps.add(responses.POST, TT_STATUS_URL, json={"data": status_payload, **OK})


# ---------------------------------------------------------------- 토큰

def test_refresh_rotates_and_caches_24h(root, rsps):
    rsps.add(responses.POST, TT_TOKEN_URL, json={
        "access_token": "act.new", "expires_in": 86400, "open_id": "oid", "refresh_token": "rft.rotated.0002",
        "refresh_expires_in": 31536000, "scope": "user.info.basic,video.publish", "token_type": "Bearer"})
    log: list[str] = []
    assert access_token(log) == "act.new"
    form = {k: v[0] for k, v in parse_qs(rsps.calls[0].request.body).items()}
    assert form == {"client_key": "ck123", "client_secret": "cs456-secret", "grant_type": "refresh_token",
                    "refresh_token": "rft.initial.0001"}
    rec = FileTokenStore().get("tiktok")
    assert rec["refresh_token"] == "rft.rotated.0002" and rec["seed"] == fingerprint("rft.initial.0001")
    assert rec["access_token"] == "act.new" and rec["open_id"] == "oid"
    assert "회전" in log[0]
    # 같은 시크릿이 그대로여도 회전된 토큰을 쓰고, 24시간 캐시 안에서는 다시 부르지 않는다
    assert access_token() == "act.new"
    assert len(rsps.calls) == 1
    # 캐시가 끝나면 회전된 refresh token 으로 갱신
    rec["access_expires_at"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    FileTokenStore().set("tiktok", rec)
    rsps.replace(responses.POST, TT_TOKEN_URL, json={"access_token": "act.3", "expires_in": 86400})
    assert access_token() == "act.3"
    assert parse_qs(rsps.calls[1].request.body)["refresh_token"] == ["rft.rotated.0002"]
    assert FileTokenStore().get("tiktok")["refresh_token"] == "rft.rotated.0002"   # 응답에 없으면 이전 값 유지


def test_new_secret_wins_over_rotated(root, rsps, monkeypatch):
    FileTokenStore().set("tiktok", {"access_token": "act.old", "refresh_token": "rft.rotated", "seed": fingerprint("rft.initial.0001"),
                                    "access_expires_at": (datetime.now(UTC) + timedelta(hours=5)).isoformat()})
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", "rft.reissued.0009")
    rsps.add(responses.POST, TT_TOKEN_URL, json={"access_token": "act.fresh", "expires_in": 86400})
    assert access_token() == "act.fresh"
    assert parse_qs(rsps.calls[0].request.body)["refresh_token"] == ["rft.reissued.0009"]
    assert FileTokenStore().get("tiktok")["seed"] == fingerprint("rft.reissued.0009")


def test_expired_refresh_token_not_retryable(root, rsps):
    rsps.add(responses.POST, TT_TOKEN_URL, status=400,
             json={"error": "invalid_grant", "error_description": "Refresh token is invalid or expired.", "log_id": "x"})
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is False and "auth tiktok" in str(e.value)
    assert "rft.initial.0001" not in str(e.value)


# ---------------------------------------------------------------- 게시

def test_unaudited_forces_self_only_and_publishes(cached_token, rsps):
    _flow(rsps, {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []})
    ctx = _ctx(_cfg(audited=False))
    res = TikTokPublisher().publish(ctx)
    assert res.status == "published" and res.remote_id == "p_pub.1" and res.url is None
    assert "SELF_ONLY" in res.note
    assert rsps.calls[0].request.headers["Authorization"] == "Bearer act.cached"
    init = _json(rsps.calls[1])
    pi = init["post_info"]
    assert pi["privacy_level"] == "SELF_ONLY"
    assert pi["title"] == captions.reel_caption(ctx.content, ctx.cfg, "tiktok")
    assert pi["disable_comment"] is False and pi["disable_duet"] is False
    assert pi["disable_stitch"] is True            # 크리에이터 설정에서 막혀 있으면 따른다
    assert "is_aigc" not in pi
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": 4096, "chunk_size": 4096, "total_chunk_count": 1}
    put = rsps.calls[2].request
    assert put.headers["Content-Range"] == "bytes 0-4095/4096" and put.headers["Content-Type"] == "video/mp4"
    assert _json(rsps.calls[3]) == {"publish_id": "p_pub.1"}
    assert any("[경고]" in line and "SELF_ONLY" in line for line in ctx.log)
    assert not any("upload_token=secret" in line for line in ctx.log)


def test_audited_public_processing_then_check(cached_token, rsps):
    _flow(rsps, {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 4096})
    pub = TikTokPublisher()
    res = pub.publish(_ctx(_cfg(audited=True, is_aigc=True, disable_duet=True)))
    assert (res.status, res.remote_id) == ("processing", "p_pub.1")
    pi = _json(rsps.calls[1])["post_info"]
    assert pi["privacy_level"] == "PUBLIC_TO_EVERYONE" and pi["is_aigc"] is True and pi["disable_duet"] is True

    rsps.replace(responses.POST, TT_STATUS_URL, json={"data": {"status": "PUBLISH_COMPLETE",
                                                               "publicaly_available_post_id": [7350000000000000001]}, **OK})
    done = pub.check(_ctx(_cfg(audited=True), remote_id="p_pub.1"))
    assert done.status == "published" and done.remote_id == "7350000000000000001"
    assert done.url == "https://www.tiktok.com/@study_daily/video/7350000000000000001"


def test_privacy_level_not_available(cached_token, rsps):
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator(options=("FOLLOWER_OF_CREATOR", "SELF_ONLY")))
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg(audited=True)))
    assert e.value.retryable is False and "FOLLOWER_OF_CREATOR" in str(e.value)
    assert not any(c.request.url == TT_VIDEO_INIT_URL for c in rsps.calls)


def test_video_longer_than_creator_max(cached_token, rsps):
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator(max_video_post_duration_sec=30))
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is False and "30" in str(e.value)


@pytest.mark.parametrize("code,status,retry", [
    ("scope_not_authorized", 401, False),
    ("spam_risk_user_banned_from_posting", 403, False),
    ("unaudited_client_can_only_post_to_private_accounts", 403, False),
    ("spam_risk_too_many_posts", 403, True),
    ("rate_limit_exceeded", 429, True),
])
def test_api_errors_classified(cached_token, rsps, code, status, retry):
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator())
    rsps.add(responses.POST, TT_VIDEO_INIT_URL, status=status, json={"error": {"code": code, "message": "nope", "log_id": "L9"}})
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is retry
    assert code in str(e.value)


def test_invalid_access_token_refreshes_once(cached_token, rsps):
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, status=401,
             json={"error": {"code": "access_token_invalid", "message": "expired", "log_id": "x"}})
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator())
    rsps.add(responses.POST, TT_TOKEN_URL, json={"access_token": "act.renewed", "expires_in": 86400})
    rsps.add(responses.POST, TT_VIDEO_INIT_URL, json={"data": {"publish_id": "p2", "upload_url": UPLOAD}, **OK})
    rsps.add(responses.PUT, UPLOAD.split("?")[0], status=201)
    rsps.add(responses.POST, TT_STATUS_URL, json={"data": {"status": "PROCESSING_DOWNLOAD"}, **OK})
    res = TikTokPublisher().publish(_ctx(_cfg()))
    assert res.status == "processing"
    init = next(c for c in rsps.calls if c.request.url == TT_VIDEO_INIT_URL)
    assert init.request.headers["Authorization"] == "Bearer act.renewed"


def test_failed_status_classification(cached_token, rsps):
    _flow(rsps, {"status": "FAILED", "fail_reason": "frame_rate_check_failed"})
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is False and "frame_rate_check_failed" in str(e.value)
    rsps.replace(responses.POST, TT_STATUS_URL, json={"data": {"status": "FAILED", "fail_reason": "internal"}, **OK})
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is True


def test_multi_chunk_upload_ranges(cached_token, rsps, monkeypatch):
    monkeypatch.setattr(tiktok, "TT_SINGLE_CHUNK_UNDER", 1000)
    monkeypatch.setattr(tiktok, "TT_CHUNK_SIZE", 400)
    data = bytes(range(256)) * 4 + b"tail" * 25    # 1124 바이트 → 400 + 724(마지막 청크가 나머지 흡수)
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator())
    rsps.add(responses.POST, TT_VIDEO_INIT_URL, json={"data": {"publish_id": "p3", "upload_url": UPLOAD}, **OK})
    rsps.add(responses.PUT, UPLOAD.split("?")[0], status=206)
    rsps.add(responses.PUT, UPLOAD.split("?")[0], status=201)
    rsps.add(responses.POST, TT_STATUS_URL, json={"data": {"status": "PROCESSING_UPLOAD"}, **OK})
    ctx = _ctx(_cfg())
    (paths.ROOT / ctx.asset.files["video"]).write_bytes(data)
    TikTokPublisher().publish(ctx)
    src = _json(next(c for c in rsps.calls if c.request.url == TT_VIDEO_INIT_URL))["source_info"]
    assert src == {"source": "FILE_UPLOAD", "video_size": 1124, "chunk_size": 400, "total_chunk_count": 2}
    puts = [c.request for c in rsps.calls if c.request.method == "PUT"]
    assert [p.headers["Content-Range"] for p in puts] == ["bytes 0-399/1124", "bytes 400-1123/1124"]
    assert puts[0].body + puts[1].body == data


def test_chunk_rejection_not_retryable(cached_token, rsps):
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator())
    rsps.add(responses.POST, TT_VIDEO_INIT_URL, json={"data": {"publish_id": "p4", "upload_url": UPLOAD}, **OK})
    rsps.add(responses.PUT, UPLOAD.split("?")[0], status=416, body="range not satisfiable")
    with pytest.raises(PublishError) as e:
        TikTokPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is False and "청크" in str(e.value)


def test_missing_local_video_downloaded_from_storage(cached_token, rsps):
    data = b"remote-video" * 100
    _flow(rsps, {"status": "PROCESSING_UPLOAD"})
    rsps.add(responses.GET, f"{PUB}/2026W42-S01/video.mp4", body=data)
    ctx = _ctx(_cfg(), write_video=False)
    TikTokPublisher().publish(ctx)
    assert (paths.ROOT / ctx.asset.files["video"]).read_bytes() == data
    src = _json(next(c for c in rsps.calls if c.request.url == TT_VIDEO_INIT_URL))["source_info"]
    assert src["video_size"] == len(data)


def test_inbox_mode(cached_token, rsps):
    rsps.add(responses.POST, TT_INBOX_INIT_URL, json={"data": {"publish_id": "p5", "upload_url": UPLOAD}, **OK})
    rsps.add(responses.PUT, UPLOAD.split("?")[0], status=201)
    rsps.add(responses.POST, TT_STATUS_URL, json={"data": {"status": "SEND_TO_USER_INBOX"}, **OK})
    res = TikTokPublisher().publish(_ctx(_cfg(mode="inbox")))
    assert res.status == "published" and res.extra["needs_manual_post"] is True
    assert not any(c.request.url == TT_CREATOR_INFO_URL for c in rsps.calls)
    assert "post_info" not in _json(rsps.calls[0])


def test_dry_run_no_network(root, rsps):
    ctx = _ctx(_cfg(), dry=True)
    res = TikTokPublisher().publish(ctx)
    assert res.remote_id.startswith("dry-run-tt-") and "SELF_ONLY" in res.note
    assert len(rsps.calls) == 0
    assert any("total_chunk_count=1" in line for line in ctx.log)


def test_chunk_plan_and_title_limit():
    mb = 1024 * 1024
    assert chunk_plan(10 * mb) == (10 * mb, 1)
    assert chunk_plan(63 * mb) == (63 * mb, 1)
    size, count = chunk_plan(100 * mb)
    assert (size, count) == (32 * mb, 3)
    assert 100 * mb - size * (count - 1) < 64 * mb       # 마지막 청크 크기 상한 안
    with pytest.raises(PublishError):
        chunk_plan(0)
    assert truncate_utf16("가" * 2300) == "가" * 2200
    assert len(truncate_utf16("😀" * 1200).encode("utf-16-le")) // 2 == 2200


# ---------------------------------------------------------------- auth tiktok

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    tiktok.register_auth(p.add_subparsers())
    return p


def test_auth_tiktok_two_steps(root, rsps, capsys):
    args = _parser().parse_args(["tiktok", "--redirect-uri", "https://example.com/cb", "--pkce"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    url = next(line.strip() for line in out.splitlines() if line.strip().startswith("https://www.tiktok.com/v2/auth/authorize/"))
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["client_key"] == "ck123" and q["response_type"] == "code" and q["redirect_uri"] == "https://example.com/cb"
    assert set(q["scope"].split(",")) == {"user.info.basic", "video.publish", "video.upload", "video.list"}
    pending = json.loads((paths.SECRETS / "tiktok_auth_pending.json").read_text())
    assert pending["state"] == q["state"]
    assert q["code_challenge"] == hashlib.sha256(pending["code_verifier"].encode()).hexdigest()
    assert q["code_challenge_method"] == "S256"

    rsps.add(responses.POST, TT_TOKEN_URL, json={"access_token": "act.first", "expires_in": 86400, "open_id": "oid",
                                                 "refresh_token": "rft.issued.777", "refresh_expires_in": 31536000,
                                                 "scope": "user.info.basic,video.publish,video.upload,video.list"})
    rsps.add(responses.POST, TT_CREATOR_INFO_URL, json=_creator())
    redirect = f"https://example.com/cb?code=abc%2Adef%21&scopes=video.publish&state={q['state']}"
    args = _parser().parse_args(["tiktok", "--code", redirect])
    assert args.func(args) == 0
    form = {k: v[0] for k, v in parse_qs(rsps.calls[0].request.body).items()}
    assert form["code"] == "abc*def!"                       # 주소창의 URL 인코딩을 풀어서 보낸다
    assert form["grant_type"] == "authorization_code" and form["redirect_uri"] == "https://example.com/cb"
    assert form["code_verifier"] == pending["code_verifier"]
    rec = FileTokenStore().get("tiktok")
    assert rec["refresh_token"] == "rft.issued.777" and rec["seed"] == fingerprint("rft.issued.777")
    assert not (paths.SECRETS / "tiktok_auth_pending.json").exists()
    out = capsys.readouterr().out
    assert "TIKTOK_REFRESH_TOKEN" in out and "rft.issued.777" not in out and "@study_daily" in out


def test_auth_tiktok_state_mismatch(root, rsps, capsys):
    args = _parser().parse_args(["tiktok", "--redirect-uri", "https://example.com/cb"])
    assert args.func(args) == 0
    args = _parser().parse_args(["tiktok", "--code", "https://example.com/cb?code=zzz&state=forged"])
    assert args.func(args) == 1
    assert len(rsps.calls) == 0
    assert "state" in capsys.readouterr().err


def test_auth_tiktok_needs_redirect(root, rsps, capsys):
    args = _parser().parse_args(["tiktok"])
    assert args.func(args) == 1
    assert "리디렉트" in capsys.readouterr().err

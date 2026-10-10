"""페이스북 페이지 릴스 발행기·`auth facebook` 테스트. 네트워크는 responses 로 흉내 낸다."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import responses

from studio import config, paths
from studio.config import Config
from studio.models import PublishTarget, RenderedAsset, VideoScript, read_model
from studio.publish import captions, facebook, meta_auth
from studio.publish.base import PublishContext, PublishError
from studio.publish.facebook import FacebookPublisher
from studio.tokens import FileTokenStore

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W42"
FB = "https://graph.facebook.com/v26.0"
PAGE = "1122334455"
TOKEN = "EAApage-token-abcdef"
PUB = f"https://ref.supabase.co/storage/v1/object/public/media/{WEEK}"
RUP = "https://rupload.facebook.com/video-upload/v26.0/v1"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    for k in ("STUDIO_DRY_RUN", "STUDIO_TOKEN_BACKEND", "META_APP_ID", "META_APP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FACEBOOK_PAGE_ID", PAGE)
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", TOKEN)
    monkeypatch.setattr(facebook, "_sleep", lambda s: None)
    monkeypatch.setattr(meta_auth, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


def _cfg(**fb) -> Config:
    return Config.model_validate({
        "app": {"name": "복습노트", "link_base": "https://ref.supabase.co/functions/v1/go"},
        "platforms": {"facebook": {"poll_timeout_seconds": 0, "poll_interval_seconds": 0, **fb}},
    })


def _ctx(cfg: Config, *, publish_at: datetime | None = None, remote: bool = True, remote_id: str | None = None,
         dry: bool = False) -> PublishContext:
    script = read_model(FIX / "video_short.json", VideoScript)
    asset = RenderedAsset(asset_id=script.id, kind="short", duration_seconds=42.0,
                          files={"video": f"out/weeks/{WEEK}/{script.id}/video.mp4"},
                          remote_urls={"video": f"{PUB}/{script.id}/video.mp4"} if remote else {})
    t = PublishTarget(asset_id=script.id, asset_kind="short", platform="facebook",
                      publish_at=publish_at or datetime.now(paths.KST) - timedelta(minutes=5), remote_id=remote_id)
    return PublishContext(week=WEEK, target=t, asset=asset, content=script, cfg=cfg, dry_run=dry)


def _form(call) -> dict[str, str]:
    body = call.request.body or ""
    body = body.decode() if isinstance(body, bytes) else body
    return {k: v[0] for k, v in parse_qs(body).items()}


def _status(video_status="ready", proc="complete", pub="complete", publish_status="published") -> dict:
    return {"id": "v1", "permalink_url": "/reel/v1/", "status": {
        "video_status": video_status,
        "uploading_phase": {"status": "complete"},
        "processing_phase": {"status": proc},
        "publishing_phase": {"status": pub, "publish_status": publish_status},
    }}


def _start(rsps) -> None:
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", json={"video_id": "v1", "upload_url": RUP})


def test_publish_now_with_file_url(root, rsps):
    _start(rsps)
    rsps.add(responses.POST, RUP, json={"success": True})
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", json={"success": True})
    rsps.add(responses.GET, f"{FB}/v1", json=_status())
    ctx = _ctx(_cfg())
    res = FacebookPublisher().publish(ctx)
    assert (res.status, res.remote_id, res.url) == ("published", "v1", "https://www.facebook.com/reel/v1/")
    assert _form(rsps.calls[0]) == {"upload_phase": "start", "access_token": TOKEN}
    up = rsps.calls[1].request
    assert up.headers["Authorization"] == f"OAuth {TOKEN}"
    assert up.headers["file_url"] == f"{PUB}/2026W42-S01/video.mp4"
    assert "access_token" not in up.url
    fin = _form(rsps.calls[2])
    assert fin["upload_phase"] == "finish" and fin["video_id"] == "v1"
    assert fin["video_state"] == "PUBLISHED" and "scheduled_publish_time" not in fin
    assert fin["description"] == captions.reel_caption(ctx.content, ctx.cfg, "facebook")
    assert "https://ref.supabase.co/functions/v1/go?" in fin["description"]     # 페북 캡션에는 추적 링크
    assert "is_ai_generated" not in fin


def test_future_publish_is_scheduled_then_check(root, rsps):
    when = datetime.now(paths.KST) + timedelta(days=2)
    _start(rsps)
    rsps.add(responses.POST, RUP, json={"success": True})
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", json={"success": True})
    rsps.add(responses.GET, f"{FB}/v1", json=_status("processing", "in_progress", "not_started", ""))
    pub = FacebookPublisher()
    res = pub.publish(_ctx(_cfg(is_ai_generated=True), publish_at=when))
    assert (res.status, res.remote_id) == ("processing", "v1")
    fin = _form(rsps.calls[2])
    assert fin["video_state"] == "SCHEDULED"
    assert fin["scheduled_publish_time"] == str(int(when.timestamp()))
    assert fin["is_ai_generated"] == "true"

    rsps.replace(responses.GET, f"{FB}/v1", json=_status("ready", "complete", "not_started", "scheduled"))
    done = pub.check(_ctx(_cfg(), publish_at=when, remote_id="v1"))
    assert (done.status, done.remote_id) == ("scheduled", "v1")


def test_binary_upload_when_no_public_url(root, rsps):
    video = paths.ROOT / f"out/weeks/{WEEK}/2026W42-S01/video.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4" * 1000)
    _start(rsps)
    rsps.add(responses.POST, RUP, json={"success": True})
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", json={"success": True})
    rsps.add(responses.GET, f"{FB}/v1", json=_status())
    res = FacebookPublisher().publish(_ctx(_cfg(), remote=False))
    assert res.status == "published"
    up = rsps.calls[1].request
    assert up.headers["offset"] == "0" and up.headers["file_size"] == "3000"
    assert "file_url" not in up.headers
    body = up.body.read() if hasattr(up.body, "read") else up.body
    assert body == b"mp4" * 1000


def test_url_mode_requires_public_url(root, rsps):
    with pytest.raises(PublishError) as e:
        FacebookPublisher().publish(_ctx(_cfg(upload="url"), remote=False))
    assert "upload-media" in str(e.value)
    assert len(rsps.calls) == 0


def test_permission_error_not_retryable(root, rsps):
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", status=403,
             json={"error": {"message": "(#200) Requires pages_manage_posts", "type": "OAuthException", "code": 200}})
    with pytest.raises(PublishError) as e:
        FacebookPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is False and "권한" in str(e.value)
    assert TOKEN not in str(e.value)


def test_rate_limit_retryable(root, rsps):
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", status=400,
             json={"error": {"message": "Page request limit reached", "code": 32}})
    with pytest.raises(PublishError) as e:
        FacebookPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is True


def test_processing_error_not_retryable(root, rsps):
    _start(rsps)
    rsps.add(responses.POST, RUP, json={"success": True})
    rsps.add(responses.POST, f"{FB}/{PAGE}/video_reels", json={"success": True})
    st = _status("error", "error", "not_started", "")
    st["status"]["processing_phase"]["errors"] = [{"code": 1363008, "message": "Video duration too long"}]
    rsps.add(responses.GET, f"{FB}/v1", json=st)
    with pytest.raises(PublishError) as e:
        FacebookPublisher().publish(_ctx(_cfg()))
    assert e.value.retryable is False and "Video duration too long" in str(e.value)


def test_too_long_reel_rejected_before_upload(root, rsps):
    ctx = _ctx(_cfg())
    ctx.asset.duration_seconds = 120.0
    with pytest.raises(PublishError) as e:
        FacebookPublisher().publish(ctx)
    assert e.value.retryable is False and len(rsps.calls) == 0


def test_dry_run(root, rsps):
    res = FacebookPublisher().publish(_ctx(_cfg(), dry=True, publish_at=datetime.now(paths.KST) + timedelta(days=1)))
    assert res.status == "scheduled" and res.remote_id.startswith("dry-run-fb-")
    assert len(rsps.calls) == 0


def test_missing_credentials(root, rsps, monkeypatch):
    monkeypatch.delenv("FACEBOOK_PAGE_TOKEN")
    with pytest.raises(PublishError) as e:
        FacebookPublisher().publish(_ctx(_cfg()))
    assert "[설정 필요]" in str(e.value) and e.value.retryable is False


# ---------------------------------------------------------------- auth facebook

def test_auth_facebook_lists_pages_and_stores(root, rsps, capsys):
    rsps.add(responses.GET, f"{FB}/oauth/access_token", json={"access_token": "EAAlong-user-token-01", "token_type": "bearer"})
    rsps.add(responses.GET, f"{FB}/me/accounts", json={"data": [
        {"id": PAGE, "name": "공부 채널", "access_token": "EAApage-never-expires", "tasks": ["CREATE_CONTENT", "MANAGE"],
         "instagram_business_account": {"id": "17841400000000001", "username": "study_daily"}}]})
    rsps.add(responses.GET, f"{FB}/debug_token", json={"data": {"expires_at": 0, "is_valid": True}})
    parser = argparse.ArgumentParser()
    meta_auth.register_auth(parser.add_subparsers())
    args = parser.parse_args(["facebook", "--token", "EAAshort-user-token", "--app-id", "123", "--app-secret", "sek-ret-value"])
    assert args.func(args) == 0
    assert rsps.calls[0].request.url.count("fb_exchange_token=EAAshort-user-token") == 1
    assert "access_token=EAAlong-user-token-01" in rsps.calls[1].request.url
    rec = FileTokenStore().get("facebook")
    assert rec["page_id"] == PAGE and rec["page_token"] == "EAApage-never-expires"
    assert rec["instagram_business_account"] == "17841400000000001"
    out = capsys.readouterr().out
    assert "만료 없음" in out and PAGE in out and "EAApage-never-expires" not in out
    # 저장된 값만으로도 발행할 수 있다(로컬 편의)
    assert facebook._credentials() == (PAGE, TOKEN)   # 환경변수가 우선


def test_auth_facebook_bad_token(root, rsps, capsys):
    rsps.add(responses.GET, f"{FB}/me/accounts", status=400,
             json={"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}})
    parser = argparse.ArgumentParser()
    meta_auth.register_auth(parser.add_subparsers())
    args = parser.parse_args(["facebook", "--token", "EAAbad-token-value"])
    assert args.func(args) == 1
    err = capsys.readouterr().err
    assert "auth facebook" in err and "EAAbad-token-value" not in err

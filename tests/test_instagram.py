"""인스타그램 발행기·`auth instagram` 테스트. 네트워크는 responses 로 흉내 낸다."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from studio import config, paths
from studio.config import Config
from studio.models import (
    CardNews,
    PublishTarget,
    RenderedAsset,
    VideoScript,
    read_model,
)
from studio.publish import captions, instagram, meta_auth
from studio.publish.base import PublishContext, PublishError
from studio.publish.instagram import InstagramPublisher
from studio.publish.meta_auth import fingerprint
from studio.tokens import FileTokenStore

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W42"
IG = "https://graph.instagram.com/v26.0"
FB = "https://graph.facebook.com/v26.0"
PUB = f"https://ref.supabase.co/storage/v1/object/public/media/{WEEK}"
UID = "17841400000000001"
TOKEN = "IGAAtoken-long-lived-0001"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    for k in ("STUDIO_DRY_RUN", "STUDIO_TOKEN_BACKEND", "INSTAGRAM_TOKEN", "INSTAGRAM_USER_ID", "FACEBOOK_PAGE_TOKEN",
              "INSTAGRAM_APP_SECRET", "META_APP_ID", "META_APP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(instagram, "_sleep", lambda s: None)
    monkeypatch.setattr(meta_auth, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


@pytest.fixture
def fresh_token(root, monkeypatch):
    """갱신이 필요 없는 상태: 시크릿 = 저장된 토큰, 발급 1일 전."""
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", UID)
    issued = datetime.now(UTC) - timedelta(days=1)
    FileTokenStore().set("instagram", {"access_token": TOKEN, "seed": fingerprint(TOKEN), "issued_at": issued.isoformat()})


def _cfg(**ig) -> Config:
    return Config.model_validate({
        "app": {"name": "복습노트"},
        "platforms": {"instagram": {"poll_timeout_seconds": 0, "poll_interval_seconds": 0, **ig}},
    })


def _short_ctx(cfg: Config, remote_id: str | None = None, dry: bool = False) -> PublishContext:
    script = read_model(FIX / "video_short.json", VideoScript)
    asset = RenderedAsset(
        asset_id=script.id, kind="short", duration_seconds=42.0,
        files={"video": f"out/weeks/{WEEK}/{script.id}/video.mp4", "cover": f"out/weeks/{WEEK}/{script.id}/cover.png"},
        remote_urls={"video": f"{PUB}/{script.id}/video.mp4", "cover": f"{PUB}/{script.id}/cover.png",
                     "cover_jpg": f"{PUB}/{script.id}/cover.jpg"},
    )
    t = PublishTarget(asset_id=script.id, asset_kind="short", platform="instagram",
                      publish_at=datetime.now(paths.KST), remote_id=remote_id)
    return PublishContext(week=WEEK, target=t, asset=asset, content=script, cfg=cfg, dry_run=dry)


def _card_ctx(cfg: Config, n: int = 3) -> PublishContext:
    card = read_model(FIX / "card_sample.json", CardNews)
    files = {f"slide{i:02d}": f"out/weeks/{WEEK}/{card.id}/slide{i:02d}.jpg" for i in range(1, n + 1)}
    asset = RenderedAsset(asset_id=card.id, kind="cardnews", files=files,
                          remote_urls={k: f"{PUB}/{card.id}/{k}.jpg" for k in files},
                          meta={"alt_texts": [f"{i}번째 카드 설명" for i in range(1, n + 1)]})
    t = PublishTarget(asset_id=card.id, asset_kind="cardnews", platform="instagram", publish_at=datetime.now(paths.KST))
    return PublishContext(week=WEEK, target=t, asset=asset, content=card, cfg=cfg)


def _form(call) -> dict[str, str]:
    body = call.request.body or ""
    body = body.decode() if isinstance(body, bytes) else body
    return {k: v[0] for k, v in parse_qs(body).items()}


def _query(call) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(call.request.url).query).items()}


def _graph_error(code: int, message: str, subcode: int | None = None, status: int = 400) -> dict:
    err = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "AbC"}
    if subcode:
        err["error_subcode"] = subcode
    return {"json": {"error": err}, "status": status}


def test_reel_success(fresh_token, rsps):
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "c1"})
    rsps.add(responses.GET, f"{IG}/c1", json={"status_code": "FINISHED", "id": "c1"})
    rsps.add(responses.POST, f"{IG}/{UID}/media_publish", json={"id": "m1"})
    rsps.add(responses.GET, f"{IG}/m1", json={"permalink": "https://www.instagram.com/reel/abc/", "id": "m1"})
    ctx = _short_ctx(_cfg())
    res = InstagramPublisher().publish(ctx)
    assert (res.status, res.remote_id, res.url) == ("published", "m1", "https://www.instagram.com/reel/abc/")
    create = _form(rsps.calls[0])
    assert create["media_type"] == "REELS"
    assert create["video_url"] == f"{PUB}/2026W42-S01/video.mp4"
    assert create["cover_url"] == f"{PUB}/2026W42-S01/cover.jpg"   # JPEG 사본을 우선
    assert create["share_to_feed"] == "true"
    assert create["caption"] == captions.reel_caption(ctx.content, ctx.cfg, "instagram")
    assert "is_ai_generated" not in create                          # 기본 False → 보내지 않음
    assert create["access_token"] == TOKEN
    assert "access_token" not in rsps.calls[0].request.url           # POST 토큰은 본문에만
    assert _form(rsps.calls[2]) == {"creation_id": "c1", "access_token": TOKEN}
    assert not any(TOKEN in line for line in ctx.log)


def test_reel_ai_flag_and_feed_setting(fresh_token, rsps):
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "c1"})
    rsps.add(responses.GET, f"{IG}/c1", json={"status_code": "IN_PROGRESS"})
    cfg = _cfg(is_ai_generated=True)
    cfg.platforms.instagram.share_reels_to_feed = False
    InstagramPublisher().publish(_short_ctx(cfg))
    create = _form(rsps.calls[0])
    assert create["is_ai_generated"] == "true" and create["share_to_feed"] == "false"


def test_reel_processing_then_check_publishes(fresh_token, rsps):
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "c9"})
    rsps.add(responses.GET, f"{IG}/c9", json={"status_code": "IN_PROGRESS"})
    pub = InstagramPublisher()
    res = pub.publish(_short_ctx(_cfg()))
    assert (res.status, res.remote_id) == ("processing", "c9")
    assert not any(c.request.url.endswith("media_publish") for c in rsps.calls)

    rsps.replace(responses.GET, f"{IG}/c9", json={"status_code": "IN_PROGRESS"})
    again = pub.check(_short_ctx(_cfg(), remote_id="c9"))
    assert again.status == "processing"

    rsps.replace(responses.GET, f"{IG}/c9", json={"status_code": "FINISHED"})
    rsps.add(responses.POST, f"{IG}/{UID}/media_publish", json={"id": "m9"})
    rsps.add(responses.GET, f"{IG}/m9", json={"permalink": "https://www.instagram.com/reel/m9/"})
    done = pub.check(_short_ctx(_cfg(), remote_id="c9"))
    assert (done.status, done.remote_id, done.url) == ("published", "m9", "https://www.instagram.com/reel/m9/")


def test_check_on_already_published_container_finds_media(fresh_token, rsps):
    ctx = _short_ctx(_cfg(), remote_id="c5")
    cap = captions.reel_caption(ctx.content, ctx.cfg, "instagram")
    rsps.add(responses.GET, f"{IG}/c5", json={"status_code": "PUBLISHED"})
    rsps.add(responses.GET, f"{IG}/{UID}/media", json={"data": [
        {"id": "m0", "caption": "다른 글"}, {"id": "m5", "caption": cap, "permalink": "https://www.instagram.com/reel/m5/"}]})
    res = InstagramPublisher().check(ctx)
    assert (res.status, res.remote_id) == ("published", "m5")


@pytest.mark.parametrize("code,sub,retry,word", [
    (10, None, False, "권한"),
    (200, None, False, "권한"),
    (190, None, False, "auth instagram"),
    (4, None, True, "한도"),
    (9, 2207042, True, "한도"),
    (100, None, False, "요청 값"),
])
def test_graph_errors_classified(fresh_token, rsps, code, sub, retry, word):
    rsps.add(responses.POST, f"{IG}/{UID}/media", **_graph_error(code, "denied", sub))
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(_short_ctx(_cfg()))
    assert e.value.retryable is retry
    assert word in str(e.value)
    assert TOKEN not in str(e.value)


def test_container_error_retryable_only_when_transient(fresh_token, rsps):
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "c1"})
    rsps.add(responses.GET, f"{IG}/c1", json={"status_code": "ERROR", "status": "Error: 2207026 unsupported video format"})
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(_short_ctx(_cfg()))
    assert e.value.retryable is False and "규격" in str(e.value)
    rsps.replace(responses.GET, f"{IG}/c1", json={"status_code": "ERROR", "status": "Error: 2207003 media download timeout"})
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(_short_ctx(_cfg()))
    assert e.value.retryable is True


def test_missing_remote_url_points_to_upload_media(fresh_token, rsps):
    ctx = _short_ctx(_cfg())
    ctx.asset.remote_urls = {}
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(ctx)
    assert "upload-media" in str(e.value)
    assert len(rsps.calls) == 0


def test_token_refreshed_after_50_days_and_saved(root, rsps, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", UID)
    old = datetime.now(UTC) - timedelta(days=51)
    FileTokenStore().set("instagram", {"access_token": TOKEN, "seed": fingerprint(TOKEN), "issued_at": old.isoformat()})
    rsps.add(responses.GET, "https://graph.instagram.com/refresh_access_token",
             json={"access_token": "IGAAnew-token-0002", "token_type": "bearer", "expires_in": 5184000})
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "c1"})
    rsps.add(responses.GET, f"{IG}/c1", json={"status_code": "IN_PROGRESS"})
    ctx = _short_ctx(_cfg())
    InstagramPublisher().publish(ctx)
    q = _query(rsps.calls[0])
    assert q == {"grant_type": "ig_refresh_token", "access_token": TOKEN}
    assert _form(rsps.calls[1])["access_token"] == "IGAAnew-token-0002"
    rec = FileTokenStore().get("instagram")
    assert rec["access_token"] == "IGAAnew-token-0002"
    assert rec["seed"] == fingerprint(TOKEN)            # 같은 시크릿이 남아 있어도 회전된 토큰을 계속 쓴다
    assert datetime.fromisoformat(rec["issued_at"]) > datetime.now(UTC) - timedelta(minutes=1)
    assert rec["expires_at"]
    assert any("갱신" in line for line in ctx.log)
    # 다음 실행: 저장된 새 토큰을 쓰고 다시 갱신하지 않는다
    rsps.calls.reset()
    InstagramPublisher().publish(_short_ctx(_cfg()))
    assert not any("refresh_access_token" in c.request.url for c in rsps.calls)
    assert _form(rsps.calls[0])["access_token"] == "IGAAnew-token-0002"


def test_new_secret_overrides_rotated_token(root, rsps, monkeypatch):
    FileTokenStore().set("instagram", {"access_token": "IGAAold-rotated-0003", "seed": fingerprint("IGAAold-seed-0000"),
                                       "issued_at": datetime.now(UTC).isoformat(), "user_id": UID})
    monkeypatch.setenv("INSTAGRAM_TOKEN", "IGAAbrand-new-0004")
    # 발급 시각을 모름 → 갱신 시도. 24시간이 안 된 토큰이라 거절되어도 발행은 계속한다.
    rsps.add(responses.GET, "https://graph.instagram.com/refresh_access_token",
             **_graph_error(100, "Session has not been active long enough"))
    rsps.add(responses.GET, f"{IG}/me", json={"user_id": UID, "username": "study"})
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "c1"})
    rsps.add(responses.GET, f"{IG}/c1", json={"status_code": "IN_PROGRESS"})
    ctx = _short_ctx(_cfg())
    res = InstagramPublisher().publish(ctx)
    assert res.status == "processing"
    assert _form(next(c for c in rsps.calls if c.request.method == "POST"))["access_token"] == "IGAAbrand-new-0004"
    rec = FileTokenStore().get("instagram")
    assert rec["access_token"] == "IGAAbrand-new-0004" and rec["seed"] == fingerprint("IGAAbrand-new-0004")
    assert rec["refresh_attempted_at"]
    assert any("[경고]" in line for line in ctx.log)


def test_expired_token_on_refresh_is_not_retryable(root, rsps, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", UID)
    rsps.add(responses.GET, "https://graph.instagram.com/refresh_access_token",
             **_graph_error(190, "Error validating access token: Session has expired"))
    rsps.add(responses.POST, f"{IG}/{UID}/media", **_graph_error(190, "Error validating access token: Session has expired"))
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(_short_ctx(_cfg()))
    assert e.value.retryable is False and "auth instagram" in str(e.value)
    assert TOKEN not in str(e.value)
    assert FileTokenStore().get("instagram")["refresh_attempted_at"]   # 하루 안에는 다시 갱신하지 않는다


def test_missing_token_is_setup_error(root, rsps):
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(_short_ctx(_cfg()))
    assert "[설정 필요]" in str(e.value) and e.value.retryable is False


def test_carousel_success_with_alt_text(fresh_token, rsps):
    for i in (1, 2, 3):
        rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": f"k{i}"})
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "parent"})
    for cid in ("k1", "k2", "k3", "parent"):
        rsps.add(responses.GET, f"{IG}/{cid}", json={"status_code": "FINISHED"})
    rsps.add(responses.POST, f"{IG}/{UID}/media_publish", json={"id": "m7"})
    rsps.add(responses.GET, f"{IG}/m7", json={"permalink": "https://www.instagram.com/p/m7/"})
    ctx = _card_ctx(_cfg())
    res = InstagramPublisher().publish(ctx)
    assert (res.status, res.remote_id) == ("published", "m7")
    posts = [_form(c) for c in rsps.calls if c.request.method == "POST" and c.request.url.endswith("/media")]
    assert [p["image_url"] for p in posts[:3]] == [f"{PUB}/2026W42-C01/slide0{i}.jpg" for i in (1, 2, 3)]
    assert all(p["is_carousel_item"] == "true" for p in posts[:3])
    assert posts[0]["alt_text"] == "1번째 카드 설명"
    assert posts[3]["media_type"] == "CAROUSEL" and posts[3]["children"] == "k1,k2,k3"
    assert posts[3]["caption"] == captions.carousel_caption(ctx.content, ctx.cfg)


def test_carousel_drops_alt_text_when_rejected(fresh_token, rsps):
    rsps.add(responses.POST, f"{IG}/{UID}/media", **_graph_error(100, "Invalid parameter alt_text"))
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "k1"})
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "k2"})
    rsps.add(responses.POST, f"{IG}/{UID}/media", json={"id": "parent"})
    for cid in ("k1", "k2"):
        rsps.add(responses.GET, f"{IG}/{cid}", json={"status_code": "FINISHED"})
    rsps.add(responses.GET, f"{IG}/parent", json={"status_code": "IN_PROGRESS"})
    res = InstagramPublisher().publish(_card_ctx(_cfg(), n=2))
    assert (res.status, res.remote_id) == ("processing", "parent")
    posts = [_form(c) for c in rsps.calls if c.request.method == "POST"]
    assert "alt_text" in posts[0] and "alt_text" not in posts[1] and "alt_text" not in posts[2]


def test_carousel_slide_count_limits(fresh_token, rsps):
    ctx = _card_ctx(_cfg(), n=3)
    ctx.asset.files = {"slide01": ctx.asset.files["slide01"]}
    with pytest.raises(PublishError) as e:
        InstagramPublisher().publish(ctx)
    assert e.value.retryable is False


def test_dry_run_sends_nothing(root, rsps):
    ctx = _short_ctx(_cfg(), dry=True)
    ctx.asset.remote_urls = {}
    res = InstagramPublisher().publish(ctx)
    assert res.status == "published" and res.remote_id.startswith("dry-run-ig-")
    assert len(rsps.calls) == 0
    assert any("media_type=REELS" in line and "/video.mp4" in line for line in ctx.log)
    card = _card_ctx(_cfg())
    card.dry_run = True
    assert InstagramPublisher().publish(card).remote_id.startswith("dry-run-ig-")
    assert len(rsps.calls) == 0


def test_env_dry_run_flag_also_blocks_network(fresh_token, rsps, monkeypatch):
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    assert InstagramPublisher().publish(_short_ctx(_cfg())).remote_id.startswith("dry-run")
    assert len(rsps.calls) == 0


def test_facebook_login_mode_uses_graph_facebook(root, rsps, monkeypatch):
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "EAApage-token-0005")
    monkeypatch.setenv("INSTAGRAM_USER_ID", UID)
    rsps.add(responses.POST, f"{FB}/{UID}/media", json={"id": "c1"})
    rsps.add(responses.GET, f"{FB}/c1", json={"status_code": "IN_PROGRESS"})
    res = InstagramPublisher().publish(_short_ctx(_cfg(login="facebook")))
    assert res.status == "processing"
    assert _form(rsps.calls[0])["access_token"] == "EAApage-token-0005"
    assert not any("refresh_access_token" in c.request.url for c in rsps.calls)


# ---------------------------------------------------------------- auth instagram

def _auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    g = parser.add_subparsers()
    meta_auth.register_auth(g)
    return parser


def test_auth_instagram_exchanges_and_stores(root, rsps, capsys):
    rsps.add(responses.GET, "https://graph.instagram.com/access_token",
             json={"access_token": "IGAAlong-lived-0006", "token_type": "bearer", "expires_in": 5183944})
    rsps.add(responses.GET, f"{IG}/me", json={"user_id": UID, "username": "study_daily", "account_type": "BUSINESS", "id": "app-scoped"})
    args = _auth_parser().parse_args(["instagram", "--token", "IGQshort-token-0007", "--app-secret", "appsecret123"])
    assert args.func(args) == 0
    q = _query(rsps.calls[0])
    assert q["grant_type"] == "ig_exchange_token" and q["access_token"] == "IGQshort-token-0007"
    assert _query(rsps.calls[1])["access_token"] == "IGAAlong-lived-0006"
    rec = FileTokenStore().get("instagram")
    assert rec["access_token"] == "IGAAlong-lived-0006" and rec["user_id"] == UID
    assert rec["seed"] == fingerprint("IGAAlong-lived-0006") and rec["expires_at"]
    out = capsys.readouterr().out
    assert UID in out and "IGAAlong-lived-0006" not in out and "@study_daily" in out


def test_auth_instagram_dry_run(root, rsps, capsys):
    args = _auth_parser().parse_args(["instagram", "--dry-run"])
    assert args.func(args) == 0
    assert len(rsps.calls) == 0
    assert FileTokenStore().get("instagram") is None

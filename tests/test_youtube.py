"""YouTube 발행기 테스트. googleapiclient 를 실제로 쓰되 HTTP 는 HttpMockSequence 로 막는다(네트워크 없음)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence
from PIL import Image

from studio import config, paths
from studio.config import Config
from studio.models import Chapter, PublishTarget, RenderedAsset, Scene, VideoScript
from studio.publish import google_auth, youtube
from studio.publish.base import PublishContext, PublishError
from studio.publish.captions import AI_NOTICE
from studio.publish.youtube import YouTubePublisher

WEEK = "2026-W42"
LINK_BASE = "https://ref.supabase.co/functions/v1/go"
LONG_AT = datetime(2026, 10, 11, 19, 0, tzinfo=paths.KST)       # = 2026-10-11T10:00:00Z
SHORT_AT = datetime(2026, 10, 12, 12, 0, tzinfo=paths.KST)      # = 2026-10-12T03:00:00Z
NOW = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(youtube, "_now", lambda: NOW)
    sleeps: list[float] = []
    monkeypatch.setattr(youtube, "_sleep", sleeps.append)
    config.load_config.cache_clear()
    google_auth.clear_cache()
    yield tmp_path
    config.load_config.cache_clear()
    google_auth.clear_cache()


def _cfg(audited: bool = True, **yt) -> Config:
    return Config.model_validate({
        "app": {"name": "복습노트", "link_base": LINK_BASE},
        "platforms": {"youtube": {"audited": audited, **yt}},
    })


def _script(fmt: str = "longform", **kw) -> VideoScript:
    sid = "2026W42-L1" if fmt == "longform" else "2026W42-S01"
    data = dict(
        id=sid,
        week=WEEK,
        format=fmt,
        title="헷갈리는 맞춤법 <되/돼> 정리" if fmt == "longform" else "'안 되요'와 '안 돼요'",
        description="되와 돼를 한 번에 구분하는 방법을 정리했습니다.",
        tags=["맞춤법", "국어 공부", "맞춤법", "#정리"],
        hashtags=["맞춤법"],
        chapters=[Chapter(title="도입", scenes=[Scene(id="s1", kind="title", narration=["안녕하세요."])])],
        pinned_comment="정리 노트는 설명란 링크에서 받으세요." if fmt == "longform" else "",
    )
    data.update(kw)
    return VideoScript(**data)


def _asset(root: Path, script: VideoScript, video_bytes: int = 3000, duration: float = 1680.0) -> RenderedAsset:
    d = root / "out" / "weeks" / WEEK / script.id
    d.mkdir(parents=True, exist_ok=True)
    (d / "video.mp4").write_bytes(os.urandom(video_bytes))
    (d / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕하세요.\n", encoding="utf-8")
    thumb_role = "thumbnail" if script.format == "longform" else "cover"
    Image.new("RGB", (64, 36), "#8C2129").save(d / f"{thumb_role}.png")
    rel = f"out/weeks/{WEEK}/{script.id}"
    return RenderedAsset(
        asset_id=script.id,
        kind=script.format,
        files={"video": f"{rel}/video.mp4", thumb_role: f"{rel}/{thumb_role}.png", "subtitles": f"{rel}/subtitles.srt"},
        duration_seconds=duration if script.format == "longform" else 45.0,
        meta={"chapters": [[0, "도입"], [60, "본론"], [600, "정리"]], "size": [1920, 1080] if script.format == "longform" else [1080, 1920]},
    )


def _ctx(cfg: Config, script: VideoScript, asset: RenderedAsset, at: datetime, *, dry: bool = False,
         remote_id: str | None = None, status: str = "pending") -> PublishContext:
    target = PublishTarget(asset_id=script.id, asset_kind=script.format, platform="youtube", publish_at=at,
                           remote_id=remote_id, status=status)
    return PublishContext(week=WEEK, target=target, asset=asset, content=script, cfg=cfg, dry_run=dry)


def _mock(monkeypatch, responses: list) -> HttpMockSequence:
    http = HttpMockSequence(responses)
    svc = build("youtube", "v3", http=http, static_discovery=True)
    monkeypatch.setattr(google_auth, "youtube_service", lambda scopes=None: svc)
    return http


def _err(status: int, reason: str, message: str = "msg") -> tuple:
    return ({"status": str(status)}, json.dumps({"error": {"code": status, "message": message, "errors": [{"reason": reason, "message": message}]}}))


def _json_body(body) -> dict:
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


# ---------------------------------------------------------------- 본문

def test_body_schedules_private_with_utc_publish_at(root):
    script = _script()
    ctx = _ctx(_cfg(), script, _asset(root, script), LONG_AT)
    body = youtube.video_body(ctx, script)
    st, sn = body["status"], body["snippet"]
    assert st["privacyStatus"] == "private"
    assert st["publishAt"] == "2026-10-11T10:00:00Z"
    assert st["selfDeclaredMadeForKids"] is False
    assert st["containsSyntheticMedia"] is False
    assert sn["categoryId"] == "27" and sn["defaultLanguage"] == "ko" and sn["defaultAudioLanguage"] == "ko"
    assert "<" not in sn["title"] and "‹되/돼›" in sn["title"]
    # 설명문은 captions.youtube_description — 원고 설명 + 앱 추적 링크 + 챕터 + AI 고지
    assert sn["description"].startswith("되와 돼를")
    assert f"{LINK_BASE}?s=youtube&m=description&c=2026-W42&a=2026W42-L1" in sn["description"]
    assert "0:00 도입" in sn["description"] and AI_NOTICE in sn["description"]
    assert sn["tags"] == ["맞춤법", "국어 공부", "정리"]


def test_body_past_publish_at_goes_public(root):
    script = _script("short")
    ctx = _ctx(_cfg(), script, _asset(root, script), NOW - timedelta(hours=1))
    st = youtube.video_body(ctx, script)["status"]
    assert st["privacyStatus"] == "public" and "publishAt" not in st
    # 임박한 예약(여유 시간 안)도 바로 공개
    ctx = _ctx(_cfg(), script, _asset(root, script), NOW + timedelta(minutes=3))
    assert youtube.video_body(ctx, script)["status"]["privacyStatus"] == "public"


def test_naive_publish_at_is_treated_as_kst(root):
    script = _script("short")
    ctx = _ctx(_cfg(), script, _asset(root, script), datetime(2026, 10, 12, 12, 0))
    assert youtube.video_body(ctx, script)["status"]["publishAt"] == "2026-10-12T03:00:00Z"


def test_synthetic_media_and_kids_flags(root):
    script = _script("short", made_for_kids=True)
    ctx = _ctx(_cfg(contains_synthetic_media=True), script, _asset(root, script), SHORT_AT)
    st = youtube.video_body(ctx, script)["status"]
    assert st["containsSyntheticMedia"] is True and st["selfDeclaredMadeForKids"] is True


def test_fit_tags_limits_total_length():
    tags = [f"태그 {i:03d}" for i in range(120)]       # 공백 포함 → 따옴표 2자씩
    out = youtube.fit_tags(tags)
    cost = sum(len(t) + 2 for t in out) + len(out) - 1
    assert cost <= youtube.TAGS_MAX_CHARS and len(out) < len(tags)
    assert youtube.fit_tags(["a,b", "<x>", ""]) == ["a b", "‹x›"]


# ---------------------------------------------------------------- API 업로드

def test_upload_longform_full_flow(root, monkeypatch):
    script = _script()
    asset = _asset(root, script)
    http = _mock(monkeypatch, [
        ({"status": "200", "location": "https://upload.example/session-1"}, ""),
        ({"status": "200"}, json.dumps({"id": "VIDlong0001"})),
        ({"status": "200"}, json.dumps({"items": []})),                 # thumbnails.set
        ({"status": "200"}, json.dumps({"id": "cap1"})),                 # captions.insert
        ({"status": "200"}, json.dumps({"id": "pli1"})),                 # playlistItems.insert
    ])
    cfg = _cfg(playlist_longform="PLlong")
    res = YouTubePublisher().publish(_ctx(cfg, script, asset, LONG_AT))

    assert res.status == "scheduled"
    assert res.remote_id == "VIDlong0001"
    assert res.url == "https://youtu.be/VIDlong0001"
    assert res.extra["publishAt"] == "2026-10-11T10:00:00Z" and res.extra["warnings"] == []
    assert any("고정 댓글" in m for m in res.extra["manual"])

    seq = http.request_sequence
    uri, method, body, headers = seq[0]
    assert method == "POST" and "uploadType=resumable" in uri and "/videos" in uri
    assert "part=snippet%2Cstatus" in uri and "notifySubscribers=true" in uri
    assert headers["X-Upload-Content-Type"] == "video/mp4"
    sent = _json_body(body)
    assert sent["status"] == {
        "privacyStatus": "private", "publishAt": "2026-10-11T10:00:00Z", "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": False, "license": "youtube", "embeddable": True,
    }
    assert sent["snippet"]["description"].startswith("되와 돼를")
    assert seq[1][0] == "https://upload.example/session-1" and seq[1][1] == "PUT"
    assert "thumbnails/set" in seq[2][0] and "videoId=VIDlong0001" in seq[2][0]
    assert "captions" in seq[3][0]
    cap_body = seq[3][2] if isinstance(seq[3][2], (bytes, str)) else b""
    cap_text = cap_body.decode("utf-8", "replace") if isinstance(cap_body, bytes) else cap_body
    assert '"language": "ko"' in cap_text and '"videoId": "VIDlong0001"' in cap_text
    pl = _json_body(seq[4][2])
    assert pl["snippet"]["playlistId"] == "PLlong" and pl["snippet"]["resourceId"]["videoId"] == "VIDlong0001"


def test_upload_short_resumes_after_5xx(root, monkeypatch):
    monkeypatch.setattr(youtube, "UPLOAD_CHUNK_BYTES", 256 * 1024)
    script = _script("short")
    size = 600 * 1024
    asset = _asset(root, script, video_bytes=size)
    http = _mock(monkeypatch, [
        ({"status": "200", "location": "https://upload.example/s2"}, ""),
        ({"status": "308", "range": "bytes=0-262143"}, ""),
        ({"status": "503"}, json.dumps({"error": {"code": 503, "message": "backend"}})),
        ({"status": "308", "range": "bytes=0-524287"}, ""),               # 상태 조회: 두 번째 청크까지 받음
        ({"status": "200"}, json.dumps({"id": "VIDshort001"})),
    ])
    ctx = _ctx(_cfg(), script, asset, SHORT_AT)
    res = YouTubePublisher().publish(ctx)

    assert res.status == "scheduled" and res.remote_id == "VIDshort001"
    assert res.url == "https://youtube.com/shorts/VIDshort001"
    seq = http.request_sequence
    assert len(seq) == 5                                                  # 쇼츠: 썸네일·자막 없음, 재생목록 없음
    assert seq[3][3]["Content-Range"] == f"bytes */{size}"
    assert seq[4][3]["Content-Range"] == f"bytes 524288-{size - 1}/{size}"
    assert any("이어 올리기" in line for line in ctx.log)


def test_quota_exceeded_is_retryable(root, monkeypatch):
    script = _script("short")
    _mock(monkeypatch, [_err(403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.")])
    with pytest.raises(PublishError) as ei:
        YouTubePublisher().publish(_ctx(_cfg(), script, _asset(root, script), SHORT_AT))
    assert ei.value.retryable is True and "할당량" in str(ei.value)


@pytest.mark.parametrize("status,reason,word", [
    (403, "forbidden", "권한"),
    (403, "insufficientPermissions", "권한"),
    (400, "uploadLimitExceeded", "업로드 한도"),
    (400, "invalidPublishAt", "예약 시각"),
])
def test_permission_and_limit_errors_need_human(root, monkeypatch, status, reason, word):
    script = _script("short")
    _mock(monkeypatch, [_err(status, reason)])
    with pytest.raises(PublishError) as ei:
        YouTubePublisher().publish(_ctx(_cfg(), script, _asset(root, script), SHORT_AT))
    assert ei.value.retryable is False and word in str(ei.value)


def test_side_step_failure_keeps_uploaded_video(root, monkeypatch):
    script = _script()
    _mock(monkeypatch, [
        ({"status": "200", "location": "https://upload.example/s3"}, ""),
        ({"status": "200"}, json.dumps({"id": "VIDlong0002"})),
        _err(403, "forbidden", "The authenticated user doesnt have permissions to upload and set custom video thumbnails."),
        ({"status": "200"}, json.dumps({"id": "cap1"})),
    ])
    res = YouTubePublisher().publish(_ctx(_cfg(), script, _asset(root, script), LONG_AT))
    assert res.status == "scheduled" and res.remote_id == "VIDlong0002"
    assert any("thumbnails.set" in w and "전화번호" in w for w in res.extra["warnings"])
    assert "경고" in res.note


def test_auth_problem_is_not_retryable(root, monkeypatch):
    script = _script("short")

    def boom(scopes=None):
        raise google_auth.GoogleAuthError("refresh token 만료")

    monkeypatch.setattr(google_auth, "youtube_service", boom)
    with pytest.raises(PublishError) as ei:
        YouTubePublisher().publish(_ctx(_cfg(), script, _asset(root, script), SHORT_AT))
    assert ei.value.retryable is False and "만료" in str(ei.value)


def test_existing_remote_id_is_not_uploaded_again(root, monkeypatch):
    script = _script("short")
    http = _mock(monkeypatch, [
        ({"status": "200"}, json.dumps({"items": [{"id": "VIDold00001", "status": {
            "uploadStatus": "processed", "privacyStatus": "private", "publishAt": "2026-10-12T03:00:00Z"}}]})),
    ])
    res = YouTubePublisher().publish(_ctx(_cfg(), script, _asset(root, script), SHORT_AT, remote_id="VIDold00001"))
    assert res.status == "scheduled" and "중복 업로드 안 함" in res.note
    assert len(http.request_sequence) == 1 and http.request_sequence[0][1] == "GET"


def test_short_over_three_minutes_is_rejected(root):
    script = _script("short")
    asset = _asset(root, script)
    asset.duration_seconds = 200
    with pytest.raises(PublishError) as ei:
        YouTubePublisher().publish(_ctx(_cfg(), script, asset, SHORT_AT))
    assert ei.value.retryable is False


# ---------------------------------------------------------------- dry-run

def test_dry_run_writes_request_body(root, monkeypatch):
    def never(scopes=None):
        raise AssertionError("dry-run 에서 API 를 부르면 안 됩니다")

    monkeypatch.setattr(google_auth, "youtube_service", never)
    script = _script()
    ctx = _ctx(_cfg(playlist_longform="PLlong"), script, _asset(root, script), LONG_AT, dry=True)
    res = YouTubePublisher().publish(ctx)
    assert res.status == "scheduled" and res.remote_id == "dryrun-2026W42-L1"
    out = root / "out" / "weeks" / WEEK / "dryrun" / "youtube-2026W42-L1.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    methods = [r["method"] for r in data["requests"]]
    assert methods == ["videos.insert", "thumbnails.set", "captions.insert", "playlistItems.insert"]
    ins = data["requests"][0]
    assert ins["body"]["status"]["publishAt"] == "2026-10-11T10:00:00Z"
    assert ins["media"]["chunk_bytes"] == 8 * 1024 * 1024 and ins["media"]["path"].endswith("video.mp4")
    # 환경변수로도 켜진다
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    res2 = YouTubePublisher().publish(_ctx(_cfg(), script, _asset(root, script), LONG_AT))
    assert res2.remote_id.startswith("dryrun-")
    assert YouTubePublisher().check(_ctx(_cfg(), script, _asset(root, script), LONG_AT, remote_id=res2.remote_id, status="scheduled")).status == "scheduled"


# ---------------------------------------------------------------- 업로드 패키지 (감사 전)

def test_package_mode_builds_manual_upload_bundle(root, monkeypatch):
    def never(scopes=None):
        raise AssertionError("패키지 모드는 API 를 쓰지 않습니다")

    monkeypatch.setattr(google_auth, "youtube_service", never)
    script = _script()
    asset = _asset(root, script)
    res = YouTubePublisher().publish(_ctx(_cfg(audited=False, playlist_longform="PLlong"), script, asset, LONG_AT))

    pkg = root / "out" / "weeks" / WEEK / "youtube-package" / "2026W42-L1"
    assert res.status == "skipped" and res.remote_id is None
    assert res.note.startswith("수동 업로드 필요: out/weeks/2026-W42/youtube-package/2026W42-L1")
    for name in ("video.mp4", "thumbnail.png", "subtitles.srt", "metadata.json", "업로드안내.md"):
        assert (pkg / name).exists(), name
    assert os.path.samefile(pkg / "video.mp4", root / asset.files["video"])     # 하드링크

    meta = json.loads((pkg / "metadata.json").read_text(encoding="utf-8"))
    assert meta["publish_at_kst"] == "2026-10-11(일) 19:00 KST"
    assert meta["publish_at_utc"] == "2026-10-11T10:00:00Z"
    assert meta["visibility"] == "schedule" and meta["playlist_id"] == "PLlong"
    assert meta["api_body"]["status"]["privacyStatus"] == "private"
    assert meta["files"] == {"video": "video.mp4", "thumbnail": "thumbnail.png", "subtitles": "subtitles.srt"}

    guide = (pkg / "업로드안내.md").read_text(encoding="utf-8")
    assert "2026-10-11(일) 19:00 KST" in guide
    assert "아니요, 아동용이 아닙니다" in guide
    assert "변경되거나 합성된 콘텐츠" in guide and "AI 변형 콘텐츠" in guide
    assert meta["title"] in guide and "PLlong" in guide and "subtitles.srt" in guide
    assert "고정" in guide and script.pinned_comment in guide
    assert "publish mark" in guide and "youtube:2026W42-L1" in guide

    # 다시 실행해도 깨지지 않고, 순서표에 쇼츠가 공개 시각 순으로 더해진다
    YouTubePublisher().publish(_ctx(_cfg(audited=False), script, asset, LONG_AT))
    short = _script("short")
    YouTubePublisher().publish(_ctx(_cfg(audited=False), short, _asset(root, short), SHORT_AT))
    index = (pkg.parent / "업로드목록.md").read_text(encoding="utf-8")
    assert index.index("2026W42-L1") < index.index("2026W42-S01")
    assert (pkg.parent / "2026W42-S01" / "cover.png").exists()


def test_package_falls_back_to_copy(root, monkeypatch):
    def no_link(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(youtube.os, "link", no_link)
    script = _script("short")
    asset = _asset(root, script)
    YouTubePublisher().publish(_ctx(_cfg(audited=False), script, asset, NOW - timedelta(hours=2)))
    pkg = root / "out" / "weeks" / WEEK / "youtube-package" / "2026W42-S01"
    assert (pkg / "video.mp4").read_bytes() == (root / asset.files["video"]).read_bytes()
    assert not os.path.samefile(pkg / "video.mp4", root / asset.files["video"])
    guide = (pkg / "업로드안내.md").read_text(encoding="utf-8")
    assert "바로 **공개**" in guide


# ---------------------------------------------------------------- 상태 확인

def _video(status: dict, processing: dict | None = None) -> tuple:
    item = {"id": "VIDchk00001", "status": status}
    if processing:
        item["processingDetails"] = processing
    return ({"status": "200"}, json.dumps({"items": [item]}))


def test_check_states(root, monkeypatch):
    script = _script("short")
    asset = _asset(root, script)
    pub = YouTubePublisher()

    def ctx():
        return _ctx(_cfg(), script, asset, SHORT_AT, remote_id="VIDchk00001", status="scheduled")

    _mock(monkeypatch, [_video({"uploadStatus": "processed", "privacyStatus": "private", "publishAt": "2026-10-12T03:00:00Z"})])
    assert pub.check(ctx()).status == "scheduled"
    _mock(monkeypatch, [_video({"uploadStatus": "uploaded", "privacyStatus": "private"}, {"processingStatus": "processing",
                                "processingProgress": {"partsProcessed": "3", "partsTotal": "4"}})])
    r = pub.check(ctx())
    assert r.status == "processing" and "75%" in r.note
    _mock(monkeypatch, [_video({"uploadStatus": "processed", "privacyStatus": "public"})])
    assert pub.check(ctx()).status == "published"

    _mock(monkeypatch, [_video({"uploadStatus": "rejected", "rejectionReason": "duplicate", "privacyStatus": "private"})])
    with pytest.raises(PublishError) as ei:
        pub.check(ctx())
    assert ei.value.retryable is False and "duplicate" in str(ei.value)

    # 예약 시각이 한참 지났는데 비공개 → 감사 미통과 잠금 의심
    _mock(monkeypatch, [_video({"uploadStatus": "processed", "privacyStatus": "private", "publishAt": "2026-10-09T03:00:00Z"})])
    with pytest.raises(PublishError) as ei:
        pub.check(ctx())
    assert ei.value.retryable is False and "audited" in str(ei.value)

    _mock(monkeypatch, [({"status": "200"}, json.dumps({"items": []}))])
    with pytest.raises(PublishError):
        pub.check(ctx())

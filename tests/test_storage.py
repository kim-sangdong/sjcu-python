"""미디어 스토리지(Supabase Storage) 테스트. STUDIO_ROOT 를 tmp_path 로 돌리고 responses 로 HTTP 를 흉내 낸다."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

import pytest
import responses
from PIL import Image

from studio import config, paths
from studio.config import Config
from studio.manifest import load_manifest, upsert_asset
from studio.models import RenderedAsset
from studio.publish import storage
from studio.publish.storage import StorageError, ensure_local, upload_media

WEEK = "2026-W42"
BASE = "https://ref.supabase.co"
OBJ = f"{BASE}/storage/v1/object/media/{WEEK}"
PUB = f"{BASE}/storage/v1/object/public/media/{WEEK}"
TUS = "https://ref.storage.supabase.co/storage/v1/upload/resumable"
JWT_KEY = "eyJhbGciOiJIUzI1NiJ9.service.role"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    monkeypatch.setenv("SUPABASE_URL", BASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", JWT_KEY)
    monkeypatch.setattr(storage, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


def _write(rel: str, data: bytes) -> str:
    p = paths.ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return rel


def _png(rel: str) -> str:
    p = paths.ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (54, 96), (200, 30, 40, 255)).save(p)
    return rel


def _jpg(rel: str, color=(10, 20, 30)) -> str:
    p = paths.ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 50), color).save(p, "JPEG")
    return rel


def _seed_week() -> None:
    o = f"out/weeks/{WEEK}"
    upsert_asset(WEEK, RenderedAsset(asset_id="2026W42-S01", kind="short", duration_seconds=40.0, files={
        "video": _write(f"{o}/2026W42-S01/video.mp4", b"\x00\x00\x00\x18ftypmp42" + b"v" * 2000),
        "cover": _png(f"{o}/2026W42-S01/cover.png"),
        "subtitles": _write(f"{o}/2026W42-S01/subtitles.srt", "1\n00:00:00,000 --> 00:00:01,000\n안녕\n".encode()),
    }))
    upsert_asset(WEEK, RenderedAsset(asset_id="2026W42-C01", kind="cardnews", files={
        "slide01": _jpg(f"{o}/2026W42-C01/slide01.jpg"),
        "slide02": _jpg(f"{o}/2026W42-C01/slide02.jpg", (90, 90, 90)),
    }, meta={"alt_texts": ["표지", "본문"]}))
    upsert_asset(WEEK, RenderedAsset(asset_id="2026W42-B01", kind="blog", files={
        "og": _png(f"{o}/2026W42-B01/og.png"),
        "html": _write(f"{o}/2026W42-B01/post.html", b"<p>x</p>"),
    }))
    upsert_asset(WEEK, RenderedAsset(asset_id="2026W42-L1", kind="longform", files={
        "video": _write(f"{o}/2026W42-L1/video.mp4", b"L" * 5000),
    }))


def _ok_object(rsps, aid: str, name: str) -> None:
    rsps.add(responses.POST, f"{OBJ}/{aid}/{name}", json={"Key": f"media/{WEEK}/{aid}/{name}"}, status=200)


def _register_all(rsps) -> None:
    for aid, name in [("2026W42-S01", "video.mp4"), ("2026W42-S01", "cover.png"), ("2026W42-S01", "cover.jpg"),
                      ("2026W42-C01", "slide01.jpg"), ("2026W42-C01", "slide02.jpg"), ("2026W42-B01", "og.png")]:
        _ok_object(rsps, aid, name)


def test_upload_records_remote_urls_and_skips_longform(root, rsps):
    _seed_week()
    _register_all(rsps)
    rep = upload_media(WEEK, cfg=Config(), log=lambda s: None)
    assert not rep.failed
    assert len(rep.by_status("uploaded")) == 6
    urls = [c.request.url for c in rsps.calls]
    assert not any("2026W42-L1" in u for u in urls), "롱폼은 올리지 않는다"
    assert not any("subtitles" in u or "post.html" in u for u in urls)
    first = rsps.calls[0].request
    assert first.headers["x-upsert"] == "true"
    assert first.headers["apikey"] == JWT_KEY
    assert first.headers["Authorization"] == f"Bearer {JWT_KEY}"
    assert first.headers["cache-control"] == "max-age=300"
    m = load_manifest(WEEK)
    s = m.get("2026W42-S01")
    assert s.remote_urls == {
        "video": f"{PUB}/2026W42-S01/video.mp4",
        "cover": f"{PUB}/2026W42-S01/cover.png",
        "cover_jpg": f"{PUB}/2026W42-S01/cover.jpg",
    }
    video = paths.ROOT / s.files["video"]
    assert s.meta["storage"]["video"]["sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert m.get("2026W42-C01").remote_urls["slide02"] == f"{PUB}/2026W42-C01/slide02.jpg"
    assert m.get("2026W42-B01").remote_urls == {"og": f"{PUB}/2026W42-B01/og.png"}
    assert m.get("2026W42-L1").remote_urls == {}
    # PNG 표지의 JPEG 사본은 진짜 JPEG 이다
    jpg_call = next(c for c in rsps.calls if c.request.url.endswith("cover.jpg"))
    assert jpg_call.request.headers["Content-Type"] == "image/jpeg"
    assert jpg_call.request.body[:3] == b"\xff\xd8\xff"


def test_second_run_skips_unchanged_and_force_reuploads(root, rsps):
    _seed_week()
    _register_all(rsps)
    upload_media(WEEK, cfg=Config(), log=lambda s: None)
    n = len(rsps.calls)
    rep = upload_media(WEEK, cfg=Config(), log=lambda s: None)
    assert len(rsps.calls) == n
    assert {i.status for i in rep.items} == {"skipped"}
    # 파일이 바뀌면 그 파일만 다시 올린다
    s = load_manifest(WEEK).get("2026W42-S01")
    (paths.ROOT / s.files["video"]).write_bytes(b"new video bytes" * 100)
    rep = upload_media(WEEK, cfg=Config(), log=lambda s: None)
    assert [i.role for i in rep.by_status("uploaded")] == ["video"]
    rep = upload_media(WEEK, ["2026W42-C01"], force=True, cfg=Config(), log=lambda s: None)
    assert [i.role for i in rep.by_status("uploaded")] == ["slide01", "slide02"]


def test_large_file_uses_tus_and_resumes_after_failure(root, rsps):
    size = 13 * storage.MB
    rel = _write(f"out/weeks/{WEEK}/2026W42-S02/video.mp4", b"\x01" * size)
    upsert_asset(WEEK, RenderedAsset(asset_id="2026W42-S02", kind="short", files={"video": rel}))
    rsps.add(responses.POST, TUS, status=201, headers={"Location": "/storage/v1/upload/resumable/up1"})
    upload_url = "https://ref.storage.supabase.co/storage/v1/upload/resumable/up1"
    state = {"offset": 0, "patches": 0}

    def patch_cb(req):
        state["patches"] += 1
        if state["patches"] == 2:      # 두 번째 청크에서 서버 오류 → HEAD 로 위치 확인 후 이어서
            return (500, {}, "boom")
        assert int(req.headers["Upload-Offset"]) == state["offset"]
        assert req.headers["Content-Type"] == "application/offset+octet-stream"
        state["offset"] += len(req.body)
        return (204, {"Upload-Offset": str(state["offset"])}, "")

    rsps.add_callback(responses.PATCH, upload_url, callback=patch_cb)
    rsps.add_callback(responses.HEAD, upload_url, callback=lambda req: (200, {"Upload-Offset": str(state["offset"])}, ""))
    rep = upload_media(WEEK, cfg=Config(), log=lambda s: None)
    assert not rep.failed, [i.note for i in rep.items]
    assert state["offset"] == size
    assert state["patches"] == 4          # 6MB + (실패) + 6MB + 1MB
    create = rsps.calls[0].request
    assert create.headers["Upload-Length"] == str(size)
    meta = dict(kv.split(" ") for kv in create.headers["Upload-Metadata"].split(","))
    assert base64.b64decode(meta["bucketName"]).decode() == "media"
    assert base64.b64decode(meta["objectName"]).decode() == f"{WEEK}/2026W42-S02/video.mp4"
    assert base64.b64decode(meta["contentType"]).decode() == "video/mp4"
    assert create.headers["x-upsert"] == "true"
    assert load_manifest(WEEK).get("2026W42-S02").remote_urls["video"] == f"{PUB}/2026W42-S02/video.mp4"


def test_file_over_limit_fails_before_upload(root, rsps):
    _seed_week()
    cfg = Config.model_validate({"storage": {"bucket": "media", "max_file_mb": 0.001}})
    rep = upload_media(WEEK, ["2026W42-S01"], cfg=cfg, log=lambda s: None)
    video = next(i for i in rep.items if i.role == "video")
    assert video.status == "failed"
    assert "상한" in video.note and "max_file_mb" in video.note
    assert not any("video.mp4" in c.request.url for c in rsps.calls)


def test_bucket_missing_is_clear_error(root, rsps):
    _seed_week()
    rsps.add(responses.POST, f"{OBJ}/2026W42-B01/og.png", status=400,
             json={"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"})
    rep = upload_media(WEEK, ["2026W42-B01"], cfg=Config(), log=lambda s: None)
    assert rep.failed and "New bucket" in rep.failed[0].note
    assert len(rsps.calls) == 1, "다시 시도해도 소용없는 오류는 재시도하지 않는다"
    assert load_manifest(WEEK).get("2026W42-B01").remote_urls == {}


def test_server_error_is_retried(root, rsps):
    _seed_week()
    url = f"{OBJ}/2026W42-B01/og.png"
    rsps.add(responses.POST, url, status=503, json={"message": "unavailable"})
    rsps.add(responses.POST, url, status=200, json={"Key": "x"})
    rep = upload_media(WEEK, ["2026W42-B01"], cfg=Config(), log=lambda s: None)
    assert not rep.failed
    assert len(rsps.calls) == 2


def test_dry_run_sends_nothing_and_keeps_manifest(root, rsps, monkeypatch):
    _seed_week()
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    before = load_manifest(WEEK).model_dump()
    lines: list[str] = []
    rep = upload_media(WEEK, dry_run=True, cfg=Config(), log=lines.append)
    assert len(rsps.calls) == 0
    assert {i.status for i in rep.items} == {"dry-run"}
    assert any(f"{PUB}/2026W42-S01/video.mp4" in line for line in lines)
    assert load_manifest(WEEK).model_dump() == before


def test_new_style_secret_key_is_not_sent_as_bearer(root, rsps, monkeypatch):
    _seed_week()
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_abc123")
    _ok_object(rsps, "2026W42-B01", "og.png")
    upload_media(WEEK, ["2026W42-B01"], cfg=Config(), log=lambda s: None)
    h = rsps.calls[0].request.headers
    assert h["apikey"] == "sb_secret_abc123" and "Authorization" not in h


def test_missing_only_id_and_cli_exit_codes(root, rsps, capsys):
    _seed_week()
    _register_all(rsps)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    storage.register(sub)
    args = parser.parse_args(["publish", "upload-media", "--week", WEEK, "--only", "2026W42-C01"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "slide01" in out and "올림 2" in out
    args = parser.parse_args(["publish", "upload-media", "--week", WEEK, "--only", "2026W42-S99"])
    assert args.func(args) == 1
    assert "2026W42-S99" in capsys.readouterr().out


# ---------------------------------------------------------------- ensure_local

def _remote_asset(data: bytes, sha: str | None = None) -> RenderedAsset:
    meta = {"storage": {"video": {"sha256": sha or hashlib.sha256(data).hexdigest(), "size": len(data)}}}
    return RenderedAsset(
        asset_id="2026W42-S03", kind="short",
        files={"video": f"out/weeks/{WEEK}/2026W42-S03/video.mp4", "subtitles": f"out/weeks/{WEEK}/2026W42-S03/subtitles.srt"},
        remote_urls={"video": f"{PUB}/2026W42-S03/video.mp4"}, meta=meta,
    )


def test_ensure_local_downloads_missing_files(root, rsps):
    data = b"mp4-bytes" * 1000
    asset = _remote_asset(data)
    rsps.add(responses.GET, f"{PUB}/2026W42-S03/video.mp4", body=data, status=200)
    ensure_local(WEEK, asset)
    p = paths.ROOT / asset.files["video"]
    assert p.read_bytes() == data
    assert not (paths.ROOT / asset.files["subtitles"]).exists()   # 올리지 않은 파일은 그대로
    assert not list(p.parent.glob("*.part"))
    ensure_local(WEEK, asset)                                       # 이미 있으면 받지 않는다
    assert len(rsps.calls) == 1


def test_ensure_local_rejects_corrupt_download(root, rsps):
    asset = _remote_asset(b"real", sha="0" * 64)
    rsps.add(responses.GET, f"{PUB}/2026W42-S03/video.mp4", body=b"tampered", status=200)
    with pytest.raises(StorageError) as e:
        ensure_local(WEEK, asset)
    assert "sha256" in str(e.value)
    assert not (paths.ROOT / asset.files["video"]).exists()


def test_ensure_local_required_role_without_remote(root, rsps):
    asset = _remote_asset(b"x")
    asset.remote_urls = {}
    ensure_local(WEEK, asset)       # 역할을 지정하지 않으면 조용히 넘어간다
    with pytest.raises(StorageError) as e:
        ensure_local(WEEK, asset, roles=("video",))
    assert "upload-media" in str(e.value) and e.value.retryable is False


def test_ensure_local_missing_remote_object(root, rsps):
    asset = _remote_asset(b"x")
    rsps.add(responses.GET, f"{PUB}/2026W42-S03/video.mp4", status=404, json={"error": "not_found"})
    with pytest.raises(StorageError) as e:
        ensure_local(WEEK, asset, roles=("video",))
    assert e.value.retryable is False


def test_ensure_local_dry_run_skips_download(root, rsps, monkeypatch):
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    ensure_local(WEEK, _remote_asset(b"x"))
    assert len(rsps.calls) == 0


def test_object_name_rules():
    assert storage.object_path(WEEK, "2026W42-S01", "video.mp4") == f"{WEEK}/2026W42-S01/video.mp4"
    with pytest.raises(StorageError):
        storage.object_path(WEEK, "2026W42-S01", "영상.mp4")
    assert storage.upload_roles(RenderedAsset(asset_id="x-L1", kind="longform", files={"video": "a.mp4"})) == []


def test_provider_none_skips(root, rsps):
    rep = upload_media(WEEK, cfg=Config.model_validate({"storage": {"provider": "none"}}), log=lambda s: None)
    assert rep.disabled and not rep.items
    assert Path(paths.ROOT).exists()

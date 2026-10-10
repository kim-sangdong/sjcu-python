"""미디어 스토리지(Supabase Storage) — 공개 URL 업로드와 로컬 복원.

  python -m studio publish upload-media --week W [--only ID ...] [--dry-run] [--force]

- 인스타·페이스북·틱톡은 공개 URL(video_url·image_url·file_url)로 파일을 가져간다. 그래서 쇼츠 video·cover,
  카드뉴스 slideNN, 블로그 og 를 공개 버킷 cfg.storage.bucket 의 <week>/<asset_id>/<파일명> 에 올리고
  공개 URL 을 manifest 의 remote_urls[역할] 에 적는다. 롱폼 영상은 올리지 않는다(유튜브 전용, 용량 큼).
- 6MB 이하는 표준 업로드, 넘으면 TUS 재개 업로드(6MB 청크). 무료 플랜은 파일당 50MB 상한
  (channel.yaml storage.max_file_mb 로 조정) — 넘으면 업로드 전에 명확한 오류를 낸다.
- 같은 파일(sha256)을 이미 올렸으면 건너뛴다(--force 로 다시). 다시 렌더하면 manifest meta 가 새로 써져 다시 올라간다.
- PNG 표지(cover)는 JPEG 사본(cover.jpg → 역할 cover_jpg)도 올린다. 인스타 cover_url 이 JPEG 만 받을 수 있어서다 [설계 판단].
- ensure_local(week, asset): 매일 발행 작업이 새 체크아웃에서 돌 때 remote_urls 에서 파일을 내려받는다.

설정(channel.yaml storage 섹션, 모두 선택):
  max_file_mb: 50            # 파일 하나 상한(무료 50MB, Pro 는 더 크게)
  cache_control_seconds: 300 # CDN 캐시. 다시 올린 파일이 늦게 바뀌지 않도록 짧게
  tus_direct_host: true      # 큰 파일은 <ref>.storage.supabase.co 직접 호스트로 올린다(공식 권장)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests

from studio import paths
from studio.cliutil import group
from studio.config import Config, env, load_config
from studio.manifest import editing, load_manifest
from studio.models import RenderedAsset
from studio.publish.base import PublishError

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

MB = 1024 * 1024
OBJECT_PATH = "/storage/v1/object/{bucket}/{path}"          # 표준 업로드(POST + x-upsert)
PUBLIC_PATH = "/storage/v1/object/public/{bucket}/{path}"   # 공개 버킷 URL
TUS_PATH = "/storage/v1/upload/resumable"                   # TUS 재개 업로드
TUS_VERSION = "1.0.0"
TUS_CHUNK = 6 * MB             # Supabase 문서: "must be set to 6MB (for now)"
RESUMABLE_OVER = 6 * MB        # 이보다 크면 TUS (문서: 6MB 초과 권장)
DEFAULT_MAX_FILE_MB = 50       # 무료 플랜 파일당 상한
DEFAULT_CACHE_SECONDS = 300
HTTP_TIMEOUT = (10, 300)
RETRIES = 3
SUPABASE_HOST_RE = re.compile(r"^https://([a-z0-9]+)\.supabase\.co/?$")
# Supabase 객체 이름에 쓸 수 있는 글자(문서: storage/uploads/file-limits)
OBJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.',!*&$@=;:+?() ]+$")

CONTENT_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}
SLIDE_RE = re.compile(r"^slide\d+$")
DERIVED_JPEG = {"cover": "cover_jpg"}      # PNG 원본 역할 → JPEG 사본 역할

_sleep = time.sleep   # 테스트에서 바꿔 끼운다


class StorageError(PublishError):
    """업로드·내려받기 실패. 발행기와 같은 규칙(retryable)으로 다룬다."""


def env_dry_run() -> bool:
    return os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- 무엇을 올리나

@dataclass
class UploadItem:
    asset_id: str
    role: str                  # remote_urls 에 적을 역할
    source_role: str           # files 의 역할(파생 사본이면 원본 역할)
    local: Path
    object_path: str
    content_type: str
    transform: str | None = None   # "jpeg" = PNG → JPEG 사본
    status: str = "pending"        # uploaded | skipped | failed | dry-run
    url: str = ""
    note: str = ""
    sha256: str = ""
    size: int = 0


@dataclass
class UploadReport:
    week: str
    items: list[UploadItem] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    disabled: bool = False

    def by_status(self, status: str) -> list[UploadItem]:
        return [i for i in self.items if i.status == status]

    @property
    def failed(self) -> list[UploadItem]:
        return self.by_status("failed")


def upload_roles(asset: RenderedAsset) -> list[tuple[str, str, str | None]]:
    """(역할, 원본 역할, 변환) 목록. 롱폼은 올리지 않는다."""
    if asset.kind == "short":
        roles = [r for r in ("video", "cover") if r in asset.files]
    elif asset.kind == "cardnews":
        roles = sorted(r for r in asset.files if SLIDE_RE.match(r))
    elif asset.kind == "blog":
        roles = [r for r in ("og",) if r in asset.files]
    else:
        return []
    out: list[tuple[str, str, str | None]] = [(r, r, None) for r in roles]
    for src, derived in DERIVED_JPEG.items():
        if src in roles and Path(asset.files[src]).suffix.lower() == ".png":
            out.append((derived, src, "jpeg"))
    return out


def object_path(week: str, asset_id: str, filename: str) -> str:
    if not OBJECT_NAME_RE.match(filename) or not OBJECT_NAME_RE.match(asset_id):
        raise StorageError(
            f"스토리지 객체 이름에 쓸 수 없는 글자가 있습니다: {asset_id}/{filename} — 영문·숫자·_-. 만 쓰도록 렌더 파일 이름을 바꾸세요.",
            retryable=False,
        )
    return f"{week}/{asset_id}/{filename}"


def content_type_of(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _settings(cfg: Config) -> dict[str, Any]:
    extra = cfg.storage.model_extra or {}
    return {
        "max_bytes": int(float(extra.get("max_file_mb", DEFAULT_MAX_FILE_MB)) * MB),
        "cache_seconds": int(extra.get("cache_control_seconds", DEFAULT_CACHE_SECONDS)),
        "direct_host": bool(extra.get("tus_direct_host", True)),
    }


def public_url(cfg: Config, path: str, base_url: str | None = None) -> str:
    """<SUPABASE_URL>/storage/v1/object/public/<bucket>/<path>. URL 이 없으면(리허설) 자리표시."""
    base = (base_url or os.environ.get("SUPABASE_URL") or "https://<SUPABASE_URL>").rstrip("/")
    return base + PUBLIC_PATH.format(bucket=cfg.storage.bucket, path=quote(path, safe="/"))


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _jpeg_bytes(src: Path, quality: int = 92) -> bytes:
    from PIL import Image

    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            out = bg
        else:
            out = im.convert("RGB")
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=quality, optimize=True)
        return buf.getvalue()


# ---------------------------------------------------------------- Supabase Storage REST

def _error(r: requests.Response, what: str, bucket: str) -> StorageError:
    try:
        body = r.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        code = int(body.get("statusCode") or r.status_code)
    except (TypeError, ValueError):
        code = r.status_code
    msg = str(body.get("message") or body.get("error") or r.text or "")[:200]
    low = msg.lower()
    if "bucket not found" in low:
        return StorageError(f"{what}: 버킷 '{bucket}' 이 없습니다. Supabase 대시보드 → Storage → New bucket 에서 "
                            f"이름 '{bucket}', Public 으로 만드세요.", retryable=False)
    if code == 413 or "maximum allowed size" in low or "too large" in low:
        return StorageError(f"{what}: 파일이 버킷·프로젝트 크기 상한을 넘습니다({msg}). 무료 플랜은 50MB — "
                            "영상 비트레이트를 낮추거나 Pro 플랜에서 Storage 설정의 상한과 storage.max_file_mb 를 올리세요.",
                            retryable=False)
    if r.status_code in (401, 403) or code in (401, 403):
        return StorageError(f"{what}: 인증 실패({msg}). SUPABASE_SERVICE_ROLE_KEY 에 service_role(또는 secret) 키를 넣었는지 확인하세요.",
                            retryable=False)
    if "mime type" in low:
        return StorageError(f"{what}: 버킷이 이 파일 형식을 허용하지 않습니다({msg}). 버킷의 Allowed MIME types 를 비우거나 추가하세요.",
                            retryable=False)
    retryable = r.status_code in (408, 409, 423, 429) or r.status_code >= 500
    return StorageError(f"{what}: HTTP {r.status_code} {msg}", retryable=retryable)


@dataclass
class SupabaseStorage:
    url: str
    key: str
    bucket: str
    cache_seconds: int = DEFAULT_CACHE_SECONDS
    direct_host: bool = True

    @classmethod
    def from_env(cls, cfg: Config) -> SupabaseStorage:
        s = _settings(cfg)
        return cls(
            url=str(env("SUPABASE_URL")).rstrip("/"),
            key=str(env("SUPABASE_SERVICE_ROLE_KEY")),
            bucket=cfg.storage.bucket,
            cache_seconds=s["cache_seconds"],
            direct_host=s["direct_host"],
        )

    def headers(self) -> dict[str, str]:
        h = {"apikey": self.key}
        if self.key.startswith("eyJ"):        # 옛 JWT 형식 키만 Bearer 로 보낸다(sb_secret_ 키는 JWT 가 아님)
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def public_url(self, path: str) -> str:
        return public_url_for(self.url, self.bucket, path)

    def tus_endpoint(self) -> str:
        m = SUPABASE_HOST_RE.match(self.url)
        base = f"https://{m.group(1)}.storage.supabase.co" if (m and self.direct_host) else self.url
        return base + TUS_PATH

    def upload(self, local: Path, path: str, content_type: str) -> None:
        size = local.stat().st_size
        if size > RESUMABLE_OVER:
            self._tus(lambda off, n: _read_at(local, off, n), size, path, content_type)
        else:
            self.upload_bytes(local.read_bytes(), path, content_type)

    def upload_bytes(self, data: bytes, path: str, content_type: str) -> None:
        if len(data) > RESUMABLE_OVER:
            self._tus(lambda off, n: data[off:off + n], len(data), path, content_type)
            return
        url = self.url + OBJECT_PATH.format(bucket=self.bucket, path=quote(path, safe="/"))
        hdr = {**self.headers(), "Content-Type": content_type, "x-upsert": "true",
               "cache-control": f"max-age={self.cache_seconds}"}
        self._send("POST", url, f"업로드 {path}", headers=hdr, data=data, ok=(200, 201))

    def _send(self, method: str, url: str, what: str, *, ok: tuple[int, ...], **kw: Any) -> requests.Response:
        last: StorageError | None = None
        for i in range(RETRIES):
            if i:
                _sleep(min(2 ** i, 20))
            try:
                r = requests.request(method, url, timeout=HTTP_TIMEOUT, **kw)
            except requests.RequestException as e:
                last = StorageError(f"{what}: 네트워크 오류 {type(e).__name__}", retryable=True)
                continue
            if r.status_code in ok:
                return r
            last = _error(r, what, self.bucket)
            if not last.retryable:
                break
        assert last is not None
        raise last

    def _tus(self, read: Callable[[int, int], bytes], size: int, path: str, content_type: str) -> None:
        endpoint = self.tus_endpoint()
        meta = {"bucketName": self.bucket, "objectName": path, "contentType": content_type,
                "cacheControl": str(self.cache_seconds)}
        hdr = {**self.headers(), "Tus-Resumable": TUS_VERSION, "Upload-Length": str(size),
               "Upload-Metadata": ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in meta.items()),
               "x-upsert": "true"}
        r = self._send("POST", endpoint, f"재개 업로드 시작 {path}", headers=hdr, ok=(201,))
        loc = r.headers.get("Location")
        if not loc:
            raise StorageError(f"재개 업로드 시작 {path}: 응답에 Location 이 없습니다.", retryable=True)
        loc = urljoin(endpoint, loc)
        base_hdr = {**self.headers(), "Tus-Resumable": TUS_VERSION}
        offset, failures = 0, 0
        while offset < size:
            chunk = read(offset, TUS_CHUNK)
            err: StorageError | None = None
            try:
                r = requests.patch(loc, data=chunk, timeout=HTTP_TIMEOUT, headers={
                    **base_hdr, "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"})
                if r.status_code == 204:
                    offset = int(r.headers.get("Upload-Offset") or offset + len(chunk))
                    failures = 0
                    continue
                err = _error(r, f"재개 업로드 {path} ({offset}/{size}바이트)", self.bucket)
                if r.status_code == 409:      # 오프셋 불일치 → 서버 기준으로 다시 맞춘다
                    err.retryable = True
            except requests.RequestException as e:
                err = StorageError(f"재개 업로드 {path}: 네트워크 오류 {type(e).__name__}", retryable=True)
            failures += 1
            if not err.retryable or failures > RETRIES:
                raise err
            _sleep(min(2 ** failures, 20))
            h = self._send("HEAD", loc, f"재개 업로드 위치 확인 {path}", headers=base_hdr, ok=(200, 204))
            offset = int(h.headers.get("Upload-Offset") or offset)


def public_url_for(base_url: str, bucket: str, path: str) -> str:
    return base_url.rstrip("/") + PUBLIC_PATH.format(bucket=bucket, path=quote(path, safe="/"))


def _read_at(p: Path, offset: int, n: int) -> bytes:
    with open(p, "rb") as fh:
        fh.seek(offset)
        return fh.read(n)


# ---------------------------------------------------------------- upload_media

def plan_uploads(week: str, asset: RenderedAsset) -> list[UploadItem]:
    items = []
    for role, src, transform in upload_roles(asset):
        rel = asset.files[src]
        name = Path(rel).name if transform is None else Path(rel).stem + ".jpg"
        items.append(UploadItem(
            asset_id=asset.asset_id, role=role, source_role=src, local=paths.ROOT / rel,
            object_path=object_path(week, asset.asset_id, name), content_type=content_type_of(name), transform=transform,
        ))
    return items


def upload_media(
    week: str,
    asset_ids: Iterable[str] | None = None,
    dry_run: bool = False,
    *,
    force: bool = False,
    cfg: Config | None = None,
    log: Callable[[str], None] = print,
) -> UploadReport:
    """manifest 의 쇼츠·카드뉴스·블로그 파일을 올리고 remote_urls 를 기록한다. 실패는 보고서에 담는다."""
    cfg = cfg or load_config()
    dry = dry_run or env_dry_run()
    report = UploadReport(week)
    if cfg.storage.provider == "none":
        log("storage.provider 가 none 입니다 — 업로드를 건너뜁니다(인스타·페북·틱톡 발행에는 공개 URL 이 필요합니다).")
        report.disabled = True
        return report
    want = list(dict.fromkeys(asset_ids or []))
    manifest = load_manifest(week)
    assets = [a for a in manifest.assets if not want or a.asset_id in want]
    report.missing_ids = [i for i in want if manifest.get(i) is None]
    s = _settings(cfg)
    client = None if dry else SupabaseStorage.from_env(cfg)
    for asset in assets:
        try:
            items = plan_uploads(week, asset)
        except StorageError as e:
            report.items.append(UploadItem(asset.asset_id, "*", "*", Path(), "", "", status="failed", note=str(e)))
            continue
        prev = asset.meta.get("storage") if isinstance(asset.meta.get("storage"), dict) else {}
        done: dict[str, dict[str, Any]] = {}
        for it in items:
            report.items.append(it)
            if not it.local.exists():
                if asset.remote_urls.get(it.role):
                    it.status, it.url, it.note = "skipped", asset.remote_urls[it.role], "로컬 파일 없음 — 이미 올린 URL 유지"
                else:
                    it.status, it.note = "failed", f"파일이 없습니다({it.local}) — 렌더를 다시 하세요."
                continue
            it.size = it.local.stat().st_size
            if it.size > s["max_bytes"]:
                it.status = "failed"
                it.note = (f"{it.size / MB:.1f}MB 로 파일당 상한 {s['max_bytes'] / MB:.0f}MB 를 넘습니다. 영상 비트레이트를 낮추거나 "
                           "Supabase Pro 에서 상한을 올린 뒤 channel.yaml storage.max_file_mb 를 바꾸세요.")
                continue
            it.sha256 = _sha256(it.local)
            old = prev.get(it.role) if isinstance(prev.get(it.role), dict) else {}
            if not force and old.get("sha256") == it.sha256 and asset.remote_urls.get(it.role):
                it.status, it.url, it.note = "skipped", asset.remote_urls[it.role], "변경 없음"
                continue
            it.url = public_url(cfg, it.object_path) if dry else client.public_url(it.object_path)  # type: ignore[union-attr]
            if dry:
                it.status, it.note = "dry-run", f"{it.content_type} {it.size / MB:.2f}MB → {it.object_path}"
                log(f"[dry-run] 업로드 {it.asset_id} {it.role}: {it.local.name} → {it.url}")
                continue
            try:
                if it.transform == "jpeg":
                    data = _jpeg_bytes(it.local)
                    client.upload_bytes(data, it.object_path, it.content_type)  # type: ignore[union-attr]
                    it.size = len(data)
                else:
                    client.upload(it.local, it.object_path, it.content_type)  # type: ignore[union-attr]
            except StorageError as e:
                it.status, it.note = "failed", str(e)
                continue
            except OSError as e:   # 이미지 변환 실패 등
                it.status, it.note = "failed", f"{type(e).__name__}: {e}"
                continue
            it.status = "uploaded"
            done[it.role] = {
                "url": it.url, "path": it.object_path, "sha256": it.sha256, "size": it.size,
                "content_type": it.content_type, "source": asset.files[it.source_role],
                "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        if done:
            _record(week, asset.asset_id, done)
    return report


def _record(week: str, asset_id: str, done: dict[str, dict[str, Any]]) -> None:
    """업로드한 역할만 잠금 상태에서 기록한다(그 사이 다시 렌더되어 파일이 바뀌었으면 기록하지 않는다)."""
    with editing(week) as m:
        a = m.get(asset_id)
        if a is None:
            return
        meta = a.meta.get("storage") if isinstance(a.meta.get("storage"), dict) else {}
        for role, info in done.items():
            info = dict(info)
            src = info.pop("source")
            if src not in a.files.values():
                continue
            a.remote_urls[role] = info.pop("url")
            meta[role] = info
        a.meta["storage"] = meta


# ---------------------------------------------------------------- ensure_local

def ensure_local(week: str, asset: RenderedAsset, *, roles: Iterable[str] | None = None) -> None:
    """asset.files 중 로컬에 없는 파일을 remote_urls 에서 내려받는다.

    roles 를 주면 그 역할만, 그리고 그 역할을 받을 수 없으면 StorageError 를 낸다.
    roles 가 없으면 받을 수 있는 것만 받는다(자막·타임라인처럼 올리지 않는 파일은 그대로 둔다).
    """
    want = list(roles) if roles is not None else None
    stored = asset.meta.get("storage") if isinstance(asset.meta.get("storage"), dict) else {}
    for role, rel in asset.files.items():
        if want is not None and role not in want:
            continue
        dest = (paths.ROOT / rel).resolve()
        if not dest.is_relative_to(paths.ROOT.resolve()):
            raise StorageError(f"{asset.asset_id}: 저장 경로가 저장소 밖입니다({rel}).", retryable=False)
        if dest.exists():
            continue
        url = asset.remote_urls.get(role)
        if not url:
            if want is not None:
                raise StorageError(
                    f"{asset.asset_id}: '{role}' 파일이 로컬에도 스토리지에도 없습니다. 렌더한 컴퓨터에서 "
                    f"`python -m studio publish upload-media --week {week} --only {asset.asset_id}` 를 실행하세요.",
                    retryable=False,
                )
            continue
        if env_dry_run():
            print(f"[dry-run] 내려받기 생략 {asset.asset_id} {role}: {url}")
            continue
        info = stored.get(role) if isinstance(stored.get(role), dict) else {}
        _download(url, dest, sha256=info.get("sha256"), what=f"{asset.asset_id} {role} 내려받기")


def _download(url: str, dest: Path, *, sha256: str | None, what: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: StorageError | None = None
    for i in range(RETRIES):
        if i:
            _sleep(min(2 ** i, 20))
        try:
            with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as r:
                if r.status_code in (400, 404):
                    raise StorageError(f"{what}: 스토리지에 파일이 없습니다(HTTP {r.status_code}). 렌더한 컴퓨터에서 "
                                       "`publish upload-media` 를 다시 실행하세요.", retryable=False)
                if r.status_code != 200:
                    last = StorageError(f"{what}: HTTP {r.status_code}", retryable=r.status_code >= 500 or r.status_code == 429)
                    if not last.retryable:
                        break
                    continue
                h = hashlib.sha256()
                with open(part, "wb") as fh:
                    for block in r.iter_content(1024 * 1024):
                        fh.write(block)
                        h.update(block)
        except requests.RequestException as e:
            last = StorageError(f"{what}: 네트워크 오류 {type(e).__name__}", retryable=True)
            continue
        if sha256 and h.hexdigest() != sha256:
            part.unlink(missing_ok=True)
            last = StorageError(f"{what}: 받은 파일의 sha256 이 업로드 기록과 다릅니다(손상 또는 교체됨).", retryable=True)
            continue
        part.replace(dest)
        return
    part.unlink(missing_ok=True)
    assert last is not None
    raise last


# ---------------------------------------------------------------- CLI

def cmd_upload_media(args: argparse.Namespace) -> int:
    week = args.week or paths.publishing_week()
    report = upload_media(week, args.only, dry_run=args.dry_run, force=args.force)
    if report.disabled:
        return 0
    for i in report.missing_ids:
        print(f"[오류] {i}: manifest 에 렌더 결과가 없습니다(먼저 render).")
    if not report.items and not report.missing_ids:
        print(f"{week}: 올릴 렌더 결과가 없습니다(쇼츠·카드뉴스·블로그). render 를 먼저 실행했는지 확인하세요.")
        return 0
    mark = {"uploaded": "올림", "skipped": "건너뜀", "failed": "실패", "dry-run": "리허설"}
    for it in report.items:
        tail = it.note if it.status in ("failed", "skipped") else it.url
        print(f"[{mark.get(it.status, it.status)}] {it.asset_id:<14} {it.role:<10} {tail}")
    counts = {k: len(report.by_status(k)) for k in mark}
    print(f"{week}: 올림 {counts['uploaded']} · 건너뜀 {counts['skipped']} · 실패 {counts['failed']}"
          + (f" · 리허설 {counts['dry-run']}" if counts["dry-run"] else ""))
    return 1 if report.failed or report.missing_ids else 0


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "publish", "발행 대기열·업로드")
    p = g.add_parser("upload-media", help="쇼츠·카드뉴스·블로그 이미지를 Supabase Storage 에 올리고 공개 URL 을 manifest 에 기록")
    p.add_argument("--week", help="주차(예: 2026-W42). 없으면 이번 정기 실행 주차")
    p.add_argument("--only", action="append", metavar="ID", help="이 콘텐츠만(여러 번 지정 가능)")
    p.add_argument("--dry-run", action="store_true", help="올리지 않고 계획만 출력")
    p.add_argument("--force", action="store_true", help="변경 없는 파일도 다시 올림")
    p.set_defaults(func=cmd_upload_media)

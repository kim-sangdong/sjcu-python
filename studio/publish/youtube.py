"""YouTube 발행기 (롱폼·쇼츠).

두 가지 모드 — channel.yaml `platforms.youtube.audited` 로 고른다.
- audited: true  → videos.insert 재개 가능 업로드(8MB 청크, 5xx·네트워크 오류 지수 백오프)로 비공개 예약(publishAt).
                   롱폼은 썸네일(thumbnails.set)·자막(captions.insert, 400 units)도 올리고, 설정이 있으면 재생목록에 넣는다.
- audited: false → 2020-07-28 이후 만든 미감사 Cloud 프로젝트의 API 업로드는 비공개로 잠긴다.
                   그래서 out/weeks/<week>/youtube-package/<id>/ 에 업로드 패키지(영상·썸네일·자막·metadata.json·업로드안내.md)를
                   만들고 사람이 YouTube Studio 에서 예약 업로드한다(결과 status=skipped).

알아 둘 제약
- publishAt 은 privacyStatus=private 일 때만 쓸 수 있다. 예약 시각이 이미 지났으면 바로 public 으로 올린다.
- 쇼츠 플래그는 API 에 없다. 세로(또는 정사각형)이고 3분 이하이면 YouTube 가 쇼츠로 분류한다.
- 고정 댓글 API 는 없다 → pinned_comment 는 사람이 Studio 에서 달고 고정한다(결과 extra.manual 에 남긴다).
- 영상이 올라간 뒤의 부가 단계(썸네일·자막·재생목록) 실패는 PublishError 로 올리지 않는다.
  재시도가 영상을 중복 업로드하기 때문이다. 경고(note·extra.warnings)로 남기고 사람이 처리한다.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from studio import paths
from studio.config import MissingSecret
from studio.models import VideoScript, write_json
from studio.publish import captions, google_auth
from studio.publish.base import PublishContext, PublishError, PublishResult

# ---------------------------------------------------------------- 상수 (API 변경 시 여기만 고친다)

VIDEO_PARTS = "snippet,status"
VIDEO_MIME = "video/mp4"
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024        # 256KB 의 배수여야 한다
UPLOAD_MAX_RETRIES = 8                      # 연속 실패 허용 횟수(진척이 있으면 다시 센다)
BACKOFF_MAX_SECONDS = 64
EXECUTE_RETRIES = 3                         # googleapiclient 내장 재시도(5xx·429·네트워크)
RETRY_HTTP_STATUS = {429, 500, 502, 503, 504}
PUBLISH_AT_MIN_LEAD = timedelta(minutes=10) # 이보다 가까운 예약은 바로 공개로 올린다(업로드 시간 여유)
SCHEDULE_GRACE = timedelta(minutes=30)      # 예약 시각 직후 공개 전환 지연 허용
SHORTS_MAX_SECONDS = 180                    # 이보다 길면 쇼츠로 분류되지 않는다
SHORTS_SAFE_SECONDS = 60                    # 1분 넘는 쇼츠는 Content ID 주장 시 전 세계 차단 위험
TAGS_MAX_CHARS = 500                        # [미확인] 공백 있는 태그는 따옴표 2자 포함, 쉼표 포함
DESCRIPTION_MAX_BYTES = 4900                # [미확인] 설명 5000바이트 제한에 여유
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024       # [미확인] 공식 페이지 기준 2MB (명세 maxSize 는 50MB)
CAPTION_LANGUAGE = "ko"
CAPTION_NAME = "한국어"
CAPTION_MIME = "application/octet-stream"
CATEGORY_NAMES = {"27": "교육", "22": "인물/블로그", "24": "엔터테인먼트", "26": "노하우/스타일", "28": "과학기술"}

WATCH_URL = "https://youtu.be/{id}"
SHORTS_URL = "https://youtube.com/shorts/{id}"
STUDIO_EDIT_URL = "https://studio.youtube.com/video/{id}/edit"
DRYRUN_PREFIX = "dryrun-"
PACKAGE_DIR = "youtube-package"
PACKAGE_GUIDE = "업로드안내.md"
PACKAGE_INDEX = "업로드목록.md"

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded", "RESOURCE_EXHAUSTED"}
AUTH_REASONS = {"authError", "unauthorized", "invalidCredentials", "UNAUTHENTICATED"}
UPLOAD_LIMIT_REASONS = {"uploadLimitExceeded"}
NO_CHANNEL_REASONS = {"youtubeSignupRequired", "channelNotFound"}

_sleep: Callable[[float], None] = time.sleep   # 테스트에서 바꾼다


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_dry(ctx: PublishContext) -> bool:
    return bool(ctx.dry_run) or os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- 본문 만들기 (순수 함수)

def to_utc(at: datetime) -> datetime:
    """시간대 없는 값은 KST 로 본다."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=paths.KST)
    return at.astimezone(timezone.utc).replace(microsecond=0)


def iso_z(at: datetime) -> str:
    return to_utc(at).strftime("%Y-%m-%dT%H:%M:%SZ")


def kst_label(at: datetime) -> str:
    k = to_utc(at).astimezone(paths.KST)
    return f"{k:%Y-%m-%d}({'월화수목금토일'[k.weekday()]}) {k:%H:%M} KST"


def clean_text(text: str) -> str:
    """YouTube 는 제목·설명의 '<' '>' 를 거부한다(invalidTitle/invalidDescription)."""
    return text.replace("<", "‹").replace(">", "›").strip()


def fit_bytes(text: str, limit: int = DESCRIPTION_MAX_BYTES) -> str:
    """설명 5000바이트 제한. 글자 단위로 잘라 UTF-8 이 깨지지 않게 한다."""
    while len(text.encode("utf-8")) > limit:
        text = text[: max(0, len(text) - 20)]
    return text


def fit_tags(tags: list[str], limit: int = TAGS_MAX_CHARS) -> list[str]:
    """중복·빈 값 제거 후 YouTube 셈법(공백 있으면 따옴표 2자, 쉼표 1자)으로 limit 이하가 되게 뒤에서 자른다."""
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        t = clean_text(t.replace(",", " ").lstrip("#"))
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)

    def cost(ts: list[str]) -> int:
        return sum(len(t) + (2 if " " in t else 0) for t in ts) + max(0, len(ts) - 1)

    while out and cost(out) > limit:
        out.pop()
    return out


def video_body(ctx: PublishContext, script: VideoScript, now: datetime | None = None) -> dict[str, Any]:
    """videos.insert 요청 본문(snippet·status)."""
    ycfg = ctx.cfg.platforms.youtube
    lang = getattr(ycfg, "default_language", None) or ctx.cfg.channel.language or "ko"
    status: dict[str, Any] = {
        "privacyStatus": "public",
        "selfDeclaredMadeForKids": bool(script.made_for_kids),
        # 합성 콘텐츠 공개 의무는 실존 인물·실제 사건을 사실처럼 꾸민 경우에 해당한다.
        # 일반 TTS 내레이션·도표 화면은 대상이 아니라는 조사 결과(docs/research/facts-google.md §1-7, §7)에 따라 기본 False.
        # 사실적인 합성 장면을 쓰면 channel.yaml platforms.youtube.contains_synthetic_media: true 로 바꾼다.
        "containsSyntheticMedia": bool(getattr(ycfg, "contains_synthetic_media", False)),
        "license": "youtube",
        "embeddable": True,
    }
    at = to_utc(ctx.target.publish_at)
    if at > (now or _now()) + PUBLISH_AT_MIN_LEAD:
        status["privacyStatus"] = "private"     # publishAt 은 private 일 때만 허용된다
        status["publishAt"] = iso_z(at)
    title = clean_text(script.title)
    if not title:
        raise PublishError(f"{script.id}: 제목이 비어 있습니다. 원고의 title 을 채우세요.", retryable=False)
    return {
        "snippet": {
            "title": title,
            "description": fit_bytes(clean_text(captions.youtube_description(script, ctx.cfg, ctx.asset))),
            "tags": fit_tags(script.tags),
            "categoryId": str(ycfg.category_id),
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": status,
    }


def playlist_id(ctx: PublishContext, script: VideoScript) -> str | None:
    ycfg = ctx.cfg.platforms.youtube
    return (ycfg.playlist_longform if script.format == "longform" else ycfg.playlist_shorts) or None


def video_url(video_id: str, fmt: str) -> str:
    return (SHORTS_URL if fmt == "short" else WATCH_URL).format(id=video_id)


def _script(ctx: PublishContext) -> VideoScript:
    if not isinstance(ctx.content, VideoScript):
        raise PublishError(f"{ctx.asset.asset_id}: YouTube 는 영상 원고(VideoScript)만 올릴 수 있습니다.", retryable=False)
    return ctx.content


def _preflight(ctx: PublishContext, script: VideoScript) -> list[str]:
    """올리기 전에 막을 것은 막고(예외), 사람이 알아야 할 것은 경고로 돌려준다."""
    warnings: list[str] = []
    dur = ctx.asset.duration_seconds
    if script.format == "short":
        if dur and dur > SHORTS_MAX_SECONDS:
            raise PublishError(
                f"{script.id}: 쇼츠 길이 {dur:.0f}초 > {SHORTS_MAX_SECONDS}초 — 쇼츠로 분류되지 않습니다. 원고를 줄여 다시 렌더하세요.",
                retryable=False,
            )
        if dur and dur > SHORTS_SAFE_SECONDS:
            warnings.append(f"쇼츠 길이 {dur:.0f}초 — 1분이 넘으면 Content ID 주장 시 전 세계 차단 위험이 있습니다.")
        size = ctx.asset.meta.get("size")
        if isinstance(size, (list, tuple)) and len(size) == 2 and size[0] > size[1]:
            warnings.append(f"쇼츠 화면이 가로({size[0]}x{size[1]})입니다 — 쇼츠로 분류되지 않습니다.")
    elif "thumbnail" not in ctx.asset.files:
        warnings.append("롱폼 썸네일(thumbnail)이 렌더 결과에 없습니다 — 자동 생성 썸네일로 공개됩니다.")
    return warnings


# ---------------------------------------------------------------- 오류 해석

def to_publish_error(e: Exception, action: str) -> PublishError:
    """HttpError → 사람이 할 일을 담은 PublishError. 할당량은 재시도, 권한·한도·형식은 사람 확인."""
    info = google_auth.api_error(e)
    s, r = info.status, info.reason
    detail = f"YouTube {action} 실패: HTTP {s} {r} — {info.message}".rstrip(" —")
    if r in QUOTA_REASONS or s == 429:
        return PublishError(
            f"{detail}. API 할당량 초과입니다 — 태평양 시간 자정(한국 16~17시)에 초기화되며 다음 실행에서 자동 재시도합니다.",
            retryable=True,
        )
    if r in UPLOAD_LIMIT_REASONS:
        return PublishError(
            f"{detail}. 채널의 업로드 한도에 걸렸습니다. YouTube Studio 에서 채널 인증·제한 상태를 확인하고, "
            "하루 뒤 `python -m studio publish retry` 로 다시 올리세요.",
            retryable=False,
        )
    if s == 401 or r in AUTH_REASONS:
        return PublishError(f"{detail}. 인증이 거부됐습니다. {google_auth.REAUTH_HINT}", retryable=False)
    if r in NO_CHANNEL_REASONS:
        return PublishError(f"{detail}. 이 Google 계정에 유튜브 채널이 없습니다. 채널 소유 계정으로 다시 인증하세요.", retryable=False)
    if s == 403:
        return PublishError(
            f"{detail}. 권한이 없습니다 — 토큰 범위(youtube.upload·youtube.force-ssl)와 채널 소유 계정을 확인하고 "
            f"필요하면 {google_auth.REAUTH_HINT}",
            retryable=False,
        )
    if s == 0 or s >= 500:
        return PublishError(f"{detail}. YouTube 서버 오류입니다 — 다음 실행에서 재시도합니다.", retryable=True)
    if s == 404:
        return PublishError(f"{detail}. 대상(영상·재생목록)을 찾지 못했습니다. ID 와 채널을 확인하세요.", retryable=False)
    return PublishError(
        f"{detail}. 요청 내용(제목·설명·태그·예약 시각·카테고리 ID)을 확인하고 원고나 channel.yaml 을 고치세요.",
        retryable=False,
    )


def transient_errors() -> tuple[type[BaseException], ...]:
    import httplib2
    from google.auth.exceptions import TransportError

    return (httplib2.HttpLib2Error, http.client.HTTPException, OSError, TransportError)


def _backoff(attempt: int) -> float:
    delay = min(2**attempt, BACKOFF_MAX_SECONDS) + random.random()
    _sleep(delay)
    return delay


def _service():
    from google.auth.exceptions import RefreshError

    try:
        return google_auth.youtube_service()
    except google_auth.GoogleAuthError as e:
        raise PublishError(str(e), retryable=False) from None
    except MissingSecret as e:
        raise PublishError(f"[설정 필요] {e}", retryable=False) from None
    except RefreshError as e:  # retryable 한 갱신 오류(서버 일시 장애)
        raise PublishError(f"Google 토큰 갱신 일시 실패: {str(e)[:200]} — 다음 실행에서 재시도합니다.", retryable=True) from None
    except transient_errors() as e:
        raise PublishError(f"Google 인증 서버 연결 실패({type(e).__name__}) — 다음 실행에서 재시도합니다.", retryable=True) from None


def run_resumable(request, ctx: PublishContext, label: str) -> dict[str, Any]:
    """재개 가능 업로드를 끝까지 보낸다. 5xx·429·네트워크 오류는 지수 백오프 후 같은 세션으로 이어 보낸다."""
    from googleapiclient.errors import HttpError

    transient = transient_errors()
    response = None
    failures = 0
    logged = -1
    while response is None:
        try:
            progress, response = request.next_chunk()
        except HttpError as e:
            info = google_auth.api_error(e)
            if info.status in RETRY_HTTP_STATUS and info.reason not in QUOTA_REASONS:
                failures += 1
                if failures > UPLOAD_MAX_RETRIES:
                    raise PublishError(
                        f"{label}: 업로드가 HTTP {info.status} 로 {UPLOAD_MAX_RETRIES}회 연속 실패했습니다 — 다음 실행에서 재시도합니다.",
                        retryable=True,
                    ) from None
                wait = _backoff(failures)
                ctx.log.append(f"youtube {label}: HTTP {info.status} → {wait:.0f}초 뒤 이어 올리기({failures}/{UPLOAD_MAX_RETRIES})")
                continue
            raise to_publish_error(e, "videos.insert") from None
        except transient as e:
            failures += 1
            if failures > UPLOAD_MAX_RETRIES:
                raise PublishError(
                    f"{label}: 네트워크 오류({type(e).__name__})가 {UPLOAD_MAX_RETRIES}회 연속 났습니다 — 다음 실행에서 재시도합니다.",
                    retryable=True,
                ) from None
            wait = _backoff(failures)
            ctx.log.append(f"youtube {label}: {type(e).__name__} → {wait:.0f}초 뒤 이어 올리기({failures}/{UPLOAD_MAX_RETRIES})")
            continue
        failures = 0
        if progress is not None:
            step = int(progress.progress() * 4)   # 25% 단위로만 기록
            if step > logged:
                logged = step
                ctx.log.append(f"youtube {label}: 업로드 {step * 25}%")
    return response


# ---------------------------------------------------------------- 부가 단계 (영상이 올라간 뒤)

def _upload_thumbnail_path(src: Path) -> Path:
    """2MB 를 넘으면 같은 폴더에 JPEG 로 줄여 쓴다."""
    if src.stat().st_size <= THUMBNAIL_MAX_BYTES:
        return src
    from PIL import Image

    dst = src.with_name(src.stem + ".upload.jpg")
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        for q in (90, 82, 74, 66, 58):
            rgb.save(dst, "JPEG", quality=q, optimize=True)
            if dst.stat().st_size <= THUMBNAIL_MAX_BYTES:
                break
    return dst


def _mime(p: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(p.suffix.lower(), "application/octet-stream")


def _side_step(name: str, fn: Callable[[], Any], warnings: list[str], ctx: PublishContext, hint: str = "") -> Any:
    from googleapiclient.errors import HttpError

    try:
        out = fn()
        ctx.log.append(f"youtube {ctx.asset.asset_id}: {name} 완료")
        return out
    except HttpError as e:
        info = google_auth.api_error(e)
        msg = f"{name} 실패(HTTP {info.status} {info.reason}: {info.message})"
        if info.reason in QUOTA_REASONS:
            msg += " — 할당량 초과, 내일 Studio 에서 직접 처리하세요"
        elif hint:
            msg += f" — {hint}"
    except PublishError as e:
        msg = f"{name} 건너뜀({e})"
    except Exception as e:  # 부가 단계는 영상 업로드 결과를 망치지 않는다
        msg = f"{name} 실패({type(e).__name__}: {str(e)[:200]})"
    warnings.append(msg)
    ctx.log.append(f"youtube {ctx.asset.asset_id}: {msg}")
    return None


# ---------------------------------------------------------------- 발행기

class YouTubePublisher:
    platform = "youtube"
    kinds = ("longform", "short")

    def publish(self, ctx: PublishContext) -> PublishResult:
        script = _script(ctx)
        warnings = _preflight(ctx, script)
        if not ctx.cfg.platforms.youtube.audited:
            return make_package(ctx, script, warnings)
        rid = ctx.target.remote_id
        if rid and not rid.startswith(DRYRUN_PREFIX):
            # 이미 올라간 영상 — 다시 올리면 중복 영상이 생긴다. 상태만 확인한다(dry-run 이면 확인도 생략).
            res = self.check(ctx)
            res.note = f"이미 업로드됨(중복 업로드 안 함). {res.note}".strip()
            return res
        body = video_body(ctx, script)
        if _is_dry(ctx):
            return self._dry_run(ctx, script, body, warnings)
        return self._upload(ctx, script, body, warnings)

    # -- API 업로드

    def _upload(self, ctx: PublishContext, script: VideoScript, body: dict[str, Any], warnings: list[str]) -> PublishResult:
        from googleapiclient.http import MediaFileUpload

        ycfg = ctx.cfg.platforms.youtube
        video = ctx.file("video")
        yt = _service()
        request = yt.videos().insert(
            part=VIDEO_PARTS,
            body=body,
            notifySubscribers=bool(ycfg.notify_subscribers),
            media_body=MediaFileUpload(str(video), mimetype=VIDEO_MIME, chunksize=UPLOAD_CHUNK_BYTES, resumable=True),
        )
        ctx.log.append(f"youtube {script.id}: videos.insert 시작 ({video.stat().st_size / 1e6:.1f}MB, {body['status']['privacyStatus']})")
        resp = run_resumable(request, ctx, script.id)
        video_id = resp.get("id")
        if not video_id:
            raise PublishError(f"{script.id}: 업로드 응답에 영상 ID 가 없습니다. Studio 에서 업로드 여부를 확인하세요.", retryable=False)
        ctx.log.append(f"youtube {script.id}: 업로드 완료 videoId={video_id}")

        if script.format == "longform":
            def set_thumbnail():
                thumb = _upload_thumbnail_path(ctx.file("thumbnail"))
                return yt.thumbnails().set(
                    videoId=video_id, media_body=MediaFileUpload(str(thumb), mimetype=_mime(thumb))
                ).execute(num_retries=EXECUTE_RETRIES)

            def insert_caption():
                srt = ctx.file("subtitles")
                return yt.captions().insert(
                    part="snippet",
                    body={"snippet": {"videoId": video_id, "language": CAPTION_LANGUAGE, "name": CAPTION_NAME, "isDraft": False}},
                    media_body=MediaFileUpload(str(srt), mimetype=CAPTION_MIME),
                ).execute(num_retries=EXECUTE_RETRIES)

            if "thumbnail" in ctx.asset.files:
                _side_step("thumbnails.set", set_thumbnail, warnings, ctx,
                           "맞춤 썸네일은 채널 전화번호 인증이 필요합니다(Studio → 설정 → 채널 → 기능 사용 자격요건). Studio 에서 직접 올리세요")
            if "subtitles" in ctx.asset.files:
                _side_step("captions.insert", insert_caption, warnings, ctx, "Studio → 자막 에서 subtitles.srt 를 직접 올리세요")

        pl = playlist_id(ctx, script)
        if pl:
            _side_step(
                "playlistItems.insert",
                lambda: yt.playlistItems().insert(
                    part="snippet",
                    body={"snippet": {"playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
                ).execute(num_retries=EXECUTE_RETRIES),
                warnings, ctx, f"재생목록 {pl} 이 이 채널 것인지 확인하세요",
            )
        return self._result(ctx, script, body, video_id, warnings)

    def _result(self, ctx: PublishContext, script: VideoScript, body: dict[str, Any], video_id: str,
                warnings: list[str], dry: bool = False) -> PublishResult:
        status = body["status"]
        scheduled = "publishAt" in status
        manual = []
        if script.pinned_comment.strip():
            manual.append(f"고정 댓글(API 불가): 공개 후 Studio 에서 댓글을 달고 고정 — {script.pinned_comment.strip()}")
        note = (f"예약 공개 {kst_label(ctx.target.publish_at)}" if scheduled else "공개 업로드")
        if dry:
            note = f"[dry-run] {note}"
        if warnings:
            note += " / 경고: " + "; ".join(warnings)
        return PublishResult(
            status="scheduled" if scheduled else "published",
            remote_id=video_id,
            url=video_url(video_id, script.format),
            note=note,
            extra={
                "privacyStatus": status["privacyStatus"],
                "publishAt": status.get("publishAt"),
                "studio_url": STUDIO_EDIT_URL.format(id=video_id),
                "warnings": warnings,
                "manual": manual,
            },
        )

    # -- dry-run

    def _dry_run(self, ctx: PublishContext, script: VideoScript, body: dict[str, Any], warnings: list[str]) -> PublishResult:
        ycfg = ctx.cfg.platforms.youtube
        video = ctx.file("video")

        def media(p: Path, mime: str, resumable: bool = False) -> dict[str, Any]:
            d: dict[str, Any] = {"path": _rel(p), "bytes": p.stat().st_size, "mimetype": mime}
            if resumable:
                d.update(resumable=True, chunk_bytes=UPLOAD_CHUNK_BYTES)
            return d

        reqs: list[dict[str, Any]] = [{
            "method": "videos.insert",
            "params": {"part": VIDEO_PARTS, "notifySubscribers": bool(ycfg.notify_subscribers)},
            "body": body,
            "media": media(video, VIDEO_MIME, resumable=True),
        }]
        if script.format == "longform":
            for role, method, extra in (
                ("thumbnail", "thumbnails.set", {"params": {"videoId": "<videoId>"}}),
                ("subtitles", "captions.insert", {"params": {"part": "snippet"}, "body": {"snippet": {
                    "videoId": "<videoId>", "language": CAPTION_LANGUAGE, "name": CAPTION_NAME, "isDraft": False}}}),
            ):
                if role in ctx.asset.files:
                    p = paths.ROOT / ctx.asset.files[role]
                    item = {"method": method, **extra}
                    item["media"] = media(p, _mime(p) if role == "thumbnail" else CAPTION_MIME) if p.exists() else {"path": _rel(p), "missing": True}
                    reqs.append(item)
        pl = playlist_id(ctx, script)
        if pl:
            reqs.append({"method": "playlistItems.insert", "params": {"part": "snippet"}, "body": {"snippet": {
                "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": "<videoId>"}}}})
        out = paths.out_dir(ctx.week) / "dryrun" / f"youtube-{script.id}.json"
        write_json(out, {
            "dry_run": True,
            "platform": "youtube",
            "asset_id": script.id,
            "generated_at": _now().isoformat(timespec="seconds"),
            "publish_at_kst": kst_label(ctx.target.publish_at),
            "warnings": warnings,
            "requests": reqs,
        })
        ctx.log.append(f"[dry-run] youtube {script.id}: 요청 {len(reqs)}건 기록 → {_rel(out)}")
        return self._result(ctx, script, body, f"{DRYRUN_PREFIX}{script.id}", warnings, dry=True)

    # -- 상태 확인

    def check(self, ctx: PublishContext) -> PublishResult:
        from googleapiclient.errors import HttpError

        vid = ctx.target.remote_id
        fmt = ctx.content.format if isinstance(ctx.content, VideoScript) else ("short" if ctx.asset.kind == "short" else "longform")
        if not vid:
            raise PublishError(
                f"{ctx.asset.asset_id}: 확인할 영상 ID 가 없습니다. 수동 업로드했다면 `python -m studio publish mark` 로 영상 ID 를 연결하세요.",
                retryable=False,
            )
        if _is_dry(ctx) or vid.startswith(DRYRUN_PREFIX):
            st = ctx.target.status if ctx.target.status in ("scheduled", "published") else "published"
            return PublishResult(status=st, remote_id=vid, url=video_url(vid, fmt), note="[dry-run] 상태 확인 생략")
        yt = _service()
        try:
            resp = yt.videos().list(part="status,processingDetails", id=vid).execute(num_retries=EXECUTE_RETRIES)
        except HttpError as e:
            raise to_publish_error(e, "videos.list") from None
        items = resp.get("items") or []
        if not items:
            raise PublishError(
                f"영상 {vid} 을(를) 찾지 못했습니다 — 삭제됐거나 다른 채널의 토큰입니다. Studio 에서 확인하세요.", retryable=False
            )
        st = items[0].get("status") or {}
        pd = items[0].get("processingDetails") or {}
        upload = st.get("uploadStatus")
        url = video_url(vid, fmt)
        if upload == "rejected":
            raise PublishError(
                f"YouTube 가 영상 {vid} 을(를) 거부했습니다(rejectionReason={st.get('rejectionReason')}). "
                "중복·저작권·약관 문제인지 Studio 에서 확인하세요.",
                retryable=False,
            )
        if upload == "failed" or pd.get("processingStatus") in ("failed", "terminated"):
            reason = st.get("failureReason") or pd.get("processingFailureReason")
            raise PublishError(
                f"영상 {vid} 처리 실패(failureReason={reason}). 렌더 결과(코덱·파일 손상)를 확인하고 다시 올리세요.", retryable=False
            )
        if upload == "deleted":
            raise PublishError(f"영상 {vid} 이(가) 삭제됐습니다.", retryable=False)
        if upload == "uploaded":
            prog = pd.get("processingProgress") or {}
            done, total = prog.get("partsProcessed"), prog.get("partsTotal")
            pct = f" {int(done) * 100 // max(int(total), 1)}%" if done and total else ""
            return PublishResult(status="processing", remote_id=vid, url=url, note=f"YouTube 처리 중{pct}")
        privacy = st.get("privacyStatus")
        if privacy in ("public", "unlisted"):
            return PublishResult(status="published", remote_id=vid, url=url, note=f"공개 상태: {privacy}")
        publish_at = st.get("publishAt")
        if publish_at:
            at = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            if at + SCHEDULE_GRACE > _now():
                return PublishResult(status="scheduled", remote_id=vid, url=url, note=f"예약 공개 {kst_label(at)}")
        raise PublishError(
            f"영상 {vid} 이(가) 예약 시각이 지났는데 비공개입니다. 미감사 Cloud 프로젝트의 API 업로드는 비공개로 잠깁니다 — "
            "감사 통과 전이면 channel.yaml platforms.youtube.audited 를 false 로 두고, Studio 에서 공개 상태를 확인하세요.",
            retryable=False,
        )


# ---------------------------------------------------------------- 업로드 패키지 (감사 전)

def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def link_or_copy(src: Path, dst: Path) -> str:
    """하드링크를 먼저 시도하고(디스크 절약), 다른 파일시스템 등으로 실패하면 복사한다."""
    if dst.exists() or dst.is_symlink():
        try:
            if os.path.samefile(src, dst):
                return "hardlink"
        except OSError:
            pass
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def make_package(ctx: PublishContext, script: VideoScript, warnings: list[str]) -> PublishResult:
    """Studio 수동 업로드용 묶음을 만든다. 네트워크를 쓰지 않으므로 dry-run 에서도 같은 결과를 만든다."""
    ycfg = ctx.cfg.platforms.youtube
    root = paths.out_dir(ctx.week) / PACKAGE_DIR
    pkg = root / script.id
    pkg.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    files["video"] = "video.mp4"
    link_or_copy(ctx.file("video"), pkg / "video.mp4")
    for role in ("thumbnail", "cover", "subtitles"):
        rel = ctx.asset.files.get(role)
        if not rel:
            continue
        src = paths.ROOT / rel
        if not src.exists():
            warnings.append(f"{role} 파일이 없습니다({rel}) — 패키지에서 빠졌습니다.")
            continue
        name = "subtitles.srt" if role == "subtitles" else f"{role}{src.suffix}"
        link_or_copy(src, pkg / name)
        files[role] = name

    body = video_body(ctx, script)
    scheduled = "publishAt" in body["status"]
    pl = playlist_id(ctx, script)
    meta = {
        "mode": "manual-upload",
        "asset_id": script.id,
        "week": ctx.week,
        "kind": script.format,
        "title": body["snippet"]["title"],
        "description": body["snippet"]["description"],
        "tags": body["snippet"]["tags"],
        "category_id": body["snippet"]["categoryId"],
        "default_language": body["snippet"]["defaultLanguage"],
        "default_audio_language": body["snippet"]["defaultAudioLanguage"],
        "visibility": "schedule" if scheduled else "public",
        "publish_at_kst": kst_label(ctx.target.publish_at),
        "publish_at_utc": iso_z(ctx.target.publish_at),
        "made_for_kids": body["status"]["selfDeclaredMadeForKids"],
        "contains_synthetic_media": body["status"]["containsSyntheticMedia"],
        "notify_subscribers": bool(ycfg.notify_subscribers),
        "playlist_id": pl,
        "pinned_comment": script.pinned_comment.strip(),
        "duration_seconds": ctx.asset.duration_seconds,
        "files": files,
        "warnings": warnings,
        "api_body": body,     # 감사 통과 후 API 모드가 보낼 본문과 같다
        "generated_at": _now().isoformat(timespec="seconds"),
    }
    write_json(pkg / "metadata.json", meta)
    (pkg / PACKAGE_GUIDE).write_text(package_guide(meta, ctx.target.key), encoding="utf-8")
    write_package_index(root)
    rel = _rel(pkg)
    ctx.log.append(f"youtube {script.id}: 업로드 패키지 생성 → {rel}")
    note = f"수동 업로드 필요: {rel}"
    if warnings:
        note += " / 경고: " + "; ".join(warnings)
    return PublishResult(status="skipped", note=note, extra={"package": rel, "publish_at_kst": meta["publish_at_kst"], "warnings": warnings})


def _fmt_duration(sec: float | None) -> str:
    if not sec:
        return "?"
    s = round(sec)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def package_guide(meta: dict[str, Any], target_key: str) -> str:
    longform = meta["kind"] == "longform"
    files = meta["files"]
    scheduled = meta["visibility"] == "schedule"
    when = meta["publish_at_kst"]
    cat = meta["category_id"]
    lines = [
        f"# 유튜브 수동 업로드 — {meta['asset_id']} ({'롱폼' if longform else '쇼츠'})",
        "",
        "YouTube API 감사 통과 전이라 API 업로드는 비공개로 잠깁니다. YouTube Studio 에서 직접 예약 업로드하세요.",
        "(감사를 통과하면 channel.yaml `platforms.youtube.audited: true` 로 바꾸면 자동 업로드됩니다.)",
        "",
        f"- **공개 예약: {when}**" + ("" if scheduled else "  ← 예약 시각이 지났거나 임박했습니다. 바로 **공개**로 올리세요."),
        f"- 영상: `{files['video']}` (길이 {_fmt_duration(meta.get('duration_seconds'))})",
    ]
    if files.get("thumbnail") or files.get("cover"):
        lines.append(f"- 썸네일: `{files.get('thumbnail') or files.get('cover')}`")
    if files.get("subtitles"):
        lines.append(f"- 자막: `{files['subtitles']}`" + ("" if longform else " (쇼츠는 화면에 자막이 입혀져 있어 올리지 않아도 됩니다)"))
    lines.append(f"- 재생목록: {meta['playlist_id'] or '(설정 없음)'}")
    lines += ["", "## 제목", "", "```text", meta["title"], "```", "", "## 설명", "", "```text", meta["description"], "```", ""]
    if meta["tags"]:
        lines += ["## 태그 (그대로 붙여넣기)", "", "```text", ", ".join(meta["tags"]), "```", ""]
    synthetic = meta["contains_synthetic_media"]
    lines += [
        "## 체크리스트",
        "",
        "- [ ] Studio → 만들기 → 동영상 업로드 → `video.mp4`",
        "- [ ] 제목·설명 붙여넣기 (설명란의 앱 링크·AI 음성 고지가 그대로 있는지 확인)",
    ]
    if longform and files.get("thumbnail"):
        lines.append(f"- [ ] 썸네일 업로드: `{files['thumbnail']}`")
    elif not longform:
        lines.append("- [ ] (선택) 쇼츠 썸네일: YouTube 앱에서 쇼츠 썸네일 프레임 지정 — `cover.png` 참고")
    if meta["playlist_id"]:
        lines.append(f"- [ ] 재생목록에 추가: {meta['playlist_id']}")
    lines += [
        "- [ ] 시청자층: **'아니요, 아동용이 아닙니다'**" if not meta["made_for_kids"] else "- [ ] 시청자층: **'예, 아동용입니다'**",
        (
            "- [ ] 자세히 보기 → 변경되거나 합성된 콘텐츠(AI 변형 콘텐츠 표시): **'예'** — channel.yaml 에서 표시하도록 설정됨"
            if synthetic
            else "- [ ] 자세히 보기 → 변경되거나 합성된 콘텐츠(AI 변형 콘텐츠 표시): **'아니요'** — 실존 인물·실제 사건을 사실처럼 꾸민 "
            "장면이 없고, 일반 TTS 내레이션은 표시 의무 대상이 아닙니다(설명란에 AI 음성 고지가 있음)"
        ),
        f"- [ ] 자세히 보기 → 태그 붙여넣기 · 동영상 언어: 한국어 · 카테고리: {CATEGORY_NAMES.get(cat, cat)}({cat})",
    ]
    if longform and files.get("subtitles"):
        lines.append(f"- [ ] 자막: 한국어 → 파일 업로드 → '타이밍 포함' → `{files['subtitles']}`")
    lines += [
        f"- [ ] 구독자에게 알림: {'켬' if meta['notify_subscribers'] else '끔'}",
        (f"- [ ] 공개 상태: **예약** → {when} (시간대: 서울)" if scheduled else "- [ ] 공개 상태: **공개**"),
    ]
    if meta["pinned_comment"]:
        lines += [
            "- [ ] 공개 후 아래 문구로 댓글을 달고 **고정** (API 로는 고정할 수 없음)",
            "",
            "```text",
            meta["pinned_comment"],
            "```",
            "",
        ]
    lines += [
        f"- [ ] 업로드 후 영상 ID(Studio 주소 `…/video/<영상ID>/edit` 의 <영상ID>)를 발행 대기열 `{target_key}` 에 연결:",
        "      `python -m studio publish mark` (사용법: `python -m studio publish mark --help`)",
        "",
    ]
    if meta["warnings"]:
        lines += ["## 경고", ""] + [f"- {w}" for w in meta["warnings"]] + [""]
    return "\n".join(lines)


def write_package_index(root: Path) -> Path:
    """주차의 모든 패키지를 공개 시각 순으로 묶은 업로드 순서표."""
    rows = []
    for mp in sorted(root.glob("*/metadata.json")):
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append(m)
    rows.sort(key=lambda m: m.get("publish_at_utc", ""))
    lines = [
        "# 유튜브 업로드 순서표",
        "",
        "공개 시각 순서입니다. 항목마다 폴더의 `업로드안내.md` 를 따라 Studio 에서 예약 업로드하세요.",
        "",
        "| 완료 | 공개 예약 | ID | 종류 | 제목 |",
        "|---|---|---|---|---|",
    ]
    for m in rows:
        title = str(m.get("title", "")).replace("|", "\\|")
        kind = "롱폼" if m.get("kind") == "longform" else "쇼츠"
        lines.append(f"| [ ] | {m.get('publish_at_kst', '')} | [{m.get('asset_id')}]({m.get('asset_id')}/{PACKAGE_GUIDE}) | {kind} | {title} |")
    out = root / PACKAGE_INDEX
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out

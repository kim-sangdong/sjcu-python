"""페이스북 페이지 릴스 발행 (POST /{page-id}/video_reels).

흐름: upload_phase=start → rupload 로 전송(공개 URL 은 file_url 헤더, 없으면 로컬 파일 바이너리)
     → upload_phase=finish (video_state=PUBLISHED, 또는 publish_at 이 충분히 미래면 SCHEDULED + scheduled_publish_time)
     → 처리 상태 확인. 처리 중이면 PublishResult(status="processing", remote_id=<video_id>) → check() 에서 이어서 확인.

토큰: FACEBOOK_PAGE_TOKEN(페이지 토큰), 페이지: FACEBOOK_PAGE_ID. 없으면 `auth facebook` 이 token_store 에 남긴 값.

설정(channel.yaml platforms.facebook, 모두 선택):
  upload: auto                # auto = 공개 URL 이 있으면 file_url, 없으면 바이너리 | url | binary
  is_ai_generated: false      # True 면 finish 에 is_ai_generated=true (False 면 보내지 않음)
  poll_timeout_seconds: 120   # finish 뒤 처리 완료를 기다리는 시간
  poll_interval_seconds: 10
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from studio import paths
from studio.config import env
from studio.models import VideoScript
from studio.publish import captions
from studio.publish.base import PublishContext, PublishError, PublishResult
from studio.publish.meta_auth import (
    ENV_FB_PAGE,
    ENV_FB_TOKEN,
    FB_GRAPH,
    GRAPH_VERSION,
    RUPLOAD,
    STORE_KEY_FB,
    env_dry_run,
    graph_call,
    graph_url,
    safe_params,
)

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

REELS_PATH = "/{page}/video_reels"
RUPLOAD_PATH = "/video-upload/{version}/{video_id}"     # [기억·미확인] start 응답 upload_url 이 있으면 그것을 쓴다
VIDEO_FIELDS = "status,permalink_url"
REEL_MIN_SECONDS, REEL_MAX_SECONDS = 3, 90              # [기억·미확인] API 페이지 릴스 3~90초
MIN_SCHEDULE_LEAD = timedelta(minutes=20)               # [기억·미확인] 예약은 최소 10분 뒤 → 여유 두고 20분
MAX_SCHEDULE_AHEAD = timedelta(days=29)                 # [기억·미확인] 예약 가능 최대 기간(보수적으로)
UPLOAD_TIMEOUT = (10, 600)
REEL_URL = "https://www.facebook.com/reel/{video_id}"

_sleep = time.sleep   # 테스트에서 바꿔 끼운다


def _settings(cfg) -> dict[str, Any]:
    extra = cfg.platforms.facebook.model_extra or {}
    mode = str(extra.get("upload", "auto")).strip().lower()
    if mode not in ("auto", "url", "binary"):
        raise PublishError(f"platforms.facebook.upload 값 {mode!r} 을 알 수 없습니다(auto | url | binary).", retryable=False)
    return {
        "upload": mode,
        "is_ai_generated": bool(extra.get("is_ai_generated", False)),
        "poll_timeout": float(extra.get("poll_timeout_seconds", 120)),
        "poll_interval": float(extra.get("poll_interval_seconds", 10)),
    }


def _credentials() -> tuple[str, str]:
    from studio.tokens import token_store

    page = env(ENV_FB_PAGE, required=False)
    tok = env(ENV_FB_TOKEN, required=False)
    if not (page and tok):
        rec = token_store().get(STORE_KEY_FB) or {}
        page, tok = page or rec.get("page_id"), tok or rec.get("page_token")
    if not (page and tok):
        raise PublishError(f"[설정 필요] {ENV_FB_PAGE}·{ENV_FB_TOKEN} 이 없습니다. `python -m studio auth facebook` 출력값을 넣으세요.",
                           retryable=False)
    return str(page), str(tok)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=paths.KST)


def _permalink(info: dict[str, Any], video_id: str) -> str:
    link = str(info.get("permalink_url") or "")
    if link.startswith("/"):
        return "https://www.facebook.com" + link
    return link or REEL_URL.format(video_id=video_id)


def _phase_error(st: dict[str, Any]) -> str | None:
    for name, key in (("업로드", "uploading_phase"), ("처리", "processing_phase"), ("게시", "publishing_phase")):
        ph = st.get(key) or {}
        if str(ph.get("status") or "").lower() == "error":
            errs = ph.get("errors") or []
            msg = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errs) if isinstance(errs, list) else str(errs)
            return f"{name} 단계 오류: {msg[:300] or '알 수 없음'}"
    if str(st.get("video_status") or "").lower() in ("error", "expired"):
        return f"영상 상태 {st.get('video_status')}"
    return None


def evaluate(info: dict[str, Any], video_id: str, scheduled: bool) -> PublishResult | None:
    """영상 상태 → 결과. 아직 처리 중이면 None. [기억·미확인] status 필드 구조."""
    st = info.get("status") or {}
    err = _phase_error(st)
    if err:
        raise PublishError(f"페이스북 릴스 {video_id} {err} — 영상 규격(MP4, 9:16, 3~90초)을 확인하세요.", retryable=False)
    url = _permalink(info, video_id)
    vs = str(st.get("video_status") or "").lower()
    proc = str((st.get("processing_phase") or {}).get("status") or "").lower()
    pub = st.get("publishing_phase") or {}
    pub_state = str(pub.get("publish_status") or "").lower()
    pub_phase = str(pub.get("status") or "").lower()
    if pub_state == "published" or (not scheduled and vs == "ready" and pub_phase == "complete"):
        return PublishResult("published", remote_id=video_id, url=url)
    if pub_state == "scheduled" or (scheduled and (proc == "complete" or vs == "ready")):
        return PublishResult("scheduled", remote_id=video_id, url=url, extra={"publish_time": pub.get("publish_time")})
    return None


class FacebookPublisher:
    platform = "facebook"
    kinds = ("short",)

    def publish(self, ctx: PublishContext) -> PublishResult:
        if not ctx.cfg.platforms.facebook.enabled:
            return PublishResult("skipped", note="platforms.facebook.enabled=false")
        if ctx.asset.kind != "short":
            raise PublishError(f"페이스북 발행기는 쇼츠만 다룹니다({ctx.asset.kind}).", retryable=False)
        script = ctx.content
        if not isinstance(script, VideoScript):
            raise PublishError(f"{ctx.asset.asset_id}: 쇼츠 원고(VideoScript)가 아닙니다.", retryable=False)
        s = _settings(ctx.cfg)
        dry = ctx.dry_run or env_dry_run()
        dur = ctx.asset.duration_seconds
        if dur is not None and not (REEL_MIN_SECONDS <= dur <= REEL_MAX_SECONDS):
            raise PublishError(f"{ctx.asset.asset_id}: 페이스북 릴스 길이 {dur:.1f}초가 허용 범위({REEL_MIN_SECONDS}~{REEL_MAX_SECONDS}초) 밖입니다.",
                               retryable=False)
        when = _aware(ctx.target.publish_at)
        lead = when - datetime.now(UTC)
        scheduled = lead >= MIN_SCHEDULE_LEAD
        if lead > MAX_SCHEDULE_AHEAD:
            raise PublishError(f"{ctx.asset.asset_id}: 공개 시각이 {lead.days}일 뒤라 예약 가능 기간을 넘습니다. 가까워지면 다시 시도합니다.",
                               retryable=True)

        file_url = ctx.asset.remote_urls.get("video")
        use_url = s["upload"] == "url" or (s["upload"] == "auto" and bool(file_url))
        if use_url and not file_url and not dry:
            file_url = ctx.remote_url("video")      # 없으면 upload-media 안내 오류
        local = None if use_url or dry else ctx.file("video")

        finish: dict[str, Any] = {
            "upload_phase": "finish",
            "video_id": "<video_id>",
            "description": captions.reel_caption(script, ctx.cfg, "facebook"),
            "title": script.title,
            "video_state": "SCHEDULED" if scheduled else "PUBLISHED",
        }
        if scheduled:
            finish["scheduled_publish_time"] = str(int(when.timestamp()))
        if s["is_ai_generated"]:
            finish["is_ai_generated"] = "true"

        if dry:
            ctx.log.append(f"[dry-run] 페이스북 릴스 — 토큰 {'있음' if env(ENV_FB_TOKEN, required=False) else '없음'}")
            ctx.log.append(f"[dry-run] POST {REELS_PATH} upload_phase=start")
            how = f"file_url={file_url or '(upload-media 후 생길 공개 URL)'}" if use_url else "바이너리(로컬 video)"
            ctx.log.append(f"[dry-run] POST {RUPLOAD}{RUPLOAD_PATH} {how}")
            ctx.log.append(f"[dry-run] POST {REELS_PATH} {safe_params(finish)}")
            return PublishResult("scheduled" if scheduled else "published", remote_id=f"dry-run-fb-{ctx.asset.asset_id}",
                                 note="dry-run: 요청을 보내지 않았습니다")

        page, tok = _credentials()
        reels_url = graph_url(FB_GRAPH, REELS_PATH.format(page=page))
        start = graph_call("POST", reels_url, token=tok, data={"upload_phase": "start"},
                           what="페이스북 릴스 업로드 시작", reauth="facebook")
        video_id = str(start.get("video_id") or "")
        if not video_id:
            raise PublishError("페이스북 릴스 업로드 시작 응답에 video_id 가 없습니다.", retryable=True)
        upload_url = start.get("upload_url") or RUPLOAD + RUPLOAD_PATH.format(version=GRAPH_VERSION, video_id=video_id)
        ctx.log.append(f"POST {REELS_PATH} upload_phase=start → video_id={video_id}")

        if use_url:
            res = graph_call("POST", upload_url, token=tok, token_in="header", headers={"file_url": str(file_url)},
                             what="릴스 파일(URL) 전송", reauth="facebook", timeout=UPLOAD_TIMEOUT)
            ctx.log.append(f"POST rupload file_url={file_url}")
        else:
            assert local is not None
            size = local.stat().st_size
            with open(local, "rb") as fh:
                res = graph_call("POST", upload_url, token=tok, token_in="header", body=fh,
                                 headers={"offset": "0", "file_size": str(size), "Content-Type": "application/octet-stream"},
                                 what="릴스 파일(바이너리) 전송", reauth="facebook", timeout=UPLOAD_TIMEOUT)
            ctx.log.append(f"POST rupload 바이너리 {size}바이트")
        if res.get("success") is False:
            raise PublishError(f"페이스북 릴스 파일 전송이 실패로 응답했습니다: {str(res)[:200]}", retryable=True)

        finish["video_id"] = video_id
        ctx.log.append(f"POST {REELS_PATH} {safe_params(finish)}")
        res = graph_call("POST", reels_url, token=tok, data=finish, what="페이스북 릴스 게시(finish)", reauth="facebook")
        if res.get("success") is False:
            raise PublishError(f"페이스북 릴스 finish 가 실패로 응답했습니다: {str(res)[:200]}", retryable=True)
        return self._poll(ctx, tok, video_id, scheduled, s["poll_timeout"], s["poll_interval"])

    def check(self, ctx: PublishContext) -> PublishResult:
        video_id = ctx.target.remote_id
        if not video_id:
            raise PublishError(f"{ctx.asset.asset_id}: 확인할 video_id(remote_id)가 없습니다. 다시 발행하세요.", retryable=True)
        if ctx.dry_run or env_dry_run():
            ctx.log.append(f"[dry-run] GET /{video_id}?fields={VIDEO_FIELDS}")
            return PublishResult("published", remote_id=video_id, note="dry-run")
        _, tok = _credentials()
        scheduled = _aware(ctx.target.publish_at) - datetime.now(UTC) > timedelta(0)
        return self._poll(ctx, tok, video_id, scheduled, 0, 0)

    def _poll(self, ctx: PublishContext, tok: str, video_id: str, scheduled: bool, timeout: float, interval: float) -> PublishResult:
        deadline = time.monotonic() + timeout
        while True:
            info = graph_call("GET", graph_url(FB_GRAPH, f"/{video_id}"), token=tok, params={"fields": VIDEO_FIELDS},
                              what="페이스북 릴스 상태 확인", reauth="facebook")
            res = evaluate(info, video_id, scheduled)
            if res:
                return res
            if time.monotonic() >= deadline:
                st = (info.get("status") or {}).get("video_status") or "processing"
                ctx.log.append(f"페이스북 릴스 {video_id} 처리 중({st}) — check() 에서 다시 확인합니다.")
                return PublishResult("processing", remote_id=video_id, note=f"처리 중({st})",
                                     extra={"video_state": "SCHEDULED" if scheduled else "PUBLISHED"})
            _sleep(max(interval, 0))

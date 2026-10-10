"""틱톡 발행 — Content Posting API. 기본은 Direct Post, 설정으로 받은편지함 업로드(inbox)도 된다.

흐름: access token(24시간 캐시, refresh_token 회전 저장) → creator_info/query(공개 범위 옵션·최대 길이)
     → video/init(FILE_UPLOAD) → upload_url 에 PUT(Content-Range, 청크 순서대로) → status/fetch 폴링.
처리 중이면 PublishResult(status="processing", remote_id=<publish_id>) → check() 에서 이어서 확인.

  python -m studio auth tiktok [--redirect-uri URI] [--pkce]        인가 URL 출력
  python -m studio auth tiktok --code <코드 또는 리디렉트 주소 전체>    코드 교환 → refresh token 저장

설정(channel.yaml platforms.tiktok):
  privacy_level: PUBLIC_TO_EVERYONE
  audited: false              # 감사 전에는 SELF_ONLY(나만 보기) 강제 + 경고
  mode: direct                # direct = Direct Post | inbox = 받은편지함 업로드(사람이 앱에서 게시)  (선택)
  disable_duet: false         # (선택)
  disable_stitch: false       # (선택)
  is_aigc: false              # (선택) True 면 post_info.is_aigc=true. 필드 존재가 미확인이라 TT_SEND_IS_AIGC 로 끈다.
  video_cover_timestamp_ms: 1000   # (선택) 표지로 쓸 프레임 시각
  poll_timeout_seconds: 120   # (선택)
  poll_interval_seconds: 5    # (선택)
  redirect_uri: ""            # (선택) auth tiktok 기본 리디렉트 URI(개발자 포털에 등록한 값과 같아야 함)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import requests

from studio import paths
from studio.config import MissingSecret, env, load_config
from studio.models import VideoScript
from studio.publish import captions
from studio.publish.base import PublishContext, PublishError, PublishResult
from studio.publish.meta_auth import env_dry_run, fingerprint, mask, now_utc, parse_time

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다) — 모두 [기억·미확인]

TT_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TT_API = "https://open.tiktokapis.com"
TT_TOKEN_URL = TT_API + "/v2/oauth/token/"
TT_CREATOR_INFO_URL = TT_API + "/v2/post/publish/creator_info/query/"
TT_VIDEO_INIT_URL = TT_API + "/v2/post/publish/video/init/"
TT_INBOX_INIT_URL = TT_API + "/v2/post/publish/inbox/video/init/"
TT_STATUS_URL = TT_API + "/v2/post/publish/status/fetch/"
TT_SCOPES = ("user.info.basic", "video.publish", "video.upload", "video.list")
TT_POST_URL = "https://www.tiktok.com/@{username}/video/{post_id}"

TT_SEND_IS_AIGC = True          # post_info.is_aigc 를 보낼 수 있는지. 거부(invalid_params)되면 False 로 바꾼다.
TT_PKCE_HEX = True              # 틱톡 PKCE 는 code_challenge 를 SHA256 hex 로 받는다고 기억(표준 base64url 아님). 틀리면 False.

MB = 1024 * 1024
TT_SINGLE_CHUNK_UNDER = 64 * MB  # 이보다 작으면 한 번에(chunk_size = video_size, total_chunk_count = 1)
TT_CHUNK_SIZE = 32 * MB          # 여러 청크일 때 크기(5~64MB 범위). 마지막 청크가 나머지를 흡수(최대 128MB 미만)
TT_MAX_CHUNKS = 1000
TT_TITLE_MAX_UTF16 = 2200
ACCESS_TOKEN_MARGIN = timedelta(minutes=10)
HTTP_TIMEOUT = (10, 60)
UPLOAD_TIMEOUT = (10, 600)
RETRIES = 3

ENV_CLIENT_KEY = "TIKTOK_CLIENT_KEY"
ENV_CLIENT_SECRET = "TIKTOK_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "TIKTOK_REFRESH_TOKEN"
STORE_KEY = "tiktok"
PENDING_FILE = "tiktok_auth_pending.json"     # .secrets/ 아래 — 인가 단계 state·code_verifier

# 오류 코드 → (다시 시도할지, 사람이 할 일)
TT_ERRORS: dict[str, tuple[bool, str]] = {
    "rate_limit_exceeded": (True, "호출 한도에 걸렸습니다. 잠시 뒤 다시 시도합니다."),
    "internal_error": (True, "틱톡 서버 오류입니다. 다시 시도합니다."),
    "spam_risk_too_many_posts": (True, "하루 게시 한도에 걸렸습니다. 다음 실행에서 다시 시도합니다."),
    "spam_risk_too_many_pending_share": (True, "받은편지함에 대기 중인 게시물이 너무 많습니다. 앱에서 정리하세요."),
    "reached_active_user_cap": (True, "앱의 하루 활성 사용자 한도에 걸렸습니다(미감사 앱 제한). 내일 다시 시도합니다."),
    "spam_risk_user_banned_from_posting": (False, "이 계정은 게시가 제한되었습니다. 틱톡 앱에서 계정 상태를 확인하세요."),
    "unaudited_client_can_only_post_to_private_accounts": (
        False, "감사 전 앱은 비공개 계정에만 게시할 수 있습니다. 틱톡 계정을 비공개로 바꾸거나 platforms.tiktok.mode: inbox 를 쓰세요."),
    "privacy_level_option_mismatch": (False, "공개 범위가 크리에이터 옵션과 맞지 않습니다. platforms.tiktok.privacy_level 을 확인하세요."),
    "url_ownership_unverified": (False, "PULL_FROM_URL 도메인 검증이 안 됐습니다(이 모듈은 FILE_UPLOAD 를 씁니다)."),
    "access_token_invalid": (False, "틱톡 토큰이 무효입니다. `python -m studio auth tiktok` 으로 다시 발급하세요."),
    "scope_not_authorized": (False, "토큰에 video.publish·video.upload 권한이 없습니다. `python -m studio auth tiktok` 으로 다시 동의하세요."),
    "scope_permission_missed": (False, "앱에 Content Posting API 권한이 없습니다. 개발자 포털에서 제품·스코프를 추가하세요."),
    "invalid_params": (False, "요청 값이 잘못되었습니다(필드 이름·청크 규칙 확인)."),
}
# status/fetch 의 fail_reason 중 다시 해 볼 만한 것
TT_FAIL_RETRYABLE = {"internal", "internal_error", "video_pull_failed", "rate_limit_exceeded", "upload_timeout",
                     "spam_risk_too_many_posts", "reached_active_user_cap", "network_error"}

_sleep = time.sleep   # 테스트에서 바꿔 끼운다


class TikTokError(PublishError):
    def __init__(self, message: str, *, retryable: bool, code: str = ""):
        super().__init__(message, retryable=retryable)
        self.code = code


def _settings(cfg) -> dict[str, Any]:
    tt = cfg.platforms.tiktok
    extra = tt.model_extra or {}
    mode = str(extra.get("mode", "direct")).strip().lower()
    if mode not in ("direct", "inbox"):
        raise PublishError(f"platforms.tiktok.mode 값 {mode!r} 을 알 수 없습니다(direct | inbox).", retryable=False)
    cover = extra.get("video_cover_timestamp_ms")
    return {
        "mode": mode,
        "disable_duet": bool(extra.get("disable_duet", False)),
        "disable_stitch": bool(extra.get("disable_stitch", False)),
        "is_aigc": bool(extra.get("is_aigc", False)),
        "cover_ms": int(cover) if cover is not None else None,
        "poll_timeout": float(extra.get("poll_timeout_seconds", 120)),
        "poll_interval": float(extra.get("poll_interval_seconds", 5)),
    }


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count). 64MB 미만이면 한 번에, 넘으면 32MB 청크 + 마지막 청크가 나머지 흡수."""
    if size <= 0:
        raise PublishError("영상 파일 크기가 0입니다. 렌더를 다시 하세요.", retryable=False)
    if size < TT_SINGLE_CHUNK_UNDER:
        return size, 1
    count = size // TT_CHUNK_SIZE
    if count > TT_MAX_CHUNKS:
        raise PublishError(f"영상이 너무 큽니다({size / MB:.0f}MB) — 청크 {count}개가 상한 {TT_MAX_CHUNKS}개를 넘습니다.", retryable=False)
    return TT_CHUNK_SIZE, count


def truncate_utf16(text: str, limit: int = TT_TITLE_MAX_UTF16) -> str:
    """틱톡 title 상한은 UTF-16 단위다(이모지는 2). 넘으면 글자 단위로 자른다."""
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    out, n = [], 0
    for ch in text:
        w = len(ch.encode("utf-16-le")) // 2
        if n + w > limit:
            break
        out.append(ch)
        n += w
    return "".join(out)


def _video_type(p: Path) -> str:
    return {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}.get(p.suffix.lower(), "video/mp4")


# ---------------------------------------------------------------- 토큰

def token_request(form: dict[str, str]) -> dict[str, Any]:
    """POST /v2/oauth/token/ (x-www-form-urlencoded). 응답 최상위에 access_token 등이 온다."""
    try:
        r = requests.post(TT_TOKEN_URL, data=form, timeout=HTTP_TIMEOUT,
                          headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
    except requests.RequestException as e:
        raise TikTokError(f"틱톡 토큰 요청: 네트워크 오류 {type(e).__name__}", retryable=True) from None
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    data = payload if payload.get("access_token") else (payload.get("data") or {})
    if r.ok and isinstance(data, dict) and data.get("access_token"):
        return data
    err = payload.get("error")
    code = str((err.get("code") if isinstance(err, dict) else err) or "")
    desc = str((err.get("message") if isinstance(err, dict) else payload.get("error_description")) or "")[:200]
    if code in ("invalid_grant", "invalid_request"):
        if form.get("grant_type") == "refresh_token":
            raise TikTokError(f"틱톡 refresh token 이 만료(365일)·취소되었습니다({code}: {desc}). "
                              f"`python -m studio auth tiktok` 으로 다시 받아 {ENV_REFRESH_TOKEN} 을 바꾸세요.", retryable=False, code=code)
        raise TikTokError(f"인가 코드를 교환하지 못했습니다({code}: {desc}). 코드가 만료·사용됐거나 리디렉트 URI 가 다릅니다.",
                          retryable=False, code=code)
    if code in ("invalid_client", "unauthorized_client"):
        raise TikTokError(f"{ENV_CLIENT_KEY}/{ENV_CLIENT_SECRET} 가 맞지 않습니다({code}: {desc}).", retryable=False, code=code)
    raise TikTokError(f"틱톡 토큰 요청 실패: HTTP {r.status_code} {code} {desc}", retryable=r.status_code >= 500 or r.status_code == 429,
                      code=code)


def token_record(data: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    now = now_utc()
    rec = dict(old or {})
    rec.update({
        "access_token": data["access_token"],
        "access_expires_at": (now + timedelta(seconds=int(data.get("expires_in") or 86400))).isoformat(timespec="seconds"),
        "refresh_token": data.get("refresh_token") or rec.get("refresh_token"),
        "open_id": data.get("open_id") or rec.get("open_id"),
        "scope": data.get("scope") or rec.get("scope"),
        "refreshed_at": now.isoformat(timespec="seconds"),
    })
    if data.get("refresh_expires_in"):
        rec["refresh_expires_at"] = (now + timedelta(seconds=int(data["refresh_expires_in"]))).isoformat(timespec="seconds")
    return rec


def _client() -> tuple[str, str]:
    try:
        return str(env(ENV_CLIENT_KEY)), str(env(ENV_CLIENT_SECRET))
    except MissingSecret as e:
        raise PublishError(f"[설정 필요] {e}", retryable=False) from None


def access_token(log: list[str] | None = None, *, force_refresh: bool = False) -> str:
    """유효한 access token. 저장된 것이 10분 넘게 남았으면 그대로, 아니면 refresh_token 으로 새로 받아 저장한다."""
    from studio.tokens import token_store

    log = log if log is not None else []
    store = token_store()
    rec = dict(store.get(STORE_KEY) or {})
    env_rt = env(ENV_REFRESH_TOKEN, required=False)
    if env_rt and env_rt != rec.get("refresh_token") and fingerprint(env_rt) != rec.get("seed"):
        rec = {"refresh_token": env_rt, "seed": fingerprint(env_rt)}   # 새로 넣은 시크릿이 회전된 값보다 우선
    if not force_refresh:
        exp = parse_time(rec.get("access_expires_at"))
        if rec.get("access_token") and exp and exp - now_utc() > ACCESS_TOKEN_MARGIN:
            return str(rec["access_token"])
    rt = rec.get("refresh_token")
    if not rt:
        raise PublishError(f"[설정 필요] {ENV_REFRESH_TOKEN} 이 없습니다. `python -m studio auth tiktok` 으로 발급하세요.", retryable=False)
    key, secret = _client()
    data = token_request({"client_key": key, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": str(rt)})
    rec = token_record(data, rec)
    store.set(STORE_KEY, rec)
    rotated = data.get("refresh_token") and data.get("refresh_token") != rt
    log.append("틱톡 access token 을 새로 받아 저장했습니다" + (" (refresh token 회전)." if rotated else "."))
    return str(rec["access_token"])


class _Session:
    """Bearer 호출. 토큰이 무효로 나오면 한 번 갱신하고 다시 보낸다."""

    def __init__(self, log: list[str]):
        self.log = log
        self.token = access_token(log)
        self.refreshed = False

    def call(self, url: str, body: dict[str, Any], what: str) -> dict[str, Any]:
        try:
            return _api(url, self.token, body, what)
        except TikTokError as e:
            if e.code == "access_token_invalid" and not self.refreshed:
                self.refreshed = True
                self.token = access_token(self.log, force_refresh=True)
                return _api(url, self.token, body, what)
            raise


def _api(url: str, token: str, body: dict[str, Any], what: str) -> dict[str, Any]:
    try:
        r = requests.post(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=HTTP_TIMEOUT,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    except requests.RequestException as e:
        raise TikTokError(f"{what}: 네트워크 오류 {type(e).__name__}", retryable=True) from None
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(err.get("code") or "")
    if r.ok and code in ("", "ok"):
        return payload.get("data") or {}
    retryable, hint = TT_ERRORS.get(code, (r.status_code >= 500 or r.status_code == 429, "오류 내용을 확인하세요."))
    msg = str(err.get("message") or r.text or "")[:300]
    raise TikTokError(f"{what} 실패 (HTTP {r.status_code}, {code or '-'}): {msg} — {hint} (log_id {err.get('log_id', '-')})",
                      retryable=retryable, code=code)


def choose_privacy(cfg, options: list[str], log: list[str]) -> str:
    """감사 전이면 SELF_ONLY 강제, 감사 후면 설정값이 옵션에 있을 때만 사용."""
    tt = cfg.platforms.tiktok
    if not tt.audited:
        log.append("[경고] 틱톡 앱이 감사 전(platforms.tiktok.audited=false)이라 SELF_ONLY(나만 보기)로 올립니다. "
                   "계정이 비공개여야 할 수 있습니다. 공개 게시는 감사 통과 후 audited: true 로 바꾸세요.")
        if options and "SELF_ONLY" not in options:
            raise PublishError(f"크리에이터 공개 범위 옵션에 SELF_ONLY 가 없습니다({', '.join(options)}).", retryable=False)
        return "SELF_ONLY"
    want = tt.privacy_level
    if not options:
        log.append(f"[경고] privacy_level_options 를 받지 못했습니다 — 설정값 {want} 로 보냅니다.")
        return want
    if want not in options:
        raise PublishError(f"설정한 공개 범위 {want} 를 이 계정에서 쓸 수 없습니다(가능: {', '.join(options)}). "
                           "channel.yaml platforms.tiktok.privacy_level 을 고치세요.", retryable=False)
    return want


# ---------------------------------------------------------------- 발행기

class TikTokPublisher:
    platform = "tiktok"
    kinds = ("short",)

    def publish(self, ctx: PublishContext) -> PublishResult:
        if not ctx.cfg.platforms.tiktok.enabled:
            return PublishResult("skipped", note="platforms.tiktok.enabled=false")
        if ctx.asset.kind != "short":
            raise PublishError(f"틱톡 발행기는 쇼츠만 다룹니다({ctx.asset.kind}).", retryable=False)
        script = ctx.content
        if not isinstance(script, VideoScript):
            raise PublishError(f"{ctx.asset.asset_id}: 쇼츠 원고(VideoScript)가 아닙니다.", retryable=False)
        s = _settings(ctx.cfg)
        dry = ctx.dry_run or env_dry_run()
        title = truncate_utf16(captions.reel_caption(script, ctx.cfg, "tiktok"))
        if dry:
            return self._dry(ctx, s, title)

        video = self._video(ctx)
        size = video.stat().st_size
        chunk, count = chunk_plan(size)
        sess = _Session(ctx.log)
        username, privacy = None, None
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        if s["mode"] == "direct":
            info = sess.call(TT_CREATOR_INFO_URL, {}, "틱톡 크리에이터 정보 조회")
            username = info.get("creator_username")
            ctx.log.append(f"틱톡 계정: {info.get('creator_nickname', '?')} (@{username or '?'})")
            privacy = choose_privacy(ctx.cfg, [str(o) for o in info.get("privacy_level_options") or []], ctx.log)
            max_dur = info.get("max_video_post_duration_sec")
            dur = ctx.asset.duration_seconds
            if max_dur and dur and dur > float(max_dur):
                raise PublishError(f"{ctx.asset.asset_id}: 영상 {dur:.0f}초가 이 계정 최대 {max_dur}초를 넘습니다.", retryable=False)
            post_info: dict[str, Any] = {
                "title": title,
                "privacy_level": privacy,
                "disable_comment": bool(info.get("comment_disabled", False)),
                "disable_duet": s["disable_duet"] or bool(info.get("duet_disabled", False)),
                "disable_stitch": s["disable_stitch"] or bool(info.get("stitch_disabled", False)),
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
            }
            if s["cover_ms"] is not None:
                post_info["video_cover_timestamp_ms"] = s["cover_ms"]
            if TT_SEND_IS_AIGC and s["is_aigc"]:
                post_info["is_aigc"] = True
            body: dict[str, Any] = {"post_info": post_info, "source_info": source}
            init_url = TT_VIDEO_INIT_URL
        else:
            body, init_url = {"source_info": source}, TT_INBOX_INIT_URL
        ctx.log.append(f"POST {init_url} {s['mode']} video_size={size} chunk_size={chunk} total_chunk_count={count}"
                       + (f" privacy_level={privacy}" if privacy else ""))
        data = sess.call(init_url, body, "틱톡 업로드 초기화")
        publish_id, upload_url = str(data.get("publish_id") or ""), str(data.get("upload_url") or "")
        if not (publish_id and upload_url):
            raise TikTokError("틱톡 업로드 초기화 응답에 publish_id·upload_url 이 없습니다.", retryable=True)
        self._upload(upload_url, video, size, chunk, count, ctx.log)
        return self._poll(ctx, sess, publish_id, s["poll_timeout"], s["poll_interval"], username, privacy)

    def check(self, ctx: PublishContext) -> PublishResult:
        publish_id = ctx.target.remote_id
        if not publish_id:
            raise PublishError(f"{ctx.asset.asset_id}: 확인할 publish_id(remote_id)가 없습니다. 다시 발행하세요.", retryable=True)
        if ctx.dry_run or env_dry_run():
            ctx.log.append(f"[dry-run] POST {TT_STATUS_URL} publish_id={publish_id}")
            return PublishResult("published", remote_id=publish_id, note="dry-run")
        sess = _Session(ctx.log)
        privacy = None if ctx.cfg.platforms.tiktok.audited else "SELF_ONLY"
        return self._poll(ctx, sess, publish_id, 0, 0, None, privacy)

    # ---- 내부
    def _video(self, ctx: PublishContext) -> Path:
        try:
            return ctx.file("video")
        except PublishError:
            from studio.publish.storage import ensure_local

            ensure_local(ctx.week, ctx.asset, roles=("video",))
            return ctx.file("video")

    def _upload(self, url: str, video: Path, size: int, chunk: int, count: int, log: list[str]) -> None:
        ctype = _video_type(video)
        host = urlparse(url).netloc
        with open(video, "rb") as fh:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                fh.seek(start)
                data = fh.read(end - start + 1)
                headers = {"Content-Type": ctype, "Content-Range": f"bytes {start}-{end}/{size}"}
                last = ""
                for attempt in range(RETRIES):
                    if attempt:
                        _sleep(min(2 ** attempt, 20))
                    try:
                        r = requests.put(url, data=data, headers=headers, timeout=UPLOAD_TIMEOUT)
                    except requests.RequestException as e:
                        last = f"네트워크 오류 {type(e).__name__}"
                        continue
                    if r.status_code in (200, 201, 206):
                        last = ""
                        break
                    last = f"HTTP {r.status_code} {r.text[:200]}"
                    if r.status_code < 500 and r.status_code != 429:
                        raise TikTokError(f"틱톡 업로드 청크 {i + 1}/{count} 거부({last}) — 청크 규칙(TT_SINGLE_CHUNK_UNDER·TT_CHUNK_SIZE)을 "
                                          "공식 문서와 대조하세요.", retryable=False)
                if last:
                    raise TikTokError(f"틱톡 업로드 청크 {i + 1}/{count} 실패: {last}", retryable=True)
                log.append(f"PUT {host} bytes {start}-{end}/{size} ({i + 1}/{count})")

    def _poll(self, ctx: PublishContext, sess: _Session, publish_id: str, timeout: float, interval: float,
              username: str | None, privacy: str | None) -> PublishResult:
        deadline = time.monotonic() + timeout
        while True:
            d = sess.call(TT_STATUS_URL, {"publish_id": publish_id}, "틱톡 게시 상태 확인")
            st = str(d.get("status") or "").upper()
            if st == "PUBLISH_COMPLETE":
                return self._complete(sess, d, publish_id, username, privacy)
            if st == "SEND_TO_USER_INBOX":
                return PublishResult("published", remote_id=publish_id, extra={"needs_manual_post": True, "publish_id": publish_id},
                                     note="틱톡 앱 받은편지함으로 보냈습니다 — 앱 알림에서 편집·게시하세요(AI 라벨도 앱에서).")
            if st == "FAILED":
                reason = str(d.get("fail_reason") or "unknown")
                retry = reason in TT_FAIL_RETRYABLE
                hint = "다시 시도합니다." if retry else "영상 규격(MP4 H.264, 23~60fps, 길이)·계정 상태를 확인하세요."
                raise TikTokError(f"틱톡 게시 실패: {reason} — {hint}", retryable=retry, code=reason)
            if time.monotonic() >= deadline:
                ctx.log.append(f"틱톡 {publish_id} 처리 중({st or '?'}) — check() 에서 다시 확인합니다.")
                return PublishResult("processing", remote_id=publish_id, note=f"처리 중({st or '?'})",
                                     extra={"publish_id": publish_id, "privacy_level": privacy})
            _sleep(max(interval, 0))

    def _complete(self, sess: _Session, d: dict[str, Any], publish_id: str, username: str | None,
                  privacy: str | None) -> PublishResult:
        ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []   # API 철자 그대로
        post_id = str(ids[0]) if ids else None
        if post_id and not username:
            try:
                username = sess.call(TT_CREATOR_INFO_URL, {}, "틱톡 크리에이터 정보 조회").get("creator_username")
            except TikTokError:
                username = None
        url = TT_POST_URL.format(username=username, post_id=post_id) if post_id and username else None
        note = "나만 보기(SELF_ONLY)로 게시됨 — 감사 통과 후 공개로 바꿀 수 있습니다." if privacy == "SELF_ONLY" else ""
        return PublishResult("published", remote_id=post_id or publish_id, url=url, note=note,
                             extra={"publish_id": publish_id, "privacy_level": privacy})

    def _dry(self, ctx: PublishContext, s: dict[str, Any], title: str) -> PublishResult:
        rel = ctx.asset.files.get("video")
        p = paths.ROOT / rel if rel else None
        size = p.stat().st_size if p and p.exists() else None
        has = bool(env(ENV_REFRESH_TOKEN, required=False))
        privacy = choose_privacy(ctx.cfg, [], ctx.log) if s["mode"] == "direct" else None
        ctx.log.append(f"[dry-run] 틱톡 {s['mode']} — refresh token {'있음' if has else '없음'}")
        ctx.log.append(f"[dry-run] POST {TT_TOKEN_URL} grant_type=refresh_token")
        if s["mode"] == "direct":
            ctx.log.append(f"[dry-run] POST {TT_CREATOR_INFO_URL}")
        plan = "로컬 영상 없음(발행 때 스토리지에서 받음)"
        if size:
            chunk, count = chunk_plan(size)
            plan = f"video_size={size} chunk_size={chunk} total_chunk_count={count}"
        init = TT_VIDEO_INIT_URL if s["mode"] == "direct" else TT_INBOX_INIT_URL
        ctx.log.append(f"[dry-run] POST {init} {plan}" + (f" privacy_level={privacy} title=({len(title)}자)" if privacy else ""))
        ctx.log.append(f"[dry-run] PUT <upload_url> Content-Range … → POST {TT_STATUS_URL}")
        return PublishResult("published", remote_id=f"dry-run-tt-{ctx.asset.asset_id}",
                             note="dry-run: 요청을 보내지 않았습니다" + (" (SELF_ONLY)" if privacy == "SELF_ONLY" else ""))


# ---------------------------------------------------------------- auth tiktok

def _pending_path() -> Path:
    return paths.SECRETS / PENDING_FILE


def _write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)[:64]
    digest = hashlib.sha256(verifier.encode("ascii"))
    challenge = digest.hexdigest() if TT_PKCE_HEX else base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_key: str, redirect_uri: str, state: str, challenge: str | None = None) -> str:
    q = {"client_key": client_key, "scope": ",".join(TT_SCOPES), "response_type": "code",
         "redirect_uri": redirect_uri, "state": state}
    if challenge:
        q.update({"code_challenge": challenge, "code_challenge_method": "S256"})
    return f"{TT_AUTH_URL}?{urlencode(q)}"


def parse_code(value: str) -> tuple[str, str | None]:
    """코드 또는 리디렉트 주소 전체 → (code, state). 주소창에서 복사한 코드는 URL 인코딩되어 있다."""
    value = value.strip()
    if "code=" in value:
        q = parse_qs(urlparse(value).query or value.split("?", 1)[-1])
        return (q.get("code") or [""])[0], (q.get("state") or [None])[0]
    return unquote(value), None


def _redirect(args: argparse.Namespace) -> str | None:
    if args.redirect_uri:
        return args.redirect_uri
    extra = load_config().platforms.tiktok.model_extra or {}
    return extra.get("redirect_uri") or os.environ.get("TIKTOK_REDIRECT_URI") or None


def cmd_auth_tiktok(args: argparse.Namespace) -> int:
    from studio.tokens import token_store

    dry = bool(args.dry_run) or env_dry_run()
    redirect = _redirect(args)
    pending_p = _pending_path()
    if not args.code:
        key = env(ENV_CLIENT_KEY, required=not dry) or f"<{ENV_CLIENT_KEY}>"
        if not redirect:
            print("[오류] 리디렉트 URI 가 없습니다. 틱톡 개발자 포털 → 앱 → Login Kit 에 등록한 주소를 --redirect-uri 로 주세요"
                  "(또는 channel.yaml platforms.tiktok.redirect_uri).", file=sys.stderr)
            return 1
        state = secrets.token_urlsafe(16)
        verifier, challenge = pkce_pair() if args.pkce else (None, None)
        url = authorize_url(key, redirect, state, challenge)
        if not dry:
            _write_private(pending_p, {"state": state, "code_verifier": verifier, "redirect_uri": redirect,
                                       "created_at": now_utc().isoformat(timespec="seconds")})
        print("1) 아래 주소를 브라우저에서 열고 운영할 틱톡 계정으로 동의하세요:\n")
        print(f"   {url}\n")
        print(f"2) 동의 후 이동한 주소({redirect}?code=...&state=...)를 통째로 복사해 다음을 실행하세요:")
        print('   python -m studio auth tiktok --code "<이동한 주소 전체 또는 code 값>"')
        print(f"   (코드는 몇 분 안에 만료됩니다. 요청 범위: {', '.join(TT_SCOPES)})")
        if dry:
            print("[dry-run] state·code_verifier 를 저장하지 않았습니다.")
        return 0

    code, state = parse_code(args.code)
    pending: dict[str, Any] = {}
    if pending_p.exists():
        try:
            pending = json.loads(pending_p.read_text(encoding="utf-8"))
        except ValueError:
            pending = {}
    if state and pending.get("state") and state != pending["state"]:
        print("[오류] state 가 인가 요청 때와 다릅니다(다른 요청의 코드이거나 위조). 1단계부터 다시 하세요.", file=sys.stderr)
        return 1
    redirect = pending.get("redirect_uri") or redirect
    if not code or not redirect:
        print("[오류] code 또는 리디렉트 URI 가 없습니다. 1단계(--code 없이 실행)부터 다시 하세요.", file=sys.stderr)
        return 1
    if dry:
        print(f"[dry-run] POST {TT_TOKEN_URL} grant_type=authorization_code redirect_uri={redirect}"
              + (" code_verifier=(있음)" if pending.get("code_verifier") else ""))
        return 0
    form = {"client_key": str(env(ENV_CLIENT_KEY)), "client_secret": str(env(ENV_CLIENT_SECRET)), "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect}
    if pending.get("code_verifier"):
        form["code_verifier"] = pending["code_verifier"]
    try:
        data = token_request(form)
    except TikTokError as e:
        print(f"[오류] {e} — 코드가 만료됐거나 이미 썼다면 1단계부터 다시 하세요.", file=sys.stderr)
        return 1
    rec = token_record(data)
    rec["seed"] = fingerprint(str(rec["refresh_token"]))
    token_store().set(STORE_KEY, rec)
    pending_p.unlink(missing_ok=True)
    granted = {x.strip() for x in str(data.get("scope") or "").split(",") if x.strip()}
    missing = [x for x in TT_SCOPES if granted and x not in granted]
    print(f"[완료] open_id={data.get('open_id', '?')} 범위={', '.join(sorted(granted)) or '?'} — token_store '{STORE_KEY}' 에 저장했습니다.")
    if missing:
        print(f"[경고] 동의되지 않은 범위: {', '.join(missing)} — 개발자 포털에서 스코프를 추가했는지 확인하세요.")
    try:
        info = _api(TT_CREATOR_INFO_URL, str(data["access_token"]), {}, "크리에이터 정보 조회")
        print(f"계정: {info.get('creator_nickname', '?')} (@{info.get('creator_username', '?')}) "
              f"공개 범위 옵션: {', '.join(info.get('privacy_level_options') or []) or '?'}")
    except TikTokError as e:
        print(f"[참고] 크리에이터 정보 확인 실패({e.code or e}) — 토큰은 저장했습니다.")
    show = (lambda v: v or "(없음)") if args.show else mask
    print("\nGitHub Secrets 에 넣을 값:")
    print(f"  {ENV_REFRESH_TOKEN:<22} = {show(rec['refresh_token'])}")
    print("refresh token 은 갱신 때 회전될 수 있어 token_store(STUDIO_TOKEN_BACKEND) 에 저장합니다. 시크릿을 새 값으로 바꾸면 그 값이 우선합니다.")
    if not args.show:
        print("(전체 값 보기: --show)")
    return 0


def register_auth(g: argparse._SubParsersAction) -> None:
    p = g.add_parser("tiktok", help="틱톡 인가 URL 출력 → --code 로 교환해 refresh token 저장")
    p.add_argument("--code", help="동의 후 리디렉트된 주소 전체 또는 code 값")
    p.add_argument("--redirect-uri", help="개발자 포털에 등록한 리디렉트 URI (없으면 platforms.tiktok.redirect_uri)")
    p.add_argument("--pkce", action="store_true", help="PKCE(code_challenge) 사용 — 데스크톱 앱 유형이면 필요")
    p.add_argument("--show", action="store_true", help="refresh token 을 가리지 않고 출력")
    p.add_argument("--dry-run", action="store_true", help="네트워크·저장 없이 계획만 출력")
    p.set_defaults(func=cmd_auth_tiktok)

"""인스타그램 발행 — 쇼츠는 릴스, 카드뉴스는 캐러셀.

흐름: 컨테이너 만들기 → status_code 폴링(FINISHED) → media_publish → permalink 조회.
최대 poll_timeout_seconds(기본 10분) 안에 FINISHED 가 안 되면 PublishResult(status="processing",
remote_id=<컨테이너 ID>) 를 돌려주고, 발행 오케스트레이터가 나중에 check() 를 부르면 이어서 게시한다.

인스타는 공개 URL 로 파일을 가져간다 → `publish upload-media` 가 먼저 돌아 remote_urls 가 있어야 한다.
게시 시각 예약 기능이 API 에 없으므로 발행 오케스트레이터가 publish_at 이 된 뒤에 publish() 를 부른다.

설정(channel.yaml platforms.instagram, 모두 선택):
  share_reels_to_feed: true
  login: instagram            # instagram = graph.instagram.com(Instagram Login, 기본) | facebook = graph.facebook.com
  is_ai_generated: false      # 사실적인 AI 인물·장면을 쓰지 않으므로 기본 False. 합성 음성(TTS) 고지는 캡션 문구로 한다.
                              # True 로 두면 컨테이너에 is_ai_generated=true 를 보낸다(False 면 아예 안 보냄 — 파라미터 의미 미확인).
  poll_timeout_seconds: 600
  poll_interval_seconds: 15

토큰: token_store().get("instagram") 우선, 없으면 INSTAGRAM_TOKEN. 새 시크릿 값을 넣으면(지문이 달라지면) 시크릿이 우선한다.
Instagram Login 토큰은 발급 50일이 지나면 refresh_access_token 으로 갱신해 token_store 에 저장한다.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from studio.config import MissingSecret, env
from studio.models import CardNews, VideoScript
from studio.publish import captions
from studio.publish.base import PublishContext, PublishError, PublishResult
from studio.publish.meta_auth import (
    ENV_FB_TOKEN,
    ENV_IG_TOKEN,
    ENV_IG_USER,
    FB_GRAPH,
    IG_GRAPH,
    STORE_KEY_FB,
    STORE_KEY_IG,
    GraphError,
    env_dry_run,
    fingerprint,
    graph_call,
    graph_url,
    ig_me,
    ig_refresh,
    ig_token_record,
    now_utc,
    parse_time,
    safe_params,
)
from studio.publish.storage import SLIDE_RE, public_url

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

MEDIA_PATH = "/{user}/media"
PUBLISH_PATH = "/{user}/media_publish"
CONTAINER_FIELDS = "status_code,status"            # [확인-SDK] 필드 이름
MEDIA_FIELDS = "permalink,shortcode,timestamp"
RECENT_FIELDS = "id,caption,permalink,timestamp"
CAROUSEL_MIN, CAROUSEL_MAX = 2, 10                 # [기억·미확인] API 캐러셀 최대 10장
REEL_MIN_SECONDS, REEL_MAX_SECONDS = 3, 900        # [기억·미확인] 3초~15분
CHILD_WAIT_SECONDS = 120                           # 캐러셀 각 장 이미지 처리 대기
TOKEN_REFRESH_AFTER_DAYS = 50                      # 60일 토큰 → 50일 지나면 갱신
TOKEN_MIN_AGE_HOURS = 24                           # [기억·미확인] 발급 24시간 이후에만 갱신 가능
# 컨테이너 ERROR 중 다시 해 볼 만한 것(다운로드 시간 초과·일시 오류) [기억·미확인]
TRANSIENT_ERROR_HINTS = ("2207003", "2207020", "2207032", "2207053", "timeout", "try again")

_sleep = time.sleep   # 테스트에서 바꿔 끼운다


def _settings(cfg) -> dict[str, Any]:
    ig = cfg.platforms.instagram
    extra = ig.model_extra or {}
    login = str(extra.get("login", "instagram")).strip().lower()
    if login not in ("instagram", "facebook"):
        raise PublishError(f"platforms.instagram.login 값 {login!r} 을 알 수 없습니다(instagram | facebook).", retryable=False)
    return {
        "login": login,
        "host": IG_GRAPH if login == "instagram" else FB_GRAPH,
        "share_to_feed": bool(ig.share_reels_to_feed),
        "is_ai_generated": bool(extra.get("is_ai_generated", False)),
        "poll_timeout": float(extra.get("poll_timeout_seconds", 600)),
        "poll_interval": float(extra.get("poll_interval_seconds", 15)),
    }


# ---------------------------------------------------------------- 토큰·계정

def _needs_refresh(rec: dict[str, Any]) -> bool:
    now = now_utc()
    tried = parse_time(rec.get("refresh_attempted_at"))
    if tried and now - tried < timedelta(hours=TOKEN_MIN_AGE_HOURS):
        return False                                   # 하루 한 번만 시도
    issued = parse_time(rec.get("issued_at"))
    if issued is None:
        return True                                    # 발급 시각을 모름(시크릿에서 시작) → 갱신해서 시각을 확정
    age = now - issued
    if age < timedelta(hours=TOKEN_MIN_AGE_HOURS):
        return False
    expires = parse_time(rec.get("expires_at"))
    return age >= timedelta(days=TOKEN_REFRESH_AFTER_DAYS) or bool(expires and expires - now <= timedelta(days=10))


def access_token(login: str, log: list[str] | None = None) -> str:
    """발행에 쓸 토큰. Instagram Login 이면 필요할 때 갱신해 token_store 에 저장한다."""
    from studio.tokens import token_store

    log = log if log is not None else []
    store = token_store()
    env_tok = env(ENV_IG_TOKEN, required=False)
    if login == "facebook":
        # Facebook Login 경로: 시스템 사용자·페이지 토큰(만료 없음)을 쓰므로 갱신하지 않는다.
        # [기억·미확인] 페이지 토큰으로 그 페이지에 연결된 IG 계정에 게시할 수 있다.
        tok = env_tok or env(ENV_FB_TOKEN, required=False) or (store.get(STORE_KEY_FB) or {}).get("page_token")
        if not tok:
            raise PublishError(f"[설정 필요] {ENV_IG_TOKEN} 또는 {ENV_FB_TOKEN} 이 없습니다(`python -m studio auth facebook`).",
                               retryable=False)
        return str(tok)
    rec = dict(store.get(STORE_KEY_IG) or {})
    changed = False
    if env_tok and env_tok != rec.get("access_token") and fingerprint(env_tok) != rec.get("seed"):
        rec = {"access_token": env_tok, "seed": fingerprint(env_tok), "issued_at": None, "login": login}
        changed = True                                 # 새로 넣은 시크릿이 저장된 회전 토큰보다 우선
    tok = rec.get("access_token")
    if not tok:
        raise PublishError(
            f"[설정 필요] 인스타 토큰이 없습니다. `python -m studio auth instagram` 으로 받은 값을 {ENV_IG_TOKEN} 에 넣으세요.",
            retryable=False,
        )
    if _needs_refresh(rec):
        rec["refresh_attempted_at"] = now_utc().isoformat(timespec="seconds")
        changed = True
        try:
            res = ig_refresh(tok)
            rec.update(ig_token_record(str(res["access_token"]), res.get("expires_in")))
            tok = rec["access_token"]
            log.append("인스타 장기 토큰을 갱신해 token_store 에 저장했습니다.")
        except GraphError as e:
            # 발급 24시간 안 된 토큰도 거절될 수 있다(오류 코드 미확인) → 기존 토큰으로 계속하고, 정말 만료됐으면
            # 실제 게시 호출이 code 190 으로 실패해 '다시 발급' 안내(재시도 안 함)가 나간다. 하루 뒤 다시 갱신을 시도한다.
            log.append(f"[경고] 인스타 토큰 갱신을 미뤘습니다(기존 토큰으로 계속): {e}")
    if changed:
        store.set(STORE_KEY_IG, rec)
    return str(tok)


def user_id(login: str, token: str) -> str:
    from studio.tokens import token_store

    uid = env(ENV_IG_USER, required=False)
    if uid:
        return uid
    store = token_store()
    if login == "facebook":
        uid = (store.get(STORE_KEY_FB) or {}).get("instagram_business_account")
    else:
        rec = store.get(STORE_KEY_IG) or {}
        uid = rec.get("user_id")
        if not uid:
            me = ig_me(token)
            uid = me.get("user_id") or me.get("id")
            if uid:
                store.set(STORE_KEY_IG, {**rec, "user_id": str(uid), "username": me.get("username", "")})
    if not uid:
        raise PublishError(f"[설정 필요] {ENV_IG_USER} 가 없습니다. `python -m studio auth instagram` 출력값을 넣으세요.", retryable=False)
    return str(uid)


class _Api:
    def __init__(self, host: str, token: str, user: str, reauth: str):
        self.host, self.token, self.user, self.reauth = host, token, user, reauth

    def get(self, path: str, what: str, **params: Any) -> dict[str, Any]:
        return graph_call("GET", graph_url(self.host, path.format(user=self.user)), token=self.token,
                          params=params, what=what, reauth=self.reauth)

    def post(self, path: str, what: str, data: dict[str, Any]) -> dict[str, Any]:
        return graph_call("POST", graph_url(self.host, path.format(user=self.user)), token=self.token,
                          data=data, what=what, reauth=self.reauth)


def _container_error(cid: str, info: dict[str, Any]) -> PublishError:
    detail = str(info.get("status") or "")[:300]
    retry = any(h in detail.lower() for h in TRANSIENT_ERROR_HINTS)
    hint = ("공개 URL 을 Meta 가 내려받지 못했을 수 있습니다. 다시 시도합니다." if retry else
            "영상·이미지 규격(MP4 H.264/AAC, 9:16, JPEG 이미지 8MB 이하)이나 공개 URL 접근을 확인하세요.")
    return PublishError(f"인스타 컨테이너 {cid} 처리 오류: {detail or 'ERROR'} — {hint}", retryable=retry)


# ---------------------------------------------------------------- 발행기

class InstagramPublisher:
    platform = "instagram"
    kinds = ("short", "cardnews")

    def __init__(self) -> None:
        self._alt_text_ok = True

    # ---- 진입점
    def publish(self, ctx: PublishContext) -> PublishResult:
        if not ctx.cfg.platforms.instagram.enabled:
            return PublishResult("skipped", note="platforms.instagram.enabled=false")
        s = _settings(ctx.cfg)
        dry = ctx.dry_run or env_dry_run()
        if ctx.asset.kind == "short":
            return self._reel(ctx, s, dry)
        if ctx.asset.kind == "cardnews":
            return self._carousel(ctx, s, dry)
        raise PublishError(f"인스타그램 발행기는 {ctx.asset.kind} 를 다루지 않습니다.", retryable=False)

    def check(self, ctx: PublishContext) -> PublishResult:
        cid = ctx.target.remote_id
        if not cid:
            raise PublishError(f"{ctx.asset.asset_id}: 확인할 컨테이너 ID(remote_id)가 없습니다. 다시 발행하세요.", retryable=True)
        if ctx.dry_run or env_dry_run():
            ctx.log.append(f"[dry-run] GET /{cid} fields={CONTAINER_FIELDS} → FINISHED 가정 → media_publish")
            return PublishResult("published", remote_id=f"dry-run-ig-{ctx.asset.asset_id}", note="dry-run")
        s = _settings(ctx.cfg)
        api = self._api(ctx, s)
        code = self._wait(api, cid, 0, 0)
        if code == "FINISHED":
            return self._publish_container(ctx, api, cid)
        if code == "PUBLISHED":
            return self._find_published(ctx, api, cid)
        return PublishResult("processing", remote_id=cid, note=f"컨테이너 처리 중({code})")

    # ---- 릴스
    def _reel(self, ctx: PublishContext, s: dict[str, Any], dry: bool) -> PublishResult:
        script = ctx.content
        if not isinstance(script, VideoScript):
            raise PublishError(f"{ctx.asset.asset_id}: 쇼츠 원고(VideoScript)가 아닙니다.", retryable=False)
        dur = ctx.asset.duration_seconds
        if dur is not None and not (REEL_MIN_SECONDS <= dur <= REEL_MAX_SECONDS):
            raise PublishError(f"{ctx.asset.asset_id}: 릴스 길이 {dur:.1f}초가 허용 범위({REEL_MIN_SECONDS}~{REEL_MAX_SECONDS}초) 밖입니다.",
                               retryable=False)
        data: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": self._url(ctx, "video", dry),
            "caption": captions.reel_caption(script, ctx.cfg, "instagram"),
            "share_to_feed": "true" if s["share_to_feed"] else "false",
        }
        cover = ctx.asset.remote_urls.get("cover_jpg") or ctx.asset.remote_urls.get("cover")
        if cover:
            data["cover_url"] = cover
        if s["is_ai_generated"]:
            data["is_ai_generated"] = "true"
        if dry:
            return self._dry(ctx, s, [("POST", MEDIA_PATH, data)], "릴스")
        api = self._api(ctx, s)
        ctx.log.append(f"POST {MEDIA_PATH} {safe_params(data)}")
        cid = str(api.post(MEDIA_PATH, "릴스 컨테이너 만들기", data)["id"])
        return self._finish(ctx, s, api, cid, "릴스")

    # ---- 캐러셀
    def _carousel(self, ctx: PublishContext, s: dict[str, Any], dry: bool) -> PublishResult:
        card = ctx.content
        if not isinstance(card, CardNews):
            raise PublishError(f"{ctx.asset.asset_id}: 카드뉴스 원고(CardNews)가 아닙니다.", retryable=False)
        roles = sorted(r for r in ctx.asset.files if SLIDE_RE.match(r))
        if not (CAROUSEL_MIN <= len(roles) <= CAROUSEL_MAX):
            raise PublishError(f"{ctx.asset.asset_id}: 캐러셀은 {CAROUSEL_MIN}~{CAROUSEL_MAX}장이어야 합니다(지금 {len(roles)}장).",
                               retryable=False)
        alts = ctx.asset.meta.get("alt_texts") or []
        items = []
        for i, role in enumerate(roles):
            d: dict[str, Any] = {"image_url": self._url(ctx, role, dry), "is_carousel_item": "true"}
            if self._alt_text_ok and i < len(alts) and alts[i]:
                d["alt_text"] = str(alts[i])[:1000]
            items.append(d)
        parent: dict[str, Any] = {"media_type": "CAROUSEL", "children": "<각 장 컨테이너 ID>",
                                  "caption": captions.carousel_caption(card, ctx.cfg)}
        if dry:
            return self._dry(ctx, s, [("POST", MEDIA_PATH, d) for d in items] + [("POST", MEDIA_PATH, parent)], "캐러셀")
        api = self._api(ctx, s)
        children = []
        for i, d in enumerate(items, start=1):
            children.append(self._child(ctx, api, d, i))
        for i, cid in enumerate(children, start=1):
            code = self._wait(api, cid, CHILD_WAIT_SECONDS, s["poll_interval"])
            if code != "FINISHED":
                raise PublishError(f"캐러셀 {i}장 이미지 처리가 {CHILD_WAIT_SECONDS}초 안에 끝나지 않았습니다({code}). 다시 시도합니다.",
                                   retryable=True)
        parent["children"] = ",".join(children)
        ctx.log.append(f"POST {MEDIA_PATH} {safe_params(parent)}")
        pid = str(api.post(MEDIA_PATH, "캐러셀 컨테이너 만들기", parent)["id"])
        return self._finish(ctx, s, api, pid, "캐러셀")

    def _child(self, ctx: PublishContext, api: _Api, d: dict[str, Any], i: int) -> str:
        if not self._alt_text_ok:
            d = {k: v for k, v in d.items() if k != "alt_text"}
        ctx.log.append(f"POST {MEDIA_PATH} {safe_params(d)}")
        try:
            return str(api.post(MEDIA_PATH, f"캐러셀 {i}장 컨테이너 만들기", d)["id"])
        except GraphError as e:
            if "alt_text" in d and e.kind == "invalid":
                # [기억·미확인] 캐러셀 항목이 alt_text 를 받지 않으면 빼고 다시(이 발행기에서는 이후 계속 뺀다)
                self._alt_text_ok = False
                d = {k: v for k, v in d.items() if k != "alt_text"}
                ctx.log.append("[경고] alt_text 를 받지 않아 빼고 다시 만듭니다.")
                return str(api.post(MEDIA_PATH, f"캐러셀 {i}장 컨테이너 만들기", d)["id"])
            raise

    # ---- 공통
    def _api(self, ctx: PublishContext, s: dict[str, Any]) -> _Api:
        try:
            tok = access_token(s["login"], ctx.log)
            uid = user_id(s["login"], tok)
        except MissingSecret as e:
            raise PublishError(f"[설정 필요] {e}", retryable=False) from None
        return _Api(s["host"], tok, uid, "instagram" if s["login"] == "instagram" else "facebook")

    def _url(self, ctx: PublishContext, role: str, dry: bool) -> str:
        url = ctx.asset.remote_urls.get(role)
        if url:
            return url
        if dry:   # 리허설: 업로드했다면 생길 주소
            name = (ctx.asset.files.get(role) or role).rsplit("/", 1)[-1]
            return public_url(ctx.cfg, f"{ctx.week}/{ctx.asset.asset_id}/{name}")
        return ctx.remote_url(role)

    def _wait(self, api: _Api, cid: str, timeout: float, interval: float) -> str:
        """FINISHED·PUBLISHED 면 그 값, 시간 안에 안 끝나면 마지막 상태. ERROR·EXPIRED 는 예외."""
        deadline = time.monotonic() + timeout
        while True:
            info = api.get(f"/{cid}", "컨테이너 상태 확인", fields=CONTAINER_FIELDS)
            code = str(info.get("status_code") or "").upper()
            if code in ("FINISHED", "PUBLISHED"):
                return code
            if code == "ERROR":
                raise _container_error(cid, info)
            if code == "EXPIRED":
                raise PublishError(f"인스타 컨테이너 {cid} 가 만료되었습니다(24시간). 처음부터 다시 발행합니다.", retryable=True)
            if time.monotonic() >= deadline:
                return code or "IN_PROGRESS"
            _sleep(max(interval, 0))

    def _finish(self, ctx: PublishContext, s: dict[str, Any], api: _Api, cid: str, what: str) -> PublishResult:
        code = self._wait(api, cid, s["poll_timeout"], s["poll_interval"])
        if code == "FINISHED":
            return self._publish_container(ctx, api, cid)
        if code == "PUBLISHED":
            return self._find_published(ctx, api, cid)
        ctx.log.append(f"{what} 컨테이너 {cid} 아직 {code} — check() 에서 이어서 게시합니다.")
        return PublishResult("processing", remote_id=cid, note=f"{what} 처리 중({code})", extra={"container_id": cid})

    def _publish_container(self, ctx: PublishContext, api: _Api, cid: str) -> PublishResult:
        ctx.log.append(f"POST {PUBLISH_PATH} creation_id={cid}")
        media_id = str(api.post(PUBLISH_PATH, "인스타 게시", {"creation_id": cid})["id"])
        url = None
        try:
            url = api.get(f"/{media_id}", "게시물 주소 조회", fields=MEDIA_FIELDS).get("permalink")
        except GraphError as e:
            ctx.log.append(f"[경고] 게시는 됐지만 주소 조회 실패: {e}")
        return PublishResult("published", remote_id=media_id, url=url, extra={"container_id": cid})

    def _find_published(self, ctx: PublishContext, api: _Api, cid: str) -> PublishResult:
        """컨테이너가 이미 게시됨(지난 실행이 게시 직후 끊김) → 최근 게시물에서 캡션으로 찾는다."""
        want = captions.reel_caption(ctx.content, ctx.cfg, "instagram") if isinstance(ctx.content, VideoScript) \
            else captions.carousel_caption(ctx.content, ctx.cfg) if isinstance(ctx.content, CardNews) else ""
        try:
            recent = api.get(MEDIA_PATH, "최근 게시물 조회", fields=RECENT_FIELDS, limit=10).get("data") or []
        except GraphError:
            recent = []
        for m in recent:
            if want and (m.get("caption") or "").strip()[:80] == want.strip()[:80]:
                return PublishResult("published", remote_id=str(m["id"]), url=m.get("permalink"), extra={"container_id": cid})
        return PublishResult("published", remote_id=cid, note="이미 게시된 컨테이너입니다(게시물 ID 를 찾지 못해 컨테이너 ID 를 기록).",
                             extra={"container_id": cid})

    def _dry(self, ctx: PublishContext, s: dict[str, Any], reqs: list[tuple[str, str, dict[str, Any]]], what: str) -> PublishResult:
        has_tok = bool(env(ENV_IG_TOKEN, required=False))
        ctx.log.append(f"[dry-run] 인스타 {what} — {s['host']} (로그인 경로 {s['login']}, 토큰 {'있음' if has_tok else '없음'})")
        for method, path, data in reqs:
            ctx.log.append(f"[dry-run] {method} {path} {safe_params(data)}")
        ctx.log.append(f"[dry-run] GET /<container>?fields={CONTAINER_FIELDS} → POST {PUBLISH_PATH} → GET permalink")
        return PublishResult("published", remote_id=f"dry-run-ig-{ctx.asset.asset_id}", note="dry-run: 요청을 보내지 않았습니다")

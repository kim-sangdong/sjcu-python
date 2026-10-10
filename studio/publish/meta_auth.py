"""Meta(인스타그램·페이스북) Graph API 공통 도우미와 토큰 발급 명령.

  python -m studio auth instagram [--token T] [--app-secret S] [--show] [--dry-run]
      단기 토큰 → 장기(60일) 토큰 교환 → 사용자 ID 조회 → token_store("instagram") 저장
      (--app-secret 이 없으면 이미 장기 토큰으로 보고 교환 없이 확인·저장)
  python -m studio auth facebook [--token T] [--app-id I --app-secret S] [--page ID] [--show] [--dry-run]
      사용자 토큰 → 장기 사용자 토큰 → 페이지 토큰(만료 없음) 조회·출력

- 엔드포인트·버전은 아래 상수에 모았다. 조사 자료(docs/research/facts-meta-tiktok-tistory.md)가
  대부분 [기억·미확인] 이므로 공식 문서와 다르면 상수만 고친다.
- 토큰은 로그·예외 메시지에 남기지 않는다(redact).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

from studio.config import MissingSecret, load_env
from studio.publish.base import PublishError

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

GRAPH_VERSION = "v26.0"                          # [확인-SDK] facebook-business 26.0.2(2026-09) 기준
FB_GRAPH = "https://graph.facebook.com"
IG_GRAPH = "https://graph.instagram.com"         # Instagram API with Instagram Login
RUPLOAD = "https://rupload.facebook.com"         # [기억·미확인] 릴스 바이너리 업로드 호스트

IG_EXCHANGE_PATH = "/access_token"               # [기억·미확인] grant_type=ig_exchange_token (버전 없이)
IG_REFRESH_PATH = "/refresh_access_token"        # [기억·미확인] grant_type=ig_refresh_token (버전 없이)
IG_ME_FIELDS = "user_id,username,account_type"   # [기억·미확인] user_id = 게시에 쓰는 프로페셔널 계정 ID
FB_ACCOUNTS_FIELDS = "id,name,access_token,tasks,instagram_business_account{id,username}"

HTTP_TIMEOUT = (10, 60)                          # (연결, 응답) 초
GET_RETRIES = 2                                  # GET 은 네트워크·5xx·일시 오류에서 더 시도한다(POST 는 중복 게시 위험 때문에 안 함)

# Graph 오류 코드 분류 [기억·미확인]
TOKEN_ERROR_CODES = {190, 102, 463, 467}         # 토큰 만료·취소
PERMISSION_ERROR_CODES = {3, 10} | set(range(200, 300))
RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002, 80004}
TRANSIENT_CODES = {1, 2}
IG_PUBLISH_LIMIT_SUBCODE = 2207042               # 24시간 게시 한도 초과

ENV_IG_TOKEN = "INSTAGRAM_TOKEN"
ENV_IG_USER = "INSTAGRAM_USER_ID"
ENV_FB_PAGE = "FACEBOOK_PAGE_ID"
ENV_FB_TOKEN = "FACEBOOK_PAGE_TOKEN"
# 아래 셋은 로컬 `auth` 명령에서만 쓴다(CI 불필요). 인자(--app-id/--app-secret)로 줘도 된다.
ENV_META_APP_ID = "META_APP_ID"
ENV_META_APP_SECRET = "META_APP_SECRET"
ENV_IG_APP_SECRET = "INSTAGRAM_APP_SECRET"       # Instagram Login 의 "Instagram 앱 시크릿"(Meta 앱 시크릿과 다름)

STORE_KEY_IG = "instagram"
STORE_KEY_FB = "facebook"

_sleep = time.sleep   # 테스트에서 바꿔 끼운다


# ---------------------------------------------------------------- 비밀값 표시·지문

def mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return "(없음)"
    if len(value) <= keep * 2 + 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"


def fingerprint(value: str) -> str:
    """토큰 원문 대신 저장하는 짧은 지문(어떤 시크릿에서 시작했는지 구분용)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


_TOKEN_QS = re.compile(r"(access_token|client_secret|fb_exchange_token|input_token)=[^&\s'\"]+")


def redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    text = _TOKEN_QS.sub(r"\1=***", text)
    for s in secrets:
        if s and len(s) >= 8:
            text = text.replace(s, "***")
    return text


def env_dry_run() -> bool:
    return os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- Graph 호출

class GraphError(PublishError):
    """Graph API 오류. retryable 은 오류 코드로 정한다."""

    def __init__(self, message: str, *, retryable: bool, status: int = 0, code: int | None = None,
                 subcode: int | None = None, kind: str = "other"):
        super().__init__(message, retryable=retryable)
        self.status = status
        self.code = code
        self.subcode = subcode
        self.kind = kind          # token | permission | rate_limit | transient | invalid | other


def graph_url(host: str, path: str, *, version: str | None = GRAPH_VERSION) -> str:
    path = path if path.startswith("/") else "/" + path
    return f"{host}/{version}{path}" if version else f"{host}{path}"


def classify(status: int, err: dict[str, Any], *, reauth: str) -> tuple[bool, str, str]:
    """(retryable, kind, 사람이 할 일) — Graph 오류 객체를 해석한다."""
    code = _int(err.get("code"))
    sub = _int(err.get("error_subcode"))
    if code in TOKEN_ERROR_CODES or err.get("type") == "OAuthException" and code in (None, 190):
        return False, "token", f"토큰이 만료·취소되었습니다. `python -m studio auth {reauth}` 로 다시 발급하고 GitHub Secret 을 바꾸세요."
    if code in RATE_LIMIT_CODES or sub == IG_PUBLISH_LIMIT_SUBCODE or (code == 9 and sub):
        return True, "rate_limit", "호출·게시 한도에 걸렸습니다. 다음 실행에서 다시 시도합니다(24시간 한도 확인)."
    if code in PERMISSION_ERROR_CODES:
        return False, "permission", (
            "앱 권한이 없습니다. 앱 대시보드에서 권한(게시 권한)·앱 역할(테스터)·페이지 작업 권한을 확인한 뒤 "
            f"`python -m studio auth {reauth}` 로 토큰을 다시 받으세요."
        )
    if err.get("is_transient") or code in TRANSIENT_CODES or status >= 500:
        return True, "transient", "Meta 서버 일시 오류입니다. 잠시 뒤 다시 시도합니다."
    if code == 100:
        return False, "invalid", "요청 값이 잘못되었습니다(형식·필드·ID 확인). 같은 요청을 다시 보내도 실패합니다."
    if status in (408, 429):
        return True, "rate_limit", "요청이 몰렸습니다. 잠시 뒤 다시 시도합니다."
    return False, "other", "오류 내용을 확인하세요."


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def graph_call(
    method: str,
    url: str,
    *,
    token: str | None,
    what: str,
    reauth: str,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: Any = None,
    token_in: str = "auto",          # auto(GET→query, POST→form) | header(Authorization: OAuth) | none
    timeout: Any = HTTP_TIMEOUT,
) -> dict[str, Any]:
    """Graph API 호출 → JSON. 실패하면 GraphError(retryable 판정 포함). 비밀값은 메시지에서 지운다."""
    method = method.upper()
    params = dict(params or {})
    data = dict(data) if data is not None else None
    headers = dict(headers or {})
    if token:
        if token_in == "header":
            headers["Authorization"] = f"OAuth {token}"
        elif token_in == "auto":
            if method == "GET" or (data is None and body is None):
                params["access_token"] = token
            else:
                data = {**(data or {}), "access_token": token}
    secrets = [token]
    attempts = 1 + (GET_RETRIES if method == "GET" else 0)
    last: GraphError | None = None
    for i in range(attempts):
        if i:
            _sleep(min(2 ** i, 10))
        try:
            r = requests.request(method, url, params=params or None, data=body if body is not None else data,
                                 headers=headers or None, timeout=timeout)
        except requests.RequestException as e:
            last = GraphError(f"{what} 실패: 네트워크 오류 {type(e).__name__}: {redact(str(e), secrets)[:200]}",
                              retryable=True, kind="transient")
            continue
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if r.ok and not (isinstance(payload, dict) and "error" in payload):
            return payload if isinstance(payload, dict) else {"data": payload}
        err = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(err, dict):
            err = {"message": (r.text or "")[:300]}
        retryable, kind, hint = classify(r.status_code, err, reauth=reauth)
        msg = str(err.get("error_user_msg") or err.get("message") or "")
        code, sub = _int(err.get("code")), _int(err.get("error_subcode"))
        last = GraphError(
            redact(f"{what} 실패 (HTTP {r.status_code}, code {code}/{sub}): {msg[:300]} — {hint}", secrets),
            retryable=retryable, status=r.status_code, code=code, subcode=sub, kind=kind,
        )
        if not retryable:
            break
    assert last is not None
    raise last


def safe_params(params: dict[str, Any]) -> str:
    """dry-run·로그용 요청 요약. 토큰은 빼고 긴 글은 글자 수만."""
    out = []
    for k, v in params.items():
        if k in ("access_token", "client_secret", "fb_exchange_token"):
            continue
        s = str(v)
        if k in ("caption", "description", "title") and len(s) > 40:
            s = f"({len(s)}자) {s[:30]}…"
        out.append(f"{k}={s}")
    return " ".join(out)


# ---------------------------------------------------------------- 인스타 토큰 교환·갱신

def ig_exchange(short_token: str, app_secret: str) -> dict[str, Any]:
    """단기(1시간) → 장기(60일). 응답: access_token, token_type, expires_in."""
    return graph_call(
        "GET", graph_url(IG_GRAPH, IG_EXCHANGE_PATH, version=None), token=short_token,
        params={"grant_type": "ig_exchange_token", "client_secret": app_secret},
        what="인스타 장기 토큰 교환", reauth="instagram",
    )


def ig_refresh(long_token: str) -> dict[str, Any]:
    """장기 토큰 갱신(발급 24시간 이후·만료 전). 응답: access_token, token_type, expires_in."""
    return graph_call(
        "GET", graph_url(IG_GRAPH, IG_REFRESH_PATH, version=None), token=long_token,
        params={"grant_type": "ig_refresh_token"},
        what="인스타 토큰 갱신", reauth="instagram",
    )


def ig_me(token: str) -> dict[str, Any]:
    return graph_call("GET", graph_url(IG_GRAPH, "/me"), token=token, params={"fields": IG_ME_FIELDS},
                      what="인스타 계정 조회", reauth="instagram")


def ig_token_record(token: str, expires_in: Any, **extra: Any) -> dict[str, Any]:
    now = now_utc()
    rec: dict[str, Any] = {"access_token": token, "issued_at": now.isoformat(timespec="seconds"), **extra}
    secs = _int(expires_in)
    if secs:
        rec["expires_at"] = (now + timedelta(seconds=secs)).isoformat(timespec="seconds")
    return rec


# ---------------------------------------------------------------- auth instagram | facebook

def _is_dry(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False)) or env_dry_run()


def _read_token(args: argparse.Namespace, prompt: str) -> str:
    tok = (args.token or "").strip()
    if not tok:
        if not sys.stdin.isatty():
            tok = sys.stdin.readline().strip()
        else:
            tok = getpass.getpass(prompt).strip()
    if not tok:
        raise MissingSecret("토큰이 없습니다. --token 으로 주거나 실행 후 붙여넣으세요.")
    return tok


def _secret_arg(value: str | None, env_name: str) -> str | None:
    load_env()
    return value or os.environ.get(env_name) or None


def cmd_auth_instagram(args: argparse.Namespace) -> int:
    from studio.tokens import token_store

    show = (lambda v: v or "(없음)") if args.show else mask
    secret = _secret_arg(args.app_secret, ENV_IG_APP_SECRET)
    if _is_dry(args):
        print("[dry-run] 인스타 토큰을 교환·저장하지 않고 계획만 출력합니다.")
        print(f"  1) {'GET ' + graph_url(IG_GRAPH, IG_EXCHANGE_PATH, version=None) + ' grant_type=ig_exchange_token' if secret else '교환 생략(장기 토큰으로 간주)'}")
        print(f"  2) GET {graph_url(IG_GRAPH, '/me')} fields={IG_ME_FIELDS}")
        print(f"  3) token_store().set('{STORE_KEY_IG}', ...)")
        return 0
    tok = _read_token(args, "인스타 액세스 토큰(앱 대시보드 또는 OAuth 로 받은 값): ")
    expires_in: Any = None
    try:
        if secret:
            res = ig_exchange(tok, secret)
            tok, expires_in = res["access_token"], res.get("expires_in")
            print(f"[1/2] 장기 토큰으로 교환했습니다(유효 {int(expires_in or 0) // 86400}일).")
        else:
            print("[1/2] 앱 시크릿이 없어 교환을 건너뜁니다 — 대시보드에서 받은 장기 토큰이라고 보고 확인합니다.")
        me = ig_me(tok)
    except GraphError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    user_id = str(me.get("user_id") or me.get("id") or "")
    if not user_id:
        print("[오류] 계정 ID 를 받지 못했습니다. 프로페셔널(비즈니스·크리에이터) 계정인지 확인하세요.", file=sys.stderr)
        return 1
    if me.get("account_type") and str(me["account_type"]).upper() not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        print(f"[경고] 계정 유형이 {me['account_type']} 입니다. 게시 API 는 프로페셔널 계정만 쓸 수 있습니다.")
    rec = ig_token_record(tok, expires_in, user_id=user_id, username=me.get("username", ""),
                          login="instagram", seed=fingerprint(tok))
    if not secret:
        rec["issued_at"] = None   # 발급 시각을 모른다 → 다음 발행 때 갱신을 시도한다
    token_store().set(STORE_KEY_IG, rec)
    print(f"[2/2] 계정: @{me.get('username', '?')} (ID {user_id}) — token_store '{STORE_KEY_IG}' 에 저장했습니다.")
    print("\nGitHub Secrets 에 넣을 값:")
    print(f"  {ENV_IG_USER:<20} = {user_id}")
    print(f"  {ENV_IG_TOKEN:<20} = {show(tok)}")
    print("이후 갱신된 토큰은 token_store(STUDIO_TOKEN_BACKEND) 에 저장되며, 시크릿을 새 값으로 바꾸면 그 값이 우선합니다.")
    if not args.show:
        print("(전체 값 보기: --show)")
    return 0


def cmd_auth_facebook(args: argparse.Namespace) -> int:
    from studio.tokens import token_store

    show = (lambda v: v or "(없음)") if args.show else mask
    app_id = _secret_arg(args.app_id, ENV_META_APP_ID)
    secret = _secret_arg(args.app_secret, ENV_META_APP_SECRET)
    if _is_dry(args):
        print("[dry-run] 페이스북 토큰을 교환·조회하지 않고 계획만 출력합니다.")
        if app_id and secret:
            print(f"  1) GET {graph_url(FB_GRAPH, '/oauth/access_token')} grant_type=fb_exchange_token")
        print(f"  2) GET {graph_url(FB_GRAPH, '/me/accounts')} fields={FB_ACCOUNTS_FIELDS}")
        print(f"  3) token_store().set('{STORE_KEY_FB}', ...) 후 FACEBOOK_PAGE_ID/TOKEN 출력")
        return 0
    tok = _read_token(args, "페이스북 사용자 액세스 토큰(Graph API 탐색기 등에서 받은 값): ")
    try:
        if app_id and secret:
            res = graph_call("GET", graph_url(FB_GRAPH, "/oauth/access_token"), token=None,
                             params={"grant_type": "fb_exchange_token", "client_id": app_id,
                                     "client_secret": secret, "fb_exchange_token": tok},
                             what="장기 사용자 토큰 교환", reauth="facebook")
            tok = res["access_token"]
            print("[1/3] 장기 사용자 토큰으로 교환했습니다 — 이 토큰으로 받은 페이지 토큰은 만료되지 않습니다.")
        else:
            print("[1/3] META_APP_ID/META_APP_SECRET 이 없어 교환을 건너뜁니다. 단기 토큰으로 받은 페이지 토큰은 곧 만료됩니다!")
        res = graph_call("GET", graph_url(FB_GRAPH, "/me/accounts"), token=tok,
                         params={"fields": FB_ACCOUNTS_FIELDS, "limit": 100},
                         what="페이지 목록 조회", reauth="facebook")
    except GraphError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    pages = res.get("data") or []
    if args.page:
        pages = [p for p in pages if str(p.get("id")) == str(args.page)]
    if not pages:
        print("[오류] 관리하는 페이지가 없습니다(또는 --page 와 맞는 페이지가 없음). 토큰에 pages_show_list 권한이 있는지 확인하세요.",
              file=sys.stderr)
        return 1
    print(f"[2/3] 페이지 {len(pages)}개:")
    for p in pages:
        tasks = p.get("tasks") or []
        ig = p.get("instagram_business_account") or {}
        print(f"  - {p.get('name')} (ID {p.get('id')}) 작업권한={','.join(tasks) or '?'}"
              + (f" 연결된 인스타 @{ig.get('username', '?')} (ID {ig.get('id')})" if ig else ""))
        if tasks and "CREATE_CONTENT" not in tasks:
            print("    [경고] CREATE_CONTENT 작업 권한이 없어 릴스를 올릴 수 없습니다.")
    page = pages[0]
    if len(pages) > 1 and not args.page:
        print("[안내] 페이지가 여러 개입니다. --page <ID> 로 고르세요. 첫 페이지 기준으로 출력합니다.")
    expires = "확인 안 함(--app-id/--app-secret 필요)"
    if app_id and secret:
        try:
            dbg = graph_call("GET", graph_url(FB_GRAPH, "/debug_token"), token=f"{app_id}|{secret}",
                             params={"input_token": page.get("access_token", "")},
                             what="페이지 토큰 점검", reauth="facebook")
            exp = _int((dbg.get("data") or {}).get("expires_at"))
            expires = "만료 없음" if exp == 0 else (datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp else "?")
        except GraphError as e:
            expires = f"확인 실패({e.kind})"
    ig = page.get("instagram_business_account") or {}
    token_store().set(STORE_KEY_FB, {
        "page_id": str(page.get("id")), "page_name": page.get("name", ""), "page_token": page.get("access_token", ""),
        "instagram_business_account": ig.get("id"), "issued_at": now_utc().isoformat(timespec="seconds"),
        "seed": fingerprint(page.get("access_token", "") or "-"),
    })
    print(f"[3/3] 페이지 토큰 만료: {expires} — token_store '{STORE_KEY_FB}' 에 저장했습니다.")
    print("\nGitHub Secrets 에 넣을 값:")
    print(f"  {ENV_FB_PAGE:<20} = {page.get('id')}")
    print(f"  {ENV_FB_TOKEN:<20} = {show(page.get('access_token'))}")
    if ig:
        print(f"  (Facebook Login 경로로 인스타를 올릴 때) {ENV_IG_USER} = {ig.get('id')}, "
              "channel.yaml platforms.instagram.login: facebook")
    if not args.show:
        print("(전체 값 보기: --show)")
    return 0


def register_auth(g: argparse._SubParsersAction) -> None:
    p = g.add_parser("instagram", help="인스타 단기 토큰 → 장기 토큰 교환·저장, 사용자 ID 조회")
    p.add_argument("--token", help="액세스 토큰(생략하면 붙여넣기 — 셸 기록에 남지 않음)")
    p.add_argument("--app-secret", help=f"Instagram 앱 시크릿(없으면 {ENV_IG_APP_SECRET}; 둘 다 없으면 교환 생략)")
    p.add_argument("--show", action="store_true", help="토큰을 가리지 않고 출력")
    p.add_argument("--dry-run", action="store_true", help="네트워크 없이 계획만 출력")
    p.set_defaults(func=cmd_auth_instagram)
    f = g.add_parser("facebook", help="페이스북 사용자 토큰 → 장기 → 페이지 토큰 조회·출력")
    f.add_argument("--token", help="사용자 액세스 토큰(생략하면 붙여넣기)")
    f.add_argument("--app-id", help=f"Meta 앱 ID(없으면 {ENV_META_APP_ID})")
    f.add_argument("--app-secret", help=f"Meta 앱 시크릿(없으면 {ENV_META_APP_SECRET})")
    f.add_argument("--page", help="페이지 ID(여러 개일 때 선택)")
    f.add_argument("--show", action="store_true", help="토큰을 가리지 않고 출력")
    f.add_argument("--dry-run", action="store_true", help="네트워크 없이 계획만 출력")
    f.set_defaults(func=cmd_auth_facebook)

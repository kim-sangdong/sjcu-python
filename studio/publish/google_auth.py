"""Google(YouTube Data·YouTube Analytics) OAuth 자격 증명과 공통 도우미.

  python -m studio auth youtube [--port 8080] [--no-browser] [--show] [--monetary]

- credentials(scopes): GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN 으로 access token 을 받는다.
  환경변수가 없으면 `auth youtube` 가 저장한 .secrets/youtube_token.json 을 쓴다(로컬 편의).
- 분석 모듈도 credentials() 를 쓴다 — 시그니처를 바꾸지 않는다.
- OAuth 동의 화면이 Testing 상태면 refresh token 이 7일 뒤 만료된다(무인 운영 불가) → Production 전환 필요.
- google-auth-oauthlib 1.5 에는 run_console 이 없다 → run_local_server 만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from studio import paths
from studio.config import MissingSecret, load_env

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"   # 댓글·자막·재생목록·썸네일
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPE_MONETARY = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"  # 수익 지표(YPP 가입 후)
DEFAULT_SCOPES: tuple[str, ...] = (SCOPE_UPLOAD, SCOPE_FORCE_SSL, SCOPE_READONLY, SCOPE_ANALYTICS)

TOKEN_FILE = "youtube_token.json"           # .secrets/ 아래
HTTP_TIMEOUT = 300                          # 초. 업로드 청크 하나가 이 시간 안에 끝나야 한다.
CONSENT_SCREEN_URL = "https://console.cloud.google.com/auth/audience"       # [미확인] 콘솔 메뉴 이름이 바뀔 수 있음
PERMISSIONS_URL = "https://myaccount.google.com/permissions"

ENV_CLIENT_ID = "GOOGLE_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "YOUTUBE_REFRESH_TOKEN"
ENV_CHANNEL_ID = "YOUTUBE_CHANNEL_ID"

TESTING_WARNING = (
    "[주의] OAuth 동의 화면의 게시 상태가 'Testing(테스트)'이면 refresh token 이 발급 7일 뒤 만료되어\n"
    "       일요일 무인 실행이 멈춥니다. Google Cloud Console → Google 인증 플랫폼(OAuth 동의 화면) → 대상(Audience)\n"
    f"       → '앱 게시(Publish app)'로 'In production' 으로 바꾼 뒤 발급하세요: {CONSENT_SCREEN_URL}\n"
    "       본인 계정만 쓰면 Google 검증 없이도 '확인되지 않은 앱' 경고만 뜹니다. Testing 때 받은 토큰은 전환 후 다시 발급하세요."
)
REAUTH_HINT = "`python -m studio auth youtube` 로 다시 발급하고 GitHub Secret YOUTUBE_REFRESH_TOKEN 을 새 값으로 바꿔 주세요."


class GoogleAuthError(MissingSecret):
    """자격 증명 문제(만료·취소·잘못된 클라이언트). 사람이 다시 인증해야 하므로 재시도해도 소용없다.

    MissingSecret 을 상속해 `python -m studio ...` 진입점이 '[설정 필요]' 로 안내하고 종료 코드 3 을 돌려준다.
    """


# ---------------------------------------------------------------- 자격 증명

def token_file_path() -> Path:
    return paths.SECRETS / TOKEN_FILE


def _saved_token() -> dict[str, Any]:
    p = token_file_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _value(env_name: str, saved: dict[str, Any], key: str) -> str | None:
    load_env()
    return os.environ.get(env_name) or saved.get(key) or None


def _client_values() -> tuple[str, str, str]:
    saved = _saved_token()
    cid = _value(ENV_CLIENT_ID, saved, "client_id")
    secret = _value(ENV_CLIENT_SECRET, saved, "client_secret")
    refresh = _value(ENV_REFRESH_TOKEN, saved, "refresh_token")
    missing = [n for n, v in ((ENV_CLIENT_ID, cid), (ENV_CLIENT_SECRET, secret), (ENV_REFRESH_TOKEN, refresh)) if not v]
    if missing:
        raise MissingSecret(
            f"환경변수 {', '.join(missing)} 가 없습니다. .env 또는 GitHub Secrets 에 넣어 주세요 "
            f"(refresh token 은 로컬에서 `python -m studio auth youtube` 로 발급, docs/SETUP.md 참고)."
        )
    return cid, secret, refresh  # type: ignore[return-value]


def explain_refresh_error(err: Exception) -> str:
    """google.auth RefreshError 를 사람이 할 일로 바꾼다. 비밀값은 메시지에 넣지 않는다."""
    text = str(err)
    low = text.lower()
    if "invalid_grant" in low:
        return (
            "YouTube refresh token 이 만료되었거나 취소되었습니다(invalid_grant). "
            "OAuth 동의 화면이 Testing 상태면 발급 7일 뒤 만료됩니다 — Production 으로 바꾼 뒤 "
            + REAUTH_HINT
            + " 6개월 미사용, 계정 비밀번호 변경, 권한 취소도 원인일 수 있습니다."
        )
    if "invalid_client" in low or "unauthorized_client" in low:
        return (
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 이 맞지 않습니다(invalid_client). OAuth 클라이언트 유형이 '데스크톱 앱'인지, "
            "refresh token 을 발급한 클라이언트와 같은 값인지 확인하세요. 클라이언트를 새로 만들었다면 " + REAUTH_HINT
        )
    if "invalid_scope" in low or "scope" in low:
        return "토큰에 요청한 권한(scope)이 없습니다. 필요한 권한으로 " + REAUTH_HINT + f" (요청 범위 오류: {text[:200]})"
    return f"Google 토큰 갱신 실패: {text[:300]} — 계속되면 " + REAUTH_HINT


_CACHE: dict[tuple[str, ...], Any] = {}


def credentials(scopes: Sequence[str] | None = None):
    """refresh 된 google.oauth2.credentials.Credentials. 같은 범위면 프로세스 안에서 재사용한다."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    want = list(scopes or DEFAULT_SCOPES)
    key = tuple(sorted(want))
    cached = _CACHE.get(key)
    if cached is not None and cached.valid:
        return cached
    cid, secret, refresh = _client_values()
    creds = Credentials(
        token=None,
        refresh_token=refresh,
        token_uri=TOKEN_URI,
        client_id=cid,
        client_secret=secret,
        scopes=want,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        if getattr(e, "retryable", False):
            raise  # 일시적인 서버 오류는 호출한 쪽이 재시도
        raise GoogleAuthError(explain_refresh_error(e)) from None
    _CACHE[key] = creds
    return creds


def clear_cache() -> None:
    _CACHE.clear()


def service(api: str, version: str, scopes: Sequence[str] | None = None):
    """googleapiclient 서비스 객체. 패키지에 들어 있는 discovery 문서를 써서 네트워크 조회를 줄인다."""
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    http = google_auth_httplib2.AuthorizedHttp(credentials(scopes), http=httplib2.Http(timeout=HTTP_TIMEOUT))
    return build(api, version, http=http, cache_discovery=False, static_discovery=True)


def youtube_service(scopes: Sequence[str] | None = None):
    """YouTube Data API v3. 테스트는 이 함수를 monkeypatch 해서 HttpMockSequence 서비스를 돌려준다."""
    return service("youtube", "v3", scopes)


# ---------------------------------------------------------------- API 오류 해석

@dataclass
class ApiError:
    status: int
    reason: str
    message: str


def api_error(e: Exception) -> ApiError:
    """googleapiclient HttpError → (HTTP 상태, reason, message). reason 예: quotaExceeded, forbidden."""
    resp = getattr(e, "resp", None)
    status = int(getattr(resp, "status", 0) or 0)
    reason, message = "", ""
    content = getattr(e, "content", b"") or b""
    try:
        data = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or "")
            errs = err.get("errors") or []
            if errs and isinstance(errs[0], dict):
                reason = str(errs[0].get("reason") or "")
            if not reason:
                for d in err.get("details") or []:
                    if isinstance(d, dict) and d.get("reason"):
                        reason = str(d["reason"])
                        break
            reason = reason or str(err.get("status") or "")
        elif isinstance(err, str):
            reason, message = err, str(data.get("error_description") or "")
    except (ValueError, AttributeError, UnicodeDecodeError):
        pass
    if not message:
        message = str(getattr(e, "reason", "") or e)[:300]
    return ApiError(status, reason, message)


# ---------------------------------------------------------------- 비밀값 표시

def mask(value: str | None, keep: int = 4) -> str:
    """앞뒤 몇 글자만 보이게 가린다. 짧은 값은 전부 가린다."""
    if not value:
        return "(없음)"
    if len(value) <= keep * 2 + 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"


def _write_secret_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- auth youtube

def _client_config(client_secrets: str | None) -> dict[str, Any]:
    """InstalledAppFlow 용 클라이언트 설정. 파일(데스크톱 앱 JSON) → 환경변수 순서."""
    if client_secrets:
        p = Path(client_secrets)
        if not p.exists():
            raise MissingSecret(f"클라이언트 JSON 파일이 없습니다: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        if "installed" in data:
            return data
        if "web" in data:
            raise MissingSecret(
                "이 클라이언트 JSON 은 '웹 애플리케이션' 유형입니다. Google Cloud Console → 사용자 인증 정보에서 "
                "OAuth 클라이언트 ID 를 '데스크톱 앱' 유형으로 만들어 그 JSON 을 쓰세요."
            )
        raise MissingSecret(f"클라이언트 JSON 형식을 알 수 없습니다: {p} ('installed' 키가 있어야 합니다)")
    load_env()
    cid = os.environ.get(ENV_CLIENT_ID)
    secret = os.environ.get(ENV_CLIENT_SECRET)
    if not cid or not secret:
        raise MissingSecret(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 이 없습니다. .env 에 넣거나 "
            "--client-secrets <데스크톱 앱 클라이언트 JSON 경로> 를 주세요 (docs/SETUP.md 참고)."
        )
    return {
        "installed": {
            "client_id": cid,
            "client_secret": secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


def _channel_info(creds) -> tuple[str, str] | None:
    """발급한 토큰이 어느 채널 것인지 확인(channels.list mine=true, 1 unit)."""
    from googleapiclient.discovery import build

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False, static_discovery=True)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        return None
    return items[0]["id"], items[0]["snippet"].get("title", "")


def _is_dry(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False)) or os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def cmd_auth_youtube(args: argparse.Namespace) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = list(DEFAULT_SCOPES) + ([SCOPE_MONETARY] if args.monetary else [])
    out = Path(args.out) if args.out else token_file_path()
    config = _client_config(args.client_secrets)
    cid = config["installed"]["client_id"]
    secret = config["installed"]["client_secret"]
    show = (lambda v: v or "(없음)") if args.show else mask

    if _is_dry(args):
        print("[dry-run] YouTube OAuth 동의를 받지 않고 계획만 출력합니다.")
        print(f"  클라이언트 ID: {cid}")
        print(f"  범위: {', '.join(scopes)}")
        print(f"  리디렉트: http://{args.host}:{args.port}/   저장 위치: {out}")
        return 0

    print(TESTING_WARNING)
    if args.no_browser:
        print(
            "\n아래 URL 을 브라우저에서 여세요. 이 컴퓨터가 원격 서버라면 먼저 내 PC 에서\n"
            f"  ssh -L {args.port}:localhost:{args.port} <서버>\n"
            "로 포트를 연결해야 동의 후 리디렉트가 이 프로그램에 도착합니다."
        )
    flow = InstalledAppFlow.from_client_config(config, scopes=scopes)
    try:
        creds = flow.run_local_server(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            authorization_prompt_message="\n동의 URL: {url}\n",
            success_message="인증이 끝났습니다. 이 창을 닫고 터미널로 돌아가세요.",
            timeout_seconds=args.timeout,
            prompt="consent",        # refresh token 을 확실히 다시 받는다
            access_type="offline",
        )
    except OSError as e:
        print(f"[오류] 로컬 리디렉트 서버를 열지 못했습니다({e}). --port 로 다른 포트를 지정하세요.", file=sys.stderr)
        return 1
    except Exception as e:  # oauthlib: access_denied, mismatching_state, 시간 초과 등
        print(f"[오류] 동의를 완료하지 못했습니다: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        return 1
    if not creds.refresh_token:
        print(
            "[오류] refresh token 을 받지 못했습니다. "
            f"{PERMISSIONS_URL} 에서 이 앱의 접근 권한을 삭제한 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        return 1
    granted = list(getattr(creds, "granted_scopes", None) or creds.scopes or scopes)
    missing = sorted(set(scopes) - set(granted))
    if missing:
        print(f"[경고] 동의 화면에서 일부 권한이 빠졌습니다: {', '.join(missing)} — 업로드·댓글·분석이 실패할 수 있습니다. 모두 체크하고 다시 실행하세요.")
    _write_secret_json(
        out,
        {
            "type": "authorized_user",
            "client_id": cid,
            "client_secret": secret,
            "refresh_token": creds.refresh_token,
            "token_uri": TOKEN_URI,
            "scopes": sorted(granted),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    clear_cache()
    channel_id, channel_title = "", ""
    try:
        info = _channel_info(creds)
        if info:
            channel_id, channel_title = info
        else:
            print("[경고] 이 계정에 유튜브 채널이 없습니다. 채널을 가진(소유한) 계정으로 다시 동의하세요.")
    except Exception as e:  # 확인 실패는 발급 자체를 막지 않는다
        print(f"[경고] 채널 확인 실패({type(e).__name__}) — 토큰은 저장했습니다.")

    try:
        shown = out.relative_to(paths.ROOT)
    except ValueError:
        shown = out
    print(f"\n[완료] refresh token 을 {shown} 에 저장했습니다(권한 600, 커밋 금지).")
    if channel_id:
        print(f"채널: {channel_title} ({channel_id})")
    print("\nGitHub Secrets 에 넣을 값 (저장소 → Settings → Secrets and variables → Actions):")
    print(f"  {ENV_CLIENT_ID:<22} = {cid}")
    print(f"  {ENV_CLIENT_SECRET:<22} = {show(secret)}")
    print(f"  {ENV_REFRESH_TOKEN:<22} = {show(creds.refresh_token)}")
    print(f"  {ENV_CHANNEL_ID:<22} = {channel_id or '(채널 ID: YouTube Studio → 설정 → 채널 → 고급 설정)'}")
    if not args.show:
        print("  (가린 값 전체 보기: --show. 화면에 찍지 않고 바로 넣기:)")
        print(
            f"  python -c \"import json;print(json.load(open('{shown}'))['refresh_token'],end='')\" "
            f"| gh secret set {ENV_REFRESH_TOKEN}"
        )
    print("\n" + TESTING_WARNING)
    return 0


def register_auth(g: argparse._SubParsersAction) -> None:
    p = g.add_parser("youtube", help="YouTube·YouTube Analytics OAuth 동의 → refresh token 발급")
    p.add_argument("--client-secrets", help="OAuth 클라이언트(데스크톱 앱) JSON 경로. 없으면 GOOGLE_CLIENT_ID/SECRET 사용")
    p.add_argument("--port", type=int, default=8080, help="로컬 리디렉트 서버 포트 (0 = 빈 포트 자동, 기본 8080)")
    p.add_argument("--host", default="localhost", help="리디렉트 호스트 (기본 localhost)")
    p.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않고 동의 URL 만 출력")
    p.add_argument("--show", action="store_true", help="refresh token·client secret 을 가리지 않고 출력")
    p.add_argument("--monetary", action="store_true", help="수익 지표용 yt-analytics-monetary.readonly 범위 추가")
    p.add_argument("--out", help=f"저장 위치 (기본 .secrets/{TOKEN_FILE})")
    p.add_argument("--timeout", type=int, default=600, help="동의 대기 시간(초)")
    p.add_argument("--dry-run", action="store_true", help="동의를 받지 않고 계획만 출력")
    p.set_defaults(func=cmd_auth_youtube)

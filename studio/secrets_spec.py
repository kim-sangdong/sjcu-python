"""비밀값(환경변수) 목록과 점검.

- SECRET_SPECS: 플랫폼·용도별로 필요한 환경변수. 이름은 모든 모듈·.env.example·docs/SETUP.md·.github/workflows 가
  함께 쓰는 계약이다. 새 비밀값이 생기면 여기에 먼저 넣고 `.env.example` 을 다시 만든다.
- check_secrets(cfg): channel.yaml 에서 켜진 플랫폼 기준으로 "필수인데 없는 값 + 형식이 틀린 값" 개수를 돌려준다.
  값은 절대 출력하지 않는다(있다/없다/형식 오류만). `python -m studio doctor` 가 부른다.

단독 실행:
  python -m studio.secrets_spec                 점검(doctor 의 비밀값 부분만)
  python -m studio.secrets_spec --github        GitHub Secrets 에 등록할 이름 목록(켜진 플랫폼 기준)
  python -m studio.secrets_spec --env-example   .env.example 내용 출력(이 목록에서 만든다)
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TextIO

from studio import paths
from studio.config import Config, load_config, load_env

SETUP_DOC = "docs/SETUP.md"

Scope = Literal["runtime", "local", "github", "function"]
# runtime : `python -m studio` 가 도는 곳(로컬·GitHub Actions)에서 읽는다 → 점검 대상
# local   : 로컬 인증 명령(auth ...)에서만 쓴다 → 점검하지 않고 안내만
# github  : GitHub Actions 단계(claude-code-action, EAS)만 쓴다 → GitHub Secrets 에만 등록
# function: Supabase Edge Function 비밀값(`supabase secrets set`) → 여기서는 확인할 수 없음


@dataclass(frozen=True)
class Fallback:
    """환경변수가 없을 때 모듈이 대신 읽는 로컬 저장소. 있는지만 보고 값은 출력하지 않는다."""

    kind: Literal["file", "store"]
    name: str                 # file: .secrets/ 아래 파일 이름 · store: token_store 키
    field: str | None = None  # JSON 안의 키(없으면 파일이 있기만 하면 된다)

    def label(self) -> str:
        if self.kind == "file":
            return f".secrets/{self.name}"
        return f"토큰 보관소 '{self.name}'"


@dataclass(frozen=True)
class SecretSpec:
    name: str
    group: str
    required: bool
    description: str
    anchor: str                                  # docs/SETUP.md 의 <a id="..."> — 발급 안내
    scope: Scope = "runtime"
    any_of: tuple[str, ...] = ()                 # 이 이름들 중 하나만 있으면 된다(자기 이름 포함)
    fallbacks: tuple[Fallback, ...] = ()
    validate: Callable[[str], str | None] | None = None   # 형식 오류 메시지(값을 넣지 않는다) 또는 None
    github: bool = True                          # GitHub Secrets 에 등록하는 값인지
    secret: bool = True                          # False 면 일반 설정값

    @property
    def doc(self) -> str:
        return f"{SETUP_DOC}#{self.anchor}"


# ---------------------------------------------------------------- 형식 검사 (메시지에 값을 넣지 않는다)

def _jwt_role(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        role = data.get("role") if isinstance(data, dict) else None
        return str(role) if role else None
    except (IndexError, ValueError, binascii.Error):
        return None


def _v_https_url(v: str) -> str | None:
    if v.startswith("https://") or re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", v):
        return None
    return "https:// 로 시작하는 주소여야 합니다(예: https://<project-ref>.supabase.co)."


def _v_service_key(v: str) -> str | None:
    if v.startswith("sb_publishable_"):
        return "공개(publishable) 키입니다. 서버 비밀 키(sb_secret_… 또는 service_role)를 넣으세요."
    if v.startswith("sb_secret_"):
        return None
    if v.startswith("eyJ"):
        role = _jwt_role(v)
        if role and role != "service_role":
            return f"JWT 의 role 이 '{role}' 입니다. Legacy API Keys 의 service_role 키를 넣으세요."
        return None
    return "service_role(JWT, eyJ…) 또는 sb_secret_… 형식이 아닙니다."


def _v_anon_key(v: str) -> str | None:
    if v.startswith("sb_secret_") or (v.startswith("eyJ") and _jwt_role(v) == "service_role"):
        return "공개 키 자리에 서버 비밀 키가 들어 있습니다 — 앱에 넣으면 DB 전체가 노출됩니다. anon/publishable 키로 바꾸세요."
    if v.startswith(("sb_publishable_", "eyJ")):
        return None
    return "anon(JWT, eyJ…) 또는 sb_publishable_… 형식이 아닙니다."


def _v_file_exists(v: str) -> str | None:
    p = Path(v).expanduser()
    if not p.is_absolute():
        p = paths.ROOT / p
    return None if p.is_file() else "가리키는 파일이 없습니다(경로를 확인하세요)."


def _v_service_account_json(v: str) -> str | None:
    try:
        data = json.loads(v)
    except ValueError:
        return "JSON 이 아닙니다. 서비스 계정 키 파일 내용 전체를 그대로 넣으세요."
    if not isinstance(data, dict) or data.get("type") != "service_account":
        return "서비스 계정 키(JSON 의 type 이 service_account)가 아닙니다."
    return None


def _v_storage_state_b64(v: str) -> str | None:
    try:
        data = json.loads(base64.b64decode(v.strip(), validate=False).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return "base64 로 인코딩한 Playwright storageState JSON 이 아닙니다(`python -m studio auth tistory` 출력값을 넣으세요)."
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        return "storageState 에 cookies 목록이 없습니다. `python -m studio auth tistory` 로 다시 만드세요."
    return None


def _v_digits(v: str) -> str | None:
    return None if re.fullmatch(r"\d{5,25}", v) else "숫자로 된 ID 여야 합니다."


def _v_channel_id(v: str) -> str | None:
    return None if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", v) else "UC 로 시작하는 24자 채널 ID 여야 합니다."


def _v_backend(v: str) -> str | None:
    return None if v in ("file", "supabase") else "file 또는 supabase 여야 합니다."


def _v_flag(v: str) -> str | None:
    return None if v.strip().lower() in ("", "0", "1", "true", "false", "yes", "no") else "1 또는 0 이어야 합니다."


# ---------------------------------------------------------------- 목록

GROUP_TITLES: dict[str, str] = {
    "supabase": "Supabase (미디어 저장·회전 토큰 보관·클릭/설치/결제 조회)",
    "youtube": "YouTube (업로드·댓글·분석)",
    "tts": "Google Cloud TTS (내레이션 음성)",
    "instagram": "Instagram (릴스·카드뉴스)",
    "facebook": "Facebook 페이지 (릴스)",
    "tiktok": "TikTok (Content Posting API)",
    "tistory": "티스토리 (Playwright 세션)",
    "law": "국가법령정보 API (문제은행 법령 대조)",
    "app": "앱·결제 (RevenueCat·추적 링크)",
    "ci": "GitHub Actions 전용",
    "settings": "실행 설정",
}

_YT_FILE = "youtube_token.json"   # studio.publish.google_auth.TOKEN_FILE

SECRET_SPECS: tuple[SecretSpec, ...] = (
    # Supabase
    SecretSpec("SUPABASE_URL", "supabase", True, "Supabase 프로젝트 주소(https://<project-ref>.supabase.co)", "supabase",
               validate=_v_https_url),
    SecretSpec("SUPABASE_SERVICE_ROLE_KEY", "supabase", True,
               "서버 전용 비밀 키(service_role 또는 sb_secret_…) — 앱·공개 저장소에 절대 넣지 않는다", "supabase",
               validate=_v_service_key),
    SecretSpec("SUPABASE_ANON_KEY", "supabase", False, "공개(anon·publishable) 키 — 앱 빌드용, 파이썬 도구는 쓰지 않는다", "supabase",
               validate=_v_anon_key),
    # YouTube
    SecretSpec("GOOGLE_CLIENT_ID", "youtube", True, "OAuth 클라이언트 ID(유형: 데스크톱 앱)", "youtube-oauth",
               fallbacks=(Fallback("file", _YT_FILE, "client_id"),)),
    SecretSpec("GOOGLE_CLIENT_SECRET", "youtube", True, "OAuth 클라이언트 보안 비밀번호", "youtube-oauth",
               fallbacks=(Fallback("file", _YT_FILE, "client_secret"),)),
    SecretSpec("YOUTUBE_REFRESH_TOKEN", "youtube", True, "`python -m studio auth youtube` 가 출력하는 refresh token", "youtube-oauth",
               fallbacks=(Fallback("file", _YT_FILE, "refresh_token"),)),
    SecretSpec("YOUTUBE_CHANNEL_ID", "youtube", False, "채널 ID(UC…) — 없으면 토큰으로 조회한다", "youtube-oauth",
               validate=_v_channel_id),
    # Cloud TTS — 로컬은 파일 경로, CI 는 JSON 내용
    SecretSpec("GOOGLE_APPLICATION_CREDENTIALS", "tts", True, "서비스 계정 키 JSON 파일 경로(로컬용)", "tts",
               any_of=("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_TTS_CREDENTIALS_JSON"), validate=_v_file_exists, github=False),
    SecretSpec("GOOGLE_TTS_CREDENTIALS_JSON", "tts", True, "서비스 계정 키 JSON 내용 전체(CI용 — 워크플로가 파일로 바꿔 쓴다)", "tts",
               any_of=("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_TTS_CREDENTIALS_JSON"), validate=_v_service_account_json),
    # Instagram
    SecretSpec("INSTAGRAM_TOKEN", "instagram", True, "인스타 장기 액세스 토큰(최초 1회 — 이후 토큰 보관소 'instagram' 에서 회전)", "instagram",
               fallbacks=(Fallback("store", "instagram", "access_token"),)),
    SecretSpec("INSTAGRAM_USER_ID", "instagram", False, "인스타 프로페셔널 계정 ID — 없으면 토큰으로 조회한다", "instagram",
               fallbacks=(Fallback("store", "instagram", "user_id"),), validate=_v_digits),
    SecretSpec("INSTAGRAM_APP_SECRET", "instagram", False, "Instagram 앱 시크릿 — `auth instagram` 의 단기→장기 토큰 교환에만", "instagram",
               scope="local", github=False),
    # Facebook
    SecretSpec("FACEBOOK_PAGE_ID", "facebook", True, "페이스북 페이지 ID", "facebook",
               fallbacks=(Fallback("store", "facebook", "page_id"),), validate=_v_digits),
    SecretSpec("FACEBOOK_PAGE_TOKEN", "facebook", True, "페이지 액세스 토큰(장기 사용자 토큰에서 받은 만료 없는 토큰)", "facebook",
               fallbacks=(Fallback("store", "facebook", "page_token"),)),
    SecretSpec("META_APP_ID", "facebook", False, "Meta 앱 ID — `auth facebook` 의 장기 토큰 교환에만", "meta-app",
               scope="local", github=False),
    SecretSpec("META_APP_SECRET", "facebook", False, "Meta 앱 시크릿 — `auth facebook` 의 장기 토큰 교환에만", "meta-app",
               scope="local", github=False),
    # TikTok
    SecretSpec("TIKTOK_CLIENT_KEY", "tiktok", True, "틱톡 개발자 앱 Client key", "tiktok"),
    SecretSpec("TIKTOK_CLIENT_SECRET", "tiktok", True, "틱톡 개발자 앱 Client secret", "tiktok"),
    SecretSpec("TIKTOK_REFRESH_TOKEN", "tiktok", True, "`auth tiktok` 이 받은 refresh token(최초 1회 — 이후 토큰 보관소 'tiktok' 에서 회전)",
               "tiktok", fallbacks=(Fallback("store", "tiktok", "refresh_token"),)),
    SecretSpec("TIKTOK_REDIRECT_URI", "tiktok", False, "`auth tiktok` 리디렉트 URI(개발자 포털에 등록한 값)", "tiktok",
               scope="local", github=False, secret=False),
    # 티스토리
    SecretSpec("TISTORY_STORAGE_STATE_B64", "tistory", True, "`auth tistory` 가 만든 로그인 세션(storageState JSON 의 base64)", "tistory",
               fallbacks=(Fallback("file", "tistory_state.json"),), validate=_v_storage_state_b64),
    # 법령 API
    SecretSpec("LAW_API_OC", "law", True, "국가법령정보 공동활용 OC 값(가입 이메일의 @ 앞부분) — 문제은행 법령 원문 대조", "law-api"),
    # 앱·결제
    SecretSpec("REVENUECAT_SECRET_KEY", "app", False, "RevenueCat REST v2 비밀 키 — 매출 지표 직접 조회용(지금은 웹훅으로 충분)", "revenuecat"),
    SecretSpec("REVENUECAT_WEBHOOK_AUTH", "app", True, "RevenueCat 웹훅 Authorization 헤더 값(대시보드와 함수에 같은 값)", "revenuecat",
               scope="function", github=False),
    SecretSpec("IP_HASH_SALT", "app", True, "클릭 IP 를 해시할 때 섞는 임의 문자열(원래 IP 는 저장하지 않는다)", "supabase-functions",
               scope="function", github=False),
    SecretSpec("ANDROID_PACKAGE", "app", False, "안드로이드 패키지명(예: kr.example.app) — Play 스토어로 보낼 때", "supabase-functions",
               scope="function", github=False, secret=False),
    SecretSpec("IOS_APP_ID", "app", False, "App Store 앱 ID(숫자) — App Store 로 보낼 때", "supabase-functions",
               scope="function", github=False, secret=False),
    SecretSpec("APPLE_PROVIDER_TOKEN", "app", False, "App Store Connect 캠페인 링크의 pt 값(숫자)", "supabase-functions",
               scope="function", github=False, secret=False),
    SecretSpec("LANDING_URL", "app", False, "PC 등 스토어가 없는 기기에서 보낼 소개 페이지(https)", "supabase-functions",
               scope="function", github=False, secret=False),
    # GitHub Actions 전용
    SecretSpec("ANTHROPIC_API_KEY", "ci", True, "Claude API 키(Console, 종량 과금) — 둘 중 하나만 등록", "claude",
               scope="github", any_of=("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")),
    SecretSpec("CLAUDE_CODE_OAUTH_TOKEN", "ci", True, "Claude 구독 토큰(`claude setup-token`) — 둘 중 하나만 등록", "claude",
               scope="github", any_of=("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")),
    SecretSpec("EXPO_TOKEN", "ci", False, "Expo 액세스 토큰 — 앱 배포(/deploy)용", "expo", scope="github"),
    # 실행 설정
    SecretSpec("STUDIO_TOKEN_BACKEND", "settings", False, "회전 토큰 보관 위치: file(로컬 .secrets) | supabase(CI 는 반드시 supabase)",
               "github-secrets", validate=_v_backend, github=False, secret=False),
    SecretSpec("STUDIO_DRY_RUN", "settings", False, "1 이면 리허설 — 업로드·유료 호출 없이 보낼 요청만 기록", "rehearsal",
               validate=_v_flag, github=False, secret=False),
)

SPECS_BY_NAME: dict[str, SecretSpec] = {s.name: s for s in SECRET_SPECS}


# ---------------------------------------------------------------- 어떤 묶음이 켜져 있나

def instagram_login(cfg: Config) -> str:
    return str((cfg.platforms.instagram.model_extra or {}).get("login", "instagram")).strip().lower()


def active_groups(cfg: Config, environ: Mapping[str, str] | None = None) -> list[str]:
    environ = os.environ if environ is None else environ
    p = cfg.platforms
    out: list[str] = []
    if cfg.storage.provider == "supabase" or environ.get("STUDIO_TOKEN_BACKEND", "").strip() == "supabase":
        out.append("supabase")
    for name in ("youtube", "instagram", "facebook", "tiktok", "tistory"):
        if getattr(p, name).enabled:
            out.append(name)
    if "google" in (cfg.tts.provider, cfg.tts.fallback):
        out.append("tts")
    if (cfg.model_extra or {}).get("exam"):
        out.append("law")
    out += ["app", "ci", "settings"]
    return out


# ---------------------------------------------------------------- 점검

@dataclass
class _Line:
    mark: str          # O 있음 · X 필수 누락 · ! 형식 오류 · - 선택 없음 · ? 확인 불가
    text: str


def _present(environ: Mapping[str, str], name: str) -> bool:
    return bool((environ.get(name) or "").strip())


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _fallback_state(fb: Fallback, secrets_dir: Path, backend: str) -> bool | None:
    """True=있음, False=없음, None=확인 불가(Supabase 보관소는 오프라인 점검에서 열지 않는다)."""
    if fb.kind == "file":
        p = secrets_dir / fb.name
        if not p.is_file():
            return False
        if fb.field is None:
            return True
        data = _load_json(p)
        return bool(data and data.get(fb.field))
    if backend == "supabase":
        return None
    data = _load_json(secrets_dir / "tokens.json") or {}
    rec = data.get(fb.name)
    return bool(isinstance(rec, dict) and (rec.get(fb.field) if fb.field else rec))


def _alternatives(spec: SecretSpec, cfg: Config) -> tuple[str, ...]:
    names = spec.any_of or (spec.name,)
    if spec.name == "INSTAGRAM_TOKEN" and instagram_login(cfg) == "facebook":
        names = (*names, "FACEBOOK_PAGE_TOKEN")    # Facebook Login 경로는 페이지 토큰으로 게시한다
    return names


def _check_group(specs: list[SecretSpec], cfg: Config, environ: Mapping[str, str], secrets_dir: Path,
                 backend: str) -> tuple[list[_Line], int, int]:
    lines: list[_Line] = []
    missing = invalid = 0
    done: set[str] = set()
    for spec in specs:
        if spec.scope != "runtime" or spec.name in done:
            continue
        names = _alternatives(spec, cfg)
        done.update(names)
        label = " 또는 ".join(names)
        desc = " / ".join(dict.fromkeys(SPECS_BY_NAME[n].description for n in names if n in SPECS_BY_NAME))
        present = [n for n in names if _present(environ, n)]
        # 형식 검사(있는 값만). 형식이 틀린 값은 [!] 로만 보이고 [O] 로 치지 않는다.
        valid = []
        for n in present:
            s = SPECS_BY_NAME.get(n)
            msg = s.validate(environ[n].strip()) if s and s.validate else None
            if msg:
                invalid += 1
                lines.append(_Line("!", f"{n} — {msg} → {spec.doc}"))
            else:
                valid.append(n)
        if present:
            if valid:
                lines.append(_Line("O", label))
            continue
        states = [(fb, _fallback_state(fb, secrets_dir, backend)) for fb in spec.fallbacks]
        found = [fb for fb, st in states if st]
        unknown = [fb for fb, st in states if st is None]
        if found:
            lines.append(_Line("O", f"{label} ({found[0].label()} 에 저장된 값 사용)"))
        elif not spec.required:
            lines.append(_Line("-", f"{label} (선택) — {desc}"))
        elif unknown:
            lines.append(_Line("?", f"{label} — 환경변수 없음. {unknown[0].label()}(Supabase)에 있으면 괜찮습니다(오프라인 점검이라 확인 안 함)"))
        else:
            missing += 1
            lines.append(_Line("X", f"{label} — {desc} → {spec.doc}"))
    return lines, missing, invalid


def check_secrets(
    cfg: Config | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    secrets_dir: Path | None = None,
) -> int:
    """켜진 플랫폼 기준 비밀값 점검. 돌려주는 값 = 필수 누락 + 형식 오류 개수. 값은 출력하지 않는다."""
    if environ is None:
        load_env()
        environ = os.environ
    cfg = cfg or load_config()
    out = out or sys.stdout
    secrets_dir = secrets_dir or paths.SECRETS
    backend = (environ.get("STUDIO_TOKEN_BACKEND") or "file").strip() or "file"
    groups = active_groups(cfg, environ)

    def say(text: str = "") -> None:
        print(text, file=out)

    say(f"── 비밀값 점검 (켜진 묶음: {', '.join(GROUP_TITLES[g].split(' (')[0] for g in groups if g not in ('ci', 'settings'))})")
    total_missing = total_invalid = 0
    for g in groups:
        specs = [s for s in SECRET_SPECS if s.group == g]
        lines, missing, invalid = _check_group(specs, cfg, environ, secrets_dir, backend)
        if not lines:
            continue
        say(f"[{GROUP_TITLES[g]}]")
        for ln in lines:
            say(f"  [{ln.mark}] {ln.text}")
        total_missing += missing
        total_invalid += invalid

    in_ci = (environ.get("GITHUB_ACTIONS") or "").lower() == "true"
    rotating = [g for g in ("instagram", "tiktok") if g in groups]
    if in_ci and rotating and backend != "supabase":
        total_invalid += 1
        say(f"  [!] STUDIO_TOKEN_BACKEND — GitHub Actions 에서는 supabase 여야 회전 토큰({', '.join(rotating)})이 다음 실행에 남습니다 "
            f"→ {SETUP_DOC}#github-secrets")

    off = [GROUP_TITLES[n].split(" (")[0] for n in ("youtube", "instagram", "facebook", "tiktok", "tistory") if n not in groups]
    if off:
        say(f"  (꺼진 플랫폼 — 점검 안 함: {', '.join(off)})")
    fn = [s.name for s in SECRET_SPECS if s.scope == "function"]
    say(f"  (Supabase 함수 비밀값은 여기서 확인할 수 없음 — `supabase secrets list`: {', '.join(fn)})")
    gh = sorted({" 또는 ".join(s.any_of) if s.any_of else s.name for s in SECRET_SPECS if s.scope == "github"})
    say(f"  (GitHub Secrets 전용: {', '.join(gh)})")
    say(f"비밀값: 필수 누락 {total_missing}건, 형식 오류 {total_invalid}건")
    return total_missing + total_invalid


# ---------------------------------------------------------------- 목록 출력

def github_secret_names(cfg: Config, *, include_optional: bool = True) -> list[tuple[str, bool, str]]:
    """GitHub Secrets 에 등록할 (이름, 필수 여부, 설명). 켜진 묶음 기준."""
    groups = set(active_groups(cfg, {"STUDIO_TOKEN_BACKEND": "supabase"}))
    rows = []
    for s in SECRET_SPECS:
        if not s.github or s.group not in groups or s.scope in ("local", "function"):
            continue
        if s.required or include_optional:
            rows.append((s.name, s.required, s.description))
    return rows


def env_example() -> str:
    """.env.example 내용. 값은 비워 둔다(예시 값이 비밀값처럼 보이지 않게)."""
    lines = [
        "# 환경변수 예시 — 복사해서 .env 로 쓰고(커밋 금지), GitHub Actions 에는 같은 이름으로 Secrets 에 넣는다.",
        "# 이 파일은 studio/secrets_spec.py 에서 만든다: python -m studio.secrets_spec --env-example > .env.example",
        "# 발급 방법: docs/SETUP.md · 점검: python -m studio doctor",
        "",
    ]
    for g, title in GROUP_TITLES.items():
        specs = [s for s in SECRET_SPECS if s.group == g]
        if not specs:
            continue
        lines.append(f"# ---------------------------------------------------------------- {title}")
        for s in specs:
            need = "필수" if s.required else "선택"
            if s.any_of:
                need += "(" + " / ".join(s.any_of) + " 중 하나)"
            where = {
                "runtime": "",
                "local": " · 로컬 인증 명령에서만",
                "github": " · GitHub Secrets 에만",
                "function": " · Supabase 함수 비밀값(supabase secrets set)",
            }[s.scope]
            lines.append(f"# [{need}{where}] {s.description} — {s.doc}")
            prefix = "# " if s.scope == "function" else ""
            lines.append(f"{prefix}{s.name}=")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--github", action="store_true", help="GitHub Secrets 에 등록할 이름 목록")
    p.add_argument("--env-example", action="store_true", help=".env.example 내용 출력")


def cmd_secrets(args: argparse.Namespace) -> int:
    if args.env_example:
        sys.stdout.write(env_example())
        return 0
    cfg = load_config()
    if args.github:
        print("GitHub → Settings → Secrets and variables → Actions → New repository secret 에 아래 이름으로 넣습니다.")
        for name, required, desc in github_secret_names(cfg):
            print(f"  {'[필수]' if required else '[선택]'} {name} — {desc}")
        print("  * ANTHROPIC_API_KEY 와 CLAUDE_CODE_OAUTH_TOKEN 은 둘 중 하나만 넣습니다.")
        return 0
    return 1 if check_secrets(cfg) else 0


def register(sub: argparse._SubParsersAction) -> None:
    """`python -m studio secrets ...` — studio.cli.COMMAND_MODULES 에 "studio.secrets_spec" 을 넣으면 붙는다."""
    p = sub.add_parser("secrets", help="비밀값 점검·GitHub Secrets 목록·.env.example (값은 출력하지 않음)")
    _add_args(p)
    p.set_defaults(func=cmd_secrets)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.secrets_spec", description="비밀값 목록·점검(값은 출력하지 않음)")
    _add_args(ap)
    return cmd_secrets(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

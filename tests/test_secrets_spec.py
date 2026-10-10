"""studio.secrets_spec — 비밀값 목록·점검(값 미출력)·.env.example·SETUP.md 앵커."""

from __future__ import annotations

import base64
import io
import json
import re

import pytest

from studio import paths
from studio.config import Config
from studio.secrets_spec import (
    SECRET_SPECS,
    SPECS_BY_NAME,
    active_groups,
    check_secrets,
    env_example,
    github_secret_names,
    main,
)

# 모든 모듈 공통 계약(작업 지시서의 비밀값 환경변수 이름)
CONTRACT = [
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CHANNEL_ID",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_TTS_CREDENTIALS_JSON",
    "INSTAGRAM_USER_ID", "INSTAGRAM_TOKEN", "FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN",
    "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN",
    "TISTORY_STORAGE_STATE_B64",
    "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "STUDIO_TOKEN_BACKEND",
    "REVENUECAT_WEBHOOK_AUTH", "REVENUECAT_SECRET_KEY", "EXPO_TOKEN",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "STUDIO_DRY_RUN",
]


def _jwt(role: str, marker: str) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'role': role, 'ref': marker})}.SIG{marker}"


def full_env() -> dict[str, str]:
    """형식까지 맞는 가짜 비밀값. 값마다 고유 표식(SECRETVAL…)을 넣어 출력에 새는지 본다."""
    state = base64.b64encode(json.dumps({"cookies": [{"name": "TSSESSION", "value": "SECRETVAL-cookie"}], "origins": []}).encode()).decode()
    return {
        "SUPABASE_URL": "https://SECRETVAL-ref.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": _jwt("service_role", "SECRETVAL-srk"),
        "SUPABASE_ANON_KEY": _jwt("anon", "SECRETVAL-anon"),
        "GOOGLE_CLIENT_ID": "SECRETVAL-cid.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "SECRETVAL-csecret",
        "YOUTUBE_REFRESH_TOKEN": "1//SECRETVAL-refresh",
        "YOUTUBE_CHANNEL_ID": "UCabcdefghijklmnopqrstuv",
        "GOOGLE_TTS_CREDENTIALS_JSON": json.dumps({"type": "service_account", "private_key": "SECRETVAL-pk"}),
        "INSTAGRAM_TOKEN": "IGSECRETVAL-token",
        "INSTAGRAM_USER_ID": "17841400000000000",
        "FACEBOOK_PAGE_ID": "100000000000001",
        "FACEBOOK_PAGE_TOKEN": "EAASECRETVAL-page",
        "TIKTOK_CLIENT_KEY": "SECRETVAL-ttkey",
        "TIKTOK_CLIENT_SECRET": "SECRETVAL-ttsecret",
        "TIKTOK_REFRESH_TOKEN": "rft.SECRETVAL-tt",
        "TISTORY_STORAGE_STATE_B64": state,
        "LAW_API_OC": "SECRETVAL-oc",
        "REVENUECAT_SECRET_KEY": "sk_SECRETVAL",
        "STUDIO_TOKEN_BACKEND": "file",
    }


def run(cfg: Config, environ: dict[str, str], tmp_path) -> tuple[int, str]:
    buf = io.StringIO()
    n = check_secrets(cfg, environ=environ, out=buf, secrets_dir=tmp_path / "secrets")
    return n, buf.getvalue()


@pytest.fixture
def cfg() -> Config:
    return Config()   # 모든 플랫폼 켜짐, storage=supabase, tts=google


# ---------------------------------------------------------------- 목록·문서

def test_contract_names_are_all_specified():
    missing = [n for n in CONTRACT if n not in SPECS_BY_NAME]
    assert not missing, f"SECRET_SPECS 에 없는 계약 이름: {missing}"
    assert len(SPECS_BY_NAME) == len(SECRET_SPECS), "이름이 중복된 항목이 있습니다"


def test_names_are_env_safe_and_described():
    for s in SECRET_SPECS:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]+", s.name), s.name
        assert s.description and s.anchor
        assert not s.name.startswith("SUPABASE_") or s.scope != "function", "Supabase 함수 비밀값은 SUPABASE_ 로 시작할 수 없다"
        for alt in s.any_of:
            assert alt in SPECS_BY_NAME, f"{s.name}.any_of 의 {alt} 가 목록에 없다"


def test_setup_doc_has_every_anchor():
    doc = (paths.ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8")
    anchors = set(re.findall(r'<a id="([a-z0-9-]+)"></a>', doc))
    missing = sorted({s.anchor for s in SECRET_SPECS} - anchors)
    assert not missing, f"docs/SETUP.md 에 없는 앵커: {missing}"
    for section in ("overview", "google-cloud", "youtube-audit", "meta", "rehearsal"):
        assert section in anchors


def test_env_example_is_generated_from_specs():
    text = (paths.ROOT / ".env.example").read_text(encoding="utf-8")
    assert text == env_example(), "`.env.example` 이 오래됐습니다: python -m studio.secrets_spec --env-example > .env.example"
    for name in CONTRACT:
        assert re.search(rf"^(# )?{name}=$", text, re.MULTILINE), f".env.example 에 {name} 가 없습니다"
    # 값은 비어 있어야 한다(예시 값이 비밀값처럼 보이지 않게)
    assert all(line.rstrip().endswith("=") for line in text.splitlines() if re.match(r"^(# )?[A-Z][A-Z0-9_]+=", line))


def test_function_secrets_are_commented_in_env_example():
    text = env_example()
    for s in SECRET_SPECS:
        if s.scope == "function":
            assert f"# {s.name}=" in text
            assert f"\n{s.name}=" not in text


def test_github_secret_names():
    names = {n for n, _, _ in github_secret_names(Config())}
    assert "GOOGLE_TTS_CREDENTIALS_JSON" in names
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in names          # 경로는 로컬 전용
    assert "REVENUECAT_WEBHOOK_AUTH" not in names                  # Supabase 함수 비밀값
    assert "META_APP_SECRET" not in names                          # 로컬 인증 명령 전용
    assert {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "SUPABASE_URL"} <= names
    off = Config.model_validate({"platforms": {"tiktok": {"enabled": False}}})
    assert not any(n.startswith("TIKTOK_") for n, _, _ in github_secret_names(off))


# ---------------------------------------------------------------- 점검

def test_all_missing_counts_required_for_enabled_platforms(cfg, tmp_path):
    n, out = run(cfg, {}, tmp_path)
    # supabase 2 + youtube 3 + tts 1 + instagram 1 + facebook 2 + tiktok 3 + tistory 1
    assert n == 13
    assert "[X] SUPABASE_URL" in out
    assert "[-] YOUTUBE_CHANNEL_ID (선택)" in out
    assert "docs/SETUP.md#youtube-oauth" in out
    assert "GOOGLE_APPLICATION_CREDENTIALS 또는 GOOGLE_TTS_CREDENTIALS_JSON" in out
    assert "필수 누락 13건" in out


def test_exam_section_activates_law_api(tmp_path):
    cfg = Config.model_validate({"exam": {"name": "공인중개사"}})
    assert "law" in active_groups(cfg, {})
    n, out = run(cfg, {}, tmp_path)
    assert n == 14 and "[X] LAW_API_OC" in out


def test_everything_present_is_ok_and_values_never_printed(cfg, tmp_path):
    env = full_env()
    n, out = run(cfg, env, tmp_path)
    assert n == 0, out
    assert "필수 누락 0건, 형식 오류 0건" in out
    assert "SECRETVAL" not in out
    for v in env.values():
        if len(v) > 4:
            assert v not in out


def test_disabled_platforms_are_not_counted(tmp_path):
    cfg = Config.model_validate({"platforms": {p: {"enabled": False} for p in ("instagram", "facebook", "tiktok", "tistory")}})
    n, out = run(cfg, {}, tmp_path)
    assert n == 13 - 1 - 2 - 3 - 1
    assert "TIKTOK_CLIENT_KEY" not in out
    assert "꺼진 플랫폼" in out


def test_blank_values_count_as_missing(cfg, tmp_path):
    env = full_env() | {"SUPABASE_URL": "   ", "TIKTOK_CLIENT_KEY": ""}
    n, out = run(cfg, env, tmp_path)
    assert n == 2
    assert "[X] SUPABASE_URL" in out and "[X] TIKTOK_CLIENT_KEY" in out


def test_tts_any_of(cfg, tmp_path):
    env = full_env()
    del env["GOOGLE_TTS_CREDENTIALS_JSON"]
    n, _ = run(cfg, env, tmp_path)
    assert n == 1
    key = tmp_path / "tts.json"
    key.write_text("{}", encoding="utf-8")
    n, out = run(cfg, env | {"GOOGLE_APPLICATION_CREDENTIALS": str(key)}, tmp_path)
    assert n == 0, out
    n, out = run(cfg, env | {"GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "없음.json")}, tmp_path)
    assert n == 1 and "가리키는 파일이 없습니다" in out
    assert str(tmp_path) not in out   # 경로도 출력하지 않는다


def test_local_file_fallbacks(cfg, tmp_path):
    env = full_env()
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "TISTORY_STORAGE_STATE_B64"):
        del env[k]
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "youtube_token.json").write_text(json.dumps({"client_id": "x", "client_secret": "SECRETVAL-y", "refresh_token": "SECRETVAL-z"}), encoding="utf-8")
    (sec / "tistory_state.json").write_text("{}", encoding="utf-8")
    n, out = run(cfg, env, tmp_path)
    assert n == 0, out
    assert ".secrets/youtube_token.json 에 저장된 값 사용" in out
    assert ".secrets/tistory_state.json" in out
    assert "SECRETVAL" not in out
    # 파일은 있지만 필요한 키가 없으면 누락
    (sec / "youtube_token.json").write_text(json.dumps({"client_id": "x"}), encoding="utf-8")
    n, _ = run(cfg, env, tmp_path)
    assert n == 2


def test_token_store_fallback_file_backend_and_supabase_backend(cfg, tmp_path):
    env = full_env()
    del env["TIKTOK_REFRESH_TOKEN"]
    del env["INSTAGRAM_TOKEN"]
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "tokens.json").write_text(json.dumps({"tiktok": {"refresh_token": "SECRETVAL-r"}}), encoding="utf-8")
    n, out = run(cfg, env, tmp_path)
    assert n == 1, out                       # tiktok 은 보관소에 있음, instagram 은 없음
    assert "토큰 보관소 'tiktok'" in out and "[X] INSTAGRAM_TOKEN" in out
    # Supabase 보관소는 오프라인 점검에서 열지 않는다 → 확인 불가로 표시하고 세지 않는다
    n, out = run(cfg, env | {"STUDIO_TOKEN_BACKEND": "supabase"}, tmp_path)
    assert n == 0, out
    assert "[?] INSTAGRAM_TOKEN" in out and "[?] TIKTOK_REFRESH_TOKEN" in out


def test_instagram_facebook_login_accepts_page_token(tmp_path):
    cfg = Config.model_validate({"platforms": {"instagram": {"login": "facebook"}}})
    env = full_env()
    del env["INSTAGRAM_TOKEN"]
    n, out = run(cfg, env, tmp_path)
    assert n == 0, out
    assert "INSTAGRAM_TOKEN 또는 FACEBOOK_PAGE_TOKEN" in out


@pytest.mark.parametrize(
    ("name", "value", "needle"),
    [
        ("SUPABASE_SERVICE_ROLE_KEY", _jwt("anon", "SECRETVAL-a"), "role 이 'anon'"),
        ("SUPABASE_SERVICE_ROLE_KEY", "sb_publishable_SECRETVAL", "공개(publishable) 키"),
        ("SUPABASE_ANON_KEY", _jwt("service_role", "SECRETVAL-s"), "서버 비밀 키"),
        ("SUPABASE_ANON_KEY", "sb_secret_SECRETVAL", "서버 비밀 키"),
        ("SUPABASE_URL", "SECRETVAL.supabase.co", "https://"),
        ("GOOGLE_TTS_CREDENTIALS_JSON", "{SECRETVAL", "JSON 이 아닙니다"),
        ("GOOGLE_TTS_CREDENTIALS_JSON", json.dumps({"type": "authorized_user", "k": "SECRETVAL"}), "서비스 계정 키"),
        ("TISTORY_STORAGE_STATE_B64", "SECRETVAL!!", "storageState"),
        ("FACEBOOK_PAGE_ID", "SECRETVAL", "숫자"),
        ("YOUTUBE_CHANNEL_ID", "SECRETVAL", "UC"),
        ("STUDIO_TOKEN_BACKEND", "redis", "file 또는 supabase"),
    ],
)
def test_format_errors_are_reported_without_values(cfg, tmp_path, name, value, needle):
    n, out = run(cfg, full_env() | {name: value}, tmp_path)
    assert n == 1, out
    assert f"[!] {name}" in out and needle in out
    assert value not in out and "SECRETVAL" not in out


def test_ci_requires_supabase_token_backend(cfg, tmp_path):
    env = full_env() | {"GITHUB_ACTIONS": "true", "STUDIO_TOKEN_BACKEND": "file"}
    n, out = run(cfg, env, tmp_path)
    assert n == 1 and "GitHub Actions 에서는 supabase" in out
    n, _ = run(cfg, env | {"STUDIO_TOKEN_BACKEND": "supabase"}, tmp_path)
    assert n == 0


def test_function_and_github_only_secrets_are_listed_not_checked(cfg, tmp_path):
    n, out = run(cfg, full_env(), tmp_path)
    assert n == 0
    assert "REVENUECAT_WEBHOOK_AUTH" in out and "supabase secrets list" in out
    assert "ANTHROPIC_API_KEY 또는 CLAUDE_CODE_OAUTH_TOKEN" in out
    assert "[X] ANTHROPIC_API_KEY" not in out


def test_doctor_integration_uses_check_secrets(monkeypatch):
    """setup_cmd.cmd_doctor 는 studio.secrets_spec.check_secrets(cfg) 를 부른다(위치 인자 하나)."""
    import inspect

    from studio import setup_cmd

    assert "check_secrets(cfg)" in inspect.getsource(setup_cmd.cmd_doctor)
    sig = inspect.signature(check_secrets)
    assert next(iter(sig.parameters)) == "cfg"


def test_main_env_example(capsys):
    assert main(["--env-example"]) == 0
    assert capsys.readouterr().out == env_example()

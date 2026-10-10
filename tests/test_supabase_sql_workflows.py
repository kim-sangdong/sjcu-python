"""GitHub Actions 워크플로(.github/workflows)와 CI 보조 스크립트(scripts/ci_*) 검사.

- YAML 파싱, cron 값과 KST 의미(channel.yaml 과 일치), 권한 최소화, 비밀값 이름 계약, claude-code-action 입력 이름
- scripts/ci_due_check.py 판단 로직, scripts/ci_commit_push.sh 의 커밋·rebase·충돌 처리(임시 git 저장소)
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from studio import paths
from studio.secrets_spec import SPECS_BY_NAME

WF = paths.ROOT / ".github" / "workflows"
SCRIPTS = paths.ROOT / "scripts"
KST = paths.KST

EXPECTED_CRON = {
    "weekly-publish.yml": "0 3 * * 0",     # 일 12:00 KST
    "publish-due.yml": "7 * * * *",        # 매시 7분
    "comments.yml": "0 12 * * *",          # 매일 21:00 KST
    "weekly-report.yml": "30 14 * * 6",    # 토 23:30 KST
}
SCHEDULED = list(EXPECTED_CRON)
ALL = [*SCHEDULED, "ci.yml"]

# anthropics/claude-code-action v1.0.248 action.yml 의 입력 이름(2026-10-09, docs/research/facts-app-stack.md 6-1 과 대조)
ACTION_INPUTS = {
    "trigger_phrase", "assignee_trigger", "label_trigger", "base_branch", "branch_prefix", "branch_name_template",
    "allowed_bots", "allowed_non_write_users", "include_comments_by_actor", "exclude_comments_by_actor", "prompt",
    "settings", "anthropic_api_key", "claude_code_oauth_token", "anthropic_federation_rule_id", "anthropic_organization_id",
    "anthropic_service_account_id", "anthropic_workspace_id", "anthropic_oidc_audience", "github_token", "use_bedrock",
    "use_vertex", "use_foundry", "claude_args", "additional_permissions", "use_sticky_comment", "classify_inline_comments",
    "use_commit_signing", "ssh_signing_key", "bot_id", "bot_name", "track_progress", "include_fix_links",
    "path_to_claude_code_executable", "path_to_bun_executable", "display_report", "show_full_output", "plugins",
    "plugin_marketplaces",
}
DEPRECATED_INPUTS = {"mode", "direct_prompt", "override_prompt", "custom_instructions", "max_turns", "model",
                     "fallback_model", "allowed_tools", "disallowed_tools", "mcp_config", "claude_env"}
CLAUDE_ACTION = "anthropics/claude-code-action@v1"


def load(name: str) -> dict:
    data = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    if True in data:              # YAML 1.1 은 `on:` 키를 True 로 읽는다
        data["on"] = data.pop(True)
    return data


def steps(wf: dict) -> list[dict]:
    return [s for job in wf["jobs"].values() for s in job["steps"]]


def run_text(wf: dict) -> str:
    return "\n".join(s.get("run", "") for s in steps(wf))


def claude_steps(wf: dict) -> list[dict]:
    return [s for s in steps(wf) if str(s.get("uses", "")).startswith("anthropics/claude-code-action")]


# ---------------------------------------------------------------- cron

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


def cron_sets(expr: str) -> list[set[int]]:
    fields = expr.split()
    assert len(fields) == 5, f"cron 은 5칸이어야 합니다: {expr!r}"
    out = []
    for f, (lo, hi) in zip(fields, RANGES):
        vals: set[int] = set()
        for part in f.split(","):
            m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part)
            assert m, f"해석할 수 없는 cron 칸: {part!r}"
            base, step = m.group(1), int(m.group(2) or 1)
            a, b = (lo, hi) if base == "*" else (int(base.split("-")[0]), int(base.split("-")[-1]))
            vals |= set(range(a, b + 1, step))
        if hi == 6:
            vals = {7 % 7 if v == 7 else v for v in vals}
        assert all(lo <= v <= hi for v in vals), f"범위 밖 값: {f!r}"
        out.append(vals)
    return out


def fire_times(expr: str, days: int = 7) -> list[datetime]:
    """2026-10-12(월) 00:00 UTC 부터 days 일 동안의 실행 시각(UTC)."""
    mi, ho, dom, mon, dow = cron_sets(expr)
    t = datetime(2026, 10, 12, tzinfo=UTC)
    res = []
    for k in range(days * 24 * 60):
        x = t + timedelta(minutes=k)
        cron_dow = (x.weekday() + 1) % 7      # cron: 0=일
        if x.minute in mi and x.hour in ho and x.day in dom and x.month in mon and cron_dow in dow:
            res.append(x)
    return res


def test_all_workflows_parse_and_have_dispatch():
    names = sorted(p.name for p in WF.glob("*.yml"))
    assert set(ALL) <= set(names)            # 다른 모듈의 워크플로(app-deploy 등)가 더 있을 수 있다
    for n in names:
        wf = load(n)
        assert wf.get("name") and "jobs" in wf, n
        assert "workflow_dispatch" in wf["on"], f"{n}: 수동 실행(workflow_dispatch)이 없습니다"


@pytest.mark.parametrize("name", SCHEDULED)
def test_cron_values(name):
    sched = load(name)["on"]["schedule"]
    assert [s["cron"] for s in sched] == [EXPECTED_CRON[name]]
    cron_sets(EXPECTED_CRON[name])


def test_weekly_publish_runs_sunday_noon_kst_matching_channel_yaml():
    times = [t.astimezone(KST) for t in fire_times(load("weekly-publish.yml")["on"]["schedule"][0]["cron"])]
    assert len(times) == 1
    t = times[0]
    cfg = yaml.safe_load((paths.ROOT / "channel.yaml").read_text(encoding="utf-8"))
    day, hm = cfg["schedule"]["weekly_run"].split()
    assert t.strftime("%a").upper() == day and t.strftime("%H:%M") == hm
    # 그 실행이 만드는 묶음은 다음 날(월)의 주차
    assert paths.publishing_week(t) == paths.week_of(t + timedelta(days=1))


def test_report_and_comment_times_in_kst():
    rep = [t.astimezone(KST) for t in fire_times(load("weekly-report.yml")["on"]["schedule"][0]["cron"])]
    assert [(t.strftime("%a"), t.strftime("%H:%M")) for t in rep] == [("Sat", "23:30")]
    pub = fire_times(load("weekly-publish.yml")["on"]["schedule"][0]["cron"], days=14)
    nxt = min(p for p in pub if p > rep[0])
    assert timedelta(hours=1) < nxt - rep[0] < timedelta(hours=24), "보고서는 다음 주간 실행 전에 끝나야 한다"
    com = [t.astimezone(KST) for t in fire_times(load("comments.yml")["on"]["schedule"][0]["cron"])]
    assert len(com) == 7 and {t.strftime("%H:%M") for t in com} == {"21:00"}
    due = fire_times(load("publish-due.yml")["on"]["schedule"][0]["cron"], days=1)
    assert len(due) == 24 and {t.minute for t in due} == {7}


# ---------------------------------------------------------------- 권한·제한

def test_permissions_are_minimal():
    assert load("ci.yml")["permissions"] == {"contents": "read"}
    for n in SCHEDULED:
        wf = load(n)
        assert wf["permissions"] == {"contents": "write", "issues": "write"}, n
        for job in wf["jobs"].values():
            assert "permissions" not in job, f"{n}: 작업 단위로 권한을 넓히지 않는다"


def test_timeouts_and_concurrency():
    for n in ALL:
        wf = load(n)
        assert wf.get("concurrency"), f"{n}: concurrency 없음"
        for job in wf["jobs"].values():
            assert isinstance(job.get("timeout-minutes"), int), f"{n}: timeout-minutes 없음"
            assert job["runs-on"] == "ubuntu-latest"
    assert load("weekly-publish.yml")["jobs"]["weekly"]["timeout-minutes"] == 240
    assert load("weekly-publish.yml")["concurrency"]["cancel-in-progress"] is False


def test_actions_are_pinned_to_major_tags():
    for n in ALL:
        for s in steps(load(n)):
            if "uses" in s:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", s["uses"]), f"{n}: {s['uses']} — 메이저 태그(@vN)로 고정"


# ---------------------------------------------------------------- 비밀값 이름

def test_secret_env_names_follow_contract():
    for n in SCHEDULED:
        wf = load(n)
        for job in wf["jobs"].values():
            env = job.get("env", {})
            assert env.get("STUDIO_TOKEN_BACKEND") == "supabase", f"{n}: STUDIO_TOKEN_BACKEND=supabase"
            for k, v in env.items():
                m = re.fullmatch(r"\$\{\{\s*secrets\.(\w+)\s*\}\}", str(v))
                if m:
                    assert m.group(1) == k, f"{n}: env {k} 가 다른 이름의 secret({m.group(1)})을 읽는다"
                    assert k in SPECS_BY_NAME, f"{n}: 계약에 없는 비밀값 {k}"
            # Claude 인증 키는 액션 입력으로만(작업 env 에 두면 모든 단계에 노출)
            assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_all_secret_references_are_known():
    allowed = set(SPECS_BY_NAME) | {"GITHUB_TOKEN"}
    for n in sorted(p.name for p in WF.glob("*.yml")):   # 다른 모듈의 워크플로도 계약 이름을 쓰는지 본다
        text = (WF / n).read_text(encoding="utf-8")
        refs = set(re.findall(r"secrets\.(\w+)", text))
        assert refs <= allowed, f"{n}: 알 수 없는 비밀값 {refs - allowed}"
        for name in refs - {"GITHUB_TOKEN"}:
            assert SPECS_BY_NAME[name].scope in ("runtime", "github"), f"{n}: {name} 은 GitHub Secret 이 아니다"
    assert "secrets." not in (WF / "ci.yml").read_text(encoding="utf-8")


def test_comment_workflow_gets_only_youtube_secrets():
    """댓글은 외부인이 쓴 글이라 프롬프트 주입 위험 — 유튜브 비밀값만 준다."""
    env = load("comments.yml")["jobs"]["reply"]["env"]
    secret_keys = {k for k, v in env.items() if "secrets." in str(v)}
    assert secret_keys == {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CHANNEL_ID"}


# ---------------------------------------------------------------- claude-code-action

@pytest.mark.parametrize(
    ("name", "skills"),
    [
        ("weekly-publish.yml", ["/weekly-publish"]),
        ("comments.yml", ["/yt-comment-reply"]),
        ("weekly-report.yml", ["/channel-revenue", "/ad-decision", "/ad-strategy"]),
    ],
)
def test_claude_action_inputs(name, skills):
    cs = claude_steps(load(name))
    assert [s["with"]["prompt"].split()[0] for s in cs] == skills
    for s in cs:
        assert s["uses"] == CLAUDE_ACTION
        w = s["with"]
        assert set(w) <= ACTION_INPUTS, f"모르는 입력: {set(w) - ACTION_INPUTS}"
        assert not set(w) & DEPRECATED_INPUTS
        assert w["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
        assert w["claude_code_oauth_token"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
        assert w["github_token"] == "${{ secrets.GITHUB_TOKEN }}"   # 앱 토큰(1시간) 대신 작업 토큰 → 긴 실행 뒤에도 푸시 가능
        args = w["claude_args"]
        assert re.search(r"--max-turns \d+", args)
        allowed = re.search(r'--allowedTools "([^"]+)"', args).group(1).split(",")
        assert "Bash(python -m studio *)" in allowed
        assert set(allowed) <= {"Bash(python -m studio *)", "Bash(python3 -m studio *)", "Bash(STUDIO_DRY_RUN=1 python -m studio *)",
                                "Read", "Write", "Edit", "Glob", "Grep"}
        assert not any(t in ("Bash", "Bash(*)", "WebFetch", "WebSearch") for t in allowed)
        for skill in skills:
            assert (paths.ROOT / ".claude" / "skills" / skill.lstrip("/") / "SKILL.md").exists()


def test_weekly_publish_prompt_is_start_with_week():
    wf = load("weekly-publish.yml")
    prep = next(s for s in steps(wf) if s.get("id") == "prep")
    assert 'args="start --week $week"' in prep["run"]
    assert "--dry-run" in prep["run"]
    assert claude_steps(wf)[0]["with"]["prompt"] == "/weekly-publish ${{ steps.prep.outputs.args }}"


# ---------------------------------------------------------------- 단계 구성

def test_weekly_publish_steps():
    wf = load("weekly-publish.yml")
    st = steps(wf)
    co = next(s for s in st if str(s.get("uses", "")).startswith("actions/checkout"))
    assert co["with"]["fetch-depth"] == 0
    py = next(s for s in st if str(s.get("uses", "")).startswith("actions/setup-python"))
    assert py["with"]["python-version"] == "3.12" and py["with"]["cache"] == "pip"
    text = run_text(wf)
    assert "apt-get install" in text and "ffmpeg" in text
    assert 'pip install -e ".[dev]"' in text
    assert "python -m studio setup fonts" in text
    assert "GOOGLE_APPLICATION_CREDENTIALS=$RUNNER_TEMP/gcp-tts.json" in text and "umask 077" in text
    assert "scripts/ci_commit_push.sh" in text and "content archive reports" in text
    up = [s for s in st if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert up and up[0]["with"]["retention-days"] == 7 and "out/" in up[0]["with"]["path"]
    fail = next(s for s in st if "ci_failure_report.sh" in s.get("run", ""))
    assert fail["if"] == "failure()"
    commit = next(s for s in st if "ci_commit_push.sh" in s.get("run", ""))
    assert "!cancelled()" in commit["if"] and "STUDIO_DRY_RUN" in commit["if"]   # 리허설은 커밋하지 않는다


def test_weekly_publish_checks_pipeline_steps_that_exist():
    """완료 확인 단계가 보는 이름이 studio/pipeline.py 가 기록하는 단계 이름과 같다."""
    text = run_text(load("weekly-publish.yml"))
    need = re.search(r"need = \[([^\]]+)\]", text).group(1)
    names = re.findall(r'"([\w-]+)"', need)
    recorded = set(re.findall(r'run_step\(week, "([\w-]+)"', (paths.ROOT / "studio" / "pipeline.py").read_text(encoding="utf-8")))
    recorded |= set(re.findall(r'\("([\w-]+)", \["render"', (paths.ROOT / "studio" / "pipeline.py").read_text(encoding="utf-8")))
    assert names and set(names) <= recorded, f"pipeline.py 에 없는 단계: {set(names) - recorded}"


def test_publish_due_steps():
    wf = load("publish-due.yml")
    text = run_text(wf)
    assert "python -m studio publish due" in text and "--all-recent" in text
    assert "python -m playwright install --with-deps chromium" in text
    assert "scripts/ci_due_check.py" in text
    assert re.search(r'ci_commit_push\.sh "[^"]*" content$', text, re.MULTILINE)
    assert "ffmpeg" not in text   # 발행만 한다(렌더 없음)


def test_weekly_report_steps():
    wf = load("weekly-report.yml")
    text = run_text(wf)
    assert "python -m studio analytics collect --week" in text
    assert "paths.week_of(paths.now_kst())" in text
    assert re.search(r'ci_commit_push\.sh "[^"]*" reports$', text, re.MULTILINE)


def test_ci_steps():
    wf = load("ci.yml")
    text = run_text(wf)
    assert "python -m pytest" in text and "ffmpeg" in text
    assert "plan validate" not in text
    assert "deno test" in text and "deno check" in text


def test_referenced_scripts_exist_and_are_executable():
    for n in ALL:
        for rel in set(re.findall(r"scripts/[\w.-]+", (WF / n).read_text(encoding="utf-8"))):
            p = paths.ROOT / rel
            assert p.exists(), f"{n}: {rel} 없음"
            assert os.access(p, os.X_OK), f"{rel} 실행 권한 없음(chmod +x)"


# ---------------------------------------------------------------- scripts/ci_due_check.py

def _due_module():
    spec = importlib.util.spec_from_file_location("ci_due_check", SCRIPTS / "ci_due_check.py")
    mod = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # scripts/__pycache__ 를 만들지 않는다
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.dont_write_bytecode = old
    return mod


def test_due_check_week_rule_matches_studio():
    mod = _due_module()
    t = datetime(2026, 10, 9, 0, 30, tzinfo=KST)
    for h in range(0, 24 * 21, 5):
        x = t + timedelta(hours=h)
        assert mod.publishing_week(x) == paths.publishing_week(x)
    from studio.publish import queue as Q

    assert mod.RECENT_WEEKS == Q.RECENT_WEEKS
    assert mod.recent_weeks(t) == Q.recent_weeks(t)
    assert mod.LOOKAHEAD >= Q.DEFAULT_LEAD


def _write_queue(root, week: str, targets: list[dict]) -> None:
    d = root / "content" / "weeks" / week
    d.mkdir(parents=True, exist_ok=True)
    (d / "queue.json").write_text(__import__("json").dumps({"week": week, "targets": targets}), encoding="utf-8")


def test_due_check_decisions(tmp_path):
    mod = _due_module()
    now = datetime(2026, 10, 14, 12, 7, tzinfo=KST)        # 수요일 12:07 → 최근 주차 2026-W40..W42
    week = paths.week_of(now)
    assert week in mod.recent_weeks(now)

    def t(platform, status, at):
        return {"asset_id": "2026W42-S01", "asset_kind": "short", "platform": platform, "status": status, "publish_at": at.isoformat()}

    # 대기열 없음
    assert mod.scan(tmp_path, now)["run"] == "false"
    # 미래 항목만 있음 → 돌지 않는다
    _write_queue(tmp_path, week, [t("instagram", "pending", now + timedelta(hours=7)), t("youtube", "scheduled", now)])
    assert mod.scan(tmp_path, now) == {"run": "false", "tistory": "false", "reason": "처리할 항목 없음"}
    # 공개 시각이 지난 pending → 돈다
    _write_queue(tmp_path, week, [t("instagram", "pending", now - timedelta(minutes=7))])
    assert mod.scan(tmp_path, now)["run"] == "true"
    # 곧(앞당김 범위) 공개 · 티스토리 → 크로미움 필요
    _write_queue(tmp_path, week, [t("tistory", "pending", now + timedelta(minutes=9))])
    assert mod.scan(tmp_path, now) | {"reason": ""} == {"run": "true", "tistory": "true", "reason": ""}
    # 처리 중(processing)은 시각과 관계없이 확인하러 돈다
    _write_queue(tmp_path, week, [t("tiktok", "processing", now + timedelta(days=2))])
    assert mod.scan(tmp_path, now)["run"] == "true"
    # 끝난 항목(published·failed·skipped)은 무시
    _write_queue(tmp_path, week, [t("tiktok", s, now - timedelta(hours=1)) for s in ("published", "failed", "skipped")])
    assert mod.scan(tmp_path, now)["run"] == "false"
    # 수동 실행은 항상 돈다, 대기 중인 티스토리가 있으면 크로미움도
    _write_queue(tmp_path, week, [t("tistory", "pending", now + timedelta(days=2))])
    assert mod.scan(tmp_path, now, force=True) | {"reason": ""} == {"run": "true", "tistory": "true", "reason": ""}
    # 읽을 수 없는 대기열 → 실제 명령이 오류를 보여 주도록 돈다
    (tmp_path / "content" / "weeks" / week / "queue.json").write_text("{broken", encoding="utf-8")
    assert mod.scan(tmp_path, now)["run"] == "true"


def test_due_check_cli_writes_github_output(tmp_path, monkeypatch):
    mod = _due_module()
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert mod.main(["--root", str(tmp_path), "--now", "2026-10-14T12:07:00+09:00"]) == 0
    assert out.read_text(encoding="utf-8").splitlines()[:2] == ["run=false", "tistory=false"]


# ---------------------------------------------------------------- scripts/ci_commit_push.sh

def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def repos(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git 없음")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    a = tmp_path / "a"
    _git(tmp_path, "clone", "-q", str(remote), str(a))
    _git(a, "checkout", "-q", "-B", "main")
    (a / "content").mkdir()
    (a / "content" / "queue.json").write_text('{"v": 0}\n', encoding="utf-8")
    (a / ".gitignore").write_text("out/\n", encoding="utf-8")
    _git(a, "add", ".")
    _git(a, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    _git(a, "push", "-q", "origin", "main")
    b = tmp_path / "b"
    _git(tmp_path, "clone", "-q", "-b", "main", str(remote), str(b))
    return remote, a, b


def _push(cwd, *paths_):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env |= {"GITHUB_REF_NAME": "main", "PUSH_RETRIES": "2", "PUSH_RETRY_DELAY": "0", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["bash", str(SCRIPTS / "ci_commit_push.sh"), "test commit", *paths_], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120, check=False)


def test_commit_push_nothing_to_do(repos):
    _, a, _ = repos
    r = _push(a, "content", "reports")
    assert r.returncode == 0 and "바뀐 파일이 없습니다" in r.stdout
    r = _push(a, "archive")
    assert r.returncode == 0 and "아직 없습니다" in r.stdout


def test_commit_push_only_given_paths_and_rebases(repos):
    _, a, b = repos
    (a / "content" / "queue.json").write_text('{"v": 1}\n', encoding="utf-8")
    (a / "out").mkdir()
    (a / "out" / "video.mp4").write_bytes(b"x")          # .gitignore → 올라가지 않는다
    (a / "untracked.txt").write_text("x", encoding="utf-8")   # 지정 경로 밖 → 올라가지 않는다
    r = _push(a, "content")
    assert r.returncode == 0, r.stdout + r.stderr
    files = _git(a, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert files == ["content/queue.json"]
    assert _git(a, "log", "-1", "--format=%an").stdout.strip() == "github-actions[bot]"
    # b 는 옛 기준에서 다른 파일을 고침 → 푸시 거절 → rebase 후 성공
    (b / "content" / "other.json").write_text("{}", encoding="utf-8")
    r = _push(b, "content")
    assert r.returncode == 0, r.stdout + r.stderr
    log = _git(b, "log", "--format=%s", "origin/main").stdout.split("\n")
    assert log[:2] == ["test commit", "test commit"]


def test_commit_push_conflict_fails_loudly(repos):
    _, a, b = repos
    (a / "content" / "queue.json").write_text('{"v": "a"}\n', encoding="utf-8")
    assert _push(a, "content").returncode == 0
    (b / "content" / "queue.json").write_text('{"v": "b"}\n', encoding="utf-8")
    r = _push(b, "content")
    assert r.returncode == 1
    assert "rebase 충돌" in r.stdout and "content/queue.json" in r.stdout
    assert not (b / ".git" / "rebase-merge").exists()   # rebase 를 정리하고 끝낸다


# ---------------------------------------------------------------- scripts/ci_failure_report.sh

def test_failure_report_redacts_secrets_and_never_fails(tmp_path):
    detail = tmp_path / "detail.txt"
    detail.write_text("publish failed: token=SEKRET-tok-123456 page=100000000000001\n두 번째 줄\n", encoding="utf-8")
    summary = tmp_path / "summary.md"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GH_"))}
    env |= {"GITHUB_STEP_SUMMARY": str(summary), "RUNNER_TEMP": str(tmp_path),
            "TIKTOK_REFRESH_TOKEN": "SEKRET-tok-123456", "FACEBOOK_PAGE_ID": "100000000000001", "PATH": os.environ["PATH"]}
    r = subprocess.run(["bash", str(SCRIPTS / "ci_failure_report.sh"), "publish-due", str(detail)], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr        # gh·토큰이 없어도 실패로 끝내지 않는다
    text = summary.read_text(encoding="utf-8")
    assert "[자동 실행 실패] publish-due" in text and "누적 실패: 1" in text
    assert "SEKRET" not in text and "100000000000001" not in text and "token=***" in text
    assert "두 번째 줄" in text and "docs/SETUP.md" in text

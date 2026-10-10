"""`python -m studio publish ...` 명령 테스트 — 가짜 발행기·가짜 업로드(네트워크 없음)."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import pytest

from studio import cliutil, config, paths, planning
from studio.manifest import upsert_asset
from studio.models import (
    BlogPost,
    CardNews,
    CardSlide,
    Chapter,
    PlanItem,
    PublishQueue,
    PublishTarget,
    RenderedAsset,
    Scene,
    VideoScript,
    WeekPlan,
    read_model,
    write_model,
)
from studio.publish import cli as pcli
from studio.publish import queue as Q
from studio.publish import registry
from studio.publish.base import PublishError, PublishResult

WEEK = "2026-W42"
KST = paths.KST
RUN = datetime(2026, 10, 11, 12, 0, tzinfo=KST)
L_AT = datetime(2026, 10, 11, 19, 0, tzinfo=KST)
S1_AT = datetime(2026, 10, 12, 12, 0, tzinfo=KST)
S2_AT = datetime(2026, 10, 12, 19, 0, tzinfo=KST)
C_AT = datetime(2026, 10, 13, 20, 0, tzinfo=KST)
B_AT = datetime(2026, 10, 12, 8, 0, tzinfo=KST)
ITEMS = [
    ("2026W42-L1", "longform", L_AT),
    ("2026W42-S01", "short", S1_AT),
    ("2026W42-S02", "short", S2_AT),
    ("2026W42-C01", "cardnews", C_AT),
    ("2026W42-B01", "blog", B_AT),
]
ROLES = {
    "longform": {"video": "video.mp4"},
    "short": {"video": "video.mp4", "cover": "cover.png"},
    "cardnews": {"slide01": "slide01.png", "slide02": "slide02.png"},
    "blog": {"html": "post.html", "meta": "meta.json"},
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"),
                      ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(Q, "_now", lambda: RUN)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def _content(i: str, k: str, week: str):
    if k in ("longform", "short"):
        return VideoScript(id=i, week=week, format=k, title="제목", description="설명",
                           chapters=[Chapter(title="장", scenes=[Scene(id="s1", kind="title", narration=["안녕하세요."])])])
    if k == "cardnews":
        return CardNews(id=i, week=week, title="카드", caption="캡션", slides=[CardSlide(kind="cover"), CardSlide(kind="cta")])
    return BlogPost(id=i, week=week, title="글", keyword="키워드", body_markdown="본문")


def make_week(root, items=ITEMS, week: str = WEEK, remote: bool = False) -> None:
    write_model(planning.plan_path(week), WeekPlan(week=week, theme="주제",
                                                   items=[PlanItem(id=i, kind=k, topic="t", publish_at=at) for i, k, at in items]))
    for i, k, _ in items:
        write_model(planning.asset_path(week, k, i), _content(i, k, week))
        files, urls = {}, {}
        for role, name in ROLES[k].items():
            rel = f"out/weeks/{week}/{i}/{name}"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x", encoding="utf-8")
            files[role] = rel
            if remote:
                urls[role] = f"https://cdn.example/{i}/{name}"
        upsert_asset(week, RenderedAsset(asset_id=i, kind=k, files=files, remote_urls=urls))


class Fake:
    def __init__(self, platform: str, status: str = "published"):
        self.platform, self.kinds, self.status = platform, Q.PLATFORM_KINDS[platform], status
        self.calls: list[tuple[str, str, bool]] = []
        self.errors: list[Exception | None] = []
        self.check_status = "published"

    def publish(self, ctx):
        self.calls.append(("publish", ctx.target.asset_id, ctx.dry_run))
        if self.errors:
            e = self.errors.pop(0)
            if e:
                raise e
        return PublishResult(self.status, remote_id=f"{self.platform}-{ctx.target.asset_id}", url=f"https://{self.platform}.example/{ctx.target.asset_id}")

    def check(self, ctx):
        self.calls.append(("check", ctx.target.asset_id, ctx.dry_run))
        return PublishResult(self.check_status, remote_id=ctx.target.remote_id)


@pytest.fixture
def fakes(monkeypatch):
    f = {p: Fake(p) for p in Q.PLATFORM_KINDS}
    f["youtube"].status = "scheduled"
    f["facebook"].status = "scheduled"
    monkeypatch.setattr(registry, "get_publisher", lambda p: f[p])
    return f


@pytest.fixture
def uploads(monkeypatch):
    from studio.publish import storage

    calls = []

    class Report:
        failed: list = []
        missing_ids: list = []

    def fake_upload(week, asset_ids=None, dry_run=False):
        calls.append((week, sorted(asset_ids or []), dry_run))
        return Report()

    monkeypatch.setattr(storage, "upload_media", fake_upload)
    return calls


def run(*argv: str) -> int:
    cliutil._GROUPS.clear()   # 테스트마다 새 파서(id 재사용으로 옛 묶음이 잡히지 않게)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    pcli.register(sub)
    args = parser.parse_args(["publish", *argv])
    return args.func(args)


def save(*targets: PublishTarget, week: str = WEEK) -> None:
    Q.save_queue(PublishQueue(week=week, targets=list(targets)))


def tgt(asset_id="2026W42-S01", platform="instagram", kind="short", at=S1_AT, **kw) -> PublishTarget:
    return PublishTarget(asset_id=asset_id, asset_kind=kind, platform=platform, publish_at=at, **kw)


def statuses(week: str = WEEK) -> dict[str, str]:
    return {t.key: t.status for t in Q.load_queue(week).targets}


# ---------------------------------------------------------------- 등록

def test_upload_media_is_registered():
    from studio.publish import storage

    cliutil._GROUPS.clear()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    pcli.register(sub)
    names = set(cliutil._GROUPS[id(sub)]["publish"].choices)
    assert {"schedule", "due", "status", "retry", "mark", "upload-media"} <= names
    # storage 가 먼저 붙어 있어도(studio.cli 목록에 추가된 경우) 충돌하지 않는다
    cliutil._GROUPS.clear()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    storage.register(sub)
    pcli.register(sub)
    assert "upload-media" in cliutil._GROUPS[id(sub)]["publish"].choices


# ---------------------------------------------------------------- schedule

def test_schedule_preschedules_youtube_and_facebook_only(root, fakes, uploads, capsys):
    make_week(root)
    assert run("schedule", "--week", WEEK) == 0
    st = statuses()
    assert st["youtube:2026W42-L1"] == "scheduled"
    assert st["youtube:2026W42-S01"] == "scheduled" and st["facebook:2026W42-S02"] == "scheduled"
    for k in ("instagram:2026W42-S01", "instagram:2026W42-C01", "tiktok:2026W42-S01", "tistory:2026W42-B01"):
        assert st[k] == "pending"
    assert fakes["instagram"].calls == [] and fakes["tiktok"].calls == [] and fakes["tistory"].calls == []
    # 인스타·페북이 쓸 공개 URL 이 없는 쇼츠·카드뉴스를 먼저 올린다
    assert uploads == [(WEEK, ["2026W42-C01", "2026W42-S01", "2026W42-S02"], False)]
    out = capsys.readouterr().out
    assert "예약 요약: 성공 5" in out and "실패 0" in out


def test_schedule_skips_upload_when_urls_exist(root, fakes, uploads):
    make_week(root, remote=True)
    assert run("schedule", "--week", WEEK) == 0
    assert uploads == []


def test_schedule_is_idempotent_and_keeps_state(root, fakes, uploads):
    make_week(root)
    run("schedule", "--week", WEEK)
    n = len(fakes["youtube"].calls)
    assert run("schedule", "--week", WEEK) == 0
    assert len(fakes["youtube"].calls) == n                     # 이미 scheduled → 다시 올리지 않음
    assert statuses()["youtube:2026W42-L1"] == "scheduled"


def test_schedule_failure_exit_code(root, fakes, uploads, capsys):
    make_week(root)
    fakes["facebook"].errors = [PublishError("페이지 권한 없음", retryable=False)]
    assert run("schedule", "--week", WEEK) == 1
    assert "실패 1" in capsys.readouterr().out


def test_schedule_without_plan(root, fakes, capsys):
    assert run("schedule", "--week", WEEK) == 1
    assert "plan init" in capsys.readouterr().out


def test_schedule_dry_run_leaves_queue_untouched(root, fakes, uploads, capsys):
    make_week(root)
    assert run("schedule", "--week", WEEK, "--dry-run") == 0
    assert not Q.queue_path(WEEK).exists()
    dry = read_model(Q.dryrun_path(WEEK), PublishQueue)
    assert dry.find("youtube", "2026W42-L1").status == "scheduled"
    assert all(c[2] is True for c in fakes["youtube"].calls)
    assert uploads and uploads[0][2] is True
    capsys.readouterr()
    assert run("status", "--week", WEEK) == 0                      # 실제 대기열이 없으면 리허설 결과를 보여 준다
    assert "[리허설 결과]" in capsys.readouterr().out


def test_schedule_env_dry_run(root, fakes, uploads, monkeypatch):
    make_week(root)
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    assert run("schedule", "--week", WEEK) == 0
    assert not Q.queue_path(WEEK).exists()


# ---------------------------------------------------------------- due

def test_due_time_window_with_lead(root, fakes):
    make_week(root, remote=True)
    save(tgt())
    assert run("due", "--week", WEEK, "--now", "2026-10-12T11:49:00+09:00") == 0
    assert fakes["instagram"].calls == []
    assert run("due", "--week", WEEK, "--now", "2026-10-12T11:55:00", "--lead-minutes", "0") == 0   # 시간대 없음 = KST
    assert fakes["instagram"].calls == []
    assert run("due", "--week", WEEK, "--now", "2026-10-12T11:50:00+09:00") == 0
    assert fakes["instagram"].calls == [("publish", "2026W42-S01", False)]
    assert statuses()["instagram:2026W42-S01"] == "published"


def test_due_platform_filter(root, fakes):
    make_week(root, remote=True)
    save(tgt(), tgt(platform="tiktok"))
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:00+09:00", "--platform", "tiktok") == 0
    assert fakes["instagram"].calls == [] and len(fakes["tiktok"].calls) == 1


def test_due_limits_per_platform(root, fakes, capsys):
    shorts = [(f"2026W42-S{i:02d}", "short", S1_AT + timedelta(minutes=i)) for i in range(1, 8)]
    blogs = [(f"2026W42-B0{i}", "blog", B_AT + timedelta(minutes=i)) for i in range(1, 4)]
    make_week(root, items=shorts + blogs, remote=True)
    save(*[tgt(i, "instagram", k, at) for i, k, at in shorts], *[tgt(i, "tistory", k, at) for i, k, at in blogs])
    assert run("due", "--week", WEEK, "--now", "2026-10-13T00:00+09:00") == 0
    assert len(fakes["instagram"].calls) == 5 and len(fakes["tistory"].calls) == 2
    st = statuses()
    assert [st[f"instagram:2026W42-S{i:02d}"] for i in range(1, 8)] == ["published"] * 5 + ["pending"] * 2   # 오래된 것부터
    assert "한도 3" in capsys.readouterr().out
    assert run("due", "--week", WEEK, "--now", "2026-10-13T00:30+09:00") == 0      # 다음 실행이 나머지를 올린다
    assert all(v == "published" for v in statuses().values())


def test_due_processing_is_checked(root, fakes):
    make_week(root, remote=True)
    save(tgt(status="processing", remote_id="c-1", at=S2_AT))
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:00+09:00") == 0
    assert fakes["instagram"].calls == [("check", "2026W42-S01", False)]
    assert statuses()["instagram:2026W42-S01"] == "published"


def test_due_retry_backoff_and_exit_codes(root, fakes, capsys):
    make_week(root, remote=True)
    save(tgt())
    fakes["instagram"].errors = [PublishError("일시 오류", retryable=True)]
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:00+09:00") == 0     # 재시도 대기는 실패가 아니다
    out = capsys.readouterr().out
    assert "[재시도 대기]" in out and "다음 시도 10-12(월) 12:05" in out
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:04+09:00") == 0
    assert len(fakes["instagram"].calls) == 1
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:05+09:00") == 0
    assert len(fakes["instagram"].calls) == 2 and statuses()["instagram:2026W42-S01"] == "published"


def test_due_non_retryable_exit_1(root, fakes, capsys):
    make_week(root, remote=True)
    save(tgt(platform="tiktok"))
    fakes["tiktok"].errors = [PublishError("감사 전에는 SELF_ONLY 만 가능", retryable=False)]
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:00+09:00") == 1
    assert "[실패]" in capsys.readouterr().out
    assert statuses()["tiktok:2026W42-S01"] == "failed"


def test_due_all_recent_weeks(root, fakes):
    make_week(root, remote=True)
    prev = "2026-W41"
    make_week(root, items=[("2026W41-S01", "short", datetime(2026, 10, 10, 12, 0, tzinfo=KST))], week=prev, remote=True)
    save(tgt())
    save(tgt("2026W41-S01", at=datetime(2026, 10, 10, 12, 0, tzinfo=KST)), week=prev)
    assert run("due", "--now", "2026-10-12T12:00+09:00") == 0                  # --week 없으면 최근 3주
    assert {c[1] for c in fakes["instagram"].calls} == {"2026W42-S01", "2026W41-S01"}


def test_due_dry_run(root, fakes):
    make_week(root, remote=True)
    save(tgt())
    before = Q.queue_path(WEEK).read_text(encoding="utf-8")
    assert run("due", "--week", WEEK, "--now", "2026-10-12T12:00+09:00", "--dry-run") == 0
    assert Q.queue_path(WEEK).read_text(encoding="utf-8") == before
    assert fakes["instagram"].calls == [("publish", "2026W42-S01", True)]
    assert read_model(Q.dryrun_path(WEEK), PublishQueue).targets[0].status == "published"


def test_due_missing_queue_for_explicit_week(root, fakes, capsys):
    assert run("due", "--week", WEEK) == 1
    assert "schedule" in capsys.readouterr().out


# ---------------------------------------------------------------- status

def test_status_table_and_exit_code(root, capsys):
    save(tgt(status="published", url="https://instagram.example/x"),
         tgt(platform="tiktok", status="failed", attempts=1, last_error="권한 없음"),
         tgt(platform="facebook", status="pending", attempts=2, last_error="일시 오류", updated_at=RUN))
    assert run("status", "--week", WEEK) == 1
    out = capsys.readouterr().out
    assert "https://instagram.example/x" in out and "권한 없음" in out
    assert "재시도 10-11(일) 12:15 이후" in out
    assert "요약: 성공 1 · 대기 1 · 건너뜀 0 · 실패 1" in out


def test_status_without_queue(root, capsys):
    assert run("status", "--week", WEEK) == 1
    assert "schedule" in capsys.readouterr().out


# ---------------------------------------------------------------- retry

def test_retry_failed_to_pending(root, capsys):
    save(tgt(status="failed", attempts=5, last_error="오류"))
    assert run("retry", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "instagram") == 0
    t = Q.load_queue(WEEK).targets[0]
    assert (t.status, t.attempts, t.last_error) == ("pending", 0, None)


def test_retry_with_remote_id_rechecks_unless_reupload(root):
    save(tgt(status="failed", remote_id="c-1", attempts=5))
    assert run("retry", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "instagram") == 0
    assert Q.load_queue(WEEK).targets[0].status == "processing"
    save(tgt(status="failed", remote_id="c-1", attempts=5))
    assert run("retry", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "instagram", "--reupload") == 0
    t = Q.load_queue(WEEK).targets[0]
    assert (t.status, t.remote_id) == ("pending", None)


def test_retry_refuses_non_failed_without_force(root, capsys):
    save(tgt(status="skipped", last_error="[안내] 패키지"))
    assert run("retry", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "instagram") == 1
    assert Q.load_queue(WEEK).targets[0].status == "skipped"
    assert run("retry", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "instagram", "--force") == 0
    assert Q.load_queue(WEEK).targets[0].status == "pending"


def test_retry_all_failed(root):
    save(tgt(status="failed"), tgt(platform="tiktok", status="failed"), tgt(platform="facebook", status="published"))
    assert run("retry", "--week", WEEK, "--all-failed") == 0
    assert statuses() == {"instagram:2026W42-S01": "pending", "tiktok:2026W42-S01": "pending", "facebook:2026W42-S01": "published"}


def test_retry_unknown_target(root):
    save(tgt())
    assert run("retry", "--week", WEEK, "--asset", "2026W42-S09", "--platform", "instagram") == 1


# ---------------------------------------------------------------- mark

def test_mark_links_manual_youtube_upload(root, capsys):
    save(tgt("2026W42-S01", "youtube", status="skipped", last_error="[안내] 수동 업로드 필요"))
    assert run("mark", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "youtube",
               "--remote-id", "https://youtube.com/shorts/AbCdEfGhIjK?feature=share") == 0
    t = Q.load_queue(WEEK).find("youtube", "2026W42-S01")
    assert (t.status, t.remote_id, t.url, t.last_error) == ("published", "AbCdEfGhIjK", "https://youtube.com/shorts/AbCdEfGhIjK?feature=share", None)
    assert "요약: 등록 1" in capsys.readouterr().out


def test_mark_scheduled_and_derived_url(root):
    save(tgt("2026W42-L1", "youtube", "longform", L_AT, status="skipped"))
    assert run("mark", "--week", WEEK, "--asset", "2026W42-L1", "--platform", "youtube",
               "--remote-id", "AbCdEfGhIjK", "--status", "scheduled") == 0
    t = Q.load_queue(WEEK).find("youtube", "2026W42-L1")
    assert (t.status, t.url) == ("scheduled", "https://youtu.be/AbCdEfGhIjK")


def test_mark_creates_target_from_plan(root):
    make_week(root)
    assert run("mark", "--week", WEEK, "--asset", "2026W42-B01", "--platform", "tistory",
               "--remote-id", "https://myblog.tistory.com/57") == 0
    t = Q.load_queue(WEEK).find("tistory", "2026W42-B01")
    assert (t.status, t.remote_id, t.url, t.publish_at) == ("published", "57", "https://myblog.tistory.com/57", B_AT)


def test_mark_rejects_unknown_asset_and_wrong_kind(root):
    make_week(root)
    assert run("mark", "--week", WEEK, "--asset", "2026W42-S99", "--platform", "youtube", "--remote-id", "x") == 1
    assert run("mark", "--week", WEEK, "--asset", "2026W42-B01", "--platform", "youtube", "--remote-id", "x") == 1


def test_marked_published_is_not_republished(root, fakes):
    make_week(root, remote=True)
    save(tgt())
    run("mark", "--week", WEEK, "--asset", "2026W42-S01", "--platform", "instagram", "--remote-id", "1789")
    assert run("due", "--week", WEEK, "--now", "2026-10-12T13:00+09:00") == 0
    assert fakes["instagram"].calls == []

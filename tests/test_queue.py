"""발행 대기열(studio.publish.queue) 테스트 — 가짜 발행기만 쓴다(네트워크 없음)."""

from __future__ import annotations

import fcntl
from datetime import datetime, timedelta

import pytest

from studio import config, paths, planning
from studio.config import Config, MissingSecret
from studio.manifest import upsert_asset
from studio.models import (
    AssetOriginality,
    BlogPost,
    CardNews,
    CardSlide,
    Chapter,
    OriginalityReport,
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
from studio.publish import queue as Q
from studio.publish import registry
from studio.publish.base import PublishError, PublishResult

WEEK = "2026-W42"
KST = paths.KST
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=KST)          # 일요일 정기 실행
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


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"),
                      ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def content_for(asset_id: str, kind: str, week: str = WEEK):
    if kind in ("longform", "short"):
        return VideoScript(id=asset_id, week=week, format=kind, title=f"제목 {asset_id}", description="설명",
                           chapters=[Chapter(title="장", scenes=[Scene(id="s1", kind="title", narration=["안녕하세요."])])])
    if kind == "cardnews":
        return CardNews(id=asset_id, week=week, title="카드", caption="캡션",
                        slides=[CardSlide(kind="cover", title="표지"), CardSlide(kind="cta", title="끝")])
    return BlogPost(id=asset_id, week=week, title=f"글 {asset_id}", keyword="키워드", body_markdown="## 소제목\n\n본문")


ROLES = {
    "longform": {"video": "video.mp4", "thumbnail": "thumbnail.png"},
    "short": {"video": "video.mp4", "cover": "cover.png"},
    "cardnews": {"slide01": "slide01.png", "slide02": "slide02.png"},
    "blog": {"html": "post.html", "meta": "meta.json", "og": "og.png"},
}


def make_week(root, items=ITEMS, week: str = WEEK, *, render: bool = True, local: bool = True, remote: bool = False) -> WeekPlan:
    plan = WeekPlan(week=week, theme="주제", items=[PlanItem(id=i, kind=k, topic="t", publish_at=at) for i, k, at in items])
    write_model(planning.plan_path(week), plan)
    for i, k, _ in items:
        write_model(planning.asset_path(week, k, i), content_for(i, k, week))
        if not render:
            continue
        files, urls = {}, {}
        for role, name in ROLES[k].items():
            rel = f"out/weeks/{week}/{i}/{name}"
            files[role] = rel
            if local:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("<p>본문</p>" if name == "post.html" else "x", encoding="utf-8")
            if remote:
                urls[role] = f"https://cdn.example/{week}/{i}/{name}"
        upsert_asset(week, RenderedAsset(asset_id=i, kind=k, files=files, remote_urls=urls))
    return plan


def cfg_of(**platforms) -> Config:
    return Config.model_validate({"platforms": platforms})


class Fake:
    """가짜 발행기. errors 는 호출마다 하나씩 꺼내 던진다(None 이면 성공)."""

    def __init__(self, platform: str, *, status: str = "published", check_status: str = "published", errors=None, check_errors=None):
        self.platform = platform
        self.kinds = Q.PLATFORM_KINDS[platform]
        self.status, self.check_status = status, check_status
        self.errors, self.check_errors = list(errors or []), list(check_errors or [])
        self.calls: list[tuple[str, str, bool]] = []
        self.on_publish = None

    def _result(self, status, ctx):
        aid = ctx.target.asset_id
        return PublishResult(status, remote_id=ctx.target.remote_id or f"{self.platform}-{aid}", url=f"https://{self.platform}.example/{aid}",
                             note="안내 메모" if status == "skipped" else "")

    def publish(self, ctx):
        self.calls.append(("publish", ctx.target.asset_id, ctx.dry_run))
        if self.on_publish:
            self.on_publish(ctx)
        if self.errors:
            e = self.errors.pop(0)
            if e is not None:
                raise e
        return self._result(self.status, ctx)

    def check(self, ctx):
        self.calls.append(("check", ctx.target.asset_id, ctx.dry_run))
        if self.check_errors:
            e = self.check_errors.pop(0)
            if e is not None:
                raise e
        return self._result(self.check_status, ctx)


@pytest.fixture
def fakes(monkeypatch):
    f = {p: Fake(p) for p in Q.PLATFORM_KINDS}
    monkeypatch.setattr(registry, "get_publisher", lambda p: f[p])
    return f


def target(asset_id="2026W42-S01", platform="instagram", kind="short", at=S1_AT, **kw) -> PublishTarget:
    return PublishTarget(asset_id=asset_id, asset_kind=kind, platform=platform, publish_at=at, **kw)


# ---------------------------------------------------------------- build_queue

def test_build_queue_targets(root):
    make_week(root)
    q = Q.build_queue(WEEK, cfg_of(), now=NOW)
    keys = {t.key for t in q.targets}
    assert keys == {
        "youtube:2026W42-L1",
        "youtube:2026W42-S01", "instagram:2026W42-S01", "facebook:2026W42-S01", "tiktok:2026W42-S01",
        "youtube:2026W42-S02", "instagram:2026W42-S02", "facebook:2026W42-S02", "tiktok:2026W42-S02",
        "instagram:2026W42-C01",
        "tistory:2026W42-B01",
    }
    assert all(t.status == "pending" for t in q.targets)
    assert q.find("tiktok", "2026W42-S02").publish_at == S2_AT
    assert q.targets[0].asset_id == "2026W42-L1"           # 공개 시각 순
    assert not Q.queue_path(WEEK).exists()                 # build_queue 는 저장하지 않는다


def test_build_queue_respects_disabled_platform(root):
    make_week(root)
    q = Q.build_queue(WEEK, cfg_of(tiktok={"enabled": False}, tistory={"enabled": False}), now=NOW)
    assert not [t for t in q.targets if t.platform in ("tiktok", "tistory")]


def test_merge_preserves_state_and_skips_removed(root):
    make_week(root)
    existing = Q.build_queue(WEEK, cfg_of(), now=NOW)
    existing.find("youtube", "2026W42-S02").status = "published"
    existing.find("instagram", "2026W42-S02").status = "scheduled"
    existing.find("facebook", "2026W42-S02").status = "failed"
    existing.find("youtube", "2026W42-L1").remote_id = "vid1"
    existing.find("youtube", "2026W42-L1").status = "scheduled"

    plan = planning.load_plan(WEEK)
    plan.items = [i for i in plan.items if i.id != "2026W42-S02"]
    moved = S1_AT + timedelta(hours=1)
    next(i for i in plan.items if i.id == "2026W42-S01").publish_at = moved
    q = Q.build_queue(WEEK, cfg_of(), existing=existing, plan=plan, blocked=set(), now=NOW)

    assert q.find("youtube", "2026W42-S02").status == "published"       # 건드리지 않음
    assert q.find("instagram", "2026W42-S02").status == "scheduled"
    assert q.find("tiktok", "2026W42-S02").status == "skipped"           # 빠진 pending
    assert q.find("tiktok", "2026W42-S02").last_error == Q.SKIP_REMOVED
    assert q.find("facebook", "2026W42-S02").status == "skipped"         # 빠진 failed 도 정리
    assert q.find("youtube", "2026W42-L1").remote_id == "vid1"
    assert q.find("instagram", "2026W42-S01").publish_at == moved        # pending 은 새 시각

    # 다시 계획에 넣으면 자동 제외였던 것만 되살아난다
    back = Q.build_queue(WEEK, cfg_of(), existing=q, plan=planning.load_plan(WEEK), blocked=set(), now=NOW)
    t = back.find("tiktok", "2026W42-S02")
    assert (t.status, t.last_error, t.attempts) == ("pending", None, 0)
    assert back.find("youtube", "2026W42-S02").status == "published"


def test_disabled_platform_skips_existing_pending_and_restores(root):
    make_week(root)
    q = Q.build_queue(WEEK, cfg_of(), now=NOW)
    off = Q.build_queue(WEEK, cfg_of(tiktok={"enabled": False}), existing=q, now=NOW)
    t = off.find("tiktok", "2026W42-S01")
    assert t.status == "skipped" and "platforms.tiktok.enabled" in t.last_error
    on = Q.build_queue(WEEK, cfg_of(), existing=off, now=NOW)
    assert on.find("tiktok", "2026W42-S01").status == "pending"


def test_publisher_skip_is_not_reverted(root):
    make_week(root)
    q = Q.build_queue(WEEK, cfg_of(), now=NOW)
    t = q.find("youtube", "2026W42-L1")
    t.status, t.last_error = "skipped", Q.NOTE_PREFIX + "수동 업로드 필요"
    again = Q.build_queue(WEEK, cfg_of(), existing=q, now=NOW)
    assert again.find("youtube", "2026W42-L1").status == "skipped"


def test_blocked_assets_are_skipped_then_restored(root):
    make_week(root)
    report = OriginalityReport(week=WEEK, generated_at=NOW, verdict="block",
                               assets=[AssetOriginality(asset_id="2026W42-S01", verdict="block"),
                                       AssetOriginality(asset_id="2026W42-S02", verdict="pass")])
    write_model(paths.content_dir(WEEK) / "originality.json", report)
    assert Q.blocked_assets(WEEK) == {"2026W42-S01"}
    q = Q.build_queue(WEEK, cfg_of(), now=NOW)
    s1 = [t for t in q.targets if t.asset_id == "2026W42-S01"]
    assert s1 and all(t.status == "skipped" and t.last_error == Q.SKIP_BLOCKED for t in s1)
    report.assets[0].verdict = "pass"
    write_model(paths.content_dir(WEEK) / "originality.json", report)
    q2 = Q.build_queue(WEEK, cfg_of(), existing=q, now=NOW)
    assert all(t.status == "pending" for t in q2.targets if t.asset_id == "2026W42-S01")


def test_missing_publish_at_is_reported(root):
    make_week(root, items=[("2026W42-S01", "short", None)])
    notes: list[str] = []
    q = Q.build_queue(WEEK, cfg_of(), blocked=set(), notes=notes, now=NOW)
    assert not q.targets and "publish_at" in notes[0]


# ---------------------------------------------------------------- 잠금·파일

def test_editing_lock_is_exclusive_and_saves_only_changes(root):
    with Q.editing(WEEK) as q:
        assert q.targets == []
    assert not Q.queue_path(WEEK).exists()          # 바뀐 게 없으면 만들지 않는다
    with Q.editing(WEEK) as q:
        q.targets.append(target())
        with open(Q.lock_path(WEEK), "a") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert Q.load_queue(WEEK).find("instagram", "2026W42-S01").status == "pending"
    with open(Q.lock_path(WEEK), "a") as other:     # 블록이 끝나면 풀린다
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_UN)


def test_editing_does_not_save_on_exception(root):
    Q.save_queue(PublishQueue(week=WEEK, targets=[target()]))
    with pytest.raises(RuntimeError):
        with Q.editing(WEEK) as q:
            q.targets[0].status = "failed"
            raise RuntimeError("중단")
    assert Q.load_queue(WEEK).targets[0].status == "pending"


# ---------------------------------------------------------------- 선택·백오프

def test_action_for_time_window_and_preschedule():
    t = target(at=S1_AT)
    assert Q.action_for(t, S1_AT - timedelta(minutes=11)) is None
    assert Q.action_for(t, S1_AT - timedelta(minutes=10)) == "publish"
    assert Q.action_for(t, NOW, preschedule=True) == "publish"
    assert Q.action_for(target(status="processing"), NOW) == "check"
    assert Q.action_for(target(status="scheduled"), S1_AT) is None
    assert Q.action_for(target(status="uploading", updated_at=NOW), NOW + timedelta(hours=1)) is None
    assert Q.action_for(target(status="uploading", updated_at=NOW), NOW + Q.UPLOADING_STALE) == "stale"


def test_naive_datetimes_are_kst():
    t = target(at=datetime(2026, 10, 12, 12, 0))
    assert Q.action_for(t, S1_AT - timedelta(minutes=10)) == "publish"


def test_recent_weeks():
    # 일요일(W41 의 마지막 날)에는 그날 19시 롱폼이 든 다음 주 묶음(W42)까지
    assert Q.recent_weeks(NOW) == ["2026-W40", "2026-W41", "2026-W42"]
    assert Q.recent_weeks(datetime(2026, 10, 14, 9, 0, tzinfo=KST)) == ["2026-W40", "2026-W41", "2026-W42"]
    assert Q.recent_weeks(datetime(2026, 10, 18, 9, 0, tzinfo=KST)) == ["2026-W41", "2026-W42", "2026-W43"]


# ---------------------------------------------------------------- process

def test_process_success_updates_target(root, fakes):
    make_week(root)
    t = target(platform="youtube")
    out = Q.process(WEEK, t, cfg_of(), now=NOW)
    assert out.ok and out.category == "성공"
    assert (t.status, t.remote_id, t.url, t.attempts, t.last_error, t.updated_at) == (
        "published", "youtube-2026W42-S01", "https://youtube.example/2026W42-S01", 0, None, NOW)
    assert fakes["youtube"].calls == [("publish", "2026W42-S01", False)]


def test_process_skipped_note_is_kept(root, fakes):
    make_week(root)
    fakes["youtube"].status = "skipped"
    t = target("2026W42-L1", "youtube", "longform", L_AT)
    Q.process(WEEK, t, cfg_of(), now=NOW)
    assert t.status == "skipped" and t.last_error == Q.NOTE_PREFIX + "안내 메모"


def test_retry_backoff_then_failed(root, fakes):
    make_week(root)
    fakes["instagram"].errors = [PublishError("일시 오류", retryable=True)] * 5
    t = target()
    clock = S1_AT
    expected = [5, 15, 45, 135]
    for n in range(1, 5):
        out = Q.process(WEEK, t, cfg_of(), now=clock)
        assert (t.status, t.attempts, out.category) == ("pending", n, "재시도 대기")
        assert t.last_error == "일시 오류"
        wait = timedelta(minutes=expected[n - 1])
        assert out.retry_at == clock + wait
        assert Q.action_for(t, clock + wait - timedelta(seconds=1)) is None
        assert Q.action_for(t, clock + wait) == "publish"
        clock = clock + wait
    out = Q.process(WEEK, t, cfg_of(), now=clock)
    assert (t.status, t.attempts, out.category) == ("failed", 5, "실패")
    assert "publish retry" in t.last_error
    assert Q.action_for(t, clock + timedelta(days=1)) is None


def test_success_after_failure_resets_attempts(root, fakes):
    make_week(root)
    fakes["instagram"].errors = [PublishError("일시 오류"), None]
    t = target()
    Q.process(WEEK, t, cfg_of(), now=S1_AT)
    assert t.attempts == 1
    Q.process(WEEK, t, cfg_of(), now=S1_AT + timedelta(minutes=5))
    assert (t.status, t.attempts, t.last_error) == ("published", 0, None)


def test_non_retryable_fails_immediately(root, fakes):
    make_week(root)
    fakes["tiktok"].errors = [PublishError("권한 없음 — 다시 인증하세요", retryable=False)]
    t = target(platform="tiktok")
    out = Q.process(WEEK, t, cfg_of(), now=NOW)
    assert (t.status, t.attempts, out.category) == ("failed", 1, "실패")
    assert out.retry_at is None


def test_missing_secret_and_unexpected_errors(root, fakes):
    make_week(root)
    fakes["facebook"].errors = [MissingSecret("환경변수 FACEBOOK_PAGE_TOKEN 가 없습니다.")]
    t = target(platform="facebook")
    Q.process(WEEK, t, cfg_of(), now=NOW)
    assert t.status == "failed" and t.last_error.startswith("[설정 필요]")
    fakes["tiktok"].errors = [ValueError("버그")]
    t2 = target(platform="tiktok")
    out = Q.process(WEEK, t2, cfg_of(), now=NOW)
    assert t2.status == "pending" and out.category == "재시도 대기" and "ValueError" in t2.last_error


def test_missing_render_or_script_fails_non_retryable(root, fakes):
    make_week(root, render=False)
    t = target()
    Q.process(WEEK, t, cfg_of(), now=NOW)
    assert t.status == "failed" and "manifest" in t.last_error and fakes["instagram"].calls == []


def test_kind_mismatch_fails(root, fakes):
    make_week(root)
    t = target("2026W42-B01", "instagram", "blog", B_AT)
    Q.process(WEEK, t, cfg_of(), now=NOW)
    assert t.status == "failed" and "다루지 않습니다" in t.last_error


def test_blocked_at_process_time(root, fakes):
    make_week(root)
    t = target()
    out = Q.process(WEEK, t, cfg_of(), now=NOW, blocked={"2026W42-S01"})
    assert t.status == "skipped" and out.category == "건너뜀" and fakes["instagram"].calls == []


def test_ensure_local_called_when_files_missing(root, fakes, monkeypatch):
    from studio.publish import storage

    make_week(root, local=False, remote=True)
    calls = []

    def fake_ensure(week, asset, **kw):
        calls.append((week, asset.asset_id))
        for rel in asset.files.values():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")

    monkeypatch.setattr(storage, "ensure_local", fake_ensure)
    seen = []
    fakes["tiktok"].on_publish = lambda ctx: seen.append(ctx.file("video").exists())
    Q.process(WEEK, target(platform="tiktok"), cfg_of(), now=NOW)
    assert calls == [(WEEK, "2026W42-S01")] and seen == [True]


def test_ensure_local_failure_is_left_to_publisher(root, fakes, monkeypatch):
    from studio.publish import storage

    make_week(root, local=False, remote=True)

    def boom(week, asset, **kw):
        raise storage.StorageError("내려받기 실패", retryable=True)

    monkeypatch.setattr(storage, "ensure_local", boom)
    t = target()
    out = Q.process(WEEK, t, cfg_of(), now=NOW)
    assert t.status == "published" and any("복원 실패" in line for line in out.log)


def test_dry_run_skips_download(root, fakes, monkeypatch):
    from studio.publish import storage

    make_week(root, local=False)
    monkeypatch.setattr(storage, "ensure_local", lambda *a, **k: pytest.fail("dry-run 에서 내려받으면 안 됨"))
    out = Q.process(WEEK, target(), cfg_of(), now=NOW, dry_run=True)
    assert fakes["instagram"].calls == [("publish", "2026W42-S01", True)]
    assert any("[dry-run]" in line for line in out.log)


# ---------------------------------------------------------------- run_week

def _save(*targets: PublishTarget) -> None:
    Q.save_queue(PublishQueue(week=WEEK, targets=list(targets)))


def test_run_week_claims_with_uploading_and_records(root, fakes):
    make_week(root)
    _save(target())
    seen = []
    fakes["instagram"].on_publish = lambda ctx: seen.append(Q.load_queue(WEEK).find("instagram", "2026W42-S01").status)
    outs = Q.run_week(WEEK, cfg_of(), now=S1_AT, media=False)
    assert seen == ["uploading"]                                  # 처리 중에는 다른 실행이 못 가져간다
    assert [o.category for o in outs] == ["성공"]
    t = Q.load_queue(WEEK).find("instagram", "2026W42-S01")
    assert (t.status, t.remote_id) == ("published", "instagram-2026W42-S01")


def test_run_week_processing_uses_check(root, fakes):
    make_week(root)
    _save(target(status="processing", remote_id="container-1", at=S1_AT))
    outs = Q.run_week(WEEK, cfg_of(), now=NOW, media=False)        # 공개 시각 전이어도 check 는 한다
    assert fakes["instagram"].calls == [("check", "2026W42-S01", False)]
    assert outs[0].action == "check" and Q.load_queue(WEEK).targets[0].status == "published"


def test_run_week_check_retryable_failure_stays_processing(root, fakes):
    make_week(root)
    fakes["instagram"].check_errors = [PublishError("아직 응답 없음", retryable=True)]
    _save(target(status="processing", remote_id="container-1"))
    Q.run_week(WEEK, cfg_of(), now=NOW, media=False)
    t = Q.load_queue(WEEK).targets[0]
    assert (t.status, t.attempts, t.remote_id) == ("processing", 1, "container-1")
    assert Q.action_for(t, NOW + timedelta(minutes=4)) is None
    assert Q.action_for(t, NOW + timedelta(minutes=5)) == "check"


def test_run_week_stale_uploading_fails(root, fakes):
    make_week(root)
    _save(target(status="uploading", updated_at=NOW - timedelta(hours=4)))
    outs = Q.run_week(WEEK, cfg_of(), now=NOW, media=False)
    t = Q.load_queue(WEEK).targets[0]
    assert t.status == "failed" and "publish mark" in t.last_error and outs[0].category == "실패"
    assert fakes["instagram"].calls == []


def test_run_week_limit_per_platform_shared_across_weeks(root, fakes):
    blogs = [(f"2026W42-B0{i}", "blog", B_AT + timedelta(minutes=i)) for i in (1, 2, 3)]
    make_week(root, items=blogs)
    _save(*[target(i, "tistory", "blog", at=at) for i, _, at in blogs])
    used = Q.Counter()
    outs = Q.run_week(WEEK, cfg_of(), now=B_AT + timedelta(hours=1), media=False, used=used)
    assert [o.category for o in outs] == ["성공", "성공", "한도 대기"]       # 티스토리 기본 상한 2
    assert Q.load_queue(WEEK).find("tistory", "2026W42-B03").status == "pending"
    # 같은 실행(used 공유)에서 다른 주차를 돌려도 상한은 이어진다
    outs2 = Q.run_week(WEEK, cfg_of(), now=B_AT + timedelta(hours=1), media=False, used=used)
    assert [o.category for o in outs2] == ["한도 대기"]


def test_run_week_custom_limit_from_config(root, fakes):
    make_week(root)
    _save(target("2026W42-S01", "tiktok"), target("2026W42-S02", "tiktok", at=S2_AT))
    outs = Q.run_week(WEEK, cfg_of(tiktok={"max_per_run": 1}), now=S2_AT, media=False)
    assert [o.category for o in outs] == ["성공", "한도 대기"]


def test_run_week_dry_run_writes_separate_file(root, fakes):
    make_week(root)
    _save(target())
    before = Q.queue_path(WEEK).read_text(encoding="utf-8")
    Q.run_week(WEEK, cfg_of(), now=S1_AT, dry_run=True, media=False)
    assert Q.queue_path(WEEK).read_text(encoding="utf-8") == before
    dry = read_model(Q.dryrun_path(WEEK), PublishQueue)
    assert dry.targets[0].status == "published"
    assert fakes["instagram"].calls == [("publish", "2026W42-S01", True)]


def test_media_fallback_uploads_missing_urls(root, fakes, monkeypatch):
    from studio.publish import storage

    make_week(root)
    _save(target(), target(platform="youtube"))
    calls = []
    monkeypatch.setattr(storage, "upload_media", lambda week, asset_ids=None, dry_run=False: calls.append((week, list(asset_ids), dry_run)))
    Q.run_week(WEEK, cfg_of(), now=S1_AT)
    assert calls == [(WEEK, ["2026W42-S01"], False)]


def test_missing_publisher_module_fails_non_retryable(root, monkeypatch):
    make_week(root)

    def missing(platform):
        raise ModuleNotFoundError("No module named 'studio.publish.tiktok'", name="studio.publish.tiktok")

    monkeypatch.setattr(registry, "get_publisher", missing)
    t = target(platform="tiktok")
    Q.process(WEEK, t, cfg_of(), now=NOW)
    assert t.status == "failed" and "불러오지 못했습니다" in t.last_error


def test_concurrent_runs_do_not_double_publish(root, fakes):
    """두 실행(예: 정기 due 와 수동 due)이 겹쳐도 uploading 표시 덕분에 대상마다 한 번만 올린다."""
    import threading
    import time

    make_week(root)
    _save(target("2026W42-S01"), target("2026W42-S02", at=S1_AT))
    fakes["instagram"].on_publish = lambda ctx: time.sleep(0.3)
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        Q.run_week(WEEK, cfg_of(), now=S1_AT, media=False)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    published = sorted(c[1] for c in fakes["instagram"].calls)
    assert published == ["2026W42-S01", "2026W42-S02"]
    assert {t.status for t in Q.load_queue(WEEK).targets} == {"published"}

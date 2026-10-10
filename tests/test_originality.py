"""독창성 검사(원고 단계·아카이브·CLI) 테스트. STUDIO_ROOT 를 tmp_path 로 돌린다. 네트워크·ffmpeg 를 쓰지 않는다."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from studio import cliutil, config, paths
from studio.cli import main as _studio_main
from studio.config import Config
from studio.models import AssetOriginality, CheckResult, OriginalityReport, WeekPlan, PlanItem, read_model, write_model
from studio.originality import archive as A
from studio.originality import text as T
from studio.originality.checker import CheckError, run_check
from studio.originality.thresholds import Rule, Thresholds
from studio.planning import build_brief

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W42"
PREV = "2026-W41"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def studio_main(argv: list[str]) -> int:
    # cliutil.group 은 id(subparsers) 로 묶음을 기억한다 — 한 프로세스에서 main 을 여러 번 부르면
    # 가비지 수집된 이전 파서의 id 가 재사용될 수 있어 비우고 부른다.
    cliutil._GROUPS.clear()
    return _studio_main(argv)


def fx(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def pool() -> list[dict]:
    return fx("orig_shorts_pool.json")["items"]


def put(data: dict, n: int, week: str = WEEK, kind: str = "short", **upd) -> str:
    d = copy.deepcopy(data)
    d.update(upd)
    aid = {"short": paths.short_id, "cardnews": paths.cardnews_id, "blog": paths.blog_id}.get(kind)
    d["id"] = aid(week, n) if aid else paths.longform_id(week, n)
    d["week"] = week
    sub = {"short": "shorts", "longform": "longform", "cardnews": "cardnews", "blog": "blog"}[kind]
    p = paths.content_dir(week) / sub / f"{d['id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d["id"]


def cfg(**orig) -> Config:
    return Config.model_validate({"originality": orig})


def check(week: str = WEEK, stage: str = "script", only=None, **orig) -> OriginalityReport:
    report, _, _ = run_check(week, stage, only=only, cfg=cfg(**orig))
    return report


def asset(report: OriginalityReport, aid: str) -> AssetOriginality:
    return next(a for a in report.assets if a.asset_id == aid)


def metric(a: AssetOriginality, name: str) -> CheckResult:
    return next(c for c in a.checks if c.metric == name)


# ---------------------------------------------------------------- 텍스트 도구

def test_normalize_and_minhash():
    assert T.normalize("복습, 10분! (하루)") == "복습10분하루"
    assert T.normalize("복습 10분과 3.5일", numbers=True) == "복습0분과0일"
    assert T.normalize("띄 어 쓰 기") == T.normalize("띄어쓰기")
    a = T.shingles(T.normalize("같은 30분을 쓴다면 어떻게 복습해야 일주일 뒤에 더 많이 남을까요"))
    b = T.shingles(T.normalize("같은 30분을 쓴다면 어떻게 공부해야 한 달 뒤에 더 많이 남을까요"))
    exact = T.jaccard(a, b)
    est = T.minhash_sim(T.minhash(a), T.minhash(b))
    assert abs(est - exact) < 0.15
    assert T.minhash_sim(T.minhash(a), T.minhash(a)) == 1.0
    assert T.minhash_sim(T.minhash(set()), T.minhash(a)) == 0.0
    sig = T.minhash(a)
    assert len(sig) == 128 and all(isinstance(x, int) and 0 <= x < 2**32 for x in sig)
    # 서명은 실행마다 같아야 한다(아카이브 호환)
    assert sig == T.minhash(set(a))


def test_serial_title_patterns():
    for t in ["기억력 퀴즈 #12", "기억력 퀴즈 제3탄", "복습법 2탄", "EP.4 집중력 기르기", "집중력 이야기 3편",
              "오늘의 문제 (5)", "시즌2 두뇌 퀴즈", "Part 2: 오답 노트"]:
        assert T.serial_marker(t), t
    for t in ["3가지 복습 방법", "5분 만에 외우는 법", "하루 10분 복습, 언제 해야 오래 남을까", "1년 계획 세우기",
              "영화 3편 비교로 배우는 기억", "No.1 공부 습관", "#공부법 정리"]:
        assert T.serial_marker(t) is None, t


def test_title_head_and_josa():
    assert T.title_head("[기억력 퀴즈] 오늘의 문제 3번") == "기억력 퀴즈 오늘의"
    assert T.title_head("하루 15분 복습, 언제") == T.title_head("하루 10분 복습, 언제")
    assert T.josa("S01", "와/과") == "S01과" and T.josa("S04", "와/과") == "S04와"
    assert T.josa("S09", "은/는") == "S09는" and T.josa("L1", "은/는") == "L1은"


def test_thresholds_override():
    th = Thresholds({"script_sim": {"warn_at": 0.4}, "longest_freeze": {"short": [5, 9]}, "tag_sim": "off", "xyz": 1})
    assert th.rule("script_sim", "short").warn_at == 0.4 and th.rule("script_sim", "short").block_at == 0.5
    assert th.rule("longest_freeze", "short") == Rule(5, 9)
    assert th.rule("longest_freeze", "longform").warn_at == 20
    assert th.rule("tag_sim", "short").verdict(1.0) == "pass"
    assert th.unknown == ["xyz"]
    assert Rule(40, 28, "le").verdict(10) == "block" and Rule(40, 28, "le").verdict(35) == "warn" and Rule(40, 28, "le").verdict(60) == "pass"
    assert Thresholds({"require_voice": True}).rule("voice_scenes", "longform").block_at == 0
    with pytest.raises(ValueError):
        Thresholds({"script_sim": "많이"}).rule("script_sim", "short")


# ---------------------------------------------------------------- 쌍 판정

def test_similar_shorts_blocked(root):
    put(fx("orig_short_base.json"), 1)
    put(fx("orig_short_similar.json"), 2)
    report = check()
    s1, s2 = asset(report, "2026W42-S01"), asset(report, "2026W42-S02")
    assert report.verdict == "block"
    assert s2.verdict == "block"
    sim = metric(s2, "script_sim")
    assert sim.value >= 0.5 and sim.verdict == "block" and sim.compared_with == "2026W42-S01"
    assert metric(s2, "hashtag_same").verdict == "block"
    # 앞선 원본(S01)은 본문 유사도로 탓하지 않는다
    assert metric(s1, "script_sim").verdict == "pass"
    # 원고를 다시 쓸 때 따를 구체 지시: 상대·문장 위치·바꿀 것
    fix = next(f for f in s2.fixes if "유사" in f and "문장" in f)
    assert fix.startswith("S01과 S02의 문장 ") and "S02는" in fix and "바꿀 것" in fix
    md = (paths.content_dir(WEEK) / "originality.md").read_text(encoding="utf-8")
    assert "2026W42-S02 (short) — 차단" in md and fix in md
    saved = read_model(paths.content_dir(WEEK) / "originality.json", OriginalityReport)
    assert saved.verdict == "block" and saved.blocked()[0].asset_id == "2026W42-S02"


def test_numbers_only_pair_blocked(root):
    put(fx("orig_short_base.json"), 1)
    put(fx("orig_short_numbers.json"), 2)
    a = asset(check(), "2026W42-S02")
    num, raw = metric(a, "script_sim_num"), metric(a, "script_sim")
    assert num.verdict == "block" and num.value >= 0.95
    assert raw.value < num.value - 0.15
    assert any(f.startswith("숫자만 바꾼 재탕") for f in a.fixes)
    assert metric(a, "title_prefix").verdict == "block"   # '하루 0분 복습' 앞머리 반복


def test_distinct_shorts_pass(root):
    p = pool()
    for n, data in enumerate([fx("orig_short_base.json"), fx("orig_short_distinct.json"), p[5], p[0], p[6], p[7], p[8]], start=1):
        put(data, n)
    report = check(voice_week="off")
    for a in report.assets:
        assert a.verdict == "pass", (a.asset_id, [(c.metric, c.value) for c in a.checks if c.verdict != "pass"])
    s2 = asset(report, "2026W42-S02")
    assert metric(s2, "script_sim").value < 0.1 and metric(s2, "script_tfidf").value < 0.2
    assert 0 < metric(s2, "fixed_phrase_ratio").value < 0.1   # 공통 CTA 는 고정 문구로 잡히되 기준 아래


def test_fixed_phrases_only_from_frame_positions(root):
    """본문 문장이 여러 편에 복사되면 '고정 문구'가 아니라 본문 재사용으로 잡는다."""
    base = fx("orig_short_base.json")
    for n in (1, 2, 3):
        put(base, n, title=f"다른 제목 {'가나다'[n - 1]}", hashtags=[f"#t{n}a", f"#t{n}b"])
    report = check(voice_week="off", format_run="off")
    s1, s3 = asset(report, "2026W42-S01"), asset(report, "2026W42-S03")
    fp = metric(s1, "fixed_phrase_ratio")
    # 훅(첫 문장)·CTA 는 세 편이 같아 고정 문구로 잡히지만, 본문(퀴즈·목록) 문장은 고정 문구에 넣지 않는다
    assert "어제 외운 단어" in fp.detail and "같은 30분" not in fp.detail and fp.value < 0.25
    assert s1.verdict != "block"
    assert metric(s3, "script_sim").value > 0.95 and s3.verdict == "block"


def test_boilerplate_ratio_warns(root):
    p = pool()
    intro = "안녕하세요, 하루 한 문제로 머리를 깨우는 기억력 교실입니다."
    for n, data in enumerate([p[0], p[5], p[6], p[7]], start=1):
        d = copy.deepcopy(data)
        d["chapters"][0]["scenes"][0]["narration"].insert(0, intro)
        put(d, n)
    a = asset(check(voice_week="off"), "2026W42-S04")
    fp = metric(a, "fixed_phrase_ratio")
    assert fp.verdict in ("warn", "block") and "안녕하세요, 하루 한 문제로" in fp.detail


# ---------------------------------------------------------------- 형식 순환

def test_same_format_consecutive(root):
    p = pool()
    for n, data in enumerate([p[0], p[1], p[2]], start=1):
        put(data, n)
    report = check(voice_week="off")
    s1, s2, s3 = (asset(report, f"2026W42-S0{i}") for i in (1, 2, 3))
    assert metric(s1, "format_run").value == 1
    assert metric(s2, "format_run").value == 2 and metric(s2, "format_run").verdict == "warn"
    assert metric(s3, "format_run").value == 3 and metric(s3, "format_run").verdict == "block"
    assert metric(s3, "script_sim").verdict == "pass"         # 글은 서로 다르다
    assert any("3편 연속" in f and "quiz" in f for f in s3.fixes)


def test_format_weekly_limit(root):
    p = pool()
    order = [p[0], p[5], p[1], p[6], p[2], p[7], p[3], p[8], p[4]]   # quiz 5편을 섞어 배치
    for n, data in enumerate(order, start=1):
        put(data, n)
    report = check(voice_week="off")
    assert all(metric(a, "format_run").value == 1 for a in report.assets)
    assert metric(asset(report, "2026W42-S05"), "format_count").verdict == "pass"
    q4, q5 = asset(report, "2026W42-S07"), asset(report, "2026W42-S09")
    assert metric(q4, "format_count").value == 4 and metric(q4, "format_count").verdict == "warn"
    assert metric(q5, "format_count").value == 5 and metric(q5, "format_count").verdict == "block"


def test_format_run_continues_from_last_week(root):
    p = pool()
    put(p[1], 13, week=PREV)
    put(p[2], 14, week=PREV)
    assert studio_main(["archive", "add", "--week", PREV]) == 0
    put(p[0], 1)
    a = asset(check(voice_week="off"), "2026W42-S01")
    assert metric(a, "format_run").value == 3 and metric(a, "format_run").verdict == "block"


# ---------------------------------------------------------------- 제목·메타

def test_serial_title_blocked(root):
    put(fx("orig_short_base.json"), 1, title="하루 10분 복습 제3탄")
    a = asset(check(), "2026W42-S01")
    assert metric(a, "serial_title").verdict == "block"
    assert any("'제3탄'" in f for f in a.fixes)


def test_title_prefix_repeat(root):
    p = pool()
    put(p[0], 1, title="기억 실험 노트: 밤샘 공부의 함정")
    put(p[5], 2, title="기억 실험 노트: 시험 일주일 계획")
    a = asset(check(voice_week="off"), "2026W42-S02")
    tp = metric(a, "title_prefix")
    assert tp.verdict == "block" and tp.compared_with == "2026W42-S01"


# ---------------------------------------------------------------- 사람 개입·고지·블로그

def test_longform_voice_blog_card_disclosure(root):
    base = fx("orig_short_base.json")
    lf = copy.deepcopy(base)
    lf["format"] = "longform"
    put(lf, 1, kind="longform", synthetic_media=False)
    put({"title": "표 없는 글", "keyword": "복습", "body_markdown": "## 소제목\n\n복습은 나눠서 합니다. 다음 날 다시 봅니다."}, 1, kind="blog")
    put({"title": "표 있는 글", "keyword": "필기", "body_markdown": "## 비교\n\n| 방법 | 장점 |\n|---|---|\n| 손 | 이해 |\n"}, 2, kind="blog")
    put({"title": "카드", "caption": "복습 요령 정리", "synthetic_media": True,
         "slides": [{"kind": "cover", "title": "복습"}, {"kind": "body", "title": "다음 날", "body": "핵심 3가지를 떠올립니다."}]}, 1, kind="cardnews")
    report = check()
    lfa = asset(report, "2026W42-L1")
    assert metric(lfa, "voice_scenes").verdict == "warn"
    assert metric(lfa, "ai_disclosure").verdict == "warn"
    assert metric(asset(report, "2026W42-B01"), "blog_visuals").verdict == "block"
    assert metric(asset(report, "2026W42-B02"), "blog_visuals").verdict == "pass"
    assert metric(asset(report, "2026W42-C01"), "ai_disclosure").verdict == "warn"
    strict = check(require_voice=True)
    assert metric(asset(strict, "2026W42-L1"), "voice_scenes").verdict == "block"


def test_voice_week_flags_only_deficit(root):
    p = pool()
    for n, data in enumerate([p[0], p[5], p[6], p[7]], start=1):
        put(data, n)
    report = check()
    flagged = [a.asset_id for a in report.assets if metric(a, "voice_week").verdict != "pass"]
    assert flagged == ["2026W42-S02", "2026W42-S03", "2026W42-S04"]   # 모자란 3편만, 뒤에서부터
    strict = check(require_voice=True)
    assert {a.asset_id for a in strict.assets if metric(a, "voice_week").verdict == "block"} == set(flagged)


# ---------------------------------------------------------------- 계획 순서·파생 관계

def test_plan_order_and_source_relation(root):
    put(fx("orig_short_base.json"), 1)
    put(fx("orig_short_similar.json"), 2)
    t0 = datetime(2026, 10, 12, 12, tzinfo=paths.KST)
    plan = WeekPlan(week=WEEK, theme="복습", items=[
        PlanItem(id="2026W42-S01", kind="short", topic="a", publish_at=t0 + timedelta(days=1)),
        PlanItem(id="2026W42-S02", kind="short", topic="b", publish_at=t0),
    ])
    write_model(paths.content_dir(WEEK) / "plan.json", plan)
    report = check()
    # 발행 순서가 S02 → S01 이므로 뒤에 나가는 S01 을 고치게 한다
    assert metric(asset(report, "2026W42-S01"), "script_sim").verdict == "block"
    assert metric(asset(report, "2026W42-S02"), "script_sim").verdict == "pass"
    plan.items[0].source_id = "2026W42-S02"
    write_model(paths.content_dir(WEEK) / "plan.json", plan)
    assert metric(asset(check(), "2026W42-S01"), "script_sim").value == 0.0


# ---------------------------------------------------------------- 아카이브

def test_archive_add_then_compare_with_past(root):
    put(fx("orig_short_base.json"), 1, week=PREV)
    put(pool()[5], 2, week=PREV)
    assert studio_main(["archive", "add", "--week", PREV]) == 0
    idx = json.loads(paths.archive_index().read_text(encoding="utf-8"))
    assert idx["version"] == 1 and [a["asset_id"] for a in idx["assets"]] == ["2026W41-S01", "2026W41-S02"]
    e = idx["assets"][0]
    for key in ("asset_id", "kind", "week", "title", "title_head", "hashtags", "minhash", "minhash_num", "skeleton",
                "thumb_phash", "frame_phashes", "phrase_hashes", "sentence_hashes", "added_at", "format"):
        assert key in e, key
    assert len(e["minhash"]) == 128 and all(isinstance(x, int) for x in e["minhash"])
    assert e["format"] == "quiz" and e["title_head"] == "하루 0분 복습"
    # 기획 입력(brief)이 아카이브 제목을 읽는다
    assert "2026W41-S01: 하루 10분 복습, 언제 해야 오래 남을까" in build_brief(WEEK)
    # 같은 ID 를 다시 넣으면 교체(중복 없음)
    assert studio_main(["archive", "add", "--week", PREV, "--only", "2026W41-S01"]) == 0
    assert len(json.loads(paths.archive_index().read_text(encoding="utf-8"))["assets"]) == 2
    # 다음 주에 비슷한 원고 → 과거와 비교해 차단
    put(fx("orig_short_similar.json"), 1)
    a = asset(check(), "2026W42-S01")
    sim = metric(a, "script_sim")
    assert sim.verdict == "block" and sim.compared_with == "2026W41-S01"
    assert metric(a, "sentence_reuse").compared_with == "2026W41-S01"
    assert metric(a, "hashtag_same").verdict == "block"
    assert any(f.startswith("과거 2026W41-S01과") for f in a.fixes)
    # 같은 주 재검사 때는 그 주 아카이브 항목을 자기 자신과 비교하지 않는다
    assert asset(check(week=PREV, voice_week="off"), "2026W41-S01").verdict == "pass"


def test_archive_skips_blocked_and_published_only(root, capsys):
    put(fx("orig_short_base.json"), 1)
    put(fx("orig_short_similar.json"), 2)
    assert studio_main(["check", "originality", "--week", WEEK]) == 2
    assert studio_main(["archive", "add", "--week", WEEK]) == 0
    ids = [a["asset_id"] for a in A.load_index()["assets"]]
    assert ids == ["2026W42-S01"]
    assert "2026W42-S02(독창성 차단)" in capsys.readouterr().out
    assert studio_main(["archive", "add", "--week", WEEK, "--published-only"]) == 1   # queue.json 없음


def test_archive_entry_size(root):
    """1년(쇼츠 728 + 롱폼 52 = 780개) 지문이 수 MB 이하인지: 쇼츠는 고유 화면 25장, 롱폼은 상한 60장으로 어림한다."""
    from studio.originality.checker import Checker
    from studio.originality.features import VisFx
    from studio.originality.visual import FrameHashes, ThumbHash

    put(fx("orig_short_base.json"), 1)
    ck = Checker(WEEK, "script", cfg())
    d = ck.docs[0]
    big = (1 << 256) - 1

    def line(n_frames: int) -> int:
        vis = VisFx(None, thumb=ThumbHash(big, big, big),
                    frames=FrameHashes([f"{(big - i):064x}" for i in range(n_frames)], [float(i) for i in range(n_frames)]))
        e = A.entry_from(d, ck.tx[d.asset_id], vis, ck.th)
        assert len(e["frame_phashes"]) == min(n_frames, 60)
        return len(A.dump({"assets": [e]}).splitlines()[1].encode("utf-8"))

    short, longform = line(25), line(200) + 4000   # 롱폼은 문장 해시 300개(약 3.3KB)를 더 얹어 어림
    year = 728 * short + 52 * longform
    assert year < 5 * 1024 * 1024, (short, longform, year)


def test_archive_version_mismatch(root):
    paths.archive_index().parent.mkdir(parents=True)
    paths.archive_index().write_text('{"version": 99, "assets": []}', encoding="utf-8")
    put(fx("orig_short_base.json"), 1)
    assert studio_main(["check", "originality", "--week", WEEK]) == 1


# ---------------------------------------------------------------- CLI·부분 재검사

def test_cli_exit_codes_and_only_merge(root, capsys):
    put(fx("orig_short_base.json"), 1)
    put(fx("orig_short_distinct.json"), 2)
    assert studio_main(["check", "originality", "--week", WEEK, "--stage", "script"]) == 0
    put(fx("orig_short_similar.json"), 3)
    assert studio_main(["check", "originality", "--week", WEEK, "--only", "2026W42-S03"]) == 2
    saved = read_model(paths.content_dir(WEEK) / "originality.json", OriginalityReport)
    assert [a.asset_id for a in saved.assets] == ["2026W42-S01", "2026W42-S02", "2026W42-S03"]
    assert saved.verdict == "block"
    out = capsys.readouterr().out
    assert "[차단] 2026W42-S03" in out
    with pytest.raises(CheckError):
        run_check(WEEK, "script", only=["2026W42-S99"], cfg=cfg())
    bad = paths.content_dir(WEEK) / "shorts" / "2026W42-S04.json"
    bad.write_text('{"id": "2026W42-S04"}', encoding="utf-8")
    assert studio_main(["check", "originality", "--week", WEEK]) == 1


def test_empty_week_is_error(root):
    assert studio_main(["check", "originality", "--week", WEEK]) == 1

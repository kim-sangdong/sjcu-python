"""시험 대비 장면(ox·table_blank·relations·calc·diff·ranking·flow hidden)과 공통 근거 배지 단위 테스트.

렌더(ffmpeg) 없이 장면 렌더러만 만든다: 단계 배분(plan), 요소 위치(안전영역·자막·근거 배지와 겹침), 애니메이션 프레임.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from studio.config import Config
from studio.models import Scene
from studio.video import design as D

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
PORTRAIT, LANDSCAPE = (1080, 1920), (1920, 1080)
NEW_KINDS = ("ox", "table_blank", "relations", "calc", "diff", "ranking")


@pytest.fixture(scope="module")
def cfg() -> Config:
    import yaml

    return Config.model_validate(yaml.safe_load((REPO / "channel.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def fonts(cfg):
    return D.Fonts(cfg.brand)


@pytest.fixture(scope="module")
def fixture_scenes() -> dict[str, dict]:
    data = json.loads((FIX / "video_exam_short.json").read_text(encoding="utf-8"))
    return {s["id"]: s for ch in data["chapters"] for s in ch["scenes"]}


def _ctx(cfg, fonts, size, asset_id="2026W42-S05", prev=None):
    from studio.video.scenes import SceneContext

    g = D.Geometry.make(size)
    return SceneContext(cfg=cfg, geo=g, pal=D.Palette.from_brand(cfg.brand), fonts=fonts, asset_id=asset_id, fmt="short",
                        chapter_title="오늘의 함정", root=REPO, prev=prev)


def _make(cfg, fonts, spec: dict, size=PORTRAIT, **params):
    from studio.video.scenes import get_renderer

    sc = Scene(id=spec.get("id", "s"), kind=spec["kind"], narration=spec.get("narration", []),
               params={**spec.get("params", {}), **params}, hold_seconds=spec.get("hold_seconds", 0))
    return get_renderer(sc.kind)(sc, _ctx(cfg, fonts, size))


def _rect(it, step):
    x, y, a, sp = it.props(step)
    return x, y, sp.w, sp.h, a


def _intersects(a, b, tol=2) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return min(ax + aw, bx + bw) - max(ax, bx) > tol and min(ay + ah, by + bh) - max(ay, by) > tol


def _check_layout(r, label: str) -> None:
    """모든 단계·요소: 화면 안, 위 안전영역 아래, 자막 영역 위. 근거 배지는 다른 요소와 겹치지 않는다."""
    g = r.g
    steps = sorted({b.step for b in r.plan()})
    basis = [it for it in r.items if it.name == "basis"]
    for st in steps:
        im = r.still(st)
        assert im.size == (g.W, g.H)
        for it in r.items:
            if not it.visible(st) or it.z < 0:
                continue
            x, y, w, h, a = _rect(it, st)
            if a <= 0.01:
                continue
            assert x >= -2 and x + w <= g.W + 2, f"{label} 단계 {st}: 가로 밖 ({x:.0f}, w={w})"
            assert y >= g.top_safe - 2, f"{label} 단계 {st}: 위 안전영역 침범 (y={y:.0f})"
            assert y + h <= g.sub.y + 4, f"{label} 단계 {st}: 자막 영역({g.sub.y})과 겹침 (bottom={y + h:.0f})"
        for bi in basis:
            if not bi.visible(st):
                continue
            br = _rect(bi, st)[:4]
            for it in r.items:
                if it is bi or not it.visible(st) or it.z < 0 or _rect(it, st)[4] <= 0.05:
                    continue
                assert not _intersects(br, _rect(it, st)[:4]), f"{label} 단계 {st}: 근거 배지가 '{it.name or type(it.sp).__name__}' 와 겹침"
    prev = -1
    for st in steps:                                   # 전환 중간 프레임도 그려진다
        if st != prev:
            r.frame(prev, st, r.transition_seconds(prev, st) / 2)
        prev = st


# ---------------------------------------------------------------- 근거 배지 글

def test_basis_badge_formats():
    from studio.video.scenes.exam import basis_badge, basis_badges, basis_problems, dot_date, parse_date

    assert basis_badge({"law_name": "공인중개사법", "article": "제33조 제1항", "effective_date": "2026-01-01"}) == \
        "[공인중개사법 제33조 제1항 · 2026.1.1 시행 기준]"
    assert basis_badge({"case_no": "2019다12345", "decided_at": "2020-03-12"}) == "[대법원 2019다12345 · 2020.3.12 선고]"
    assert basis_badge({"case_no": "2019헌바123", "decided_at": "2020.3.26"}) == "[헌법재판소 2019헌바123 · 2020.3.26 결정]"
    assert basis_badge({"kind": "calc", "note": "가액 = 순영업소득 ÷ 환원이율"}) == "[계산 · 가액 = 순영업소득 ÷ 환원이율]"
    assert basis_badge("표준국어대사전") == "[표준국어대사전]"
    assert basis_badge("[공인중개사법 제10조 · 2026.1.1 시행 기준]") == "[공인중개사법 제10조 · 2026.1.1 시행 기준]"
    assert basis_badge({"law_name": "공인중개사법", "article": "제10조"}) == "[공인중개사법 제10조 · 시행일 미상]"
    two = basis_badges([{"law_name": "가", "article": "제1조", "effective_date": date(2026, 7, 1)}, "나"])
    assert two == ["[가 제1조 · 2026.7.1 시행 기준]", "[나]"]
    assert basis_badges(None) == [] and basis_badges("") == []
    for raw in ("2026-01-01", "2026.1.1", "2026. 1. 1.", "20260101", "2026년 1월 1일"):
        assert dot_date(raw) == "2026.1.1", raw
    assert parse_date("2026-02-30") is None and parse_date(True) is None
    assert any("시행일" in m for m in basis_problems({"law_name": "공인중개사법", "article": "제10조"}))
    assert any("선고일" in m for m in basis_problems({"case_no": "2019다1"}))
    assert basis_problems({"law_name": "공인중개사법", "article": "제10조", "effective_date": "2026-01-01"}) == []


def test_basis_badge_matches_question_bank_format():
    """문제은행 Basis.badge() 와 같은 글이 나온다(원고 params.basis 에 badges[0] 를 그대로 넣어도 같은 모양)."""
    bank = pytest.importorskip("studio.bank.models")
    from studio.video.scenes.exam import basis_badge

    cases = [
        {"kind": "law", "law_name": "공인중개사법", "article": "제33조 제1항 제6호", "effective_date": "2026-01-01", "quote": "x" * 10},
        {"kind": "law", "law_name": "국토의 계획 및 이용에 관한 법률 시행령", "article": "별표 1", "effective_date": "2025-12-30"},
        {"kind": "precedent", "case_no": "2019다12345", "decided_at": "2020-05-14"},
        {"kind": "precedent", "case_no": "2019헌바123", "decided_at": "2020-03-26"},
        {"kind": "calc", "note": "직접환원법"},
    ]
    for c in cases:
        b = bank.Basis.model_validate(c)
        assert basis_badge(c) == b.badge() == basis_badge(b.badge())


def test_badge_shortens_long_law_names_only_for_screen():
    from studio.video.scenes.exam import badge_markup, shorten_badge

    t = "[국토의 계획 및 이용에 관한 법률 시행령 제84조 제1항 · 2026.1.1 시행 기준]"
    assert shorten_badge(t) == "[국토계획법 시행령 제84조 제1항 · 2026.1.1 시행 기준]"
    assert shorten_badge("[공인중개사법 제10조 · 2026.1.1 시행 기준]") == "[공인중개사법 제10조 · 2026.1.1 시행 기준]"
    assert badge_markup("[가 제1조 · 2026.1.1 시행 기준]") == "[**가 제1조** · 2026.1.1 시행 기준]"
    assert badge_markup("[가 제1조 · 2026.1.1 시행 기준]", split=True) == "[**가 제1조** ·\n2026.1.1 시행 기준]"


# ---------------------------------------------------------------- 레지스트리·레이아웃

def test_registry_has_exam_kinds():
    from studio.video.scenes import available

    assert set(NEW_KINDS) <= set(available())


@pytest.mark.parametrize("size", [LANDSCAPE, PORTRAIT], ids=["landscape", "portrait"])
@pytest.mark.parametrize("sid", ["ox1", "tb1", "rel1", "calc1", "diff1", "rank1", "flow1"])
def test_exam_scene_layouts_all_variants(cfg, fonts, fixture_scenes, sid, size):
    """새 kind × 변형 3벌 × 가로/세로: 화면 안, 자막·안전영역 침범 없음, 근거 배지 겹침 없음, 넘침 경고 없음."""
    spec = fixture_scenes[sid]
    for v in range(3):
        r = _make(cfg, fonts, spec, size, variant=v)
        assert r.variant == v
        _check_layout(r, f"{sid} v{v} {'세로' if size == PORTRAIT else '가로'}")
        overflow = [w for w in r.warnings if "넘" in w or "줄였습니다" in w and "글자가" in w]
        assert not overflow, overflow
        r.release()


def test_variant_is_hashed_and_avoids_repeating_previous(cfg, fonts, fixture_scenes):
    from studio.video.scenes import get_renderer

    spec = fixture_scenes["ox1"]
    seen = set()
    for i in range(24):
        sc = Scene(id=f"ox{i}", kind="ox", narration=spec["narration"], params=spec["params"])
        seen.add(get_renderer("ox")(sc, _ctx(cfg, fonts, PORTRAIT)).variant)
    assert seen == {0, 1, 2}
    sc = Scene(id="ox1", kind="ox", narration=spec["narration"], params=spec["params"])
    v = get_renderer("ox")(sc, _ctx(cfg, fonts, PORTRAIT)).variant
    assert get_renderer("ox")(sc, _ctx(cfg, fonts, PORTRAIT, prev=("ox", v))).variant != v


# ---------------------------------------------------------------- 공통 근거 배지(기존 장면)

BASIS = {"law_name": "공인중개사법", "article": "제33조 제1항", "effective_date": "2026-01-01"}
EXISTING = {
    "title": dict(narration=["a", "b"], params={"title": "개설등록 결격사유 정리", "subtitle": "숫자만 외우기"}),
    "text": dict(narration=["a", "b"], params={"heading": "왜 헷갈릴까", "body": "기간이 비슷합니다.\n숫자를 바꿔 냅니다."}),
    "bullets": dict(narration=["a", "b", "c"], params={"heading": "오늘 볼 것", "items": ["결격사유", "등록기준", "보증 설정"]}),
    "compare": dict(narration=["a", "b"], params={"left_title": "지상권", "right_title": "전세권", "rows": [["지료 요건 아님", "전세금 필수"]]}),
    "timeline": dict(narration=["a", "b"], params={"events": [{"label": "계약", "text": "체결"}, {"label": "잔금", "text": "지급"}]}),
    "flow": dict(narration=["a", "b", "c"], params={"steps": ["신청", "통지", "교부"]}),
    "bars": dict(narration=["a", "b"], params={"title": "오답률", "items": [{"label": "가", "value": 60}, {"label": "나", "value": 40}], "unit": "%"}),
    "sequence": dict(narration=["a", "정답 순서", "c"], params={"items": ["신청", "통지", "교부"]}),
    "case": dict(narration=["a", "b"], params={"title": "사례", "body": "갑이 을에게 팔았습니다.\n병이 가등기했습니다.", "reconstructed": True}),
    "quote": dict(narration=["a"], params={"text": "인도와 주민등록을 마친 때에는 그 다음 날부터", "source": "주택임대차보호법"}),
    "cta": dict(narration=["a"], params={"headline": "앱에서 다시 풀기", "body": "틀린 지문만 모아 줍니다."}),
}


@pytest.mark.parametrize("size", [LANDSCAPE, PORTRAIT], ids=["landscape", "portrait"])
@pytest.mark.parametrize("kind", sorted(EXISTING))
def test_any_scene_takes_basis_badge(cfg, fonts, kind, size):
    """기존 장면도 params.basis 를 받으면 본문 아래 띠(자막 위)에 같은 형식 배지를 그리고, 본문은 그 위로 줄어든다."""
    from studio.video.scenes import get_renderer

    spec = EXISTING[kind]
    for v in range(3):
        sc = Scene(id=f"{kind}{v}", kind=kind, narration=spec["narration"], params={**spec["params"], "basis": BASIS, "variant": v})
        r = get_renderer(kind)(sc, _ctx(cfg, fonts, size))
        badge = [it for it in r.items if it.name == "basis"]
        assert len(badge) == 1 and badge[0].show == 0
        g = r.g
        x, y, w, h, _ = _rect(badge[0], 0)
        assert y + h <= g.sub.y and abs((y + h) - g.content.b) <= 1          # 자막 바로 위(본문 영역 맨 아래)
        assert r.content().b < y                                             # 본문 영역이 배지 위로 줄었다
        _check_layout(r, f"{kind} v{v}")
    plain = get_renderer(kind)(Scene(id="p", kind=kind, narration=spec["narration"], params=spec["params"]), _ctx(cfg, fonts, size))
    assert not [it for it in plain.items if it.name == "basis"] and plain.content() == plain.g.content


def test_quiz_keeps_basis_inside_explanation_in_badge_format(cfg, fonts, monkeypatch):
    from studio.video.scenes import get_renderer

    seen: list[str] = []
    orig = D.text_block

    def spy(fonts_, text, **kw):
        seen.append(text)
        return orig(fonts_, text, **kw)

    monkeypatch.setattr(D, "text_block", spy)
    params = {"question": "결격사유는?", "choices": ["가", "나"], "answer": 0, "explanation": "해설", "basis": BASIS}
    r = get_renderer("quiz")(Scene(id="q", kind="quiz", narration=["문제", "정답은 1번"], params=params, hold_seconds=3),
                             _ctx(cfg, fonts, PORTRAIT))
    # 해설 상자 폭에 한 줄로 안 들어가면 ' · ' 뒤에서 나눈다(배지 글 자체는 같은 형식 — 2026-W42 리허설 수정)
    assert "[공인중개사법 제33조 제1항 · 2026.1.1 시행 기준]" in [t.replace(" ·\n", " · ") for t in seen]
    assert not [it for it in r.items if it.name == "basis"] and r.content() == r.g.content


def test_basis_without_date_warns(cfg, fonts):
    from studio.video.scenes import get_renderer

    r = get_renderer("text")(Scene(id="t", kind="text", narration=["a"], params={"body": "본문", "basis": {"law_name": "공인중개사법",
                                                                                                         "article": "제10조"}}),
                             _ctx(cfg, fonts, PORTRAIT))
    assert any("시행일" in w for w in r.warnings)


# ---------------------------------------------------------------- ox

def test_ox_plan_timer_and_reveal(cfg, fonts, fixture_scenes):
    spec = fixture_scenes["ox1"]
    r = _make(cfg, fonts, spec, PORTRAIT, variant=0)
    beats = r.plan()
    seq = [(b.kind, b.step, b.line) for b in beats]
    assert seq[:3] == [("lead", -1, None), ("speech", 0, 0), ("speech", 1, 1)]          # 훅 → 지문
    timer = beats[3]
    assert timer.kind == "hold" and timer.live and timer.sfx == "countdown" and timer.seconds == 3.0 and timer.step == 1
    assert [(b.line, b.step) for b in beats[4:]] == [(2, r.s_rev), (3, r.s_fix), (4, r.s_basis)]   # '정답' 문장에서 공개
    audio = r.hold_audio(timer, 3.0)
    assert audio is not None and audio.size == 3 * 48000 and np.abs(audio).max() > 0
    a, b = r.still(1).copy(), r.still(1).copy()
    r.live(a, timer, 0.2, 3.0)
    r.live(b, timer, 2.6, 3.0)
    assert a.tobytes() != b.tobytes()                                                  # 타이머 막대가 실제로 줄어든다
    basis = [it for it in r.items if it.name == "basis"]
    assert len(basis) == 1 and basis[0].show == r.s_basis


def test_ox_correction_animation_frames(cfg, fonts, fixture_scenes):
    """X 지문: 공개 → 교정 단계에서 그 줄부터 아래가 밀려 자리가 열리고, 취소선이 그어지고, 정답 말풍선이 들어온다."""
    spec = fixture_scenes["ox1"]
    for size in (PORTRAIT, LANDSCAPE):
        for v in range(3):
            r = _make(cfg, fonts, spec, size, variant=v)
            assert r.fix == "strike"
            secs = r.transition_seconds(r.s_rev, r.s_fix)
            assert secs == pytest.approx(r.fix_seconds) and 1.2 <= secs <= 2.0
            n = math.ceil(secs * r.g.fps)
            assert n >= 36                                                             # 30fps 로 1.2초 이상 = 36프레임 이상
            frames = [r.frame(r.s_rev, r.s_fix, i / r.g.fps).tobytes() for i in range(n)]
            assert len(set(frames)) >= int(n * 0.8)                                    # 거의 매 프레임 움직인다
            assert r.frame(r.s_rev, r.s_fix, secs + 0.01).tobytes() == r.still(r.s_fix).tobytes()
            moved = [it for it in r.items if r.s_fix in it.keys and it.keys[r.s_fix].y is not None]
            assert moved and all(it.props(r.s_fix)[1] > it.props(r.s_rev)[1] for it in moved)
            new = [it for it in r.items if it.show == r.s_fix]
            assert len(new) >= 3                                                       # 흐림 덮개 + 취소선 + 정답 말풍선
            r.release()


def test_ox_true_statement_highlights_right_word_without_shift(cfg, fonts):
    spec = {"kind": "ox", "narration": ["이 지문 맞을까요?", "정답은 O입니다.", "다음 날부터가 맞습니다."],
            "params": {"statement": "대항력은 인도와 주민등록을 마친 다음 날부터 생긴다.", "answer": True, "right_word": "다음 날부터", "timer": 2}}
    r = _make(cfg, fonts, spec, PORTRAIT)
    assert r.fix == "marker" and r.s_fix == r.s_rev + 1 and r.s_basis is None
    assert not [it for it in r.items if r.s_fix in it.keys and it.keys[r.s_fix].y is not None]
    assert [b.seconds for b in r.plan() if b.live] == [2.0]


def test_ox_validation_and_missing_word_warning(cfg, fonts):
    from studio.video.scenes import SceneError

    base = {"kind": "ox", "narration": ["a", "b"], "params": {"statement": "지문입니다.", "answer": "X"}}
    r = _make(cfg, fonts, base, PORTRAIT, wrong_word="없는말", right_word="말")
    assert r.answer is False and r.fix is None and any("없는말" in w for w in r.warnings)
    with pytest.raises(SceneError, match="answer"):
        _make(cfg, fonts, base, PORTRAIT, answer=1)
    with pytest.raises(SceneError, match="timer"):
        _make(cfg, fonts, base, PORTRAIT, timer=-1)
    r0 = _make(cfg, fonts, base, PORTRAIT, timer=0)
    assert not [b for b in r0.plan() if b.live]
    one = _make(cfg, fonts, {"kind": "ox", "narration": ["지문 하나"], "params": {"statement": "지문", "answer": True}}, PORTRAIT)
    kinds = [b.kind for b in one.plan()]
    assert kinds == ["lead", "speech", "hold", "hold"]                               # 공개 문장이 없으면 조용히 공개


# ---------------------------------------------------------------- table_blank

def test_table_blank_steps_fill_and_highlight(cfg, fonts, fixture_scenes):
    spec = fixture_scenes["tb1"]
    r = _make(cfg, fonts, spec, PORTRAIT, variant=0)
    assert r.row_steps == [0, 0, 0, 1, 1, 1] and r.fill_steps == [2, 3] and r.hl_step == 4   # 문장 5 = 행 2 + 빈칸 2 + 강조 1
    beats = r.plan()
    think = [b for b in beats if b.live]
    assert [(b.step, b.seconds, b.sfx) for b in think] == [(1, 1.5, "think"), (2, 1.5, "think")]
    assert [(b.line, b.step) for b in beats if b.kind == "speech"] == [(i, i) for i in range(5)]
    # 빈칸 '?' 칩은 채우는 단계에 사라지고 값이 나타난다
    chips = [it for it in r.items if it.keys and any(k.alpha == 0.0 for k in it.keys.values())]
    assert len(chips) == 2
    for chip, fs in zip(chips, r.fill_steps):
        assert chip.props(fs - 1)[2] == 1.0 and chip.props(fs)[2] == 0.0
    a, b = r.still(1).copy(), r.still(1).copy()
    r.live(a, think[0], 0.1, 1.5)
    r.live(b, think[0], 0.6, 1.5)
    assert a.tobytes() != b.tobytes()                                                  # 생각할 시간에 '?' 가 깜박인다
    dimmed = [it for it in r.items if r.hl_step in it.keys and it.keys[r.hl_step].alpha == 0.35]
    assert dimmed                                                                       # 강조 행 말고는 흐려진다


def test_table_blank_few_lines_use_holds_and_validation(cfg, fonts):
    from studio.video.scenes import SceneError

    spec = {"kind": "table_blank", "narration": ["표입니다."],
            "params": {"columns": ["구분", "값"], "rows": [["가", "1"], ["나", "2"]], "blanks": [[0, 1], [1, 1]], "highlight_rows": [0, 1]}}
    r = _make(cfg, fonts, spec, PORTRAIT)
    beats = r.plan()
    assert [b.kind for b in beats] == ["lead", "speech", "hold", "hold", "hold"]
    assert [b.step for b in beats[2:]] == [1, 2, 3]
    for bad, msg in (({"rows": [["가"]]}, "칸 수"), ({"blanks": [[5, 0]]}, "blanks"), ({"highlight_rows": [9]}, "highlight_rows")):
        with pytest.raises(SceneError, match=msg):
            _make(cfg, fonts, spec, PORTRAIT, **bad)


# ---------------------------------------------------------------- relations

def test_relations_steps_and_labels_do_not_overlap(cfg, fonts, fixture_scenes):
    spec = fixture_scenes["rel1"]
    for size in (PORTRAIT, LANDSCAPE):
        for v in range(3):
            r = _make(cfg, fonts, spec, size, variant=v)
            assert r.edge_steps == [0, 1, 2] and (r.s_q, r.s_c) == (3, 4)
            assert [(b.line, b.step) for b in r.plan() if b.kind == "speech"] == [(i, i) for i in range(5)]
            last = r.max_step()
            pills = [it for it in r.items if it.enter == "pop" and it.z == 4]
            nodes = [it for it in r.items if it.z == 3]
            assert len(pills) == 3 and len(nodes) == 3
            boxes = [_rect(it, last)[:4] for it in pills + nodes]
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    if i < len(pills) or j < len(pills):
                        assert not _intersects(boxes[i], boxes[j], tol=4), f"v{v} {size}: 이름표 {i}·{j} 겹침"
            hl = [it for it in nodes if r.s_c in it.keys]
            assert len(hl) == 1                                                       # highlight: 병만 결론에서 강조
            basis = [it for it in r.items if it.name == "basis"]
            assert basis and basis[0].show == r.s_c


# ---------------------------------------------------------------- calc

def test_calc_steps_bars_and_portrait_collapse(cfg, fonts, fixture_scenes):
    spec = fixture_scenes["calc1"]
    r = _make(cfg, fonts, spec, PORTRAIT, variant=0)
    assert r.step_steps == [1, 2, 3] and r.s_bars == 4
    moved = [it for it in r.items if r.s_bars in it.keys and it.keys[r.s_bars].y is not None]
    gone = [it for it in r.items if r.s_bars in it.keys and it.keys[r.s_bars].alpha == 0.0]
    assert len(moved) == 3 and len(gone) == 1                                         # 세로: 조건을 접고 수식을 위로
    bars = [it for it in r.items if it.show == r.s_bars and it.enter == "wipe"]
    assert len(bars) == 3
    widths = [it.sp.w for it in bars]
    assert widths.index(max(widths)) == 2                                             # 450만 원(가장 큰 오답)이 가장 길다
    one = _make(cfg, fonts, {"kind": "calc", "narration": ["a"], "params": {"conditions": ["조건"], "steps": ["1+1", "= 2"], "answer": 2}},
                LANDSCAPE)
    assert [b.kind for b in one.plan()] == ["lead", "speech", "hold", "hold"]


# ---------------------------------------------------------------- diff

@pytest.mark.parametrize("n, groups", [(4, {"before": 0, "after": 1, "hl": 2, "tl": 3}), (3, {"before": 0, "after": 0, "hl": 1, "tl": 2}),
                                       (2, {"before": 0, "after": 0, "hl": 1, "tl": 1}), (1, {"before": 0, "after": 0, "hl": 1, "tl": 1})])
def test_diff_merges_steps_by_sentence_count(cfg, fonts, fixture_scenes, n, groups):
    spec = dict(fixture_scenes["diff1"], narration=fixture_scenes["diff1"]["narration"][:n])
    r = _make(cfg, fonts, spec, PORTRAIT)
    assert r.step_of == groups


def test_diff_timeline_uses_exam_config(cfg, fonts, fixture_scenes):
    from studio.video.scenes import get_renderer

    spec = fixture_scenes["diff1"]
    custom = cfg.model_copy(deep=True)
    custom.__pydantic_extra__["exam"] = {"name": "공인중개사", "round": 38, "next_exam_date": "2027-10-30"}
    sc = Scene(id="d", kind="diff", narration=spec["narration"], params=spec["params"])
    r = get_renderer("diff")(sc, _ctx(custom, fonts, LANDSCAPE))
    _, marks, _ = r._timeline_layout(r.g.content.w)
    assert len(marks) == 2 and marks[0][0] < marks[1][0]                              # 시행일(2021) 왼쪽, 시험일(2027) 오른쪽
    off = get_renderer("diff")(Scene(id="d", kind="diff", narration=spec["narration"], params={**spec["params"], "show_exam": False}),
                               _ctx(cfg, fonts, LANDSCAPE))
    assert len(off._timeline_layout(off.g.content.w)[1]) == 1
    from studio.video.scenes.exam import exam_info

    assert exam_info(Config())["next_exam_date"] == date(2026, 10, 31)                # exam 섹션이 없으면 기본값(37회)
    assert exam_info(Config())["round"] == 37 and exam_info(custom)["round"] == 38


# ---------------------------------------------------------------- ranking

def test_ranking_reveals_bottom_up_then_focus(cfg, fonts, fixture_scenes):
    spec = fixture_scenes["rank1"]
    r = _make(cfg, fonts, spec, PORTRAIT)
    assert [r.bar_step[k] for k in range(5)] == [3, 2, 1, 0, 0]                        # 5·4위 먼저, 1위가 마지막
    assert r.s_focus == 4
    focus = [it for it in r.items if it.show == r.s_focus]
    assert len(focus) == 1 and focus[0].enter == "pop"
    dim = [it for it in r.items if r.s_focus in it.keys and it.keys[r.s_focus].alpha == 0.14]
    assert dim
    nonote = _make(cfg, fonts, {"kind": "ranking", "narration": ["a"], "params": {"items": [{"label": "가", "value": 3}, {"label": "나", "value": 1}]}})
    assert any("note" in w for w in nonote.warnings)


# ---------------------------------------------------------------- flow hidden

def test_flow_hidden_think_then_reveal(cfg, fonts, fixture_scenes):
    spec = fixture_scenes["flow1"]
    for v in range(3):
        r = _make(cfg, fonts, spec, PORTRAIT, variant=v)
        assert r.hidden == [3] and r.R == 2 and r.s_reveal == max(r.node_steps) + 1
        beats = r.plan()
        think = [b for b in beats if b.live]
        assert len(think) == 1 and think[0].seconds == 2.0 and think[0].step == max(r.node_steps)
        assert beats[-1].kind == "speech" and beats[-1].step == r.s_reveal
        chip = [it for it in r.items if r.s_reveal in it.keys and it.keys[r.s_reveal].alpha == 0.0]
        assert len(chip) == 1 and chip[0].show == r.node_steps[3]
        assert [it for it in r.items if it.name == "basis"][0].show == r.s_reveal
        a, b = r.still(max(r.node_steps)).copy(), r.still(max(r.node_steps)).copy()
        r.live(a, think[0], 0.1, 2.0)
        r.live(b, think[0], 0.5, 2.0)
        assert a.tobytes() != b.tobytes()
        assert r.hold_audio(think[0], 2.0) is not None


def test_flow_without_hidden_is_unchanged(cfg, fonts):
    from studio.video.scenes import Beat, SceneError, get_renderer

    sc = Scene(id="f", kind="flow", narration=["a", "b", "c"], params={"steps": ["읽기", "넣기", "고르기"]}, hold_seconds=2)
    r = get_renderer("flow")(sc, _ctx(cfg, fonts, PORTRAIT))
    assert r.hidden == [] and not [b for b in r.plan() if b.live]
    assert [(b.kind, b.step) for b in r.plan()] == [("lead", -1), ("speech", 0), ("speech", 1), ("speech", 2), ("hold", 2)]
    assert isinstance(r.plan()[0], Beat)
    with pytest.raises(SceneError, match="hidden"):
        get_renderer("flow")(Scene(id="f", kind="flow", narration=["a"], params={"steps": ["a", "b"], "hidden": [5]}), _ctx(cfg, fonts, PORTRAIT))

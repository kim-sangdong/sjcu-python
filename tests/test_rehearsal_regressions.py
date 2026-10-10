"""2026-W42·W43 통합 리허설에서 찾은 결함의 재현 테스트(결함마다 하나 이상).

리허설: STUDIO_DRY_RUN=1 · TTS silent 로 /weekly-publish start 를 두 주 돌리고, 발행 대기열·분석·문제은행 팩·앱 검증까지 확인했다.
각 테스트 위 주석에 '증상 → 원인'을 적었다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

from studio import cliutil, config, paths
from studio.config import Config
from studio.models import CardNews, Scene, VideoScript
from studio.video import design as D

REPO = Path(__file__).resolve().parents[1]
PORTRAIT, LANDSCAPE = (1080, 1920), (1920, 1080)
BASIS_LONG = {"law_name": "국토의 계획 및 이용에 관한 법률", "article": "제77조 제1항 제1호", "effective_date": "2026-01-01"}
BASIS = {"law_name": "지방세법", "article": "제115조 제1항", "effective_date": "2026-01-01"}


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.model_validate(yaml.safe_load((REPO / "channel.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def fonts(cfg):
    return D.Fonts(cfg.brand)


def _renderer(cfg, fonts, kind, narration, params, size=PORTRAIT, asset_id="2026W42-S01", hold=0.0):
    from studio.video.scenes import SceneContext, get_renderer

    g = D.Geometry.make(size)
    ctx = SceneContext(cfg=cfg, geo=g, pal=D.Palette.from_brand(cfg.brand), fonts=fonts, asset_id=asset_id,
                       fmt="short" if size == PORTRAIT else "longform", chapter_title="리허설", root=REPO)
    sc = Scene(id="s", kind=kind, narration=narration, params=params, hold_seconds=hold)
    return get_renderer(kind)(sc, ctx)


def _rect(it, step):
    x, y, a, sp = it.props(step)
    return x, y, sp.w, sp.h, a


def _overlap(a, b, tol=2) -> bool:
    return min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > tol and min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > tol


def _basis_clear(r) -> None:
    """마지막 단계에서 근거 배지가 다른 보이는 요소와 겹치지 않는다."""
    last = max(b.step for b in r.plan())
    basis = [it for it in r.items if it.name == "basis" and it.visible(last)]
    assert basis, "근거 배지가 없다"
    br = _rect(basis[0], last)[:4]
    for it in r.items:
        if it is basis[0] or not it.visible(last) or it.z < 0 or _rect(it, last)[4] <= 0.05:
            continue
        assert not _overlap(br, _rect(it, last)[:4]), f"근거 배지가 {it.name or '요소'} 와 겹침"


# ---------------------------------------------------------------- 영상 장면

def test_table_blank_question_chip_stays_hidden_after_highlight(cfg, fonts):
    """증상: 강조 단계 뒤 채운 값 위에 '?' 칩이 흐리게 다시 보임('40%? 이하').
    원인: 칩에 '채움 단계 alpha 0' 키와 '강조 단계 alpha 0.35(흐림)' 키를 함께 달아 뒤 키가 앞 키를 덮음."""
    r = _renderer(cfg, fonts, "table_blank", ["표", "행", "90", "40", "강조"],
                  {"columns": ["용도지역", "건폐율"], "rows": [["주거", "70%"], ["상업", "90%"], ["계획관리", "40%"]],
                   "blanks": [[1, 1], [2, 1]], "highlight_rows": [0, 1], "basis": BASIS}, size=LANDSCAPE)
    last = r.T - 1
    assert r.hl_step == last
    chips = [it for it in r.items if it.keys.get(r.fill_steps[0]) is not None and it.keys[r.fill_steps[0]].alpha == 0.0
             or any(k == fs and it.keys[k].alpha == 0.0 for fs in r.fill_steps for k in it.keys)]
    assert len(chips) == 2
    for it in chips:
        assert _rect(it, last)[4] == 0.0, "강조 단계에서 '?' 칩이 다시 보인다"


def test_landscape_timeline_fits_above_two_line_basis(cfg, fonts):
    """증상: 가로 롱폼 타임라인(사건 3개 + 근거 배지 2줄)에서 마지막 사건 글이 근거 배지에 가려짐.
    원인: 세로 목록이 최소 글자로도 넘치면 경고만 하고 그대로 그림. → 간격을 좁히고, 그래도 넘치면 가로 한 줄 배치로 바꾼다."""
    params = {"title": "재산세 달력", "events": [{"label": "6월 1일", "text": "과세기준일"},
                                                 {"label": "7.16~7.31", "text": "건축물분 · 주택분 1/2"},
                                                 {"label": "9.16~9.30", "text": "토지분 · 주택분 나머지 1/2"}],
              "basis": [{"law_name": "지방세법", "article": "제114조", "effective_date": "2026-01-01"}, BASIS]}
    for v in range(3):
        r = _renderer(cfg, fonts, "timeline", ["a", "b", "c"], {**params, "variant": v}, size=LANDSCAPE, asset_id="2026W42-L1")
        assert not r.defects, (v, r.defects)
        _basis_clear(r)


def test_ox_truncated_statement_stops_render_before_encoding(cfg, fonts, tmp_path):
    """증상: 세로 쇼츠 OX 지문이 '…'로 잘린 채(빌드 종료 코드 0) 발행 직전까지 감.
    원인: 말줄임은 경고뿐이고 /weekly-publish 는 렌더 경고를 읽지 않음. → 넘침은 SceneRenderer.defects 로 모아 합성 전에 LayoutError."""
    from studio.video.compose import RenderOptions, VideoBuilder
    from studio.video.scenes import LayoutError

    long_stmt = "국토계획법상 상업지역의 건폐율 최대한도는 70퍼센트 이하의 범위에서 조례로 정한다는 내용을 길게 늘인 지문이다."
    params = {"hook": "상업지역 건폐율, **70**일까 90일까?", "statement": long_stmt, "answer": False,
              "wrong_word": "70퍼센트", "right_word": "90퍼센트", "basis": BASIS_LONG}
    r = _renderer(cfg, fonts, "ox", ["a", "b", "정답은 X", "d"], params)
    assert r.defects and any("말줄임" in d for d in r.defects)
    script = VideoScript(id="2026W42-S01", week="2026-W42", format="short", title="t", description="d",
                         chapters=[{"title": "c", "scenes": [{"id": "s", "kind": "ox", "narration": ["a", "b", "정답은 X", "d"],
                                                              "params": params}]}])
    with pytest.raises(LayoutError, match="화면에 글이 다 들어가지 않습니다"):
        VideoBuilder(script, cfg, RenderOptions(tts="silent"), tmp_path).build_scenes()
    b = VideoBuilder(script, cfg, RenderOptions(tts="silent", allow_overflow=True), tmp_path)
    b.build_scenes()                                   # 사람이 미리보기로 볼 때만 경고로 낮춘다
    assert any("말줄임" in w for w in b.warnings)


@pytest.mark.parametrize("v", [0, 1, 2])
def test_ox_explanation_box_never_covers_basis(cfg, fonts, v):
    """증상: 세로 쇼츠 OX 해설 상자가 근거 배지를 덮음(W42 S01, W43 S01).
    원인: 해설 자리를 '본문 2.6줄'로 어림하고, 실제 상자 높이보다 작은 자리에 상자를 그대로 놓음."""
    params = {"hook": "갑구 vs 을구, 순위는 **무엇으로**?", "statement": "갑구와 을구에 나뉜 등기의 순위는 순위번호로 정한다.",
              "answer": False, "wrong_word": "순위번호로", "right_word": "접수번호로", "explanation": "다른 구 사이는 접수번호",
              "basis": BASIS_LONG, "variant": v}
    r = _renderer(cfg, fonts, "ox", ["a", "b", "정답은 X", "d", "e"], params, asset_id="2026W43-S01")
    assert not r.defects, r.defects
    _basis_clear(r)


def test_calc_three_distractors_fit_portrait(cfg, fonts):
    """증상: 문서가 허용한 오답 3개(세로)에서 막대가 근거 배지 쪽으로 넘침. 원인: 막대 두께·간격이 고정."""
    params = {"conditions": ["농지가 아닌 토지 매매", "과세표준 3억 원", "중과·감면·부가 세목 제외"],
              "steps": ["취득세 = 과세표준 × 표준세율", "표준세율 = 1천분의 40", "3억 원 × 40/1,000 = **1,200만 원**"],
              "answer": 12000000, "answer_text": "1,200만 원", "unit": "원",
              "distractors": [{"label": "농지 1천분의 30", "value": 9000000, "text": "900만 원"},
                              {"label": "상속 1천분의 28", "value": 8400000, "text": "840만 원"},
                              {"label": "주택 1천분의 10", "value": 3000000, "text": "300만 원"}],
              "basis": {"law_name": "지방세법", "article": "제11조 제1항 제7호", "effective_date": "2026-01-01"}}
    for v in range(3):
        r = _renderer(cfg, fonts, "calc", ["a", "b", "c", "d", "e"], {**params, "variant": v}, asset_id="2026W42-S02")
        assert not r.defects, (v, r.defects)
        _basis_clear(r)


def test_quiz_inline_badge_is_abbreviated_one_per_line(cfg, fonts):
    """증상: 퀴즈 해설 상자 안 근거가 정식 법령명으로 두 줄('… 법률 제6조 · / 2026.1.1 …'), 배지 둘이면 말없이 잘림.
    원인: 배지들을 한 문자열로 이어 max_lines=2 로 자름, 약칭 규칙(SCENES 1장)을 안 씀."""
    from studio.video.scenes.exam import fit_inline_badges

    r = _renderer(cfg, fonts, "quiz", ["문제", "정답은 5번", "해설"],
                  {"question": "국토계획법상 용도지역에 해당하지 않는 것은?", "choices": ["도시지역", "관리지역", "농림지역", "자연환경보전지역", "개발진흥지구"],
                   "answer": 4, "explanation": "개발진흥지구는 용도지구다.",
                   "basis": [{"law_name": "국토의 계획 및 이용에 관한 법률", "article": "제6조", "effective_date": "2026-01-01"},
                             {"law_name": "민법", "article": "제280조 제1항", "effective_date": "2026-01-01"}]},
                  size=LANDSCAPE, hold=5)
    assert not r.defects, r.defects
    text = fit_inline_badges(r, 43, 1000)
    assert text.splitlines()[0] == "[국토계획법 제6조 · 2026.1.1 시행 기준]"
    narrow = fit_inline_badges(r, 43, 330).splitlines()       # 약칭으로도 길면 '·' 뒤에서 나눈다
    assert any(line.endswith("·") for line in narrow) and not any(line.endswith("시행") for line in narrow)


def test_quiz_five_choices_fit_landscape(cfg, fonts):
    """증상: 가로 롱폼 5지선다가 최소 글자에서도 자막 쪽으로 20px 넘침. 원인: 보기 상자 여백이 고정 → 넘치면 여백을 줄인다."""
    r = _renderer(cfg, fonts, "quiz", ["문제", "정답은 5번", "해설"],
                  {"question": "국토계획법상 용도지역에 해당하지 않는 것은?", "choices": ["도시지역", "관리지역", "농림지역", "자연환경보전지역", "개발진흥지구"],
                   "answer": 4, "explanation": "국토는 도시·관리·농림·자연환경보전지역으로 나눈다. 개발진흥지구는 용도지구다.",
                   "basis": {"law_name": "국토의 계획 및 이용에 관한 법률", "article": "제6조", "effective_date": "2026-01-01"}, "variant": 1},
                  size=LANDSCAPE, hold=5, asset_id="2026W42-L1")
    assert not r.defects, r.defects


def test_wrap_breaks_long_dotted_word_after_middle_dot(fonts):
    """증상: '국토는 / 도시·관리·농림·자연환경보전지역으 / 로' — 첫 줄에 한 어절만 남고 낱말 중간에서 잘림.
    원인: 줄보다 긴 어절은 글자 단위로만 끊음. → 가운뎃점·빗금·물결·쉼표 뒤에서 먼저 나눈다."""
    f = fonts.get("regular", 54)
    t = "국토는 도시·관리·농림·자연환경보전지역으로 나눈다."
    spans = D.wrap_spans(t, f, 740)
    lines = [t[a:b] for a, b in spans]
    assert lines[0].startswith("국토는 도시·") and lines[0].endswith("·")
    assert "자연환경보전지역으로" in lines[1]
    assert not D._broke(t, spans)


def test_dateless_string_basis_is_a_problem():
    """증상: '[부동산 실권리자명의 등기에 관한 법률 제4조 제2항]' 처럼 시행일 없는 문자열 근거가 경고 없이 화면에 나감(W43)."""
    from studio.video.scenes.exam import basis_problems

    assert basis_problems("[부동산 실권리자명의 등기에 관한 법률 제4조 제2항]")
    assert basis_problems("[대법원 2019다12345]")
    assert basis_problems("[공인중개사법 제10조 · 2026.1.1 시행 기준]") == []
    assert basis_problems("[대법원 2019다12345 · 2020.3.12 선고]") == []
    assert basis_problems("표준국어대사전") == []


# ---------------------------------------------------------------- 카드뉴스

def _card_slides(cfg, slides):
    from studio.cardnews.render import build_slides

    card = CardNews(id="2026W42-C01", week="2026-W42", title="t", caption="c", slides=slides)
    return build_slides(card, cfg)


def test_cardnews_body_slide_draws_basis_badge(cfg):
    """증상: 표 없는 body 장의 params.basis(주택분 20만 원 예외 등)가 말없이 사라짐.
    원인: 근거 배지를 OX·숫자 표 레이아웃만 그림. → 모든 본문 장에 배지, 표지·cta 는 오류."""
    from studio.cardnews.layouts import CardRenderError, draw_slide

    body = {"kind": "body", "title": "주택분에는 예외 하나", "body": "주택분 세액이 20만 원 이하면 조례로 7월에 한꺼번에 걷을 수 있습니다."}
    plain_s, based_s = _card_slides(cfg, [{"kind": "cover", "title": "표지"}, body,
                                          {**body, "params": {"basis": {"law_name": "지방세법", "article": "제115조 제1항 제3호",
                                                                        "effective_date": "2026-01-01"}}}])[1:]
    based_s.variant = plain_s.variant
    a = np.asarray(draw_slide(plain_s).finish().convert("L"), dtype=np.int16)
    b = np.asarray(draw_slide(based_s).finish().convert("L"), dtype=np.int16)
    band = slice(1050, 1200)                                     # 본문 끝(BOTTOM) 바로 위 배지 자리
    assert np.abs(a[band] - b[band]).mean() > 1.0, "근거 배지가 그려지지 않았다"
    cover = _card_slides(cfg, [{"kind": "cover", "title": "표지", "params": {"basis": BASIS}}, body])[0]
    with pytest.raises(CardRenderError, match="표지·마무리"):
        draw_slide(cover)


# ---------------------------------------------------------------- 원고 검증(plan validate)

@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"),
                      ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    (tmp_path / "channel.yaml").write_text((REPO / "channel.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    config.load_config.cache_clear()
    cliutil._GROUPS.clear()
    yield tmp_path
    config.load_config.cache_clear()


WEEK = "2026-W42"
DRAFT_OX = {"id": "Q-PUB-f4c2fb0b", "subject": "PUB", "unit": "국토계획법", "type": "ox",
            "stem": "국토계획법상 상업지역의 건폐율 최대한도는 70퍼센트 이하의 범위에서 조례로 정한다.", "answer": False,
            "explanation": "상업지역은 90퍼센트 이하다.", "trap": "숫자바꿈",
            "basis": [{"kind": "law", "law_name": "국토의 계획 및 이용에 관한 법률", "article": "제77조 제1항 제1호",
                       "effective_date": "2026-01-01", "quote": "상업지역: 90퍼센트 이하"}],
            "difficulty": 2, "status": "draft", "created_week": "2026-W41", "updated_at": "2026-10-10T19:34:34+09:00"}


def _week(root: Path, *, ox_params: dict, status: str = "draft", extra_scene: dict | None = None) -> None:
    from studio import planning
    from studio.models import write_model

    cfg = config.load_config()
    d = paths.content_dir(WEEK)
    plan = planning.skeleton(WEEK, cfg)
    plan.items = [i for i in plan.items if i.id == "2026W42-S01"]
    plan.theme = "리허설"
    plan.items[0].topic = "건폐율"
    write_model(planning.plan_path(WEEK), plan)
    (root / "content" / "bank").mkdir(parents=True, exist_ok=True)
    (root / "content" / "bank" / "PUB.jsonl").write_text(json.dumps({**DRAFT_OX, "status": status}, ensure_ascii=False) + "\n",
                                                          encoding="utf-8")
    scenes = [{"id": "ox", "kind": "ox", "narration": ["상업지역 건폐율은?", "정답은 X입니다."], "params": ox_params}]
    if extra_scene:
        scenes.append(extra_scene)
    s = {"id": "2026W42-S01", "week": WEEK, "format": "short", "title": "상업지역 건폐율 함정", "description": "설명",
         "chapters": [{"title": "c", "scenes": scenes}]}
    (d / "shorts").mkdir(parents=True, exist_ok=True)
    (d / "shorts" / "2026W42-S01.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")


OX_PARAMS = {"statement": "상업지역 건폐율 상한은 70퍼센트 이하다.", "answer": False, "bank_id": "Q-PUB-f4c2fb0b",
             "basis": {"law_name": "국토의 계획 및 이용에 관한 법률", "article": "제77조 제1항 제1호", "effective_date": "2026-01-01"}}


def test_validate_bank_id_draft_is_error_except_rehearsal(root, monkeypatch):
    """증상: verified 문항만 쓴다는 규칙(CONTENT_GUIDE 5장)을 어떤 명령도 검사하지 않음 — bank_id 는 기록용으로만 쓰였다."""
    from studio import planning

    _week(root, ox_params=OX_PARAMS)
    f = planning.validate_week(WEEK, config.load_config())
    assert any("Q-PUB-f4c2fb0b 는 draft" in e for e in f.errors)
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    f = planning.validate_week(WEEK, config.load_config())
    assert not f.errors and any("리허설이라 경고" in w for w in f.warnings)


def test_validate_bank_id_missing_and_flipped_answer(root):
    from studio import planning

    _week(root, ox_params={**OX_PARAMS, "answer": True}, status="verified",
          extra_scene={"id": "q2", "kind": "ox", "narration": ["a", "정답은 O"],
                       "params": {"statement": "지문", "answer": True, "bank_id": "Q-PUB-00000000",
                                  "basis": OX_PARAMS["basis"]}})
    f = planning.validate_week(WEEK, config.load_config())
    assert any("정답 불일치" in e and "Q-PUB-f4c2fb0b" in e for e in f.errors)
    assert any("문제은행에 없는 문항 Q-PUB-00000000" in e for e in f.errors)


def test_validate_banned_phrase_in_nested_params_and_dateless_basis(root, monkeypatch):
    """증상: 표 칸·관계도 이름표 등 params 안쪽 글의 금지 표현, 날짜 없는 근거가 검증을 통과(최상위 문자열 params 만 보았음)."""
    from studio import planning

    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    _week(root, ox_params=OX_PARAMS,
          extra_scene={"id": "tb", "kind": "table_blank", "narration": ["a"],
                       "params": {"columns": ["구분", "내용"], "rows": [["광고", "합격 보장 강의"]],
                                  "bank_id": "Q-PUB-f4c2fb0b", "basis": "[부동산 실권리자명의 등기에 관한 법률 제4조 제2항]"}})
    f = planning.validate_week(WEEK, config.load_config())
    assert any("금지 표현 '합격 보장'" in e and "scene tb params.rows" in e for e in f.errors)
    assert any("시행일이 없습니다" in e for e in f.errors)


def test_estimate_includes_exam_timers():
    """증상: 롱폼 예상 2.8분 → 실제 3.8분. 원인: OX 타이머·빈칸 think·퀴즈 카운트다운을 길이 추정에서 뺌."""
    from studio import planning

    cfg = Config()
    base = {"id": "2026W42-L1", "week": WEEK, "format": "longform", "title": "t", "description": "d"}
    s = VideoScript(**base, chapters=[{"title": "c", "scenes": [
        {"id": "a", "kind": "ox", "narration": ["가나다"], "params": {"statement": "x", "answer": True, "timer": 3}},
        {"id": "b", "kind": "table_blank", "narration": ["가나다"], "params": {"columns": ["a", "b"], "rows": [["1", "2"]],
                                                                             "blanks": [[0, 1]], "think": 1.5}},
        {"id": "c", "kind": "quiz", "narration": ["가나다"], "params": {"question": "q", "choices": ["a", "b"], "answer": 0}}]}])
    without = sum(len(x) for x in s.narration_sentences()) / planning.CHARS_PER_SECOND / cfg.tts.speaking_rate + 0.4 * 3
    assert planning.estimate_seconds(s, cfg) == pytest.approx(without + 3 + 1.5 + 5, abs=0.01)


# ---------------------------------------------------------------- 파이프라인·기획 입력

def test_build_failure_clears_stale_pass_and_ship_detects_edits(root):
    """증상: (1) build 가 중간에 실패해도 지난번 originality-render '통과' 기록이 남아 ship 이 열림.
    (2) build 뒤에 원고를 고쳐도 ship 이 그대로 발행 — 고친 원고는 렌더·검사를 안 거침."""
    from studio import pipeline

    _week(root, ox_params=OX_PARAMS)
    pipeline._record(WEEK, "originality-render", 0, 1.0)
    st = json.loads(pipeline._state_path(WEEK).read_text(encoding="utf-8"))
    st["build_inputs"] = pipeline.content_hashes(WEEK)
    pipeline._state_path(WEEK).write_text(json.dumps(st), encoding="utf-8")
    assert pipeline.stale_since_check(WEEK) == []
    p = paths.content_dir(WEEK) / "shorts" / "2026W42-S01.json"
    p.write_text(p.read_text(encoding="utf-8").replace("상업지역 건폐율 함정", "고친 제목"), encoding="utf-8")
    assert pipeline.stale_since_check(WEEK) == ["shorts/2026W42-S01.json"]
    pipeline._reset_steps(WEEK, pipeline.BUILD_STEPS)
    st = json.loads(pipeline._state_path(WEEK).read_text(encoding="utf-8"))
    assert "originality-render" not in st["steps"] and "build_inputs" not in st


def test_ship_marks_used_bank_items(root, monkeypatch):
    """증상: 발행 뒤 bank mark-used 를 스킬 문서가 손으로 부르게 해 빠뜨리기 쉬움 → ship 이 원고의 bank_id 로 대신 부른다."""
    from studio import pipeline

    _week(root, ox_params=OX_PARAMS, extra_scene={"id": "tb", "kind": "table_blank", "narration": ["a"],
                                                  "params": {"columns": ["a", "b"], "rows": [["1", "2"]],
                                                             "bank_id": ["Q-PUB-f4c2fb0b", "Q-CIV-00000001"]}})
    calls = []
    monkeypatch.setattr(pipeline, "run_step", lambda week, step, argv, **kw: calls.append(argv) or 0)
    assert pipeline.mark_used(WEEK) == 0
    assert calls == [["bank", "mark-used", "--asset", "2026W42-S01", "--items", "Q-PUB-f4c2fb0b", "Q-CIV-00000001"]]


def test_brief_carries_last_run_report_and_formats(root):
    """증상: RUN_REPORT 의 '다음 주에 반영할 점'을 다음 주 기획이 읽지 않음, 지난주 쇼츠 형식이 brief 에 없어
    주를 넘는 '같은 형식 연속'을 피할 수 없음."""
    from studio import planning
    from studio.models import write_json

    write_json(paths.archive_index(), {"version": 1, "assets": [
        {"asset_id": "2026W42-S01", "kind": "short", "week": "2026-W42", "title": "상업지역 건폐율", "format": "ox"},
        {"asset_id": "2026W42-S02", "kind": "short", "week": "2026-W42", "title": "취득세 계산", "format": "calc"}]})
    rr = paths.content_dir("2026-W42") / "RUN_REPORT.md"
    rr.parent.mkdir(parents=True, exist_ok=True)
    rr.write_text("# 보고\n\n## 다음 주에 반영할 점\n- OX 지문은 30자 안팎\n\n## 기타\n- 무시\n", encoding="utf-8")
    b = planning.build_brief("2026-W43")
    assert "OX 지문은 30자 안팎" in b and "무시" not in b
    assert "- 2026W42-S01: 상업지역 건폐율 (쇼츠, 형식 ox)" in b
    assert "ox(2026W42-S01) → calc(2026W42-S02)" in b


def test_originality_fix_for_ox_scene_names_the_quiz_content():
    """증상: 과거 OX 쇼츠와 본문 1.00 같음 → 수정 지시가 '도입을 다른 사례로 바꿀 것'뿐(시험 장면 kind 가 지시 표에 없었음)."""
    from studio.originality.checker import CONTENT_KINDS, KIND_ACTIONS

    for k in ("ox", "table_blank", "relations", "calc", "diff", "ranking"):
        assert k in KIND_ACTIONS
    assert "지문과 정답" in KIND_ACTIONS["ox"][1]
    assert CONTENT_KINDS[:4] == ["ox", "table_blank", "relations", "calc"]


# ---------------------------------------------------------------- 분석·점검·문제은행 팩

def test_analytics_collect_now_allows_rehearsing_future_week(root, capsys):
    """증상: 리허설에서 아직 시작 안 한 주(W42)를 analytics collect --fake 하면 거부되고 시각을 줄 방법이 없음
    (publish due 에는 --now 가 있는데 analytics 에는 없었다)."""
    import argparse

    from studio.analytics.cli import register
    ap = argparse.ArgumentParser()
    register(ap.add_subparsers())
    args = ap.parse_args(["analytics", "collect", "--week", WEEK, "--fake", "--now", "2026-10-19T00:00+09:00"])
    monkey_now = datetime(2026, 10, 10, 12, 0, tzinfo=paths.KST)
    assert paths.week_monday(WEEK) > monkey_now.date()
    assert args.func(args) == 0
    m = json.loads((paths.reports_dir(WEEK) / "metrics.json").read_text(encoding="utf-8"))
    assert m["fake"] is True
    args = ap.parse_args(["analytics", "collect", "--week", WEEK, "--fake", "--now", "어제"])
    assert args.func(args) == 1


def test_doctor_reports_empty_tistory_blog_name_and_link_base():
    """증상: publish due 가 티스토리 글을 blog_name 빈칸으로 실패시키는데 doctor 는 비밀값만 봐서 몰랐다."""
    from studio.setup_cmd import config_findings

    out = config_findings(Config())
    assert ("X" in [lvl for lvl, _ in out]) and any("blog_name" in m for _, m in out)
    assert any(lvl == "!" and "link_base" in m for lvl, m in out)
    ok = config_findings(Config.model_validate({"platforms": {"tistory": {"blog_name": "https://hamjeong.tistory.com/"}},
                                                "app": {"link_base": "https://x.supabase.co/functions/v1/go"}}))
    assert ok == [("O", "티스토리 블로그 hamjeong.tistory.com")]


def test_upload_refuses_empty_pack(tmp_path):
    """증상: 검증 문항 0개로 export 한 빈 팩도 latest/ 에 올라가 앱 문항이 모두 사라질 수 있었다."""
    from studio.bank import upload
    from studio.bank.exam import bank_cfg, exam_cfg
    from studio.bank.export import build_pack
    from studio.bank.store import Bank

    c = Config.model_validate(yaml.safe_load((REPO / "channel.yaml").read_text(encoding="utf-8")))
    res = build_pack(Bank([]), exam_cfg(c), bank_cfg(c), "고지", tmp_path / "bank")
    assert res.total == 0
    with pytest.raises(upload.UploadError, match="비어 있습니다"):
        upload.plan(tmp_path / "bank")
    version, items = upload.plan(tmp_path / "bank", allow_empty=True)
    assert items and version == res.version


def test_app_contract_fixture_matches_pack_schema():
    """앱 쪽 계약 테스트(app/src/core/__tests__/studio-pack-contract.test.ts)와 같은 팩 파일이 파이썬 팩 규격에도 맞는다."""
    from studio.bank.schema import validate

    d = REPO / "app" / "src" / "core" / "__tests__" / "fixtures" / "studio-pack"
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    validate(m, "#/$defs/manifest")
    assert m["total"] > 0 and m["decks"]
    for s in m["subjects"]:
        validate(json.loads((d / s["file"]).read_text(encoding="utf-8")), "#/$defs/subjectPack")

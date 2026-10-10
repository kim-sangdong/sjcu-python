"""카드뉴스 시험 레이아웃: OX 퀴즈 장(quiz + choices ["O","X"]), OX 정답 장, 숫자 표 장(body + params.table), 근거 배지."""

from __future__ import annotations

import pytest
from PIL import Image

from studio.cardnews.layouts import CardRenderError
from studio.cardnews.render import build_slides, render_card
from studio.config import Config
from studio.models import CardNews

BASIS = {"law_name": "주택임대차보호법", "article": "제3조 제1항", "effective_date": "2023-07-19"}
TABLE = {"columns": ["구분", "용도지역", "건폐율"],
         "rows": [["전용주거", "제1종", "50%"], ["전용주거", "제2종", "50%"], ["일반주거", "제1종", "60%"], ["일반주거", "제2종", "**60%**"],
                  ["일반주거", "제3종", "**50%**"], ["준주거", "-", "70%"]],
         "highlight_rows": [3, 4]}


@pytest.fixture
def cfg() -> Config:
    return Config.model_validate({"channel": {"handle": "hamjeong_note"}, "app": {"name": "함정노트"}})


def _card(answer="X", **quiz_params) -> CardNews:
    return CardNews.model_validate({
        "id": "2026W42-C01", "week": "2026-W42", "title": "대항력 함정", "caption": "테스트",
        "slides": [
            {"kind": "cover", "title": "대항력, **당일**일까 다음 날일까?", "body": "헷갈리는 시점 한 번에"},
            {"kind": "body", "title": "주거지역 건폐율 상한", "body": "시행령 기준 상한입니다.",
             "params": {"table": TABLE, "basis": {"law_name": "국토의 계획 및 이용에 관한 법률 시행령", "article": "제84조 제1항",
                                                  "effective_date": "2026-01-01"}}},
            {"kind": "quiz", "title": "주택임대차의 대항력은 인도와 주민등록을 마친 당일부터 생긴다.",
             "params": {"choices": ["O", "X"], "answer": answer, "basis": BASIS, **quiz_params}},
            {"kind": "answer", "title": "다음 날부터입니다", "body": "인도와 주민등록을 마친 **다음 날**부터 제3자에게 효력이 생깁니다.",
             "params": {"basis": BASIS}},
            {"kind": "cta", "title": "틀린 지문은 앱이 다시"},
        ],
    })


def _pixels_differ(a: Image.Image, b: Image.Image) -> bool:
    return a.convert("RGB").tobytes() != b.convert("RGB").tobytes()


@pytest.mark.parametrize("v", [0, 1, 2])
def test_ox_and_table_slides_render_all_variants(tmp_path, cfg, v):
    from studio.cardnews.exam import OX_ANSWER, OX_QUIZ, TABLE as TABLE_LAYOUTS, special_layout

    card = _card()
    for s in card.slides:
        s.params = {**s.params, "layout": v}
    slides = build_slides(card, cfg)
    assert special_layout(slides[1]) is TABLE_LAYOUTS[v]
    assert special_layout(slides[2]) is OX_QUIZ[v]
    assert special_layout(slides[3]) is OX_ANSWER[v]
    assert special_layout(slides[0]) is None and special_layout(slides[4]) is None
    res = render_card(card, cfg, tmp_path / f"v{v}")
    assert len(res.jpgs) == 5 and res.warnings == []
    for p in res.pngs:
        with Image.open(p) as im:
            assert im.size == (1080, 1350)
    with Image.open(res.pngs[2]) as quiz, Image.open(res.pngs[3]) as ans:
        assert _pixels_differ(quiz, ans)


def test_ox_answer_parsing_is_unambiguous(cfg):
    for answer, want in (("X", 1), ("x", 1), ("O", 0), (False, 1), (True, 0), (0, 0), (2, 1)):
        slides = build_slides(_card(answer=answer), cfg)
        assert slides[2].answer == want, answer
        assert slides[3].answer == want and slides[3].choices == ["O", "X"]       # 정답 장은 앞 퀴즈를 따른다
    with pytest.raises(CardRenderError, match="해석이 갈립니다"):
        build_slides(_card(answer=1), cfg)
    with pytest.raises(CardRenderError, match='"O" 또는 "X"'):
        build_slides(_card(answer="세모"), cfg)


def test_regular_quiz_keeps_one_based_numbers(cfg):
    card = _card()
    card.slides[2].params = {"choices": ["가", "나", "다"], "answer": 2}
    slides = build_slides(card, cfg)
    assert slides[2].answer == 1                                                  # 기존 규칙(1부터) 그대로


def test_basis_badge_is_drawn_and_validated(tmp_path, cfg):
    with_basis = render_card(_card(), cfg, tmp_path / "a")
    card = _card()
    card.slides[2].params = {"choices": ["O", "X"], "answer": "X"}
    without = render_card(card, cfg, tmp_path / "b")
    with Image.open(with_basis.pngs[2]) as a, Image.open(without.pngs[2]) as b:
        # 배지 자리(아래쪽 본문 끝 근처)만 다르다
        crop = (96, 1060, 1000, 1190)
        assert _pixels_differ(a.crop(crop), b.crop(crop))
    bad = _card()
    bad.slides[2].params = {"choices": ["O", "X"], "answer": "X", "basis": {"law_name": "주택임대차보호법", "article": "제3조"}}
    with pytest.raises(CardRenderError, match="시행일"):
        render_card(bad, cfg, tmp_path / "c")


def test_table_validation_and_overflow(tmp_path, cfg):
    card = _card()
    card.slides[1].params = {"table": {"columns": ["하나"], "rows": [["1"]]}}
    with pytest.raises(CardRenderError, match="columns"):
        render_card(card, cfg, tmp_path / "a")
    card.slides[1].params = {"table": {"columns": ["가", "나"], "rows": [["1", "2", "3"]]}}
    with pytest.raises(CardRenderError, match="칸 수"):
        render_card(card, cfg, tmp_path / "b")
    card.slides[1].params = {"table": {"columns": ["가", "나"], "rows": [["아주 긴 칸 글 " * 12, "2"]] * 8}}
    with pytest.raises(CardRenderError, match="줄이"):
        render_card(card, cfg, tmp_path / "c")

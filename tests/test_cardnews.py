"""카드뉴스 렌더 테스트. STUDIO_ROOT 를 tmp_path 로 돌리고 저장소 글꼴을 그대로 쓴다."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from studio import config, paths
from studio.cardnews import cli as card_cli
from studio.cardnews.layouts import LAYOUTS, PROFILE_LINK, CardRenderError, slide_variant
from studio.cardnews.render import build_slides, render_card, render_week
from studio.cardnews.text import TextOverflow, fit_text, parse_rich, plain, sanitize, sizes
from studio.config import Config
from studio.manifest import load_manifest
from studio.models import CardNews, read_model

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W42"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


@pytest.fixture
def cfg() -> Config:
    return Config.model_validate({"channel": {"handle": "study_daily"}, "app": {"name": "복습노트"}})


def _card(name: str = "card_sample.json") -> CardNews:
    return read_model(FIX / name, CardNews)


def _place(root: Path, name: str) -> Path:
    card = json.loads((FIX / name).read_text(encoding="utf-8"))
    dst = paths.content_dir(WEEK) / "cardnews" / f"{card['id']}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / name, dst)
    return dst


# ---------------------------------------------------------------- 렌더 결과·manifest

def test_render_week_writes_jpeg_png_and_manifest(root, cfg):
    _place(root, "card_sample.json")
    done, failed = render_week(WEEK, cfg, log=lambda _: None)
    assert failed == []
    assert len(done) == 1
    res = done[0]
    assert len(res.jpgs) == 7 and len(res.pngs) == 7
    out = root / "out" / "weeks" / WEEK / "2026W42-C01"
    assert sorted(p.name for p in out.glob("*.jpg")) == [f"slide{i:02d}.jpg" for i in range(1, 8)]
    for jpg, png in zip(res.jpgs, res.pngs):
        with Image.open(jpg) as im:
            assert im.format == "JPEG" and im.size == (1080, 1350) and im.mode == "RGB"
            assert not im.info.get("progressive")          # 기본(baseline) JPEG
            assert im.info.get("icc_profile")                # sRGB 프로필
        with Image.open(png) as im:
            assert im.format == "PNG" and im.size == (1080, 1350)
        assert jpg.stat().st_size < 8 * 1024 * 1024

    asset = load_manifest(WEEK).get("2026W42-C01")
    assert asset is not None and asset.kind == "cardnews"
    assert list(asset.files) == [f"slide{i:02d}" for i in range(1, 8)]
    assert asset.files["slide01"] == f"out/weeks/{WEEK}/2026W42-C01/slide01.jpg"
    assert all((root / v).exists() and v.endswith(".jpg") for v in asset.files.values())
    assert asset.meta["slide_count"] == 7
    assert len(asset.meta["alt_texts"]) == 7
    assert PROFILE_LINK in asset.meta["alt_texts"][-1]       # 마지막 cta 장 안내
    assert set(asset.meta["layouts"]) == {"cover", "body", "list", "quiz", "answer", "cta"}


def test_rerender_removes_stale_slides(root, cfg):
    _place(root, "card_sample.json")
    render_week(WEEK, cfg, log=lambda _: None)
    card = _card()
    card.slides = card.slides[:3] + card.slides[-1:]
    p = paths.content_dir(WEEK) / "cardnews" / "2026W42-C01.json"
    p.write_text(card.model_dump_json(), encoding="utf-8")
    render_week(WEEK, cfg, log=lambda _: None)
    out = root / "out" / "weeks" / WEEK / "2026W42-C01"
    assert len(list(out.glob("slide*.jpg"))) == 4
    assert load_manifest(WEEK).get("2026W42-C01").meta["slide_count"] == 4


@pytest.mark.parametrize("fixture", ["card_sample.json", "card_quiz.json"])
def test_every_layout_variant_renders(tmp_path, cfg, fixture):
    """kind 마다 변형 3벌이 모두 넘침 없이 그려지는지."""
    card = _card(fixture)
    for v in range(3):
        c = card.model_copy(deep=True)
        for s in c.slides:
            s.params = {**s.params, "layout": v}
        res = render_card(c, cfg, tmp_path / f"v{v}")
        assert len(res.jpgs) == len(card.slides)
        assert set(res.layouts.values()) == {v}


def test_variant_is_deterministic_and_varies_by_card_id():
    assert all(len(v) >= 3 for v in LAYOUTS.values())
    assert slide_variant("2026W42-C01", "body") == slide_variant("2026W42-C01", "body")
    for kind in LAYOUTS:
        seen = {slide_variant(f"2026W{w:02d}-C{n:02d}", kind) for w in range(40, 50) for n in range(1, 4)}
        assert seen == {0, 1, 2}, kind


def test_same_card_different_ids_get_different_covers():
    ids = [f"2026W42-C{n:02d}" for n in range(1, 30)]
    variants = {slide_variant(i, "cover") for i in ids}
    assert len(variants) == 3


# ---------------------------------------------------------------- 줄바꿈·글자 크기

FONT = "NanumGothicExtraBold.ttf"


def test_wrap_breaks_only_between_words():
    text = "하루에 세 번 몰아서 보는 것보다 사흘에 한 번씩 나눠 보는 쪽이 일주일 뒤에 더 많이 남습니다"
    block = fit_text(text, font=FONT, sizes=[48], max_width=500, max_height=1e9)
    words = text.split()
    got = [" ".join("".join(r.text for r in w) for w in ln.words) for ln in block.lines]
    assert " ".join(got).split() == words                 # 어절이 잘리지 않았다
    assert len(block.lines) > 1
    assert all(ln.width <= 500 + 0.5 for ln in block.lines)


def test_font_shrinks_then_errors_with_guidance():
    short = fit_text("짧은 제목", font=FONT, sizes=sizes(80, 40, 4), max_width=800, max_height=200)
    assert short.size == 80
    long = "가나다라 " * 30
    mid = fit_text(long, font=FONT, sizes=sizes(80, 30, 2), max_width=800, max_height=600)
    assert mid.size < 80 and mid.height <= 600
    with pytest.raises(TextOverflow) as e:
        fit_text(long * 4, font=FONT, sizes=sizes(80, 40, 4), max_width=800, max_height=300, label="본문")
    msg = str(e.value)
    assert "본문" in msg and "40px" in msg and "자 이하로 줄이세요" in msg
    assert 0 < e.value.suggest_chars < e.value.chars


def test_long_word_is_char_broken_only_at_min_size():
    word = "가" * 40
    block = fit_text(word, font=FONT, sizes=[60, 40], max_width=600, max_height=1e9)
    assert block.size == 40 and len(block.lines) > 1


def test_rich_text_emphasis_and_paragraphs():
    paras = parse_rich("첫 **강조 부분** 끝\n\n둘째 문단")
    assert len(paras) == 2
    runs = [r for w in paras[0] for r in w]
    assert any(r.emph and r.text == "강조" for r in runs)
    assert plain("**a** b") == "a b"
    # 짝이 안 맞는 ** 는 글자 그대로
    assert any("**" in r.text for w in parse_rich("값은 5**2 입니다")[0] for r in w)
    # 제목용: 짧은 강조 구절은 한 어절로 묶고, 앞뒤 어절과는 붙이지 않는다
    words = parse_rich("이유, **복습 간격** 에 있다", keep_emph=True)[0]
    texts = ["".join(r.text for r in w) for w in words]
    assert texts == ["이유,", "복습 간격", "에", "있다"]
    assert len(parse_rich("이유, **복습 간격** 에 있다")[0]) == 5


def test_balance_keeps_line_count_and_evens_lines():
    t = "한 번 보고 잊는 이유 복습 간격에 있습니다"
    greedy = fit_text(t, font=FONT, sizes=[100], max_width=880, max_height=1e9)
    bal = fit_text(t, font=FONT, sizes=[100], max_width=880, max_height=1e9, balance=True)
    assert len(bal.lines) == len(greedy.lines)
    widths = [ln.width for ln in bal.lines]
    assert max(widths) <= max(ln.width for ln in greedy.lines) + 0.5


def test_sanitize_replaces_or_drops_missing_glyphs():
    clean, removed = sanitize("✅ 확인 👉 다음 🎉", [FONT])
    assert "√" in clean and "→" in clean and "🎉" not in clean
    assert removed == ["🎉"]


# ---------------------------------------------------------------- 원고 오류·경고

def test_overflowing_body_raises_clear_error(tmp_path, cfg):
    card = _card()
    card.slides[1].body = "아주 긴 본문이 계속 이어집니다. " * 80
    with pytest.raises(CardRenderError) as e:
        render_card(card, cfg, tmp_path)
    msg = str(e.value)
    assert "2026W42-C01" in msg and "슬라이드 2/7" in msg and "줄이세요" in msg


def test_quiz_params_are_validated(tmp_path, cfg):
    card = _card()
    card.slides[3].params = {"choices": ["가", "나"], "answer": 5}
    with pytest.raises(CardRenderError, match="1~2 번호"):
        build_slides(card, cfg)
    card.slides[3].params = {"answer": 1}
    with pytest.raises(CardRenderError, match="choices"):
        build_slides(card, cfg)


def test_answer_slide_inherits_previous_quiz(cfg):
    slides = build_slides(_card("card_quiz.json"), cfg)
    a1, a2 = slides[2], slides[4]
    assert a1.kind == "answer" and a1.choices[a1.answer] == "720원"   # 정답을 선택지 글로 쓴 경우
    assert a2.answer == 1 and a2.choices[1] == "7.5도"
    assert slides[1].next_kind == "answer"


def test_structure_warnings_and_emoji_warning(tmp_path, cfg):
    card = _card()
    card.slides = card.slides[:2]           # cta 없음
    card.slides[0].title = "복습 간격 ✅ 정리 🎉"
    res = render_card(card, cfg, tmp_path)
    joined = " ".join(res.warnings)
    assert "마지막 장이 cta 가 아닙니다" in joined
    assert "🎉" in joined


def test_custom_cta_text_still_mentions_profile_link(tmp_path):
    cfg = Config.model_validate({"cardnews": {"cta_button": "앱 받으러 가기"}})
    card = _card()
    res = render_card(card, cfg, tmp_path)
    assert PROFILE_LINK in res.alt_texts[-1] and "앱 받으러 가기" in res.alt_texts[-1]


def test_invalid_json_and_id_mismatch_are_reported(root, cfg):
    d = paths.content_dir(WEEK) / "cardnews"
    d.mkdir(parents=True)
    (d / "2026W42-C09.json").write_text('{"id": "2026W42-C09"}', encoding="utf-8")
    data = json.loads((FIX / "card_sample.json").read_text(encoding="utf-8"))
    (d / "2026W42-C08.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")  # 안의 id 는 C01
    done, failed = render_week(WEEK, cfg, log=lambda _: None)
    assert done == []
    msgs = dict(failed)
    assert "형식 오류" in msgs["2026W42-C09"]
    assert "id/week" in msgs["2026W42-C08"]


# ---------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    card_cli.register(sub)
    return parser


def test_cli_render_cardnews_only(root, capsys):
    _place(root, "card_sample.json")
    _place(root, "card_quiz.json")
    args = _parser().parse_args(["render", "cardnews", "--week", WEEK, "--only", "2026W42-C02"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "2026W42-C02" in out and "카드뉴스 1개 렌더" in out
    m = load_manifest(WEEK)
    assert m.get("2026W42-C02") and not m.get("2026W42-C01")


def test_cli_missing_only_id_fails(root, capsys):
    _place(root, "card_sample.json")
    args = _parser().parse_args(["render", "cardnews", "--week", WEEK, "--only", "2026W42-C07"])
    assert args.func(args) == 1
    assert "원고가 없습니다" in capsys.readouterr().err


def test_cli_no_sources(root, capsys):
    args = _parser().parse_args(["render", "cardnews", "--week", WEEK])
    assert args.func(args) == 1
    assert "원고가 없습니다" in capsys.readouterr().err

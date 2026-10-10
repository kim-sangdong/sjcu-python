"""블로그 렌더 테스트. STUDIO_ROOT 를 tmp_path 로 돌린다. 네트워크를 쓰지 않는다."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from studio import config, paths
from studio.blog import cli as blog_cli
from studio.blog.html import Palette, flow_html, parse, render
from studio.blog.og import split_title
from studio.blog.render import NO_TABLE_WARNING, build_html, meta_description, render_post, render_week
from studio.cardnews.canvas import brand_of
from studio.config import Config
from studio.manifest import load_manifest
from studio.models import BlogPost, PublishQueue, PublishTarget, read_model, write_model

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W42"
LINK_BASE = "https://ref.supabase.co/functions/v1/go"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def _cfg(link_base: str = LINK_BASE, **extra) -> Config:
    data = {
        "channel": {"handle": "study_daily"},
        "app": {"name": "복습노트", "link_base": link_base},
        "platforms": {"tistory": {"blog_name": "studydaily", "category": "공부법"}},
    }
    data.update(extra)
    return Config.model_validate(data)


def _post(name: str = "blog_sample.json") -> BlogPost:
    return read_model(FIX / name, BlogPost)


def _place(name: str) -> None:
    data = json.loads((FIX / name).read_text(encoding="utf-8"))
    dst = paths.content_dir(WEEK) / "blog" / f"{data['id']}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / name, dst)


def _queue(week: str, asset_id: str, remote_id: str | None, status: str = "scheduled") -> None:
    q = PublishQueue(
        week=week,
        targets=[
            PublishTarget(asset_id=asset_id, asset_kind="short", platform="youtube", publish_at=datetime(2026, 10, 13, 12, 0), status=status, remote_id=remote_id),
            PublishTarget(asset_id=asset_id, asset_kind="short", platform="tiktok", publish_at=datetime(2026, 10, 13, 12, 0), remote_id="tt-should-not-be-used"),
        ],
    )
    write_model(paths.content_dir(week) / "queue.json", q)


class Tags(HTMLParser):
    """태그 이름 순서와 속성을 모은다(구조 검사용)."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: v or "" for k, v in attrs}))

    def names(self) -> list[str]:
        return [t for t, _ in self.tags]

    def all(self, tag: str) -> list[dict[str, str]]:
        return [a for t, a in self.tags if t == tag]


def _tags(html: str) -> Tags:
    p = Tags()
    p.feed(html)
    return p


# ---------------------------------------------------------------- 출력 파일·manifest

def test_render_week_outputs_and_manifest(root):
    _place("blog_sample.json")
    _queue(WEEK, "2026W42-S03", "abcDEF12345")
    done, failed = render_week(WEEK, _cfg(), log=lambda _: None)
    assert failed == [] and len(done) == 1
    out = root / "out" / "weeks" / WEEK / "2026W42-B01"
    for name in ("post.html", "og.png", "meta.json", "preview.html"):
        assert (out / name).exists(), name
    with Image.open(out / "og.png") as im:
        assert im.size == (1200, 630) and im.format == "PNG"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert set(meta) == {"title", "tags", "category", "meta_description"}
    assert meta["title"] == _post().title
    assert meta["tags"] == ["복습", "공부법", "간격 효과"]          # '#' 제거·중복 제거
    assert meta["category"] == "공부법"
    assert 0 < len(meta["meta_description"]) <= 151
    asset = load_manifest(WEEK).get("2026W42-B01")
    assert asset is not None and asset.kind == "blog"
    assert asset.files == {
        "html": f"out/weeks/{WEEK}/2026W42-B01/post.html",
        "og": f"out/weeks/{WEEK}/2026W42-B01/og.png",
        "meta": f"out/weeks/{WEEK}/2026W42-B01/meta.json",
    }
    assert asset.meta["table_count"] == 1 and asset.meta["diagram_count"] == 1
    assert asset.meta["video_embed"] == "abcDEF12345"
    assert asset.meta["warnings"] == []


def test_html_structure_tables_links_embed(root):
    _queue(WEEK, "2026W42-S03", "abcDEF12345")
    html, info, warns = build_html(_post(), _cfg())
    t = _tags(html)
    names = t.names()
    assert names[0] == "div"                                      # 전체를 감싸는 상자
    assert names.count("table") == 1 and "thead" in names and "tbody" in names
    assert all("border" in a["style"] for a in t.all("th") + t.all("td"))   # 인라인 스타일
    assert any("background" in a.get("style", "") for a in t.all("tr"))     # 줄무늬
    assert "blockquote" in names and "ol" in names and "strong" in names and "figure" in names

    app = [a for a in t.all("a") if a["href"].startswith(LINK_BASE)]
    assert len(app) == 2                                          # 첫 소제목 앞 + 본문 끝
    assert app[0]["href"] == f"{LINK_BASE}?s=tistory&m=blog&c={WEEK}&a=2026W42-B01"
    assert all(a["target"] == "_blank" and "noopener" in a["rel"] for a in app)
    # 첫 앱 링크는 첫 소제목(h2) 앞, 둘째는 마지막 h2 뒤
    first_a = next(i for i, (n, a) in enumerate(t.tags) if n == "a" and a.get("href", "").startswith(LINK_BASE))
    h2s = [i for i, n in enumerate(names) if n == "h2"]
    last_a = max(i for i, (n, a) in enumerate(t.tags) if n == "a" and a.get("href", "").startswith(LINK_BASE))
    assert first_a < h2s[0] and last_a > h2s[-1]

    frames = t.all("iframe")
    assert len(frames) == 1 and frames[0]["src"] == "https://www.youtube.com/embed/abcDEF12345"
    assert "max-width:360px" in html                               # 쇼츠는 세로 임베드
    iframe_pos = names.index("iframe")
    assert h2s[0] < iframe_pos < h2s[1]                            # 둘째 소제목 앞
    ext = [a for a in t.all("a") if "wikipedia" in a["href"]][0]
    assert ext["target"] == "_blank" and "color" in ext["style"]
    assert warns == []
    assert info["app_link"].endswith("a=2026W42-B01")


def test_no_link_base_means_no_app_box(root):
    html, info, _ = build_html(_post(), _cfg(link_base=""))
    assert "functions/v1/go" not in html
    assert info["app_link"] == ""
    assert "앱에서" not in html


def test_missing_video_is_omitted_with_warning(root):
    html, info, warns = build_html(_post(), _cfg())
    assert "<iframe" not in html and info["video_embed"] is None
    assert any("2026W42-S03" in w for w in warns)


def test_failed_upload_is_not_embedded(root):
    _queue(WEEK, "2026W42-S03", "abcDEF12345", status="failed")
    html, _, _ = build_html(_post(), _cfg())
    assert "<iframe" not in html


def test_video_from_other_week_queue(root):
    post = _post()
    post.related_video_id = "2026W41-L1"
    _queue("2026-W41", "2026W41-L1", "LONGvid0001")
    html, info, _ = build_html(post, _cfg())
    assert info["video_embed"] == "LONGvid0001"
    assert "padding-bottom:56.25%" in html                          # 롱폼은 16:9


def test_plain_post_warns_no_table(root, capsys):
    _place("blog_plain.json")
    args = _parser().parse_args(["render", "blog", "--week", WEEK])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "[경고] 2026W42-B02" in out and "표나 도식" in out
    asset = load_manifest(WEEK).get("2026W42-B02")
    assert NO_TABLE_WARNING in asset.meta["warnings"]
    meta = json.loads((root / "out" / "weeks" / WEEK / "2026W42-B02" / "meta.json").read_text(encoding="utf-8"))
    assert meta["meta_description"].startswith("공부하다 집중이")


def test_raw_html_table_or_svg_counts_as_figure():
    p = Palette.of(brand_of(Config()))
    conv = parse("## 비교\n\n<table><tr><td>a</td></tr></table>\n\n<svg width='10'></svg>\n", p)
    assert conv.table_count == 1 and conv.diagram_count == 1


# ---------------------------------------------------------------- 마크다운 변환 세부

def test_markdown_features_and_heading_shift():
    p = Palette.of(brand_of(Config()))
    md = (
        "# 큰 제목\n\n## 작은 제목\n\n문장 *기울임* ~~취소~~ `코드` [링크](/local)\n\n"
        "```python\nprint('<a>')\n```\n\n| 왼 | 오 |\n|:--|--:|\n| 1 | 2 |\n\n![그림](https://x.test/a.png)\n\n---\n"
    )
    conv = parse(md, p)
    html = render(conv.tokens, p)
    t = _tags(html)
    names = t.names()
    assert "h1" not in names and names.count("h2") == 1 and names.count("h3") == 1   # 글 제목이 h1 이므로 한 단계 내림
    assert "em" in names and "s" in names and "code" in names and "pre" in names and "hr" in names
    assert "&lt;a&gt;" in html                                    # 코드 블록은 이스케이프
    assert "text-align:right" in t.all("td")[1]["style"]          # 표 정렬 유지
    img = t.all("img")[0]
    assert "max-width:100%" in img["style"] and img["alt"] == "그림"
    local = [a for a in t.all("a") if a["href"] == "/local"][0]
    assert "target" not in local                                  # 내부 링크는 새 창 아님


def test_flow_diagram_horizontal_and_vertical():
    p = Palette.of(brand_of(Config()))
    h = flow_html("제목: 신청 절차\n접수 -> 심사 -> 결과", p)
    assert h.count("→") == 2 and "display:flex" in h and "신청 절차" in h
    v = flow_html("1. 책을 덮고 오늘 배운 것을 떠올려 적습니다\n2. 책을 펴서 빠진 부분을 확인합니다", p)
    assert "↓" in v and "display:flex" not in v
    assert flow_html("", p) == ""


def test_html_escapes_text_and_attributes():
    post = _post()
    post.body_markdown = "## 제목 <b>\n\n1 < 2 & 3 > 2 \"따옴표\"\n\n| a |\n|--|\n| x |\n"
    cfg = _cfg(app={"name": "A&B \"앱\"", "link_base": LINK_BASE + "?x=1"})
    html, _, _ = build_html(post, cfg)
    assert "A&amp;B" in html and "&quot;앱&quot;" in html
    assert "1 &lt; 2 &amp; 3 &gt; 2" in html


def test_meta_description_auto_trim():
    post = _post()
    long = "가나다라마 " * 60
    assert len(meta_description(post, long)) <= 151
    assert meta_description(post, "짧은 첫 문단") == "짧은 첫 문단"
    post.meta_description = "직접 쓴 설명"
    assert meta_description(post, long) == "직접 쓴 설명"


def test_disclaimer_and_custom_box_text(root):
    cfg = _cfg(rules={"disclaimer": "개인 경험을 바탕으로 한 정보입니다."}, platforms={"tistory": {"app_box_title": "직접 연습해 보세요"}})
    html, _, _ = build_html(_post(), cfg)
    assert "개인 경험을 바탕으로 한 정보입니다." in html and "직접 연습해 보세요" in html
    assert html.index("개인 경험") < html.rindex(LINK_BASE)


def test_og_title_split_and_long_title_truncates(tmp_path):
    assert split_title("복습 간격 계획표 만드는 법: 달력 한 장으로") == ("복습 간격 계획표 만드는 법", "달력 한 장으로")
    assert split_title("A: 짧음") == ("A: 짧음", "")
    post = _post()
    post.title = "아주 긴 제목 " * 30
    res = render_post(post, _cfg(), tmp_path / "x")
    assert any("대표 이미지 제목" in w for w in res.warnings)
    with Image.open(res.og) as im:
        assert im.size == (1200, 630)


# ---------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    blog_cli.register(sub)
    return parser


def test_cli_render_blog_only(root, capsys):
    _place("blog_sample.json")
    _place("blog_plain.json")
    args = _parser().parse_args(["render", "blog", "--week", WEEK, "--only", "2026W42-B01"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "블로그 1편 렌더" in out
    m = load_manifest(WEEK)
    assert m.get("2026W42-B01") and not m.get("2026W42-B02")


def test_cli_missing_post(root, capsys):
    args = _parser().parse_args(["render", "blog", "--week", WEEK, "--only", "2026W42-B09"])
    assert args.func(args) == 1
    assert "원고가 없습니다" in capsys.readouterr().err

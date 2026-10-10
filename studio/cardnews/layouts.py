"""카드뉴스 슬라이드 레이아웃(1080×1350, 인스타 4:5).

kind 마다 변형 3벌을 두고, 어느 변형을 쓸지는 카드 ID 해시로 정한다(slide_variant).
같은 카드 안에서는 같은 kind 가 같은 변형을 쓰고, 카드가 바뀌면 변형이 바뀐다
→ 매주 같은 틀이 반복돼 보이는 것(템플릿 반복 판정)을 줄인다.
슬라이드 params.layout(0~2)으로 변형을 직접 고를 수도 있다.

kind 별 원고 규칙
  cover  : title=큰 제목, body=부제(선택), params.kicker=제목 위 작은 이름표(선택)
  body   : title=소제목, body=본문(\\n 문단 나눔, **강조**)
  list   : title=소제목, params.items=[항목 2~6개] (없으면 body 의 줄을 항목으로)
  quiz   : title=문제, body=보충 지문(선택), params.choices=[선택지 2~5개], params.answer=정답 번호(1부터) 또는 선택지 글
  answer : title=해설 제목(선택), body=해설, params.answer/choices 가 없으면 바로 앞 quiz 를 따른다
  cta    : title·body(선택). 항상 "프로필 링크" 안내를 넣는다.

레이아웃은 먼저 글 덩어리를 모두 맞춰(fit) 높이를 잰 뒤, 남는 높이를 위아래로 나눠 그린다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from studio.cardnews.canvas import Brand, Canvas, mix
from studio.cardnews.text import TextBlock, TextOverflow, plain, sizes

W, H = 1080, 1350
M = 96                 # 좌우 여백(인스타 프로필 그리드 3:4 잘림 34px 보다 넉넉히)
HEADER_Y = 96          # 슬라이드 번호 세로 중심
FOOTER_Y = H - 90      # 핸들·넘김 안내 세로 중심
TOP = 176              # 본문 영역 시작
BOTTOM = H - 160       # 본문 영역 끝

TITLE_COVER = sizes(120, 64, 4)
TITLE = sizes(84, 52, 2)
BODY = sizes(58, 38, 2)
SUB = sizes(48, 34, 2)
ITEM = sizes(54, 36, 2)
CHOICE = sizes(50, 34, 2)
QUESTION = sizes(70, 44, 4)

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
PROFILE_LINK = "프로필 링크"
PROFILE_LINK_NB = "프로필\u00a0링크"   # 줄바꿈으로 갈라지지 않게


class CardRenderError(ValueError):
    """원고를 그릴 수 없다(글 넘침·잘못된 params). 메시지에 고칠 방법을 담는다."""


@dataclass
class Slide:
    card_id: str
    kind: str
    title: str
    body: str
    params: dict[str, Any]
    index: int                 # 1부터
    total: int
    seq: int                   # body·list 장 중 몇 번째인지(번호 장식용)
    variant: int
    brand: Brand
    handle: str = ""
    app_name: str = ""
    next_kind: str | None = None
    choices: list[str] = field(default_factory=list)   # quiz·answer
    answer: int | None = None                          # 0부터
    texts: dict[str, str] = field(default_factory=dict)
    logo: Path | None = None


def slide_variant(card_id: str, kind: str, n: int = 3) -> int:
    """카드 ID 와 kind 로 정해지는 레이아웃 변형 번호(0..n-1). 같은 입력이면 항상 같다."""
    h = hashlib.sha1(f"{card_id}:{kind}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % n


def _slack(free: float, ratio: float = 0.42) -> float:
    """남는 높이 중 위쪽에 둘 몫(가운데보다 살짝 위)."""
    return max(0.0, free) * ratio


# ---------------------------------------------------------------- 공통 요소

def _chrome(cv: Canvas, s: Slide, *, top_bg, dark_top: bool = False, dark_bottom: bool = False, handle: bool = True) -> None:
    """오른쪽 위 슬라이드 번호(3 / 7), 왼쪽 아래 핸들(또는 로고)."""
    b = s.brand
    fill = mix(top_bg, b.bg, 0.2) if dark_top else mix(top_bg, b.primary, 0.1)
    color = b.on_primary if dark_top else b.primary
    cv.pill(f"{s.index} / {s.total}", W - M, HEADER_Y - 27, font=b.font_bold, size=28, fill=fill, color=color, height=54, align="right")
    if not handle:
        return
    if s.logo and s.kind in ("cover", "cta"):
        try:
            logo = Image.open(s.logo).convert("RGBA")
            hh = 56 * cv.s
            logo = logo.resize((max(1, round(logo.width * hh / logo.height)), hh), Image.Resampling.LANCZOS)
            cv.img.paste(logo, (round(M * cv.s), round((FOOTER_Y - 28) * cv.s)), logo)
            return
        except OSError:
            pass
    if s.handle:
        color = mix(b.bg, b.primary, 0.3) if dark_bottom else b.text_soft
        cv.label("@" + s.handle.lstrip("@"), M, FOOTER_Y, font=b.font_medium, size=30, fill=color, anchor="lm")


def _swipe(cv: Canvas, s: Slide, color, *, center: bool = False) -> None:
    text = s.texts.get("swipe_hint", "넘겨서 보기") + " →"
    x, anchor = (W / 2, "mm") if center else (W - M, "rm")
    cv.label(text, x, FOOTER_Y, font=s.brand.font_bold, size=32, fill=color, anchor=anchor)


def _answer_hint(cv: Canvas, s: Slide, color) -> None:
    if s.next_kind == "answer":
        cv.label(s.texts.get("answer_hint", "정답은 다음 장에서") + " →", W - M, FOOTER_Y, font=s.brand.font_medium, size=30, fill=color, anchor="rm")


def _fit_title(cv: Canvas, s: Slide, text: str, width: float, max_h: float, *, sz=TITLE, label="제목") -> TextBlock:
    b = s.brand
    return cv.fit(text, font=b.font_bold, sizes=sz, max_width=width, max_height=max_h, line_spacing=1.28, balance=True, keep_emph=True, label=label)


def _fit_body(cv: Canvas, s: Slide, text: str, width: float, max_h: float, *, sz=BODY, label="본문", balance=False) -> TextBlock:
    b = s.brand
    return cv.fit(
        text, font=b.font_regular, emph_font=b.font_bold, sizes=sz, max_width=width, max_height=max(0.0, max_h), line_spacing=1.58, balance=balance, label=label
    )


def _fit_group(
    cv: Canvas,
    texts: list[str],
    *,
    font: str,
    emph_font: str,
    sz: list[int],
    width: float,
    max_height: float,
    gap: float,
    pad: float = 0,
    min_item: float = 0,
    line_spacing: float = 1.4,
    label: str = "항목",
) -> list[TextBlock]:
    """여러 항목을 같은 글자 크기로 맞춘다. 항목 높이 = max(글 높이 + pad, min_item)."""
    total = 0.0
    blocks: list[TextBlock] = []
    for size in sz:
        blocks = [
            cv.fit(t, font=font, emph_font=emph_font, sizes=[size], max_width=width, max_height=1e9, line_spacing=line_spacing, label=f"{label} {i + 1}")
            for i, t in enumerate(texts)
        ]
        total = _group_height(blocks, gap=gap, pad=pad, min_item=min_item)
        if total <= max_height + 0.5:
            return blocks
    chars = sum(len(re.sub(r"\s", "", plain(t))) for t in texts)
    if all(len(bl.lines) == 1 for bl in blocks):
        raise TextOverflow(
            f"{label} {len(texts)}개가 남은 공간({max_height:.0f}px)에 들어가지 않습니다(필요 {total:.0f}px). "
            f"{label} 수를 줄이거나, 같은 장의 제목·본문을 줄이세요.",
            chars=chars,
            suggest_chars=chars,
        )
    suggest = max(1, int(chars * max_height / total * 0.9)) if total else chars
    raise TextOverflow(
        f"{label} {len(texts)}개: 가장 작은 글자 크기({sz[-1]}px)에서도 높이 {total:.0f}px 로 남은 공간 {max_height:.0f}px 를 넘습니다. "
        f"{label} 글을 모두 합쳐 {chars}자 → 약 {suggest}자 이하로 줄이거나 {label} 수를 줄이세요.",
        chars=chars,
        suggest_chars=suggest,
    )


def _group_height(blocks: list[TextBlock], *, gap: float, pad: float = 0, min_item: float = 0) -> float:
    if not blocks:
        return 0.0
    return sum(max(bl.height + pad, min_item) for bl in blocks) + gap * (len(blocks) - 1)


def list_items(s: Slide) -> list[str]:
    raw = s.params.get("items")
    if raw is None:
        raw = [ln for ln in s.body.splitlines() if ln.strip()]
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise CardRenderError("list 장의 params.items 는 글 목록이어야 합니다.")
    items = [re.sub(r"^\s*(?:[-*•·]|\d+[.)])\s*", "", x).strip() for x in raw]
    items = [x for x in items if x]
    if not items:
        raise CardRenderError("list 장에 항목이 없습니다. params.items 에 2~6개를 넣거나 body 에 한 줄에 하나씩 쓰세요.")
    if len(items) > 6:
        raise CardRenderError(f"list 장 항목이 {len(items)}개입니다. 6개 이하로 줄이거나 두 장으로 나누세요.")
    return items


def _num_circle(cv: Canvas, s: Slide, cx: float, cy: float, r: float, n: str, *, fill, color, outline=None, size: int | None = None) -> None:
    cv.circle(cx, cy, r, fill=fill, outline=outline, width=4 if outline else 0)
    cv.label(n, cx, cy, font=s.brand.font_bold, size=size or round(r * 1.05), fill=color, anchor="mm")


def _mid1(block: TextBlock) -> float:
    """덩어리 첫 줄의 세로 중심(덩어리 위쪽 기준)."""
    return block.line_height / 2


# ---------------------------------------------------------------- cover

def cover_0(s: Slide) -> Canvas:
    """밝은 바탕, 왼쪽 정렬, 모서리 원 장식."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    cv.circle(W - 30, H - 190, 340, fill=b.accent_soft)
    cv.circle(-30, -30, 150, fill=b.primary_soft)
    w = W - 2 * M
    kicker = str(s.params.get("kicker") or "")
    title = _fit_title(cv, s, s.title, w, 560, sz=TITLE_COVER)
    sub = _fit_body(cv, s, s.body, w - 80, 250, sz=SUB, label="부제(body)", balance=True)
    kick_h = 108 if kicker else 0
    total = kick_h + 60 + title.height + (48 + sub.height if sub.lines else 0)
    y = max(TOP + 30, (H - total) / 2 - 60)
    if kicker:
        cv.pill(kicker, M, y, font=b.font_bold, size=36, fill=b.primary, color=b.on_primary)
        y += kick_h
    cv.rect((M, y, M + 120, y + 14), fill=b.primary)
    y += 60
    y = cv.put(title, M, y, w, color=b.text, emph_color=b.primary)
    if sub.lines:
        cv.put(sub, M, y + 48, w - 80, color=b.text_soft, emph_color=b.primary)
    _chrome(cv, s, top_bg=b.bg)
    _swipe(cv, s, b.primary)
    return cv


def cover_1(s: Slide) -> Canvas:
    """진한 바탕(primary), 제목 덩어리를 아래쪽에 붙인다."""
    b = s.brand
    cv = Canvas(W, H, b.primary)
    cv.circle(-60, H + 40, 400, fill=mix(b.primary, (0, 0, 0), 0.16))
    cv.dots(W - M - 5 * 44, TOP + 20, 6, 4, 44, 6, fill=mix(b.primary, b.bg, 0.3))
    w = W - 2 * M
    kicker = str(s.params.get("kicker") or "")
    title = _fit_title(cv, s, s.title, w, 560, sz=TITLE_COVER)
    sub = _fit_body(cv, s, s.body, w - 60, 220, sz=SUB, label="부제(body)", balance=True)
    kick_h = 74 if kicker else 0
    total = kick_h + 50 + title.height + (44 + sub.height if sub.lines else 0)
    y = max(TOP + 150, H - 230 - total) if total < H - 230 - TOP - 150 else TOP
    if kicker:
        cv.label(kicker, M, y + 20, font=b.font_bold, size=40, fill=b.accent, anchor="lm")
        y += kick_h
    cv.rect((M, y, M + 96, y + 12), fill=b.accent)
    y += 50
    y = cv.put(title, M, y, w, color=b.on_primary, emph_color=b.accent)
    if sub.lines:
        cv.put(sub, M, y + 44, w - 60, color=mix(b.bg, b.primary, 0.2), emph_color=b.accent)
    _chrome(cv, s, top_bg=b.primary, dark_top=True, dark_bottom=True)
    _swipe(cv, s, b.accent)
    return cv


def cover_2(s: Slide) -> Canvas:
    """테두리 액자, 가운데 정렬."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    cv.rect((44, 44, W - 44, H - 44), outline=b.primary, width=6, radius=44)
    for cx, cy in ((44, 300), (W - 44, H - 300)):
        cv.circle(cx, cy, 18, fill=b.accent)
    w = W - 2 * M - 40
    kicker = str(s.params.get("kicker") or "")
    title = _fit_title(cv, s, s.title, w, 560, sz=TITLE_COVER)
    sub = _fit_body(cv, s, s.body, w, 250, sz=SUB, label="부제(body)", balance=True)
    kick_h = 116 if kicker else 0
    total = kick_h + title.height + (96 + sub.height if sub.lines else 0)
    y = max(TOP + 20, (H - total) / 2 - 30)
    x = (W - w) / 2
    if kicker:
        cv.pill(kicker, W / 2, y, font=b.font_bold, size=36, fill=b.accent_soft, color=b.primary, align="center")
        y += kick_h
    y = cv.put(title, x, y, w, color=b.text, emph_color=b.primary, align="center")
    if sub.lines:
        cv.rect((W / 2 - 40, y + 38, W / 2 + 40, y + 48), fill=b.accent, radius=5)
        cv.put(sub, x, y + 96, w, color=b.text_soft, emph_color=b.primary, align="center")
    _chrome(cv, s, top_bg=b.bg, handle=False)
    _swipe(cv, s, b.primary, center=True)
    return cv


# ---------------------------------------------------------------- body

def body_0(s: Slide) -> Canvas:
    """큰 번호 + 제목 + 밑줄 + 본문."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    w = W - 2 * M
    title = _fit_title(cv, s, s.title, w, 340)
    head = 112 + title.height + 90
    body = _fit_body(cv, s, s.body, w, BOTTOM - TOP - head)
    y = TOP + _slack(BOTTOM - TOP - head - body.height, 0.38)
    cv.label(f"{s.seq:02d}", M, y + 36, font=b.font_bold, size=72, fill=b.accent, anchor="lm")
    y = cv.put(title, M, y + 112, w, color=b.text, emph_color=b.primary)
    cv.rect((M, y + 28, M + 90, y + 38), fill=b.primary)
    cv.put(body, M, y + 90, w, color=b.text, emph_color=b.primary)
    _chrome(cv, s, top_bg=b.bg)
    return cv


def body_1(s: Slide) -> Canvas:
    """왼쪽 띠 + 제목(primary) + 흰 패널 안 본문."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    cv.rect((0, 0, 28, H), fill=b.primary)
    w, pad, gap = W - 2 * M, 56, 50
    title = _fit_title(cv, s, s.title, w, 340)
    body = _fit_body(cv, s, s.body, w - 2 * pad, BOTTOM - TOP - title.height - gap - 2 * pad)
    total = title.height + gap + body.height + 2 * pad
    y = TOP + _slack(BOTTOM - TOP - total, 0.38)
    y = cv.put(title, M, y, w, color=b.primary, emph_color=b.text) + gap
    cv.rect((M, y, W - M, y + body.height + 2 * pad), fill=b.white, radius=36)
    cv.put(body, M + pad, y + pad, w - 2 * pad, color=b.text, emph_color=b.primary)
    _chrome(cv, s, top_bg=b.bg)
    return cv


def body_2(s: Slide) -> Canvas:
    """옅은 머리 띠 안에 제목, 오른쪽에 흐린 큰 번호."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    w = W - 2 * M
    tw = w - 260
    title = _fit_title(cv, s, s.title, tw, 360)
    head_h = TOP + 40 + title.height + 76
    cv.rect((0, 0, W, head_h), fill=b.primary_soft)
    cv.label(f"{s.seq:02d}", W - M + 10, head_h - 34, font=b.font_bold, size=200, fill=mix(b.primary_soft, b.primary, 0.22), anchor="rs")
    cv.put(title, M, TOP + 40, tw, color=b.text, emph_color=b.primary)
    top = head_h + 70
    body = _fit_body(cv, s, s.body, w - 40, BOTTOM - top)
    y = top + _slack(BOTTOM - top - body.height, 0.3)
    cv.rect((M, y + 12, M + 10, y + max(body.height, 60) - 12), fill=b.accent, radius=5)
    cv.put(body, M + 40, y, w - 40, color=b.text, emph_color=b.primary)
    _chrome(cv, s, top_bg=b.primary_soft)
    return cv


# ---------------------------------------------------------------- list

def list_0(s: Slide) -> Canvas:
    """번호 원 + 항목."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    items = list_items(s)
    w = W - 2 * M
    title = _fit_title(cv, s, s.title, w, 300)
    tx, tw, gap, mi = M + 108, W - 2 * M - 108, 46, 76
    head = title.height + 100
    blocks = _fit_group(cv, items, font=b.font_medium, emph_font=b.font_bold, sz=ITEM, width=tw, max_height=BOTTOM - TOP - head, gap=gap, min_item=mi)
    y = TOP + _slack(BOTTOM - TOP - head - _group_height(blocks, gap=gap, min_item=mi), 0.38)
    y = cv.put(title, M, y, w, color=b.text, emph_color=b.primary)
    cv.rect((M, y + 28, M + 90, y + 38), fill=b.primary)
    y += 100
    for i, bl in enumerate(blocks):
        cy = y + max(_mid1(bl), mi / 2)
        _num_circle(cv, s, M + 38, cy, 38, str(i + 1), fill=b.primary, color=b.on_primary, size=38)
        cv.put(bl, tx, cy - _mid1(bl), tw, color=b.text, emph_color=b.primary)
        y += max(bl.height, mi) + gap
    _chrome(cv, s, top_bg=b.bg)
    return cv


def list_1(s: Slide) -> Canvas:
    """체크 상자 + 구분선."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    items = list_items(s)
    w = W - 2 * M
    title = _fit_title(cv, s, s.title, w, 300)
    tx, tw, gap, mi = M + 104, W - 2 * M - 104, 66, 68
    head = title.height + 76
    blocks = _fit_group(cv, items, font=b.font_regular, emph_font=b.font_bold, sz=ITEM, width=tw, max_height=BOTTOM - TOP - head, gap=gap, min_item=mi)
    y = TOP + _slack(BOTTOM - TOP - head - _group_height(blocks, gap=gap, min_item=mi), 0.38)
    y = cv.put(title, M, y, w, color=b.primary, emph_color=b.text) + 76
    for i, bl in enumerate(blocks):
        cy = y + max(_mid1(bl), mi / 2)
        cv.rect((M, cy - 34, M + 68, cy + 34), fill=b.accent_soft, radius=18)
        cv.check(M + 34, cy + 2, 38, fill=b.primary, width=8)
        cv.put(bl, tx, cy - _mid1(bl), tw, color=b.text, emph_color=b.primary)
        y += max(bl.height, mi)
        if i < len(blocks) - 1:
            cv.rect((M, y + gap / 2 - 1, W - M, y + gap / 2 + 1), fill=b.muted)
        y += gap
    _chrome(cv, s, top_bg=b.bg)
    return cv


def list_2(s: Slide) -> Canvas:
    """옅은 바탕 위 흰 카드를 쌓는다."""
    b = s.brand
    cv = Canvas(W, H, b.primary_soft)
    items = list_items(s)
    w = W - 2 * M
    title = _fit_title(cv, s, s.title, w, 300)
    pad, label_w, gap, mi = 34, 92, 22, 116
    tw = W - 2 * M - 2 * pad - label_w
    head = title.height + 60
    blocks = _fit_group(cv, items, font=b.font_medium, emph_font=b.font_bold, sz=ITEM, width=tw, max_height=BOTTOM - TOP - head, gap=gap, pad=2 * pad, min_item=mi)
    y = TOP + _slack(BOTTOM - TOP - head - _group_height(blocks, gap=gap, pad=2 * pad, min_item=mi), 0.38)
    y = cv.put(title, M, y, w, color=b.text, emph_color=b.primary) + 60
    for i, bl in enumerate(blocks):
        hh = max(bl.height + 2 * pad, mi)
        cv.rect((M, y, W - M, y + hh), fill=b.white, radius=28)
        ty = y + (hh - bl.height) / 2
        cv.label(f"{i + 1:02d}", M + pad, ty + _mid1(bl), font=b.font_bold, size=40, fill=b.primary, anchor="lm")
        cv.put(bl, M + pad + label_w, ty, tw, color=b.text, emph_color=b.primary)
        y += hh + gap
    _chrome(cv, s, top_bg=b.primary_soft)
    return cv


# ---------------------------------------------------------------- quiz

@dataclass
class _Choices:
    blocks: list[TextBlock]
    height: float
    grid: bool = False


def _st(n: int) -> tuple[int, int, int, int]:
    """세로로 쌓는 선택지 치수(pad, 번호칸 폭, 간격, 최소 높이). 5개면 조금 촘촘하게."""
    return (24, 92, 18, 96) if n <= 4 else (20, 92, 14, 86)


def _stack_fit(cv: Canvas, s: Slide, avail: float, sz: list[int] = CHOICE) -> _Choices:
    b = s.brand
    pad, nw, gap, mi = _st(len(s.choices))
    tw = W - 2 * M - 2 * pad - nw
    blocks = _fit_group(
        cv, s.choices, font=b.font_medium, emph_font=b.font_bold, sz=sz, width=tw, max_height=avail,
        gap=gap, pad=2 * pad, min_item=mi, line_spacing=1.32, label="선택지",
    )
    return _Choices(blocks, _group_height(blocks, gap=gap, pad=2 * pad, min_item=mi))


def _stack_draw(cv: Canvas, s: Slide, y: float, ch: _Choices, *, style: int) -> None:
    b = s.brand
    pad, nw, gap, mi = _st(len(s.choices))
    tw = W - 2 * M - 2 * pad - nw
    for i, bl in enumerate(ch.blocks):
        hh = max(bl.height + 2 * pad, mi)
        if style == 0:
            cv.rect((M, y, W - M, y + hh), fill=b.white, outline=b.muted, width=3, radius=24)
        else:
            cv.rect((M, y, W - M, y + hh), fill=b.white, radius=24)
        ty = y + (hh - bl.height) / 2
        cy = ty + _mid1(bl)
        if style == 0:
            _num_circle(cv, s, M + pad + 31, cy, 31, str(i + 1), fill=b.white, color=b.primary, outline=b.primary, size=34)
        else:
            _num_circle(cv, s, M + pad + 31, cy, 31, str(i + 1), fill=b.accent_soft, color=b.primary, size=34)
        cv.put(bl, M + pad + nw, ty, tw, color=b.text, emph_color=b.primary)
        y += hh + gap


_GR = {"pad": 30, "gap": 24, "num": 74}   # 2열 격자 선택지 치수


def _grid_fit(cv: Canvas, s: Slide, avail: float, sz: list[int] = CHOICE) -> _Choices:
    b = s.brand
    cw = (W - 2 * M - _GR["gap"]) / 2
    rows = len(s.choices) // 2
    total = 0.0
    for size in sz:
        blocks = [
            cv.fit(c, font=b.font_medium, emph_font=b.font_bold, sizes=[size], max_width=cw - 2 * _GR["pad"], max_height=1e9, line_spacing=1.32, label=f"선택지 {i + 1}")
            for i, c in enumerate(s.choices)
        ]
        if any(len(bl.lines) > 3 for bl in blocks):
            continue
        total = sum(max(bl.height for bl in blocks[2 * r: 2 * r + 2]) + 2 * _GR["pad"] + _GR["num"] for r in range(rows)) + _GR["gap"] * (rows - 1)
        if total <= avail:
            return _Choices(blocks, total, grid=True)
    raise TextOverflow(f"선택지가 2열 격자에 들어가지 않습니다(필요 {total:.0f}px > {avail:.0f}px).")


def _grid_draw(cv: Canvas, s: Slide, y: float, ch: _Choices) -> None:
    b = s.brand
    pad, gap, num = _GR["pad"], _GR["gap"], _GR["num"]
    cw = (W - 2 * M - gap) / 2
    for r in range(len(ch.blocks) // 2):
        pair = ch.blocks[2 * r: 2 * r + 2]
        hh = max(bl.height for bl in pair) + 2 * pad + num
        for k, bl in enumerate(pair):
            x = M + k * (cw + gap)
            cv.rect((x, y, x + cw, y + hh), fill=b.white, radius=28)
            cv.label(str(2 * r + k + 1), x + pad, y + pad + 28, font=b.font_bold, size=56, fill=b.primary, anchor="lm")
            cv.put(bl, x + pad, y + pad + num, cw - 2 * pad, color=b.text, emph_color=b.primary)
        y += hh + gap


def _fit_quiz(
    cv: Canvas, s: Slide, width: float, avail: float, gap: float, fit_choices: Callable[[Canvas, Slide, float, list[int]], _Choices]
) -> tuple[TextBlock, TextBlock | None, _Choices]:
    """문제 글 크기를 큰 것부터 줄여 가며, 선택지까지 avail 높이에 함께 들어가는 첫 조합을 찾는다.

    1차: 선택지를 44px 이상으로 유지(문제를 먼저 줄인다). 2차: 선택지도 최소 크기까지 줄인다.
    """
    b = s.brand
    err: TextOverflow | None = None
    big = [x for x in CHOICE if x >= 44]
    for size, csz in [(q, big) for q in QUESTION] + [(q, CHOICE) for q in QUESTION]:
        q = cv.fit(s.title, font=b.font_bold, sizes=[size], max_width=width, max_height=1e9, line_spacing=1.28, balance=True, label="문제(title)")
        sub = None
        if s.body.strip():
            sub = cv.fit(
                s.body, font=b.font_regular, emph_font=b.font_bold, sizes=[max(32, round(size * 0.62))], max_width=width, max_height=1e9, line_spacing=1.5, label="보충 지문(body)"
            )
        qh = q.height + (20 + sub.height if sub else 0)
        if qh > avail * 0.55:
            err = TextOverflow("문제(title)·보충 지문(body)이 너무 깁니다 — 문제는 2~3줄(40자 안팎)로 줄이세요.")
            continue
        try:
            return q, sub, fit_choices(cv, s, avail - qh - gap, csz)
        except TextOverflow as e:
            err = e
    assert err is not None
    raise err


def _put_question(cv: Canvas, q: TextBlock, sub: TextBlock | None, y: float, *, color, emph, sub_color) -> float:
    w = W - 2 * M
    y = cv.put(q, M, y, w, color=color, emph_color=emph)
    if sub:
        y = cv.put(sub, M, y + 20, w, color=sub_color, emph_color=emph)
    return y


def quiz_0(s: Slide) -> Canvas:
    """Q. 표시 + 문제 + 테두리 선택지."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    head, gap = 136, 52
    q, sub, ch = _fit_quiz(cv, s, W - 2 * M, BOTTOM - TOP - head, gap, _stack_fit)
    qh = q.height + (20 + sub.height if sub else 0)
    y = TOP + _slack(BOTTOM - TOP - head - qh - gap - ch.height, 0.4)
    cv.label("Q.", M - 4, y + 52, font=b.font_bold, size=116, fill=b.primary, anchor="lm")
    y = _put_question(cv, q, sub, y + head, color=b.text, emph=b.primary, sub_color=b.text_soft)
    _stack_draw(cv, s, y + gap, ch, style=0)
    _chrome(cv, s, top_bg=b.bg)
    _answer_hint(cv, s, b.text_soft)
    return cv


def quiz_1(s: Slide) -> Canvas:
    """진한 머리 띠 안에 문제, 아래 흰 선택지."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    q_top, band_pad, gap = TOP + 96, 66, 56
    q, sub, ch = _fit_quiz(cv, s, W - 2 * M, BOTTOM - q_top - band_pad, band_pad + gap, _stack_fit)
    qh = q.height + (20 + sub.height if sub else 0)
    head_h = q_top + qh + band_pad
    cv.rect((0, 0, W, head_h), fill=b.primary)
    cv.label(s.texts.get("quiz_label_en", "QUIZ"), M, TOP + 36, font=b.font_bold, size=40, fill=b.accent, anchor="lm")
    _put_question(cv, q, sub, q_top, color=b.on_primary, emph=b.accent, sub_color=mix(b.bg, b.primary, 0.2))
    top = head_h + gap
    _stack_draw(cv, s, top + _slack(BOTTOM - top - ch.height, 0.3), ch, style=1)
    _chrome(cv, s, top_bg=b.primary, dark_top=True)
    _answer_hint(cv, s, b.text_soft)
    return cv


def quiz_2(s: Slide) -> Canvas:
    """'퀴즈' 이름표 + 문제 + 선택지 2열 격자(2·4개일 때). 격자에 안 들어가면 세로 목록."""
    b = s.brand
    cv = Canvas(W, H, b.accent_soft)
    head, gap = 110, 50

    def fit_choices(c: Canvas, sl: Slide, avail: float, sz: list[int]) -> _Choices:
        if len(sl.choices) in (2, 4):
            try:
                return _grid_fit(c, sl, avail, sz)
            except TextOverflow:
                pass
        return _stack_fit(c, sl, avail, sz)

    q, sub, ch = _fit_quiz(cv, s, W - 2 * M, BOTTOM - TOP - head, gap, fit_choices)
    qh = q.height + (20 + sub.height if sub else 0)
    y = TOP + _slack(BOTTOM - TOP - head - qh - gap - ch.height, 0.4)
    cv.pill(s.texts.get("quiz_label", "퀴즈"), M, y, font=b.font_bold, size=34, fill=b.primary, color=b.on_primary)
    y = _put_question(cv, q, sub, y + head, color=b.text, emph=b.primary, sub_color=b.text_soft) + gap
    if ch.grid:
        _grid_draw(cv, s, y, ch)
    else:
        _stack_draw(cv, s, y, ch, style=1)
    _chrome(cv, s, top_bg=b.accent_soft)
    _answer_hint(cv, s, b.primary)
    return cv


# ---------------------------------------------------------------- answer

def _fit_explain(cv: Canvas, s: Slide, avail: float, *, sz=BODY) -> tuple[TextBlock | None, TextBlock, float]:
    """해설 제목(있으면)과 해설 본문. (제목, 본문, 전체 높이)."""
    w = W - 2 * M
    title = s.title.strip()
    t = None
    if title and title != "정답":
        t = _fit_title(cv, s, title, w, min(220, avail * 0.4), sz=sizes(54, 38, 2), label="해설 제목(title)")
    th = t.height + 24 if t else 0
    body = _fit_body(cv, s, s.body, w, avail - th, sz=sz, label="해설(body)")
    return t, body, th + body.height


def _put_explain(cv: Canvas, s: Slide, t: TextBlock | None, body: TextBlock, y: float, *, color, emph, title_color) -> None:
    w = W - 2 * M
    if t:
        y = cv.put(t, M, y, w, color=title_color, emph_color=emph) + 24
    cv.put(body, M, y, w, color=color, emph_color=emph)


def answer_0(s: Slide) -> Canvas:
    """'정답' 이름표 + 큰 번호 원 + 선택지 + 해설."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    w = W - 2 * M
    r = 82
    head = 112
    ch = None
    if s.answer is not None:
        tw = w - 2 * r - 44
        ch = _fit_title(cv, s, s.choices[s.answer] if s.choices else "", tw, 270, sz=sizes(68, 40, 2), label="정답 선택지")
        head += max(2 * r, ch.height) + 106
    t, body, eh = _fit_explain(cv, s, BOTTOM - TOP - head)
    y = TOP + _slack(BOTTOM - TOP - head - eh, 0.38)
    cv.pill(s.texts.get("answer_label", "정답"), M, y, font=b.font_bold, size=36, fill=b.primary, color=b.on_primary)
    y += 112
    if ch is not None and s.answer is not None:
        rowh = max(2 * r, ch.height)
        _num_circle(cv, s, M + r, y + rowh / 2, r, str(s.answer + 1), fill=b.primary, color=b.on_primary, size=100)
        cv.put(ch, M + 2 * r + 44, y + (rowh - ch.height) / 2, w - 2 * r - 44, color=b.text, emph_color=b.primary)
        y += rowh + 52
        cv.rect((M, y, W - M, y + 3), fill=b.muted)
        y += 54
    _put_explain(cv, s, t, body, y, color=b.text, emph=b.primary, title_color=b.primary)
    _chrome(cv, s, top_bg=b.bg)
    return cv


def answer_1(s: Slide) -> Canvas:
    """진한 머리 띠에 정답, 아래 해설."""
    b = s.brand
    if s.answer is None:
        return answer_0(s)
    cv = Canvas(W, H, b.bg)
    w = W - 2 * M
    text = f"{CIRCLED[s.answer]} {s.choices[s.answer]}" if s.choices else CIRCLED[s.answer]
    ch = _fit_title(cv, s, text, w, 340, sz=sizes(72, 42, 2), label="정답 선택지")
    head_h = TOP + 96 + ch.height + 72
    cv.rect((0, 0, W, head_h), fill=b.primary)
    cv.label(s.texts.get("answer_label", "정답"), M, TOP + 34, font=b.font_bold, size=40, fill=b.accent, anchor="lm")
    cv.put(ch, M, TOP + 96, w, color=b.on_primary, emph_color=b.accent)
    top = head_h + 72
    t, body, eh = _fit_explain(cv, s, BOTTOM - top)
    _put_explain(cv, s, t, body, top + _slack(BOTTOM - top - eh, 0.3), color=b.text, emph=b.primary, title_color=b.primary)
    _chrome(cv, s, top_bg=b.primary, dark_top=True)
    return cv


def answer_2(s: Slide) -> Canvas:
    """선택지를 다시 보여 주고 정답만 칠한다. 아래 해설."""
    b = s.brand
    if s.answer is None or not s.choices:
        return answer_0(s)
    cv = Canvas(W, H, b.bg)
    pad, nw, gap, mi = (22, 86, 14, 86) if len(s.choices) <= 4 else (18, 86, 12, 78)
    tw = W - 2 * M - 2 * pad - nw
    head = 96
    room = BOTTOM - TOP - head - 44 - 300   # 해설 자리 300px 이상 남긴다
    blocks = _fit_group(cv, s.choices, font=b.font_medium, emph_font=b.font_bold, sz=sizes(42, 30, 2), width=tw, max_height=room, gap=gap, pad=2 * pad, min_item=mi, label="선택지")
    chh = _group_height(blocks, gap=gap, pad=2 * pad, min_item=mi)
    t, body, eh = _fit_explain(cv, s, BOTTOM - TOP - head - chh - 44, sz=sizes(48, 32, 2))
    y = TOP + _slack(BOTTOM - TOP - head - chh - 44 - eh, 0.38)
    cv.label(s.texts.get("answer_label", "정답"), M, y + 26, font=b.font_bold, size=48, fill=b.primary, anchor="lm")
    y += head
    faded = mix(b.text, b.bg, 0.55)
    for i, bl in enumerate(blocks):
        hh = max(bl.height + 2 * pad, mi)
        ok = i == s.answer
        if ok:
            cv.rect((M, y, W - M, y + hh), fill=b.primary, radius=22)
        else:
            cv.rect((M, y, W - M, y + hh), outline=b.muted, width=3, radius=22)
        ty = y + (hh - bl.height) / 2
        cy = ty + _mid1(bl)
        if ok:
            cv.circle(M + pad + 29, cy, 29, fill=b.on_primary)
            cv.check(M + pad + 29, cy + 1, 32, fill=b.primary, width=7)
        else:
            cv.label(str(i + 1), M + pad + 29, cy, font=b.font_bold, size=32, fill=faded, anchor="mm")
        cv.put(bl, M + pad + nw, ty, tw, color=b.on_primary if ok else faded, emph_color=b.accent if ok else faded)
        y += hh + gap
    _put_explain(cv, s, t, body, y - gap + 44, color=b.text, emph=b.primary, title_color=b.primary)
    _chrome(cv, s, top_bg=b.bg)
    return cv


# ---------------------------------------------------------------- cta

def cta_texts(s: Slide) -> tuple[str, str, str]:
    title = s.title.strip()
    body = s.body.strip()
    if not body and s.app_name:
        body = f"{s.app_name} — {PROFILE_LINK}에서 받을 수 있어요."
    button = s.texts.get("cta_button", f"{PROFILE_LINK}에서 확인하세요")
    if PROFILE_LINK not in button:
        button = f"{PROFILE_LINK} · {button}"
    nb = lambda x: x.replace(PROFILE_LINK, PROFILE_LINK_NB)  # noqa: E731
    return nb(title), nb(body), nb(button)


def cta_0(s: Slide) -> Canvas:
    """밝은 바탕, 가운데 정렬, 큰 버튼."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    cv.circle(-40, 120, 300, fill=b.accent_soft)
    cv.circle(W + 20, H - 160, 220, fill=b.primary_soft)
    title, body, button = cta_texts(s)
    w = W - 2 * M
    t = _fit_title(cv, s, title, w, 400, sz=sizes(88, 52, 2))
    bd = _fit_body(cv, s, body, w, 260, sz=SUB, balance=True)
    btn = cv.fit(button + " →", font=b.font_bold, sizes=sizes(46, 30, 2), max_width=w - 120, max_height=70, max_lines=1, label="버튼 문구")
    total = t.height + (44 + bd.height if bd.lines else 0) + 84 + 124 + (76 if s.handle else 0)
    y = max(TOP + 20, (H - total) / 2)
    if t.lines:
        y = cv.put(t, M, y, w, color=b.text, emph_color=b.primary, align="center")
    if bd.lines:
        y = cv.put(bd, M, y + 44, w, color=b.text_soft, emph_color=b.primary, align="center")
    y += 84
    bw = btn.width + 120
    cv.rect((W / 2 - bw / 2, y, W / 2 + bw / 2, y + 124), fill=b.primary, radius=62)
    cv.put(btn, W / 2 - bw / 2, y + (124 - btn.height) / 2, bw, color=b.on_primary, align="center")
    if s.handle:
        cv.label("@" + s.handle.lstrip("@"), W / 2, y + 124 + 60, font=b.font_medium, size=34, fill=b.text_soft, anchor="mm")
    _chrome(cv, s, top_bg=b.bg, handle=False)
    return cv


def cta_1(s: Slide) -> Canvas:
    """진한 바탕 + 흰 패널 안 '프로필 링크'."""
    b = s.brand
    cv = Canvas(W, H, b.primary)
    title, body, button = cta_texts(s)
    w = W - 2 * M
    t = _fit_title(cv, s, title, w, 380, sz=sizes(84, 50, 2))
    bd = _fit_body(cv, s, body, w, 240, sz=SUB, balance=True)
    pad = 56
    big = _fit_title(cv, s, PROFILE_LINK, w - 2 * pad - 124, 100, sz=sizes(76, 48, 2), label="프로필 링크")
    default_button = button == f"{PROFILE_LINK_NB}에서 확인하세요"
    note_text = ("@" + s.handle.lstrip("@") + " 프로필 맨 위 링크" if s.handle else "프로필 맨 위 링크를 눌러 주세요") if default_button else button
    note = cv.fit(note_text, font=b.font_medium, sizes=sizes(40, 28, 2), max_width=w - 2 * pad, max_height=120, line_spacing=1.4, label="버튼 문구")
    panel_h = 2 * pad + big.height + 26 + note.height
    total = t.height + (40 + bd.height if bd.lines else 0) + 72 + panel_h
    y = max(TOP + 20, (H - total) / 2 - 20)
    if t.lines:
        y = cv.put(t, M, y, w, color=b.on_primary, emph_color=b.accent)
    if bd.lines:
        y = cv.put(bd, M, y + 40, w, color=mix(b.bg, b.primary, 0.2), emph_color=b.accent)
    y += 72
    cv.rect((M, y, W - M, y + panel_h), fill=b.white, radius=40)
    cy = y + pad + big.height / 2
    cv.circle(M + pad + 46, cy, 46, fill=b.accent_soft)
    cv.arrow_up(M + pad + 46, cy, 48, fill=b.primary)
    cv.put(big, M + pad + 124, y + pad, w - 2 * pad - 124, color=b.primary)
    cv.put(note, M + pad, y + pad + big.height + 26, w - 2 * pad, color=b.text_soft)
    if y + panel_h + 70 + 2 * 44 < H - 90:
        cv.dots(M + 6, max(y + panel_h + 70, H - 250), 5, 3, 44, 6, fill=mix(b.primary, b.bg, 0.3))
    _chrome(cv, s, top_bg=b.primary, dark_top=True, dark_bottom=True, handle=False)
    return cv


def cta_2(s: Slide) -> Canvas:
    """위쪽 화살표 아이콘(프로필 방향) + 제목 + 안내."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    title, body, button = cta_texts(s)
    w = W - 2 * M
    t = _fit_title(cv, s, title, w, 360, sz=sizes(84, 50, 2))
    bd = _fit_body(cv, s, body, w, 240, sz=SUB, balance=True)
    btn = cv.fit(button, font=b.font_bold, sizes=sizes(52, 34, 2), max_width=w, max_height=150, line_spacing=1.3, balance=True, label="버튼 문구")
    total = 240 + 64 + t.height + (40 + bd.height if bd.lines else 0) + 64 + 14 + 54 + btn.height
    y = max(TOP + 10, (H - total) / 2 - 20)
    cv.circle(W / 2, y + 120, 120, fill=b.accent_soft)
    cv.arrow_up(W / 2, y + 120, 124, fill=b.primary)
    y += 240 + 64
    if t.lines:
        y = cv.put(t, M, y, w, color=b.text, emph_color=b.primary, align="center")
    if bd.lines:
        y = cv.put(bd, M, y + 40, w, color=b.text_soft, emph_color=b.primary, align="center")
    y += 64
    cv.rect((W / 2 - 60, y, W / 2 + 60, y + 14), fill=b.accent, radius=7)
    y += 68
    cv.put(btn, M, y, w, color=b.primary, align="center")
    _chrome(cv, s, top_bg=b.bg)
    return cv


LAYOUTS: dict[str, list[Callable[[Slide], Canvas]]] = {
    "cover": [cover_0, cover_1, cover_2],
    "body": [body_0, body_1, body_2],
    "list": [list_0, list_1, list_2],
    "quiz": [quiz_0, quiz_1, quiz_2],
    "answer": [answer_0, answer_1, answer_2],
    "cta": [cta_0, cta_1, cta_2],
}


def draw_slide(s: Slide) -> Canvas:
    from studio.cardnews.exam import special_layout   # OX·숫자 표 장(params 로 고르는 레이아웃)

    fn = special_layout(s) or LAYOUTS[s.kind][s.variant % len(LAYOUTS[s.kind])]
    return fn(s)

"""카드뉴스 시험 대비 레이아웃: OX 퀴즈 장, OX 정답 장, 숫자 표 장 + 근거 배지.

CardSlide.kind 는 그대로 두고 params 로 고른다(layouts.draw_slide 가 special_layout() 을 먼저 묻는다).
  quiz   + params.choices 가 ["O", "X"]  → OX 큰 레이아웃(변형 3벌)
           params.answer: "O"·"X"(권장) 또는 true/false. 숫자는 0(=O)·2(=X)만 받고 1 은 거부한다
           — 0부터(영상·문제은행)와 1부터(카드뉴스 보기 번호) 해석이 갈려 정답이 뒤집힐 수 있어서.
  answer + 바로 앞 quiz 가 OX(또는 params.choices 가 OX) → 큰 O/X 도장 + 해설 + 근거
  body   + params.table = {columns: [...], rows: [[...], ...], highlight_rows?: [행, ...]} → 숫자 표(변형 3벌)
           body(본문)는 표 아래 짧은 메모로 쓴다.
세 레이아웃 모두 params.basis(영상 장면과 같은 형식: 문자열·{law_name, article, effective_date}·{case_no, decided_at}·목록)를
아래쪽에 '[공인중개사법 제33조 제1항 · 2026.1.1 시행 기준]' 형식 배지로 그린다.
변형 번호는 다른 장과 같이 카드 ID 해시(params.layout 으로 강제)로 정한다.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from studio.cardnews.canvas import Canvas, mix
from studio.cardnews.layouts import (
    BODY, BOTTOM, H, M, QUESTION, TOP, W, CardRenderError, Slide, _answer_hint, _chrome, _fit_body, _fit_title, _slack,
)
from studio.cardnews.text import TextBlock, TextOverflow, sizes
from studio.video.scenes.exam import badge_markup, basis_badges, basis_problems, shorten_badge

OX_ALIASES = {"O": "O", "o": "O", "○": "O", "◯": "O", "⭕": "O", "X": "X", "x": "X", "×": "X", "✕": "X", "✖": "X", "❌": "X"}
BADGE = sizes(34, 26, 2)


# ---------------------------------------------------------------- OX 판별·정답

def ox_of(text: Any) -> str | None:
    return OX_ALIASES.get(str(text).strip())


def is_ox(choices: list[str]) -> bool:
    return len(choices) == 2 and sorted(ox_of(c) or "?" for c in choices) == ["O", "X"]


def ox_answer(answer: Any, choices: list[str], where: str) -> int | None:
    """OX 선택지면 정답 번호(0부터), 아니면 None(보통 퀴즈 규칙을 따른다).

    "O"·"X"·true(=O)·false(=X) 를 받는다. 숫자는 해석이 하나뿐인 0(0부터 → O)·2(1부터 → X)만 받고,
    1 은 '0부터면 X, 1부터면 O' 라 정답이 뒤집힐 수 있어 오류로 막는다.
    """
    if not is_ox(choices):
        return None
    norm = [ox_of(c) for c in choices]
    if isinstance(answer, bool):
        want = "O" if answer else "X"
    elif isinstance(answer, int):
        if answer == 1:
            raise CardRenderError(f"{where}: OX 장의 answer=1 은 0부터(X)·1부터(O) 해석이 갈립니다 — \"O\" 또는 \"X\"(또는 true/false)로 쓰세요.")
        if answer == 0:
            want = norm[0]
        elif answer == 2:
            want = norm[1]
        else:
            raise CardRenderError(f"{where}: OX 장의 answer 는 \"O\" 또는 \"X\" 로 쓰세요(지금 {answer!r}).")
    else:
        want = ox_of(answer)
        if want is None:
            raise CardRenderError(f"{where}: OX 장의 answer 는 \"O\" 또는 \"X\"(또는 true/false)로 쓰세요(지금 {answer!r}).")
    return norm.index(want)


def _table(s: Slide) -> dict | None:
    t = s.params.get("table")
    if t is None:
        return None
    where = "params.table"
    if not isinstance(t, dict):
        raise CardRenderError(f"{where} 는 {{columns, rows}} 여야 합니다.")
    cols, rows = t.get("columns"), t.get("rows")
    if not isinstance(cols, list) or not 2 <= len(cols) <= 4:
        raise CardRenderError(f"{where}.columns 는 2~4개 목록이어야 합니다(카드 폭에 4칸까지).")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8 or any(not isinstance(r, list) or len(r) != len(cols) for r in rows):
        raise CardRenderError(f"{where}.rows 는 1~8행이고 각 행의 칸 수가 columns({len(cols)}개)와 같아야 합니다.")
    hl = t.get("highlight_rows") or []
    if not isinstance(hl, list) or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(rows) for i in hl):
        raise CardRenderError(f"{where}.highlight_rows 는 행 번호(0부터) 목록이어야 합니다.")
    return {"columns": [str(c) for c in cols], "rows": [[str(c) for c in r] for r in rows], "highlight_rows": hl}


def special_layout(s: Slide) -> Callable[[Slide], Canvas] | None:
    """내용으로 고르는 레이아웃(없으면 None → kind 기본 레이아웃)."""
    if s.kind == "quiz" and is_ox(s.choices):
        return OX_QUIZ[s.variant % len(OX_QUIZ)]
    if s.kind == "answer" and s.answer is not None and is_ox(s.choices):
        return OX_ANSWER[s.variant % len(OX_ANSWER)]
    if s.kind == "body" and "table" in s.params:
        return TABLE[s.variant % len(TABLE)]
    return None


# ---------------------------------------------------------------- 근거 배지

def _badge_blocks(cv: Canvas, s: Slide, width: float) -> list[TextBlock]:
    b = s.brand
    raw = s.params.get("basis")
    probs = basis_problems(raw)
    if probs:
        raise CardRenderError("근거(params.basis): " + " / ".join(probs))
    out = []
    for t in basis_badges(raw)[:3]:
        block = None
        for cand in dict.fromkeys([t, shorten_badge(t)]):
            try:
                block = cv.fit(badge_markup(cand), font=b.font_regular, emph_font=b.font_bold, sizes=BADGE, max_width=width, max_height=1e9,
                               max_lines=1, line_spacing=1.3, keep_emph=True, label="근거 배지")
                break
            except TextOverflow:
                continue
        if block is None:
            block = cv.fit(badge_markup(shorten_badge(t), split=True), font=b.font_regular, emph_font=b.font_bold, sizes=BADGE,
                           max_width=width, max_height=1e9, max_lines=3, line_spacing=1.3, keep_emph=True, label="근거 배지")
        out.append(block)
    return out


_BPAD, _BBAR = 18, 8


def basis_height(cv: Canvas, s: Slide) -> float:
    """근거 배지 높이(없으면 0) — 레이아웃이 본문 자리를 계산할 때 뺀다."""
    blocks = _badge_blocks(cv, s, W - 2 * M - 2 * _BPAD - _BBAR - 16)
    if not blocks:
        return 0.0
    return sum(bl.height for bl in blocks) + 6 * (len(blocks) - 1) + 2 * _BPAD


def draw_basis(cv: Canvas, s: Slide, bottom: float, *, dark: bool = False) -> float:
    """근거 배지를 아래끝 bottom 에 맞춰 그리고 위끝 y 를 돌려준다."""
    b = s.brand
    inner = W - 2 * M - 2 * _BPAD - _BBAR - 16
    blocks = _badge_blocks(cv, s, inner)
    if not blocks:
        return bottom
    h = sum(bl.height for bl in blocks) + 6 * (len(blocks) - 1) + 2 * _BPAD
    w = max(bl.width for bl in blocks) + 2 * _BPAD + _BBAR + 16
    y0 = bottom - h
    cv.rect((M, y0, M + w, bottom), fill=b.white, outline=mix(b.muted, b.bg, 0.2), width=2, radius=min(h / 2, 26))
    cv.rect((M + _BPAD, y0 + _BPAD, M + _BPAD + _BBAR, bottom - _BPAD), fill=b.primary, radius=_BBAR / 2)
    y = y0 + _BPAD
    for bl in blocks:
        y = cv.put(bl, M + _BPAD + _BBAR + 16, y, inner, color=b.text_soft, emph_color=b.primary) + 6
    return y0


# ---------------------------------------------------------------- O·X 모양

def _o(cv: Canvas, cx: float, cy: float, r: float, color, width: float) -> None:
    cv.circle(cx, cy, r, outline=color, width=width)


def _x(cv: Canvas, cx: float, cy: float, r: float, color, width: float) -> None:
    d = r * 0.82
    cv.line([(cx - d, cy - d), (cx + d, cy + d)], fill=color, width=width)
    cv.line([(cx + d, cy - d), (cx - d, cy + d)], fill=color, width=width)


def _mark(cv: Canvas, which: str, cx: float, cy: float, r: float, color, width: float) -> None:
    (_o if which == "O" else _x)(cv, cx, cy, r, color, width)


def _mark_color(s: Slide, which: str):
    return (47, 122, 85) if which == "O" else s.brand.primary   # 영상 Palette.correct 기본값과 같은 초록


def _tiles(cv: Canvas, s: Slide, y: float, h: float, *, style: int, reveal: int | None = None) -> None:
    """O·X 큰 단추 두 개. reveal=정답 번호면 정답만 채우고 다른 쪽은 흐리게."""
    b = s.brand
    gap = 28
    tw = (W - 2 * M - gap) / 2
    for k, c in enumerate(s.choices):
        which = ox_of(c) or "O"
        x = M + k * (tw + gap)
        col = _mark_color(s, which)
        r = min(tw, h) * 0.3
        cx, cy = x + tw / 2, y + h / 2
        if reveal is not None and k == reveal:
            cv.rect((x, y, x + tw, y + h), fill=col, radius=36)
            _mark(cv, which, cx, cy, r, b.white, r * 0.28)
            cv.check(x + tw - 52, y + 50, 42, fill=b.white, width=9)
            continue
        faded = reveal is not None
        if style == 2:
            cv.circle(cx, cy, min(tw, h) * 0.46, fill=b.white if not faded else mix(b.white, b.bg, 0.5))
        else:
            cv.rect((x, y, x + tw, y + h), fill=b.white if not faded else mix(b.white, b.bg, 0.5),
                    outline=mix(b.muted, b.bg, 0.1) if style == 0 else None, width=3 if style == 0 else 0, radius=36)
        _mark(cv, which, cx, cy, r, mix(col, b.bg, 0.72) if faded else col, r * 0.28)


def _fit_statement(cv: Canvas, s: Slide, width: float, max_h: float) -> tuple[TextBlock, TextBlock | None]:
    b = s.brand
    q = cv.fit(s.title, font=b.font_bold, sizes=QUESTION, max_width=width, max_height=max_h * 0.8, line_spacing=1.32, balance=True,
               label="OX 지문(title)")
    sub = None
    if s.body.strip():
        sub = cv.fit(s.body, font=b.font_regular, emph_font=b.font_bold, sizes=sizes(40, 30, 2), max_width=width,
                     max_height=max(60.0, max_h - q.height - 20), line_spacing=1.45, label="보충 글(body)")
    return q, sub


# ---------------------------------------------------------------- OX 퀴즈 장

def ox_quiz_0(s: Slide) -> Canvas:
    """밝은 바탕: 'OX 퀴즈' 이름표 + 큰 지문 + O·X 단추 두 개."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 34 if bh else 0)
    tile_h, head, gap = 270, 104, 56
    q, sub = _fit_statement(cv, s, W - 2 * M, bottom - TOP - head - tile_h - gap)
    qh = q.height + (20 + sub.height if sub else 0)
    tile_h = min(360, max(tile_h, (bottom - TOP - head - qh - gap) * 0.8))
    y = TOP + _slack(bottom - TOP - head - qh - gap - tile_h, 0.4)
    cv.pill(s.texts.get("ox_label", "OX 퀴즈"), M, y, font=b.font_bold, size=34, fill=b.primary, color=b.on_primary)
    y = cv.put(q, M, y + head, W - 2 * M, color=b.text, emph_color=b.primary)
    if sub:
        y = cv.put(sub, M, y + 20, W - 2 * M, color=b.text_soft, emph_color=b.primary)
    _tiles(cv, s, y + gap, tile_h, style=0)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=b.bg)
    _answer_hint(cv, s, b.text_soft)
    return cv


def ox_quiz_1(s: Slide) -> Canvas:
    """진한 머리 띠 안에 지문, 아래 O·X 단추."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 34 if bh else 0)
    q_top, band_pad, gap, tile_h = TOP + 96, 64, 60, 260
    q, sub = _fit_statement(cv, s, W - 2 * M, bottom - q_top - band_pad - gap - tile_h)
    qh = q.height + (20 + sub.height if sub else 0)
    head_h = q_top + qh + band_pad
    cv.rect((0, 0, W, head_h), fill=b.primary)
    cv.label(s.texts.get("ox_label_en", "O / X"), M, TOP + 36, font=b.font_bold, size=40, fill=b.accent, anchor="lm")
    y = cv.put(q, M, q_top, W - 2 * M, color=b.on_primary, emph_color=b.accent)
    if sub:
        cv.put(sub, M, y + 20, W - 2 * M, color=mix(b.bg, b.primary, 0.2), emph_color=b.accent)
    top = head_h + gap
    tile_h = min(340, max(tile_h, (bottom - top) * 0.8))
    _tiles(cv, s, top + _slack(bottom - top - tile_h, 0.35), tile_h, style=1)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=b.primary, dark_top=True)
    _answer_hint(cv, s, b.text_soft)
    return cv


def ox_quiz_2(s: Slide) -> Canvas:
    """옅은 바탕 + 흰 지문 카드 + 둥근 O·X."""
    b = s.brand
    cv = Canvas(W, H, b.accent_soft)
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 34 if bh else 0)
    pad, gap, tile_h = 48, 54, 300
    q, sub = _fit_statement(cv, s, W - 2 * M - 2 * pad, bottom - TOP - 2 * pad - gap - tile_h - 40)
    qh = q.height + (20 + sub.height if sub else 0)
    card_h = qh + 2 * pad
    tile_h = min(360, max(tile_h, (bottom - TOP - card_h - gap) * 0.8))
    y = TOP + _slack(bottom - TOP - card_h - gap - tile_h, 0.4)
    cv.rect((M, y, W - M, y + card_h), fill=b.white, radius=40)
    cv.pill(s.texts.get("ox_label", "OX 퀴즈"), M + pad, y - 27, font=b.font_bold, size=30, fill=b.primary, color=b.on_primary, height=54)
    yy = cv.put(q, M + pad, y + pad, W - 2 * M - 2 * pad, color=b.text, emph_color=b.primary)
    if sub:
        cv.put(sub, M + pad, yy + 20, W - 2 * M - 2 * pad, color=b.text_soft, emph_color=b.primary)
    _tiles(cv, s, y + card_h + gap, tile_h, style=2)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=b.accent_soft)
    _answer_hint(cv, s, b.primary)
    return cv


# ---------------------------------------------------------------- OX 정답 장

def _answer_texts(cv: Canvas, s: Slide, width: float, avail: float) -> tuple[TextBlock | None, TextBlock]:
    t = None
    if s.title.strip() and s.title.strip() not in ("정답", "정답 O", "정답 X"):
        t = _fit_title(cv, s, s.title, width, min(200, avail * 0.35), sz=sizes(54, 38, 2), label="해설 제목(title)")
    body = _fit_body(cv, s, s.body, width, avail - (t.height + 22 if t else 0), sz=BODY, label="해설(body)")
    return t, body


def _put_answer_texts(cv: Canvas, s: Slide, t: TextBlock | None, body: TextBlock, y: float, width: float, *, color=None) -> float:
    b = s.brand
    if t:
        y = cv.put(t, M, y, width, color=b.primary, emph_color=b.text) + 22
    return cv.put(body, M, y, width, color=color or b.text, emph_color=b.primary)


def _which(s: Slide) -> str:
    return ox_of(s.choices[s.answer]) or "O"


def ox_answer_0(s: Slide) -> Canvas:
    """큰 도장(O/X) + '정답 X · 틀린 지문' + 해설 + 근거."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    which = _which(s)
    col = _mark_color(s, which)
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 34 if bh else 0)
    r = 118
    head = 2 * r + 60
    t, body = _answer_texts(cv, s, W - 2 * M, bottom - TOP - head)
    eh = (t.height + 22 if t else 0) + body.height
    y = TOP + _slack(bottom - TOP - head - eh, 0.38)
    cx, cy = M + r, y + r
    cv.circle(cx, cy, r, fill=col)
    cv.circle(cx, cy, r - 14, outline=mix(col, b.white, 0.45), width=4)
    _mark(cv, which, cx, cy, r * 0.55, b.white, r * 0.16)
    label = f"정답 {which}"
    cv.label(label, M + 2 * r + 44, cy - 34, font=b.font_bold, size=72, fill=col, anchor="lm")
    cv.label("맞는 지문" if which == "O" else "틀린 지문", M + 2 * r + 46, cy + 46, font=b.font_medium, size=40, fill=b.text_soft, anchor="lm")
    _put_answer_texts(cv, s, t, body, y + head, W - 2 * M)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=b.bg)
    return cv


def ox_answer_1(s: Slide) -> Canvas:
    """정답 색 머리 띠(가운데 큰 O/X) + 해설 + 근거."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    which = _which(s)
    col = _mark_color(s, which)
    band_h = TOP + 330
    cv.rect((0, 0, W, band_h), fill=col)
    cx, cy = W / 2, TOP + 120
    _mark(cv, which, cx, cy, 92, b.white, 26)
    cv.label(f"정답 {which} · {'맞는 지문' if which == 'O' else '틀린 지문'}", W / 2, TOP + 268, font=b.font_bold, size=46, fill=b.white, anchor="mm")
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 34 if bh else 0)
    top = band_h + 64
    t, body = _answer_texts(cv, s, W - 2 * M, bottom - top)
    eh = (t.height + 22 if t else 0) + body.height
    _put_answer_texts(cv, s, t, body, top + _slack(bottom - top - eh, 0.3), W - 2 * M)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=col, dark_top=True)
    return cv


def ox_answer_2(s: Slide) -> Canvas:
    """퀴즈 장의 O·X 단추를 다시 보여 주고 정답만 채움 + 해설 + 근거."""
    b = s.brand
    cv = Canvas(W, H, b.bg)
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 34 if bh else 0)
    head, tile_h, gap = 96, 220, 54
    t, body = _answer_texts(cv, s, W - 2 * M, bottom - TOP - head - tile_h - gap)
    eh = (t.height + 22 if t else 0) + body.height
    y = TOP + _slack(bottom - TOP - head - tile_h - gap - eh, 0.38)
    cv.label(s.texts.get("answer_label", "정답"), M, y + 26, font=b.font_bold, size=48, fill=b.primary, anchor="lm")
    _tiles(cv, s, y + head, tile_h, style=1, reveal=s.answer)
    _put_answer_texts(cv, s, t, body, y + head + tile_h + gap, W - 2 * M)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=b.bg)
    return cv


# ---------------------------------------------------------------- 숫자 표 장

CELL = sizes(44, 28, 2)


def _fit_table(cv: Canvas, s: Slide, table: dict, width: float, avail: float, *, gap: float = 0) -> tuple[list[float], list, list, float, float]:
    """칸 폭, 머리 칸 글, 행별 칸 글, 머리 높이, 행 높이들. 넘치면 TextOverflow."""
    b = s.brand
    cols, rows = table["columns"], table["rows"]
    n = len(cols)
    last_err = ""
    for size in CELL:
        pad_x, pad_y = size * 0.45, size * 0.38
        nat = []
        for c in range(n):
            w = cv.text_width(re.sub(r"\*\*", "", cols[c]), b.font_bold, int(size * 0.9))
            for r in rows:
                w = max(w, cv.text_width(re.sub(r"\*\*", "", r[c]), b.font_medium if c == 0 else b.font_regular, size))
            nat.append(max(w, size * 1.6) + 2 * pad_x)
        total = sum(nat)
        widths = [width * x / total for x in nat] if total > width else [x + (width - total) * x / total for x in nat]
        try:
            heads = [cv.fit(cols[c], font=b.font_bold, sizes=[int(size * 0.9)], max_width=widths[c] - 2 * pad_x, max_height=1e9, max_lines=2,
                            line_spacing=1.25, balance=True, label=f"표 머리칸 {c + 1}") for c in range(n)]
            cells = [[cv.fit(r[c], font=b.font_medium if c == 0 else b.font_regular, emph_font=b.font_bold, sizes=[size],
                             max_width=widths[c] - 2 * pad_x, max_height=1e9, max_lines=2, line_spacing=1.25, balance=True,
                             label=f"표 {i + 1}행 {c + 1}칸") for c in range(n)] for i, r in enumerate(rows)]
        except TextOverflow as e:
            last_err = str(e)
            continue
        head_h = max(h.height for h in heads) + 2 * pad_y
        row_h = [max(max(cl.height for cl in row), size * 1.25) + 2 * pad_y for row in cells]
        if head_h + sum(row_h) + gap * len(rows) <= avail:
            return widths, heads, cells, head_h, row_h
        last_err = f"표 높이 {head_h + sum(row_h):.0f}px 가 남은 자리 {avail:.0f}px 를 넘습니다"
    raise TextOverflow(f"표가 카드에 들어가지 않습니다({last_err}) — 행을 줄이거나(8행 이하) 칸 글을 짧게 쓰세요.")


def _table_slide(s: Slide, style: int) -> Canvas:
    b = s.brand
    table = _table(s)
    bg = b.primary_soft if style == 1 else b.bg
    cv = Canvas(W, H, bg)
    bh = basis_height(cv, s)
    bottom = BOTTOM - (bh + 30 if bh else 0)
    w = W - 2 * M
    title = _fit_title(cv, s, s.title, w, 220) if s.title.strip() else None
    head = (title.height + 56) if title else 0
    note = _fit_body(cv, s, s.body, w, 170, sz=sizes(40, 30, 2), label="표 아래 메모(body)") if s.body.strip() else None
    note_h = (note.height + 30) if note else 0
    gap = 12 if style == 1 else 0
    widths, heads, cells, head_h, row_h = _fit_table(cv, s, table, w, bottom - TOP - head - note_h, gap=gap)
    total = head + head_h + sum(row_h) + gap * len(row_h) + note_h
    y = TOP + _slack(bottom - TOP - total, 0.35)
    if title:
        y = cv.put(title, M, y, w, color=b.text if style != 2 else b.primary, emph_color=b.primary)
        if style == 2:
            cv.rect((M, y + 18, M + 90, y + 28), fill=b.accent, radius=5)
        y += 56
    hl = table["highlight_rows"]
    # 머리행
    if style == 0:
        cv.rect((M, y, W - M, y + head_h + 24), fill=b.primary, radius=24)
        cv.rect((M, y + head_h, W - M, y + head_h + 24), fill=b.white)
    elif style == 2:
        cv.rect((M, y + head_h - 6, W - M, y + head_h), fill=b.accent, radius=3)
    x = M
    for c, hb in enumerate(heads):
        color = b.on_primary if style == 0 else (b.text_soft if style == 1 else b.primary)
        cv.put(hb, x, y + (head_h - hb.height) / 2, widths[c], color=color, align="center")
        x += widths[c]
    y += head_h + gap
    for i, (row, rh) in enumerate(zip(cells, row_h)):
        hlk = hl.index(i) if i in hl else None
        if style == 1:
            fill = b.white if hlk is None else mix(b.accent, b.white, 0.65)
            cv.rect((M, y, W - M, y + rh), fill=fill, radius=22, outline=b.accent if hlk is not None else None, width=4 if hlk is not None else 0)
        else:
            if style == 0:
                fill = b.white if i % 2 == 0 else mix(b.white, b.primary_soft, 0.6)
                last = i == len(cells) - 1
                cv.rect((M, y, W - M, y + rh + (0 if last else 0)), fill=fill, radius=0)
            if hlk is not None:
                tone = (b.accent, b.primary)[hlk % 2]
                cv.rect((M - 6, y + 2, W - M + 6, y + rh - 2), fill=mix(tone, b.white, 0.78), outline=tone, width=4, radius=16)
            if style == 2:
                cv.rect((M, y + rh - 2, W - M, y + rh), fill=mix(b.muted, b.bg, 0.2))
        x = M
        for c, cl in enumerate(row):
            if style == 2 and c == 0:
                cv.rect((x + 6, y + 10, x + widths[0] - 10, y + rh - 10), fill=b.primary_soft, radius=(rh - 20) / 2)
            color = b.primary if (c == 0 and style != 0) else b.text
            cv.put(cl, x, y + (rh - cl.height) / 2, widths[c], color=color, emph_color=b.primary, align="center")
            x += widths[c]
        y += rh + gap
    if style == 0:
        cv.rect((M, y - 4, W - M, y), fill=b.primary)
    if note:
        cv.put(note, M, y + 30 - gap, w, color=b.text_soft, emph_color=b.primary)
    if bh:
        draw_basis(cv, s, BOTTOM)
    _chrome(cv, s, top_bg=bg)
    return cv


def table_0(s: Slide) -> Canvas:
    """진한 머리행 + 줄무늬 표."""
    return _table_slide(s, 0)


def table_1(s: Slide) -> Canvas:
    """옅은 바탕 위 행마다 흰 카드."""
    return _table_slide(s, 1)


def table_2(s: Slide) -> Canvas:
    """밑줄 머리행 + 첫 칸 이름표."""
    return _table_slide(s, 2)


OX_QUIZ: list[Callable[[Slide], Canvas]] = [ox_quiz_0, ox_quiz_1, ox_quiz_2]
OX_ANSWER: list[Callable[[Slide], Canvas]] = [ox_answer_0, ox_answer_1, ox_answer_2]
TABLE: list[Callable[[Slide], Canvas]] = [table_0, table_1, table_2]

"""블로그 대표 이미지(og.png, 1200×630). 제목을 크게, 변형 3벌을 글 ID 해시로 고른다."""

from __future__ import annotations

import hashlib
from functools import partial
from pathlib import Path

from studio.cardnews.canvas import Brand, Canvas, mix, save_png_jpeg
from studio.cardnews.text import TextOverflow, fit_text, sizes, truncate_to_fit

OG_W, OG_H = 1200, 630
TITLE_SIZES = sizes(84, 44, 2)


def og_variant(key: str, n: int = 3) -> int:
    return int(hashlib.sha1(f"og:{key}".encode("utf-8")).hexdigest()[:8], 16) % n


def split_title(title: str) -> tuple[str, str]:
    """'주제: 부제' → ('주제', '부제'). 콜론이 없거나 한쪽이 너무 짧으면 나누지 않는다."""
    if ": " in title:
        a, b = title.split(": ", 1)
        if len(a.strip()) >= 4 and len(b.strip()) >= 4:
            return a.strip(), b.strip()
    return title, ""


def render_og(title: str, brand: Brand, out: Path, *, key: str, label: str = "", site: str = "") -> list[str]:
    """대표 이미지를 그려 PNG 로 저장한다. 제목이 너무 길면 끝을 말줄임하고 경고를 돌려준다."""
    v = og_variant(key)
    dark = v == 1
    cv = Canvas(OG_W, OG_H, brand.primary if dark else brand.bg)
    warns: list[str] = []
    x0 = 120 if v != 2 else 100
    tw = OG_W - x0 - 100
    max_h = 330 if label else 380
    title = cv.clean(title, [brand.font_bold])
    main, subtitle = split_title(title)
    sub_block = None
    if subtitle:
        try:
            sub_block = fit_text(
                subtitle, font=brand.font_medium, sizes=sizes(40, 28, 2), max_width=tw, max_height=110, line_spacing=1.35, balance=True, scale=cv.s, label="og 부제"
            )
            max_h -= sub_block.height + 26
        except TextOverflow:  # 부제가 길면 나누지 않고 제목 하나로
            main, subtitle = title, ""
    fit = partial(fit_text, font=brand.font_bold, sizes=TITLE_SIZES, max_width=tw, max_height=max_h, line_spacing=1.26, balance=True, scale=cv.s, label="og 제목")
    block, cut = truncate_to_fit(main, lambda t: fit(t))
    if cut:
        warns.append("대표 이미지 제목이 길어 끝을 줄였습니다 — 제목을 40자 안팎으로 줄이면 전부 보입니다.")
    fg = brand.on_primary if dark else brand.text
    emph = brand.accent if dark else brand.primary
    sub = mix(brand.bg, brand.primary, 0.25) if dark else brand.text_soft

    if v == 0:
        cv.circle(OG_W - 40, OG_H + 40, 260, fill=brand.accent_soft)
        cv.rect((60, 70, 76, OG_H - 70), fill=brand.primary, radius=8)
    elif v == 1:
        cv.dots(OG_W - 290, 70, 6, 3, 40, 5, fill=mix(brand.primary, brand.bg, 0.3))
        cv.circle(-40, OG_H + 60, 220, fill=mix(brand.primary, (0, 0, 0), 0.16))
    else:
        cv.rect((40, 40, OG_W - 40, OG_H - 40), fill=brand.white, radius=36)
        cv.rect((40, 40, OG_W - 40, 58), fill=brand.primary, radius=9)

    label_h = 78 if label else 0
    total = label_h + block.height + (26 + sub_block.height if sub_block else 0)
    y = (OG_H - total) / 2 - (14 if site else 0)
    if label:
        if dark:
            cv.label(label, x0, y + 22, font=brand.font_bold, size=32, fill=brand.accent, anchor="lm")
        else:
            cv.pill(label, x0, y, font=brand.font_bold, size=28, fill=brand.primary_soft if v == 0 else brand.accent_soft, color=brand.primary)
        y += label_h
    y = cv.put(block, x0, y, tw, color=fg, emph_color=emph)
    if sub_block:
        cv.put(sub_block, x0, y + 26, tw, color=emph if not dark else mix(brand.bg, brand.primary, 0.12))
    if site:
        cv.label(site, x0, OG_H - 70, font=brand.font_medium, size=26, fill=sub, anchor="lm")
    out.parent.mkdir(parents=True, exist_ok=True)
    save_png_jpeg(cv.finish(), out, None)
    if cv.removed_chars:
        warns.append(f"대표 이미지에서 글꼴에 없는 글자를 뺐습니다: {''.join(dict.fromkeys(cv.removed_chars))!r}")
    return warns

"""장면들이 같이 쓰는 그리기 조각: 머리말(kicker), 번호 원, 카드, 글자 맞춤, 가로 묶음."""

from __future__ import annotations

import re

from PIL import Image

from studio.video import design as D
from studio.video.design import Box, Sprite, TextBlock


def kicker_text(r) -> str:
    """장면 위 작은 머리말. params.kicker 가 있으면 그것(빈 문자열이면 없음), 없으면 챕터 제목."""
    if "kicker" in r.p:
        return str(r.p.get("kicker") or "")
    return r.ctx.chapter_title or ""


def kicker(r, text: str, *, on_dark: bool = False) -> Sprite:
    g, pal, S = r.g, r.pal, r.g.s
    style = r.ctx.motif % 3
    color = pal.on_primary if on_dark else pal.primary
    if style == 0:
        tb = D.text_block(r.fonts, text, role="bold", size=S.small, color=color, max_w=int(g.content.w * 0.8), max_lines=1)
        padx, pady = int(S.small * 0.62), int(S.small * 0.30)
        w, h = tb.w + 2 * padx, int(tb.h + 2 * pady)
        fill = D.rgba(pal.on_primary, 0.18) if on_dark else pal.primary_soft
        im = D.rounded(w, h, h // 2, fill)
        D.paste(im, tb.img, padx - tb.ox, pady - tb.oy)
        return D.plain(im)
    tb = D.text_block(r.fonts, text, role="bold", size=S.small, color=color if style == 1 else (pal.on_primary if on_dark else pal.ink2),
                      max_w=int(g.content.w * 0.8), max_lines=1)
    if style == 1:
        dot = int(S.small * 0.42)
        lead = D.rounded(dot, dot, dot // 4, pal.accent)
        gap = int(S.small * 0.5)
    else:
        dot = int(S.small * 1.3)
        lead = D.rounded(dot, max(4, S.small // 9), 2, pal.primary if not on_dark else pal.on_primary)
        gap = int(S.small * 0.45)
    w, h = lead.width + gap + tb.w, tb.h
    im = D.blank(w, h)
    D.paste(im, lead, 0, (h - lead.height) / 2)
    D.paste(im, tb.img, lead.width + gap - tb.ox, -tb.oy)
    return D.plain(im)


def header(r, box: Box, *, on_dark: bool = False, align: str = "left") -> Box:
    """kicker 를 상자 위쪽에 놓고(처음부터 보임) 남은 상자를 돌려준다."""
    text = kicker_text(r)
    if not text:
        return box
    sp = kicker(r, text, on_dark=on_dark)
    x = box.x if align == "left" else box.cx - sp.w / 2
    r.add(sp, x, box.y, show=-1, enter="fade", dur=0.3, name="kicker")
    _, rest = box.take_top(sp.h, int(r.g.unit * 1.4))
    return rest


def badge(r, label: str, size: int, *, fill=None, color=None, outline=None, width: int = 0, role: str = "heavy") -> Image.Image:
    """원 안의 번호·글자."""
    pal = r.pal
    im = D.ellipse(size, size, fill, outline=outline, width=width)
    fs = int(size * (0.52 if len(label) <= 1 else 0.42))
    tb = D.text_block(r.fonts, label, role=role, size=fs, color=color or pal.on_primary, max_w=size * 2, max_lines=1)
    D.paste(im, tb.img, (size - tb.w) / 2 - tb.ox, (size - tb.h) / 2 - tb.oy)
    return im


def pill(r, text: str, *, size: int, fill, color, role: str = "bold", outline=None, width: int = 0, max_w: int | None = None) -> Image.Image:
    tb = D.text_block(r.fonts, text, role=role, size=size, color=color, max_w=max_w or r.g.content.w, max_lines=1)
    padx, pady = int(size * 0.62), int(size * 0.28)
    w, h = tb.w + 2 * padx, int(tb.h + 2 * pady)
    im = D.rounded(w, h, h // 2, fill, outline=outline, width=width)
    D.paste(im, tb.img, padx - tb.ox, pady - tb.oy)
    return im


def card(w: int, h: int, *, fill, radius: int, border=None, border_w: int = 0, shadow: bool = True,
         shadow_alpha: float = 0.14, blur: int = 16) -> Sprite:
    im = D.rounded(w, h, radius, fill, outline=border, width=border_w)
    if not shadow:
        return D.plain(im)
    return D.shadowed(im, blur=blur, offset=(0, max(4, blur // 2)), alpha=shadow_alpha)


def fit_blocks(r, texts: list[str], *, max_w: int, max_h: float, size: int, min_size: int, gap: float = 0.55,
               extra_h: float = 0.0, **kw) -> list[TextBlock]:
    """여러 글 상자를 같은 크기로, 전체 높이(사이 간격 gap×글자크기 + extra_h 포함)가 max_h 에 들어가게."""
    sz = size
    while True:
        blocks = [D.text_block(r.fonts, t, size=sz, max_w=max_w, **kw) for t in texts]
        total = sum(b.h for b in blocks) + gap * sz * max(0, len(blocks) - 1) + extra_h
        if (total <= max_h and not any(b.broke for b in blocks)) or sz <= min_size:
            break
        sz = max(min_size, sz - 2)
    if total > max_h:
        # 최소 크기로도 넘치면 각 상자 높이를 나눠 말줄임
        share = max(sz * 1.3, (max_h - extra_h - gap * sz * max(0, len(texts) - 1)) / max(1, len(texts)))
        blocks = [D.text_block(r.fonts, t, size=sz, max_w=max_w, max_h=share, min_size=sz, **kw) for t in texts]
        r.defect("글이 길어 화면에 다 들어가지 않습니다 — 항목 수나 글자 수를 줄이세요.")
    for b, t in zip(blocks, texts):
        if b.overflow:
            r.defect(f"글자가 넘쳐 말줄임했습니다: '{D.strip_emphasis(t)[:24]}…' — 원고를 짧게 고치세요.")
    return blocks


def hrow(parts: list[tuple[Image.Image, int, int]], gap: int, *, h: int | None = None, valign: str = "center") -> Sprite:
    """[(그림, ox, oy)] 를 가로로 잇는다. ox/oy 는 각 그림의 논리 원점(여백)."""
    widths = [im.width - 2 * ox for im, ox, _ in parts]
    heights = [im.height - 2 * oy for im, _, oy in parts]
    H = h or max(heights)
    W = sum(widths) + gap * (len(parts) - 1)
    pad = max([ox for _, ox, _ in parts] + [oy for _, _, oy in parts] + [0])
    canvas = D.blank(W + 2 * pad, H + 2 * pad)
    x = 0
    for (im, ox, oy), w, hh in zip(parts, widths, heights):
        y = (H - hh) / 2 if valign == "center" else 0
        D.paste(canvas, im, pad + x - ox, pad + y - oy)
        x += w + gap
    return Sprite(canvas, W, H, pad, pad)


def split_parts(text: str, lines: int) -> list[str]:
    """본문 → 공개 단위. 줄바꿈이 있으면 문단, 한 문단인데 문장이 여러 개고 narration 도 여럿이면 문장 단위."""
    paras = [p.strip() for p in str(text).split("\n") if p.strip()]
    if len(paras) > 1 or lines <= 1:
        return paras or [""]
    sents = [s.strip() for s in re.split(r"(?<=[.!?。])\s+", paras[0]) if s.strip()]
    return sents if 1 < len(sents) <= max(2, lines + 1) else paras


def vcenter(total: float, box: Box, bias: float = 0.45) -> float:
    """상자 안에서 세로 위치(살짝 위로 치우친 가운데)."""
    return box.y + max(0.0, (box.h - total) * bias)

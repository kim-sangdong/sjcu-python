"""썸네일(롱폼 1280x720 PNG, 2MB 이하)과 쇼츠 커버(1080x1920 PNG, 인스타 cover_url 용).

script.thumbnail = {headline(≤40), subline(≤60), style, params}
  style: bold | split | card | photo | default(=asset_id 해시로 bold/split/card 중 하나)
  params.highlight: 강조할 낱말(문자열 또는 목록) — headline 안의 **낱말** 표시도 된다
  params.big: 오른쪽에 크게 놓을 짧은 글(예: "5문제", "28분") / params.badge: 작은 딱지 글
  params.image: photo 스타일의 사진(ROOT 기준 경로)
쇼츠 커버: 글자는 가운데 4:5(1080x1350) 안에 둔다(프로필 격자에서 잘리지 않게). style: bold | block | frame
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from studio import paths
from studio.config import Config
from studio.models import Thumbnail, VideoScript
from studio.video import design as D
from studio.video.design import Fonts, Palette

THUMB_SIZE = (1280, 720)
COVER_SIZE = (1080, 1920)
MAX_BYTES = 2 * 1024 * 1024
LONG_STYLES = ("bold", "split", "card")
COVER_STYLES = ("bold", "block", "frame")


def _thumb(script: VideoScript) -> Thumbnail:
    return script.thumbnail or Thumbnail(headline=script.title[:40])


def _marked(text: str, params: dict) -> str:
    """params.highlight 낱말에 ** 강조 표시를 붙인다."""
    hl = params.get("highlight")
    words = [hl] if isinstance(hl, str) else list(hl or [])
    for w in words:
        if w and w in text and f"**{w}**" not in text:
            text = text.replace(w, f"**{w}**", 1)
    return text


def _style(th: Thumbnail, asset_id: str, allowed: tuple[str, ...], extra: tuple[str, ...] = ()) -> str:
    s = (th.style or "default").lower()
    if s in allowed or s in extra:
        return s
    return allowed[D.pick(len(allowed), asset_id, "thumb")]


def save_png(im: Image.Image, out: Path, limit: int = MAX_BYTES) -> Path:
    """PNG 로 저장하고 limit 를 넘으면 색 수를 줄여 다시 저장."""
    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = im.convert("RGB")
    for colors in (None, 256, 128, 64):
        buf = io.BytesIO()
        (rgb if colors is None else rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)).save(buf, "PNG", optimize=True)
        if buf.tell() <= limit:
            out.write_bytes(buf.getvalue())
            return out
    out.write_bytes(buf.getvalue())
    return out


def render_thumbnail(script: VideoScript, cfg: Config, out: Path) -> Path:
    W, H = THUMB_SIZE
    pal, fonts = Palette.from_brand(cfg.brand), Fonts(cfg.brand)
    th = _thumb(script)
    p = dict(th.params)
    style = _style(th, script.id, LONG_STYLES, ("photo",))
    if style == "photo" and not p.get("image"):
        style = "bold"
    head = _marked(th.headline, p)
    sub = th.subline
    big = str(p.get("big") or "")
    badge_text = str(p.get("badge") or "")
    im = Image.new("RGBA", (W, H), pal.bg + (255,))
    M = 64
    if style == "bold":
        D.paste(im, D.ellipse(560, 560, D.rgba(pal.accent_soft, 0.9)), W - 400, -150)
        tw = int(W * (0.66 if big else 0.86))
        hb = D.text_block(fonts, head, role="heavy", size=124, min_size=84, max_w=tw, max_lines=3, max_h=H * 0.62, lh=1.14,
                          color=pal.ink, emph=pal.primary, marker=pal.accent_soft, balance=True)
        sb = _pill(fonts, sub, 44, pal.primary, pal.on_primary, max_w=tw) if sub else None
        total = hb.h + ((28 + sb.height) if sb else 0)
        y = (H - 26 - total) / 2
        D.paste(im, hb.img, M - hb.ox, y - hb.oy)
        if sb:
            D.paste(im, sb, M, y + hb.h + 28)
        if big:
            _big_circle(im, fonts, pal, big, W - 300, H / 2 - 150, 300, pal.primary, pal.on_primary)
        im.paste(pal.primary + (255,), (0, H - 26, W, H))
    elif style == "split":
        lw = int(W * 0.6)
        panel = D.polygon(lw + 80, H, [(0, 0), (lw + 80, 0), (lw - 20, H), (0, H)], pal.primary)
        D.paste(im, panel, 0, 0)
        hb = D.text_block(fonts, head, role="heavy", size=112, min_size=76, max_w=lw - M - 50, max_lines=3, max_h=H * 0.7, lh=1.14,
                          color=pal.on_primary, emph=pal.accent, balance=True)
        D.paste(im, hb.img, M - hb.ox, (H - hb.h) / 2 - hb.oy)
        rx = lw + 70
        rw = W - rx - 48
        if big:
            bb = D.text_block(fonts, big, role="heavy", size=150, min_size=80, max_w=rw, max_lines=1, color=pal.primary, align="center")
            D.paste(im, bb.img, rx + (rw - bb.w) / 2 - bb.ox, H * 0.2 - bb.oy)
        if sub:
            sb = D.text_block(fonts, sub, role="bold", size=52, min_size=40, max_w=rw, max_lines=4, color=pal.ink, align="center",
                              balance=True)
            D.paste(im, sb.img, rx + (rw - sb.w) / 2 - sb.ox, (H * 0.62 if big else (H - sb.h) / 2) - sb.oy)
    elif style == "card":
        im = Image.new("RGBA", (W, H), pal.primary + (255,))
        D.paste(im, D.ellipse(520, 520, D.rgba(pal.accent, 0.35)), -160, H - 300)
        D.paste(im, D.ellipse(300, 300, D.rgba(pal.on_primary, 0.10)), W - 220, -90)
        cw, ch = W - 2 * 90, H - 2 * 96
        hb = D.text_block(fonts, head, role="heavy", size=118, min_size=78, max_w=cw - 120, max_lines=3, max_h=ch - 150, lh=1.14,
                          color=pal.ink, emph=pal.primary, align="center", balance=True)
        sp = D.shadowed(D.rounded(cw, ch, 36, pal.surface), blur=24, offset=(0, 14), alpha=0.35)
        D.paste(im, sp.img, 90 - sp.ox, 80 - sp.oy)
        D.paste(im, hb.img, W / 2 - hb.w / 2 - hb.ox, 80 + (ch - 40 - hb.h) / 2 - hb.oy)
        if sub:
            sb = _pill(fonts, sub, 46, pal.accent, pal.ink, max_w=cw - 80)
            D.paste(im, sb, W / 2 - sb.width / 2, 80 + ch - sb.height / 2)
    else:  # photo
        src = paths.ROOT / str(p["image"])
        photo = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
        pw = int(W * 0.58)
        photo = ImageOps.fit(photo, (pw, H), Image.LANCZOS)
        fade = Image.linear_gradient("L").rotate(90, expand=True).resize((pw, H))
        mask = Image.eval(fade, lambda v: min(255, int(v * 1.8)))
        photo.putalpha(mask)
        D.paste(im, photo, W - pw, 0)
        hb = D.text_block(fonts, head, role="heavy", size=118, min_size=80, max_w=int(W * 0.6), max_lines=3, max_h=H * 0.62, lh=1.14,
                          color=pal.ink, emph=pal.primary, marker=pal.accent_soft, stroke=(6, pal.bg))
        D.paste(im, hb.img, M - hb.ox, (H - hb.h) / 2 - 30 - hb.oy)
        if sub:
            sb = _pill(fonts, sub, 44, pal.primary, pal.on_primary, max_w=int(W * 0.6))
            D.paste(im, sb, M, (H + hb.h) / 2)
    if badge_text:
        bp = _pill(fonts, badge_text, 34, pal.accent, pal.ink)
        D.paste(im, bp, W - bp.width - 36, 32)
    return save_png(im, out)


def render_cover(script: VideoScript, cfg: Config, out: Path) -> Path:
    W, H = COVER_SIZE
    pal, fonts = Palette.from_brand(cfg.brand), Fonts(cfg.brand)
    th = _thumb(script)
    p = dict(th.params)
    style = _style(th, script.id, COVER_STYLES)
    head = _marked(th.headline or script.title, p)
    sub = th.subline
    kicker = str(p.get("badge") or (script.chapters[0].title if script.chapters else ""))
    safe_top, safe_h = (H - 1350) // 2, 1350
    M = 84
    if style == "block":
        im = Image.new("RGBA", (W, H), pal.bg + (255,))
        bw = W - 2 * 56
        hb = D.text_block(fonts, head, role="heavy", size=172, min_size=84, max_w=bw - 2 * 64, max_lines=4, max_h=safe_h * 0.55,
                          lh=1.14, color=pal.on_primary, emph=pal.accent, balance=True)
        sb = D.text_block(fonts, sub, role="bold", size=60, min_size=48, max_w=W - 2 * M, max_lines=3, color=pal.ink2,
                          balance=True) if sub else None
        bh = int(hb.h + 2 * 110)
        total = bh + ((56 + sb.h) if sb else 0)
        by = safe_top + (safe_h - total) / 2
        D.paste(im, D.rounded(bw, bh, 44, pal.primary), 56, by)
        D.paste(im, hb.img, 56 + 64 - hb.ox, by + (bh - hb.h) / 2 - hb.oy)
        if kicker:
            kp = _pill(fonts, kicker, 44, pal.accent, pal.ink)
            D.paste(im, kp, 56 + 64, by - kp.height / 2)
        if sub:
            D.paste(im, sb.img, M - sb.ox, by + bh + 56 - sb.oy)
    elif style == "frame":
        im = Image.new("RGBA", (W, H), pal.bg + (255,))
        D.paste(im, D.rounded(W - 80, safe_h, 40, None, outline=pal.primary, width=14), 40, safe_top)
        D.paste(im, D.ellipse(260, 260, pal.accent), W - 230, safe_top - 90)
        hb = D.text_block(fonts, head, role="heavy", size=168, min_size=86, max_w=W - 2 * 110, max_lines=4, max_h=safe_h * 0.6,
                          lh=1.16, color=pal.ink, emph=pal.primary, marker=pal.accent_soft, align="center", balance=True)
        sb = D.text_block(fonts, sub, role="bold", size=58, min_size=48, max_w=W - 2 * 130, max_lines=3, color=pal.ink2,
                          align="center", balance=True) if sub else None
        total = hb.h + ((50 + sb.h) if sb else 0)
        y = safe_top + (safe_h - total) / 2
        D.paste(im, hb.img, W / 2 - hb.w / 2 - hb.ox, y - hb.oy)
        if sb:
            D.paste(im, sb.img, W / 2 - sb.w / 2 - sb.ox, y + hb.h + 50 - sb.oy)
        if kicker:
            kp = _pill(fonts, kicker, 44, pal.primary, pal.on_primary)
            D.paste(im, kp, W / 2 - kp.width / 2, safe_top - kp.height / 2)
    else:
        im = Image.new("RGBA", (W, H), pal.bg + (255,))
        D.paste(im, D.ellipse(900, 900, D.rgba(pal.accent_soft, 0.85)), W - 520, safe_top - 420)
        D.paste(im, D.ellipse(520, 520, D.rgba(pal.primary_soft, 1.0)), -240, safe_top + safe_h - 300)
        hb = D.text_block(fonts, head, role="heavy", size=172, min_size=90, max_w=W - 2 * M, max_lines=4, max_h=safe_h * 0.62,
                          lh=1.14, color=pal.ink, emph=pal.primary, marker=pal.accent_soft, balance=True)
        y = safe_top + 150
        if kicker:
            kp = _pill(fonts, kicker, 46, pal.primary, pal.on_primary)
            D.paste(im, kp, M, y)
            y += kp.height + 48
        D.paste(im, hb.img, M - hb.ox, y - hb.oy)
        y += hb.h + 40
        D.paste(im, D.rounded(150, 14, 4, pal.accent), M, y)
        if sub:
            sb = D.text_block(fonts, sub, role="bold", size=60, min_size=48, max_w=W - 2 * M, max_lines=3, color=pal.ink2)
            D.paste(im, sb.img, M - sb.ox, y + 60 - sb.oy)
    return save_png(im, out)


def _pill(fonts: Fonts, text: str, size: int, fill, color, max_w: int = 1100) -> Image.Image:
    tb = D.text_block(fonts, text, role="heavy", size=size, color=color, max_w=max_w - size, max_lines=1)
    padx, pady = int(size * 0.6), int(size * 0.3)
    w, h = tb.w + 2 * padx, int(tb.h + 2 * pady)
    im = D.rounded(w, h, h // 2, fill)
    D.paste(im, tb.img, padx - tb.ox, pady - tb.oy)
    return im


def _big_circle(im: Image.Image, fonts: Fonts, pal: Palette, text: str, x: float, y: float, d: int, fill, color) -> None:
    c = D.shadowed(D.ellipse(d, d, fill), blur=18, offset=(0, 10), alpha=0.25)
    D.paste(im, c.img, x - c.ox, y - c.oy)
    tb = D.text_block(fonts, text, role="heavy", size=int(d * 0.3), min_size=int(d * 0.16), max_w=int(d * 0.8), max_lines=2,
                      color=color, align="center", lh=1.1)
    D.paste(im, tb.img, x + (d - tb.w) / 2 - tb.ox, y + (d - tb.h) / 2 - tb.oy)

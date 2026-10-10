"""글 중심 장면: title, chapter, text, bullets, case, quote, cta, outro.

각 장면은 레이아웃 변형 3벌(variant 0~2)을 갖고, asset_id+scene.id 해시로 고른다(params.variant 로 강제 가능).
단계 배분(문장 i → 단계):
  title    제목 0, 부제 min(1, 마지막)
  chapter  모두 0(시차를 두고 등장)
  text     본문을 문단(또는 문장)으로 나눠 문장마다 하나씩. 제목이 있고 문장이 더 많으면 첫 문장은 제목만
  bullets  항목을 문장마다 하나씩(제목이 있고 문장이 항목보다 많으면 첫 문장은 제목만). 현재 항목 강조
  case     text 와 같음 + '재구성 사례' 표시(params.reconstructed=true)
  quote    인용문 0, 출처 min(1, 마지막)
  cta      헤드라인 0, 본문 1, 버튼 2 (문장 수에 맞춰 당김)
  outro    모두 0
"""

from __future__ import annotations

from studio.video import design as D
from studio.video.design import Box
from studio.video.scenes import Key, SceneRenderer, make_background, scene
from studio.video.scenes.common import badge, card, fit_blocks, header, hrow, pill, split_parts, vcenter

PLACEHOLDER_NAMES = ("채널 이름", "미정")


def channel_label(r) -> str:
    name = (r.ctx.channel_name or "").strip()
    return "" if (not name or any(p in name for p in PLACEHOLDER_NAMES)) else name


def last_or(r, k: int) -> int:
    return max(0, min(k, r.n - 1))


# ---------------------------------------------------------------- title

@scene("title")
class Title(SceneRenderer):
    required = ("title",)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        title, sub = str(self.p["title"]), str(self.p.get("subtitle") or "")
        s_sub = last_or(self, 1)
        d_sub = 0.45 if s_sub == 0 else 0.0
        v = self.variant
        if v == 0:
            tb = self.text(title, role="heavy", size=int(S.hero * (1.0 if self.g.portrait else 1.15)), min_size=S.h1,
                           max_w=int(C.w * 0.94), max_lines=3, align="center", balance=True, lh=1.22, emph=pal.primary)
            sb = self.text(sub, size=S.h2, min_size=S.body_min, max_w=int(C.w * 0.86), max_lines=3, align="center",
                           color=pal.ink2, balance=True) if sub else None
            bar = D.plain(D.rounded(u * 4, max(8, u // 3), u // 6, pal.accent))
            total = bar.h + u * 1.4 + tb.h + ((u * 1.2 + sb.h) if sb else 0)
            y = vcenter(total, C)
            self.add(bar, C.cx - bar.w / 2, y, enter="wipe", dur=0.5)
            y += bar.h + u * 1.4
            self.add(tb, C.cx - tb.w / 2, y, delay=0.12)
            if sb:
                self.add(sb, C.cx - sb.w / 2, y + tb.h + u * 1.2, show=s_sub, delay=d_sub)
        elif v == 1:
            bw = C.w if self.g.portrait else int(C.w * 0.74)
            pad = int(u * 2.2)
            tb = self.text(title, role="heavy", size=S.hero, min_size=S.h1, max_w=bw - 2 * pad, max_lines=3,
                           balance=True, lh=1.2, color=pal.on_primary, emph=pal.accent)
            bh = tb.h + 2 * pad
            block = card(bw, bh, fill=pal.primary, radius=u, shadow_alpha=0.22, blur=22)
            sb = self.text(sub, size=S.h2, min_size=S.body_min, max_w=bw, max_lines=3, color=pal.ink2) if sub else None
            total = bh + ((u * 1.6 + sb.h) if sb else 0)
            y = vcenter(total, C)
            self.add(block, C.x, y, enter="wipe", dur=0.55)
            self.add(D.plain(tb.img), C.x + pad - tb.ox, y + pad - tb.oy, delay=0.25)
            dot = int(u * 3.2)
            self.add(D.plain(D.ellipse(dot, dot, pal.accent)), C.x + bw - dot * 0.7, y - dot * 0.3, enter="pop", delay=0.45, dur=0.45)
            if sb:
                self.add(sb, C.x, y + bh + u * 1.6, show=s_sub, delay=d_sub)
        else:
            label = channel_label(self) or self.ctx.chapter_title
            w = int(C.w * (0.94 if self.g.portrait else 0.78))
            tb = self.text(title, role="heavy", size=S.hero, min_size=S.h1, max_w=w, max_lines=3, lh=1.2,
                           balance=True, emph=pal.primary)
            sb = self.text(sub, size=S.h2, min_size=S.body_min, max_w=w - u, max_lines=3, color=pal.ink2) if sub else None
            lab = self.text(label, role="bold", size=S.small, color=pal.primary, max_w=w, max_lines=1) if label else None
            line = D.plain(D.rounded(u * 3, max(6, u // 4), 2, pal.primary))
            total = (lab.h + u if lab else 0) + tb.h + u * 1.2 + line.h + ((u * 1.4 + sb.h) if sb else 0)
            y = vcenter(total, C)
            if lab:
                self.add(lab, C.x, y, enter="fade")
                y += lab.h + u
            self.add(tb, C.x, y, enter="left", delay=0.05)
            y += tb.h + u * 1.2
            self.add(line, C.x, y, enter="wipe", delay=0.3, dur=0.45)
            y += line.h + u * 1.4
            if sb:
                bar = D.plain(D.rounded(max(6, u // 4), sb.h, 2, pal.accent))
                self.add(bar, C.x, y, show=s_sub, enter="wipe_down", delay=d_sub)
                self.add(sb, C.x + u * 1.1, y, show=s_sub, delay=d_sub + 0.1)
            if not self.g.portrait:
                ring = int(C.h * 0.62)
                self.add(D.plain(D.ellipse(ring, ring, None, outline=D.rgba(pal.accent, 0.9), width=max(6, u // 3))),
                         C.r - ring * 0.82, C.cy - ring / 2, show=-1, enter="fade", z=-1)


# ---------------------------------------------------------------- chapter

@scene("chapter")
class Chapter(SceneRenderer):
    required = ("title",)

    def background(self):
        if self.variant == 2:
            return make_background(self.ctx, fill=self.pal.primary)
        return make_background(self.ctx)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        idx = self.p.get("index") or (self.ctx.chapter_index + 1)
        title = str(self.p["title"])
        v = self.variant
        if v == 0:
            num = self.text(f"{int(idx):02d}" if str(idx).isdigit() else str(idx), role="light", size=int(S.hero * 2.1),
                            color=pal.accent, max_w=C.w, max_lines=1, lh=1.0)
            tw = C.w if self.g.portrait else int(C.w * 0.66)
            tb = self.text(title, role="heavy", size=S.hero, min_size=S.h2, max_w=tw, max_lines=3, balance=True, lh=1.2,
                           emph=pal.primary)
            line = D.plain(D.rounded(u * 5, max(6, u // 4), 2, pal.primary))
            if self.g.portrait:
                total = num.h + u + line.h + u * 1.5 + tb.h
                y = vcenter(total, C)
                self.add(num, C.x, y, enter="fade", dur=0.6)
                self.add(line, C.x, y + num.h + u, enter="wipe", delay=0.2)
                self.add(tb, C.x, y + num.h + u * 2.5 + line.h, delay=0.3)
            else:
                y = vcenter(max(num.h, tb.h), C)
                self.add(num, C.x, C.y + (C.h - num.h) * 0.45, enter="fade", dur=0.6)
                tx = C.x + num.w + u * 3
                self.add(line, tx, y + (max(num.h, tb.h) - tb.h) / 2 - u * 1.4, enter="wipe", delay=0.2)
                self.add(tb, tx, y + (max(num.h, tb.h) - tb.h) / 2, delay=0.3)
        elif v == 1:
            pl = D.plain(pill(self, f"{idx}장" if str(idx).isdigit() else str(idx), size=S.body, fill=pal.primary, color=pal.on_primary))
            tb = self.text(title, role="heavy", size=S.hero, min_size=S.h2, max_w=int(C.w * 0.9), max_lines=3, align="center",
                           balance=True, lh=1.2, emph=pal.primary)
            dots = D.blank(u * 6, u)
            for i in range(3):
                D.paste(dots, D.ellipse(int(u * 0.75), int(u * 0.75), pal.accent if i == 1 else D.mix(pal.line, pal.primary, 0.2)),
                        u * 0.5 + i * u * 1.9, u * 0.12)
            total = pl.h + u * 1.6 + tb.h + u * 1.6 + u
            y = vcenter(total, C)
            self.add(pl, C.cx - pl.w / 2, y, enter="pop")
            self.add(tb, C.cx - tb.w / 2, y + pl.h + u * 1.6, delay=0.15)
            self.add(D.plain(dots), C.cx - dots.width / 2, y + pl.h + u * 3.2 + tb.h, enter="fade", delay=0.4)
        else:
            ring = int(S.hero * 1.6)
            rimg = badge(self, f"{int(idx):02d}" if str(idx).isdigit() else str(idx), ring, fill=None,
                         outline=pal.on_primary, width=max(5, u // 4), color=pal.on_primary)
            tb = self.text(title, role="heavy", size=S.hero, min_size=S.h2, max_w=int(C.w * 0.9), max_lines=3, align="center",
                           balance=True, lh=1.2, color=pal.on_primary, emph=pal.accent)
            total = ring + u * 2 + tb.h
            y = vcenter(total, C)
            self.add(D.plain(rimg), C.cx - ring / 2, y, enter="pop", dur=0.5)
            self.add(tb, C.cx - tb.w / 2, y + ring + u * 2, delay=0.2)


# ---------------------------------------------------------------- text / case 공통

class _Paragraphs(SceneRenderer):
    """본문을 문단(문장) 단위로 차례로 보여 주는 장면의 공통 부분."""

    focus_dim = 0.66

    def parts_and_steps(self, body: str, has_title: bool) -> tuple[list[str], list[int], int]:
        parts = split_parts(body, self.n)
        lead = 1 if has_title and self.n > len(parts) else 0
        return parts, self.spread(len(parts), self.n, lead), lead

    def place_parts(self, blocks, steps, x: float, y: float, gap: float, *, dim: bool = True) -> float:
        for i, (b, s) in enumerate(zip(blocks, steps)):
            keys = {}
            if dim:
                nxt = next((t for t in steps[i + 1:] if t > s), None)
                if nxt is not None:
                    keys[nxt] = Key(alpha=self.focus_dim, dur=0.4)
            delay = (0.15 if s == 0 else 0.05) + 0.18 * steps[:i].count(s)
            self.add(b, x, y, show=s, delay=delay, keys=keys)
            y += b.h + gap
        return y


@scene("text")
class Text(_Paragraphs):
    required = ("body",)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        heading = str(self.p.get("heading") or "")
        parts, steps, _ = self.parts_and_steps(str(self.p["body"]), bool(heading))
        v = self.variant
        if v == 0:
            box = header(self, C)
            hb = self.text(heading, role="heavy", size=S.h1, min_size=S.h2, max_w=box.w, max_lines=2, balance=True,
                           emph=pal.primary) if heading else None
            line = D.plain(D.rounded(u * 3, max(6, u // 4), 2, pal.accent))
            top = (hb.h + u * 0.8 + line.h + u * 1.6) if hb else 0
            bw = box.w if self.g.portrait else int(box.w * 0.88)
            blocks = fit_blocks(self, parts, max_w=bw, max_h=box.h - top, size=S.lead, min_size=S.body_min,
                                color=pal.ink, emph=pal.primary, marker=pal.accent_soft, gap=0.6)
            total = top + sum(b.h for b in blocks) + 0.6 * blocks[0].size * (len(blocks) - 1)
            y = vcenter(total, box, 0.42)
            if hb:
                self.add(hb, box.x, y, enter="left")
                self.add(line, box.x, y + hb.h + u * 0.8, enter="wipe", delay=0.2)
                y += top
            self.place_parts(blocks, steps, box.x, y, 0.6 * blocks[0].size)
        elif v == 1:
            pad = int(u * 2.2)
            cw = C.w if self.g.portrait else int(C.w * 0.86)
            hb = self.text(heading, role="heavy", size=S.h2, min_size=S.body_min, max_w=cw - 2 * pad, max_lines=2,
                           color=pal.primary) if heading else None
            top = (hb.h + u * 1.4 + 3 + u * 1.4) if hb else 0
            blocks = fit_blocks(self, parts, max_w=cw - 2 * pad, max_h=C.h - 2 * pad - top, size=S.lead, min_size=S.body_min,
                                color=pal.ink, emph=pal.primary, gap=0.6)
            inner = top + sum(b.h for b in blocks) + 0.6 * blocks[0].size * (len(blocks) - 1)
            ch = int(inner + 2 * pad)
            y = vcenter(ch, C)
            x = C.cx - cw / 2
            self.add(card(cw, ch, fill=pal.surface, radius=u, border=pal.line, border_w=2), x, y, show=-1, enter="fade", dur=0.3)
            yy = y + pad
            if hb:
                self.add(hb, x + pad, yy, enter="fade")
                yy += hb.h + u * 1.4
                self.add(D.plain(D.rounded(cw - 2 * pad, 3, 1, pal.line)), x + pad, yy, enter="wipe", delay=0.1)
                yy += 3 + u * 1.4
            self.place_parts(blocks, steps, x + pad, yy, 0.6 * blocks[0].size)
        else:
            if self.g.portrait:
                pad = int(u * 1.4)
                hb = self.text(heading or self.ctx.chapter_title or " ", role="heavy", size=S.h2, min_size=S.body_min,
                               max_w=C.w - 2 * pad, max_lines=2, color=pal.on_primary)
                band_h = hb.h + 2 * pad
                blocks = fit_blocks(self, parts, max_w=C.w - u, max_h=C.h - band_h - u * 2, size=S.lead, min_size=S.body_min,
                                    color=pal.ink, emph=pal.primary, marker=pal.accent_soft, gap=0.6)
                total = band_h + u * 2 + sum(b.h for b in blocks) + 0.6 * blocks[0].size * (len(blocks) - 1)
                y = vcenter(total, C, 0.4)
                self.add(card(C.w, band_h, fill=pal.primary, radius=u, shadow=False), C.x, y, show=-1, enter="wipe", dur=0.4)
                self.add(hb, C.x + pad, y + pad, show=-1, enter="fade", delay=0.15)
                self.place_parts(blocks, steps, C.x + u * 0.5, y + band_h + u * 2, 0.6 * blocks[0].size)
            else:
                box = header(self, C)
                left, right = box.split_x(0.36, int(u * 3))
                hb = self.text(heading or " ", role="heavy", size=S.h1, min_size=S.h2, max_w=left.w - u * 2, max_lines=4,
                               balance=True, emph=pal.primary)
                blocks = fit_blocks(self, parts, max_w=right.w, max_h=right.h, size=S.lead, min_size=S.body_min,
                                    color=pal.ink, emph=pal.primary, marker=pal.accent_soft, gap=0.65)
                total = sum(b.h for b in blocks) + 0.65 * blocks[0].size * (len(blocks) - 1)
                y = vcenter(total, right, 0.4)
                bar = D.plain(D.rounded(max(8, u // 3), int(max(hb.h, total)), 3, pal.accent))
                self.add(bar, left.x, y, show=-1, enter="wipe_down", dur=0.5)
                self.add(hb, left.x + u * 1.6, y, show=-1 if not heading else 0, enter="left")
                self.place_parts(blocks, steps, right.x, y, 0.65 * blocks[0].size)


@scene("case")
class Case(_Paragraphs):
    required = ("body",)
    focus_dim = 0.66

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        title = str(self.p.get("title") or "")
        label = str(self.p.get("label") or "사례")
        recon = bool(self.p.get("reconstructed"))
        recon_text = str(self.p.get("reconstructed_label") or "재구성 사례")
        parts, steps, _ = self.parts_and_steps(str(self.p["body"]), bool(title))
        v = self.variant
        pad = int(u * 2)
        cw = C.w if self.g.portrait else int(C.w * 0.9)
        x = C.cx - cw / 2
        tag = pill(self, label, size=S.small, fill=pal.primary, color=pal.on_primary)
        rtag = pill(self, recon_text, size=int(S.small * 0.9), fill=None, color=pal.ink2, outline=pal.ink3, width=2,
                    role="bold") if recon else None
        if v == 1:
            hb = self.text(title or label, role="heavy", size=S.h2, min_size=S.body_min, max_w=cw - 2 * pad - (rtag.width if rtag else 0) - u,
                           max_lines=2, color=pal.on_primary)
            band_h = int(hb.h + 2 * pad * 0.8)
            blocks = fit_blocks(self, parts, max_w=cw - 2 * pad, max_h=C.h - band_h - 2 * pad, size=S.lead,
                                min_size=S.body_min, color=pal.ink, emph=pal.primary, gap=0.6)
            body_h = int(sum(b.h for b in blocks) + 0.6 * blocks[0].size * (len(blocks) - 1) + 2 * pad)
            y = vcenter(band_h + body_h, C)
            self.add(card(cw, band_h + body_h, fill=pal.surface, radius=u, border=pal.line, border_w=2), x, y, show=-1, enter="fade", dur=0.3)
            self.add(D.plain(_top_rounded(cw, band_h, u, pal.primary)), x, y, show=-1, enter="wipe", dur=0.45)
            self.add(hb, x + pad, y + pad * 0.8, show=-1 if not title else 0, enter="fade", delay=0.1)
            if rtag:
                self.add(D.plain(pill(self, recon_text, size=int(S.small * 0.9), fill=D.rgba(pal.on_primary, 0.2),
                                      color=pal.on_primary)), x + cw - pad - rtag.width, y + (band_h - rtag.height) / 2,
                         show=-1, enter="fade", delay=0.3)
            self.place_parts(blocks, steps, x + pad, y + band_h + pad, 0.6 * blocks[0].size)
            return
        hb = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=cw - 2 * pad - u, max_lines=2) if title else None
        top = tag.height + u * 1.2 + ((hb.h + u * 1.2) if hb else 0)
        blocks = fit_blocks(self, parts, max_w=cw - 2 * pad - u, max_h=C.h - top - 2 * pad - u * 2, size=S.lead,
                            min_size=S.body_min, color=pal.ink, emph=pal.primary, marker=pal.accent_soft, gap=0.6)
        body_h = sum(b.h for b in blocks) + 0.6 * blocks[0].size * (len(blocks) - 1)
        ch = int(top + body_h + 2 * pad)
        y = vcenter(ch + (u * 2 if v == 2 else 0), C)
        if v == 0:
            self.add(card(cw, ch, fill=pal.surface, radius=u, border=pal.line, border_w=2), x, y, show=-1, enter="fade", dur=0.3)
            self.add(D.plain(D.rounded(max(8, u // 3), ch - 2 * u, 3, pal.accent)), x + u * 0.8, y + u, show=-1, enter="wipe_down")
        else:
            bubble = _bubble(cw, ch, u, pal.surface, pal.line)
            self.add(bubble, x, y, show=-1, enter="fade", dur=0.3)
        xx, yy = x + pad + u * 0.5, y + pad
        self.add(D.plain(tag), xx, yy, show=-1, enter="pop", delay=0.1)
        if rtag:
            self.add(D.plain(rtag), x + cw - pad - rtag.width, yy, show=-1, enter="fade", delay=0.25)
        yy += tag.height + u * 1.2
        if hb:
            self.add(hb, xx, yy, enter="fade")
            yy += hb.h + u * 1.2
        self.place_parts(blocks, steps, xx, yy, 0.6 * blocks[0].size)


def _top_rounded(w: int, h: int, r: int, fill):
    im = D.rounded(w, h + r * 2, r, fill)
    return im.crop((0, 0, w, h))


def _bubble(w: int, h: int, u: int, fill, border) -> D.Sprite:
    tail = int(u * 1.6)
    im = D.blank(w, h + tail)
    body = D.rounded(w, h, u, fill, outline=border, width=2)
    tri = D.polygon(tail * 2, tail + 3, [(0, 0), (tail * 1.6, 0), (tail * 0.3, tail + 2)], fill)
    D.paste(im, body, 0, 0)
    D.paste(im, tri, u * 3, h - 3)
    return D.shadowed(im, blur=16, offset=(0, 8), alpha=0.12)


# ---------------------------------------------------------------- bullets

@scene("bullets")
class Bullets(SceneRenderer):
    required = ("items",)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        items = [str(i) for i in self.p["items"]]
        heading = str(self.p.get("heading") or "")
        lead = 1 if heading and self.n > len(items) else 0
        steps = self.spread(len(items), self.n, lead)
        box = header(self, C)
        v = self.variant
        hb = self.text(heading, role="heavy", size=S.h1 if len(items) <= 3 else S.h2, min_size=S.body_min, max_w=box.w,
                       max_lines=2, emph=pal.primary) if heading else None
        head_h = int(hb.h + hb.size * 0.75) if hb else 0
        two_col = (not self.g.portrait and len(items) >= 5 and max(len(i) for i in items) <= 22)
        cols = 2 if two_col else 1
        col_w = (box.w - u * 2) // cols if cols == 2 else (box.w if self.g.portrait else int(box.w * 0.88))
        per = -(-len(items) // cols)
        sz = S.lead
        while True:
            bsize = int(sz * 1.22)
            pad = int(sz * 0.42) if v == 1 else 0
            text_w = col_w - bsize - int(sz * 0.55) - (2 * pad + int(sz * 0.45) if v == 1 else 0)
            gap_rows = sz * (0.42 if v == 1 else 0.85)
            tbs = [D.text_block(self.fonts, t, size=sz, max_w=text_w, color=pal.ink, emph=pal.primary) for t in items]
            heights = [int(max(bsize, tb.h) + 2 * pad) for tb in tbs]
            col_h = max(sum(heights[c * per:(c + 1) * per]) + gap_rows * (len(heights[c * per:(c + 1) * per]) - 1)
                        for c in range(cols))
            if (col_h <= box.h - head_h and not any(tb.broke for tb in tbs)) or sz <= S.body_min:
                break
            sz -= 2
        if col_h > box.h - head_h:
            self.warn("항목이 많거나 길어 화면을 넘칩니다 — 항목을 줄이거나 짧게 쓰세요.")
        y = vcenter(head_h + col_h, box, 0.42)
        if hb:
            self.add(hb, box.x, y, enter="left")
            y += head_h
        for i, (t, tb, s) in enumerate(zip(items, tbs, steps)):
            c, rr = divmod(i, per)
            x = box.x + c * (col_w + u * 2)
            yy = y + sum(heights[c * per:c * per + rr]) + gap_rows * rr
            active, normal = self._row(v, i, t, tb, col_w, bsize, heights[i], pad, sz)
            nxt = next((q for q in steps[i + 1:] if q > s), None)
            keys = {nxt: Key(sprite=normal, dur=0.35)} if nxt is not None else {}
            self.add(active, x, yy, show=s, enter="left" if v != 1 else "rise", delay=0.05, keys=keys)
            if v == 0 and rr < per - 1 and i < len(items) - 1:
                self.add(D.plain(D.rounded(col_w, 2, 1, pal.line)), x, yy + heights[i] + gap_rows / 2 - 1, show=s, enter="wipe",
                         delay=0.25)

    def _row(self, v: int, i: int, text: str, tb: D.TextBlock, w: int, bsize: int, h: int, pad: int, sz: int):
        pal = self.pal
        gap = int(sz * 0.55)
        if v == 0:
            on = badge(self, str(i + 1), bsize, fill=pal.primary, color=pal.on_primary)
            off = badge(self, str(i + 1), bsize, fill=None, outline=pal.primary, width=max(3, bsize // 16), color=pal.primary)
            tb_off = D.text_block(self.fonts, text, size=sz, max_w=tb.w, color=pal.ink2, emph=pal.primary)
            return (hrow([(on, 0, 0), (tb.img, tb.ox, tb.oy)], gap, valign="center"),
                    hrow([(off, 0, 0), (tb_off.img, tb_off.ox, tb_off.oy)], gap, valign="center"))
        if v == 1:
            bar_w = max(8, sz // 7)

            def mk(active: bool):
                im = D.rounded(w, h, int(sz * 0.32), pal.surface, outline=pal.primary if active else pal.line, width=3 if active else 2)
                D.paste(im, D.rounded(bar_w, h - pad * 2, 3, pal.accent if active else pal.line), pad * 0.55, pad)
                b = badge(self, str(i + 1), bsize, fill=pal.primary if active else pal.primary_soft,
                          color=pal.on_primary if active else pal.primary)
                bx = pad * 0.55 + bar_w + int(sz * 0.45)
                D.paste(im, b, bx, (h - bsize) / 2)
                D.paste(im, tb.img, bx + bsize + gap - tb.ox, (h - tb.h) / 2 - tb.oy)
                return D.shadowed(im, blur=14, offset=(0, 6), alpha=0.14 if active else 0.06)
            return mk(True), mk(False)
        ic = int(bsize * 0.92)

        def chk(active: bool):
            circ = D.ellipse(ic, ic, pal.primary if active else pal.accent_soft)
            circ.alpha_composite(D.icon_check(ic, pal.on_primary if active else pal.primary, width=ic * 0.12))
            return circ
        tb_off = D.text_block(self.fonts, text, size=sz, max_w=tb.w, color=pal.ink2, emph=pal.primary)
        return (hrow([(chk(True), 0, 0), (tb.img, tb.ox, tb.oy)], gap, valign="center"),
                hrow([(chk(False), 0, 0), (tb_off.img, tb_off.ox, tb_off.oy)], gap, valign="center"))


# ---------------------------------------------------------------- quote

@scene("quote")
class Quote(SceneRenderer):
    required = ("text",)

    def background(self):
        if self.variant == 2:
            return make_background(self.ctx, fill=self.pal.primary)
        return make_background(self.ctx)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        text, source = str(self.p["text"]), str(self.p.get("source") or "")
        s_src = last_or(self, 1)
        v = self.variant
        long = len(D.strip_emphasis(text)) > 60
        size = S.h2 if long else S.h1
        if v in (0, 2):
            dark = v == 2
            ink = pal.on_primary if dark else pal.ink
            mark = self.text("“", role="heavy", size=int(S.hero * 2.2), color=pal.accent if not dark else pal.accent_soft,
                             max_w=C.w, max_lines=1, lh=0.9)
            qb = self.text(text, role="bold", size=size, min_size=S.body_min, max_w=int(C.w * 0.9), max_h=C.h * 0.62,
                           align="center", balance=True, lh=1.4, color=ink, emph=pal.accent if dark else pal.primary)
            sb = self.text(f"— {source}", size=S.small, color=D.mix(ink, pal.primary if dark else pal.bg, 0.3), max_w=C.w,
                           max_lines=2, align="center") if source else None
            total = mark.h * 0.75 + qb.h + ((u * 1.6 + sb.h) if sb else 0)
            y = vcenter(total, C)
            self.add(mark, C.cx - mark.w / 2, y - mark.h * 0.25, enter="pop", dur=0.45)
            y += mark.h * 0.75
            self.add(qb, C.cx - qb.w / 2, y, delay=0.15, dur=0.6)
            if sb:
                self.add(sb, C.cx - sb.w / 2, y + qb.h + u * 1.6, show=s_src, delay=0.4 if s_src == 0 else 0)
        else:
            box = header(self, C)
            w = int(box.w * (0.95 if self.g.portrait else 0.82))
            qb = self.text(text, role="bold", size=size, min_size=S.body_min, max_w=w - u * 2, max_h=box.h * 0.75, lh=1.42,
                           emph=pal.primary, marker=pal.accent_soft)
            sb = self.text(f"— {source}", size=S.small, color=pal.ink2, max_w=w, max_lines=2) if source else None
            total = qb.h + ((u * 1.4 + sb.h) if sb else 0)
            y = vcenter(total, box, 0.4)
            self.add(D.plain(D.rounded(max(10, u // 2), int(qb.h), 4, pal.primary)), box.x, y, enter="wipe_down", dur=0.5)
            self.add(qb, box.x + u * 2, y, enter="left", delay=0.15)
            if sb:
                self.add(sb, box.x + u * 2, y + qb.h + u * 1.4, show=s_src, delay=0.4 if s_src == 0 else 0)


# ---------------------------------------------------------------- cta

@scene("cta")
class Cta(SceneRenderer):
    required = ("headline",)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        head, body, button = str(self.p["headline"]), str(self.p.get("body") or ""), str(self.p.get("button") or "")
        s_body, s_btn = last_or(self, 1), last_or(self, 2)
        v = self.variant
        if v == 0:
            cw = C.w if self.g.portrait else int(C.w * 0.82)
            pad = int(u * 2.4)
            hb = self.text(head, role="heavy", size=S.h1, min_size=S.h2, max_w=cw - 2 * pad, max_lines=3, align="center",
                           balance=True, color=pal.on_primary, emph=pal.accent, lh=1.25)
            bb = self.text(body, size=S.body, min_size=S.body_min, max_w=cw - 2 * pad, max_lines=4, align="center",
                           color=D.mix(pal.on_primary, pal.primary, 0.12), emph=pal.accent) if body else None
            btn = pill(self, button, size=S.body, fill=pal.accent, color=pal.ink, role="heavy", max_w=cw - 2 * pad) if button else None
            inner = hb.h + ((u * 1.4 + bb.h) if bb else 0) + ((u * 2 + btn.height) if btn else 0)
            ch = int(inner + 2 * pad)
            y = vcenter(ch, C)
            x = C.cx - cw / 2
            self.add(card(cw, ch, fill=pal.primary, radius=int(u * 1.2), shadow_alpha=0.25, blur=24), x, y, enter="pop", dur=0.5)
            yy = y + pad
            self.add(hb, C.cx - hb.w / 2, yy, delay=0.2)
            yy += hb.h + u * 1.4
            if bb:
                self.add(bb, C.cx - bb.w / 2, yy, show=s_body, delay=0.35 if s_body == 0 else 0)
                yy += bb.h + u * 2
            if btn:
                self.add(D.shadowed(btn, blur=10, offset=(0, 5), alpha=0.25), C.cx - btn.width / 2, yy, show=s_btn, enter="pop",
                         delay=0.5 if s_btn == 0 else 0.1)
        elif v == 1:
            hb = self.text(head, role="heavy", size=S.hero if len(head) < 18 else S.h1, min_size=S.h2, max_w=int(C.w * 0.92),
                           max_lines=3, align="center", balance=True, emph=pal.ink, marker=pal.accent_soft, lh=1.25)
            bb = self.text(body, size=S.body, min_size=S.body_min, max_w=int(C.w * 0.84), max_lines=4, align="center",
                           color=pal.ink2) if body else None
            btn = pill(self, button, size=S.body, fill=pal.primary, color=pal.on_primary, role="heavy") if button else None
            total = hb.h + ((u * 1.6 + bb.h) if bb else 0) + ((u * 2 + btn.height) if btn else 0)
            y = vcenter(total, C)
            blob = int(min(C.h * 1.05, C.w * 0.9))
            self.add(D.plain(D.ellipse(blob, blob, D.rgba(pal.accent_soft, 0.55))), C.cx - blob / 2, y + total / 2 - blob / 2,
                     show=-1, enter="fade", z=-1)
            self.add(hb, C.cx - hb.w / 2, y, enter="pop")
            y += hb.h + u * 1.6
            if bb:
                self.add(bb, C.cx - bb.w / 2, y, show=s_body, delay=0.3 if s_body == 0 else 0)
                y += bb.h + u * 2
            if btn:
                self.add(D.shadowed(btn, blur=10, offset=(0, 5), alpha=0.25), C.cx - btn.width / 2, y, show=s_btn, enter="pop",
                         delay=0.5 if s_btn == 0 else 0.1)
        else:
            ic = int(S.hero * 1.5)
            circ = D.ellipse(ic, ic, pal.accent)
            arrow = D.icon_arrow(int(ic * 0.5), max(6, ic // 14), pal.ink, "down" if self.g.portrait else "right")
            D.paste(circ, arrow, (ic - arrow.width) / 2, (ic - arrow.height) / 2)
            if self.g.portrait:
                hb = self.text(head, role="heavy", size=S.h1, min_size=S.h2, max_w=C.w, max_lines=3, balance=True, emph=pal.primary)
                bb = self.text(body, size=S.body, min_size=S.body_min, max_w=C.w, max_lines=4, color=pal.ink2) if body else None
                btn = pill(self, button, size=S.body, fill=pal.primary, color=pal.on_primary, role="heavy") if button else None
                total = ic + u * 2 + hb.h + ((u * 1.4 + bb.h) if bb else 0) + ((u * 2 + btn.height) if btn else 0)
                y = vcenter(total, C)
                self.add(D.plain(circ), C.x, y, enter="pop")
                y += ic + u * 2
                tx = C.x
            else:
                tw = int(C.w * 0.62)
                hb = self.text(head, role="heavy", size=S.h1, min_size=S.h2, max_w=tw, max_lines=3, balance=True, emph=pal.primary)
                bb = self.text(body, size=S.body, min_size=S.body_min, max_w=tw, max_lines=4, color=pal.ink2) if body else None
                btn = pill(self, button, size=S.body, fill=pal.primary, color=pal.on_primary, role="heavy") if button else None
                total = hb.h + ((u * 1.4 + bb.h) if bb else 0) + ((u * 2 + btn.height) if btn else 0)
                y = vcenter(total, C)
                self.add(D.plain(circ), C.x + u, C.y + (C.h - ic) * 0.45, enter="pop")
                tx = C.x + ic + u * 4
            self.add(hb, tx, y, enter="left", delay=0.15)
            y += hb.h + u * 1.4
            if bb:
                self.add(bb, tx, y, show=s_body, delay=0.3 if s_body == 0 else 0)
                y += bb.h + u * 2
            if btn:
                self.add(D.shadowed(btn, blur=10, offset=(0, 5), alpha=0.25), tx, y, show=s_btn, enter="pop",
                         delay=0.5 if s_btn == 0 else 0.1)


# ---------------------------------------------------------------- outro

@scene("outro")
class Outro(SceneRenderer):
    DEFAULT = "시청해 주셔서 감사합니다"

    def background(self):
        if self.variant == 1:
            return make_background(self.ctx, fill=self.pal.primary)
        return make_background(self.ctx)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        head = str(self.p.get("headline") or self.DEFAULT)
        name = channel_label(self)
        v = self.variant
        dark = v == 1
        ink = pal.on_primary if dark else pal.ink
        if v in (0, 1):
            hb = self.text(head, role="heavy", size=S.h1, min_size=S.h2, max_w=int(C.w * 0.9), max_lines=3, align="center",
                           balance=True, color=ink, emph=pal.accent if dark else pal.primary)
            nb = self.text(name, role="bold", size=S.body, color=pal.accent if dark else pal.primary, max_w=C.w, max_lines=1,
                           align="center") if name else None
            dots = D.blank(u * 6, u)
            for i in range(3):
                D.paste(dots, D.ellipse(u // 2 + 4, u // 2 + 4, pal.accent if i == 1 else (D.rgba(pal.on_primary, 0.5) if dark else pal.line)),
                        u * 0.8 + i * u * 1.8, u / 4)
            total = hb.h + u * 2 + u + ((u * 1.6 + nb.h) if nb else 0)
            y = vcenter(total, C)
            self.add(hb, C.cx - hb.w / 2, y, enter="rise")
            self.add(D.plain(dots), C.cx - dots.width / 2, y + hb.h + u * 2, enter="fade", delay=0.3)
            if nb:
                self.add(nb, C.cx - nb.w / 2, y + hb.h + u * 4.6, enter="fade", delay=0.5)
        else:
            w = int(C.w * (0.95 if self.g.portrait else 0.6))
            hb = self.text(head, role="heavy", size=S.hero, min_size=S.h1, max_w=w, max_lines=3, balance=True, emph=pal.primary, lh=1.2)
            nb = self.text(name, role="bold", size=S.body, color=pal.primary, max_w=w, max_lines=1) if name else None
            total = hb.h + ((u * 2 + nb.h) if nb else 0)
            y = vcenter(total, C)
            self.add(hb, C.x, y, enter="left")
            if nb:
                self.add(nb, C.x, y + hb.h + u * 2, enter="fade", delay=0.35)
            if self.g.portrait:
                free_top = y + total + u * 2
                ring = int(min(C.w * 0.5, C.b - free_top))
                rx, ry = C.r - ring, C.b - ring
            else:
                ring = int(min(C.h * 0.8, C.w - w - u * 4))
                rx, ry = C.r - ring, C.cy - ring / 2
            if ring > u * 4:
                self.add(D.plain(D.ellipse(ring, ring, D.rgba(pal.accent_soft, 0.8))), rx, ry, show=-1, enter="fade", z=-1)
                self.add(D.plain(D.ellipse(ring // 3, ring // 3, pal.primary)), rx + ring * 0.6, ry + ring * 0.06, enter="pop", delay=0.3)

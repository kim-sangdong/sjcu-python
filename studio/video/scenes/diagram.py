"""도식 장면: compare, sequence, timeline, flow, bars.

단계 배분(문장 수 N, 요소 수 M): 기본은 문장마다 요소 하나(spread). 문장이 요소보다 많으면 첫 문장은 도입(제목만).
  compare   행 단위 공개
  sequence  문장 0..S-1: 섞인 카드 → (countdown 이 있으면 생각할 시간) → 문장 S: 정렬 애니메이션(카드가 제자리로 이동,
            번호 표시) → 이후 문장마다 카드 하나씩 강조. S = sort_line → '정답'이 처음 나오는 문장 → 1
  timeline  사건 하나씩(선이 이어지며 자란다)
  flow      단계 하나씩(상자 + 화살표). params.hidden(숨길 단계 번호, 0부터)이 있으면 그 단계는 '?' 칩으로 가렸다가
            문장 R(reveal_line → '정답'·'답은' 문장 → 마지막 문장)에서 공개. 공개 전 생각할 시간(countdown → hold_seconds → 3초)
            동안 '?'가 깜박인다(째깍). 근거 배지(basis)는 공개 단계에 나온다.
  bars      막대 하나씩(왼쪽부터 자라남), 값 표시
"""

from __future__ import annotations

from studio.video import design as D
from studio.video.design import Box
from studio.video.scenes import Beat, Key, SceneError, SceneRenderer, scene
from studio.video.scenes.common import badge, card, header, vcenter
from studio.video.scenes.exam import pulse, q_chip
from studio.video.scenes.quiz import CountdownMixin, countdown_audio


def _lead(r: SceneRenderer, count: int, has_title: bool = True) -> int:
    return 1 if (has_title and r.n > count) else 0


# ---------------------------------------------------------------- compare

@scene("compare")
class Compare(SceneRenderer):
    required = ("left_title", "right_title", "rows")

    def validate(self) -> None:
        super().validate()
        rows = self.p.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, (list, tuple)) and len(r) == 2 for r in rows):
            raise SceneError(f"장면 {self.scene.id}(compare): rows 는 [[왼쪽, 오른쪽], ...] 형식이어야 합니다.")

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        rows = [(str(a), str(b)) for a, b in self.p["rows"]]
        steps = self.spread(len(rows), self.n, _lead(self, len(rows)))
        box = header(self, C)
        v = self.variant
        mid = int(u * (3.4 if v != 1 else 0))
        col_w = (box.w - mid) // 2
        hs = S.h1 if not self.g.portrait else S.h2
        tcolor = (pal.on_primary, pal.on_primary) if v == 0 else (pal.primary, pal.ink)
        sz = S.lead
        while True:
            pad = int(sz * 0.5)
            hsz = max(sz, int(hs * sz / S.lead))
            lt = D.text_block(self.fonts, str(self.p["left_title"]), role="heavy", size=hsz, min_size=S.body_min, max_w=col_w - 2 * pad,
                              max_lines=2, align="center", color=tcolor[0], balance=True)
            rt = D.text_block(self.fonts, str(self.p["right_title"]), role="heavy", size=hsz, min_size=S.body_min, max_w=col_w - 2 * pad,
                              max_lines=2, align="center", color=tcolor[1], balance=True)
            head_h = int(max(lt.h, rt.h) + 1.4 * pad)
            cells = [(D.text_block(self.fonts, a, size=sz, max_w=col_w - 2 * pad, align="center", color=pal.ink, emph=pal.primary,
                                   balance=True),
                      D.text_block(self.fonts, b, size=sz, max_w=col_w - 2 * pad, align="center", color=pal.ink, emph=pal.primary,
                                   balance=True)) for a, b in rows]
            rh = [int(max(a.h, b.h) + 1.5 * pad) for a, b in cells]
            H = head_h + pad * 0.6 + sum(rh) + pad * 0.6
            broke = any(a.broke or b.broke for a, b in cells)
            if (H <= box.h and not broke) or sz <= S.body_min:
                break
            sz -= 2
        if H > box.h:
            self.defect("비교 행이 많거나 길어 화면을 넘칩니다 — 행을 줄이거나 짧게 쓰세요.")
        H = int(H)
        y0 = vcenter(H, box, 0.42)
        xl, xr = box.x, box.x + col_w + mid
        vs = int(max(mid * 1.2, sz * 1.5))
        if v == 0:
            for x, tb, fill in ((xl, lt, pal.primary), (xr, rt, pal.ink)):
                self.add(card(col_w, H, fill=pal.surface, radius=u, border=pal.line, border_w=2), x, y0, show=-1, enter="fade", dur=0.3)
                self.add(D.plain(_top_round(col_w, head_h, u, fill)), x, y0, show=0, enter="wipe", dur=0.4)
                self.add(tb, x + (col_w - tb.w) / 2, y0 + (head_h - tb.h) / 2, show=0, enter="fade", delay=0.2)
            self.add(D.plain(badge(self, "VS", vs, fill=pal.accent, color=pal.ink)), box.x + col_w + mid / 2 - vs / 2,
                     y0 + head_h / 2 - vs / 2, show=0, enter="pop", delay=0.35, z=5)
        elif v == 1:
            self.add(card(box.w, H, fill=pal.surface, radius=u, border=pal.line, border_w=2), box.x, y0, show=-1, enter="fade", dur=0.3)
            self.add(D.plain(_top_round(box.w, head_h, u, pal.primary_soft)), box.x, y0, show=0, enter="fade")
            self.add(lt, xl + (col_w - lt.w) / 2, y0 + (head_h - lt.h) / 2, show=0, enter="rise", delay=0.1)
            self.add(rt, xr + (col_w - rt.w) / 2, y0 + (head_h - rt.h) / 2, show=0, enter="rise", delay=0.2)
            self.add(D.plain(D.rounded(3, int(H - head_h - pad), 1, pal.line)), box.x + col_w, y0 + head_h + pad / 2, show=0,
                     enter="wipe_down", delay=0.2, dur=0.6)
        else:
            for x, tb, fill in ((xl, lt, pal.primary_soft), (xr, rt, pal.accent_soft)):
                self.add(card(col_w, H, fill=fill, radius=int(u * 1.2), shadow=False), x, y0, show=-1, enter="fade", dur=0.35)
                self.add(tb, x + (col_w - tb.w) / 2, y0 + (head_h - tb.h) / 2 + pad * 0.3, show=0, enter="rise")
            self.add(D.plain(badge(self, "VS", vs, fill=pal.primary, color=pal.on_primary)), box.x + col_w + mid / 2 - vs / 2,
                     y0 + head_h / 2 - vs / 2, show=0, enter="pop", delay=0.3, z=5)
        y = y0 + head_h + pad * 0.6
        for j, ((a, b), h, s) in enumerate(zip(cells, rh, steps)):
            if v == 1 and j % 2 == 1:
                self.add(D.plain(D.rounded(box.w - 4, h, 0, D.mix(pal.surface, pal.primary_soft, 0.5))), box.x + 2, y, show=s,
                         enter="fade", dur=0.3, z=-1)
            if v != 1 and j > 0:
                for x in (xl, xr):
                    self.add(D.plain(D.rounded(col_w - 2 * pad, 2, 1, D.mix(pal.line, pal.bg, 0.2))), x + pad, y, show=s, enter="wipe")
            self.add(a, xl + (col_w - a.w) / 2, y + (h - a.h) / 2, show=s, enter="left", delay=0.05)
            self.add(b, xr + (col_w - b.w) / 2, y + (h - b.h) / 2, show=s, enter="left", delay=0.25)
            y += h


def _top_round(w: int, h: int, r: int, fill):
    im = D.rounded(w, h + r * 2, r, fill)
    return im.crop((0, 0, w, h))


# ---------------------------------------------------------------- sequence

@scene("sequence")
class Sequence(CountdownMixin, SceneRenderer):
    required = ("items",)

    def validate(self) -> None:
        super().validate()
        items = self.p.get("items")
        if not isinstance(items, list) or len(items) < 2:
            raise SceneError(f"장면 {self.scene.id}(sequence): items 는 2개 이상 목록(정답 순서)이어야 합니다.")

    def _order(self) -> list[int]:
        items = [str(i) for i in self.p["items"]]
        m = len(items)
        sh = self.p.get("shuffled")
        if isinstance(sh, list) and len(sh) == m:
            if all(isinstance(x, int) and not isinstance(x, bool) for x in sh) and sorted(sh) == list(range(m)):
                return list(sh)
            if all(isinstance(x, str) for x in sh) and sorted(sh) == sorted(items):
                used: set[int] = set()
                out = []
                for x in sh:
                    k = next(i for i, t in enumerate(items) if t == x and i not in used)
                    used.add(k)
                    out.append(k)
                return out
            raise SceneError(f"장면 {self.scene.id}(sequence): shuffled 는 items 의 순서를 바꾼 목록(번호 또는 글)이어야 합니다.")
        # 결정적 섞기(정답 순서와 같지 않게)
        order = sorted(range(m), key=lambda i: D.stable_hash(self.ctx.asset_id, self.scene.id, i))
        if order == list(range(m)):
            order = order[1:] + order[:1]
        return order

    def _phases(self) -> None:
        N = self.n
        S_ = self.p.get("sort_line")
        if not (isinstance(S_, int) and 1 <= S_ < N):
            S_ = next((i for i, s in enumerate(self.scene.narration) if i >= 1 and "정답" in s), None)
            if S_ is None and N >= 2:
                S_ = 1
        self.S = S_
        cd = self.p.get("countdown")
        explicit = isinstance(cd, (int, float)) and cd > 0
        self.cd = float(cd) if explicit else float(self.scene.hold_seconds)
        self.end_hold = float(self.scene.hold_seconds) if explicit else 0.0

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        pre = self.S if self.S is not None else self.n
        for i in range(pre):
            beats.append(Beat("speech", 0, line=i))
        if pre == 0:
            beats.append(Beat("hold", 0, seconds=2.5))
        if self.cd > 0:
            beats.append(Beat("hold", 0, seconds=self.cd, live=True, sfx="countdown"))
        m = len(self.p["items"])
        if self.S is not None:
            beats.append(Beat("speech", 1, line=self.S))
            for j, i in enumerate(range(self.S + 1, self.n)):
                beats.append(Beat("speech", 2 + min(j, m - 1), line=i))
        else:
            beats.append(Beat("hold", 1, seconds=2.5))
        if self.end_hold > 0:
            beats.append(Beat("hold", beats[-1].step, seconds=self.end_hold))
        return beats

    def live(self, canvas, beat, t, dur) -> None:
        if beat.sfx == "countdown":
            self.draw_countdown(canvas, t, dur)

    def hold_audio(self, beat, seconds):
        if beat.sfx == "countdown" and self.ctx.cfg.video.model_extra.get("sfx", True):
            return countdown_audio(seconds)
        return None

    def build(self) -> None:
        self._phases()
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        items = [str(i) for i in self.p["items"]]
        m = len(items)
        order = self._order()          # order[slot] = item
        slot_of = {item: slot for slot, item in enumerate(order)}
        v = self.variant
        box = header(self, C)
        if self.cd > 0:
            cd_h = int(S.h2 * 1.1)
            self.cd_box = Box(box.x, box.y, box.w if self.g.portrait else int(box.w * 0.5), cd_h)
            _, box = box.take_top(cd_h, int(u * 1.4))
        horizontal = v == 1 and not self.g.portrait and m <= 5
        grid2 = v == 1 and self.g.portrait and m >= 4
        rail = v == 2
        sz = S.lead
        if horizontal:
            gap = int(u * 1.2)
            cw = (box.w - gap * (m - 1)) // m
            while True:
                bs = int(sz * 1.2)
                tbs = [D.text_block(self.fonts, t, size=sz, max_w=cw - int(sz * 0.9), align="center", color=pal.ink, emph=pal.primary,
                                    balance=True) for t in items]
                ch = int(max(tb.h for tb in tbs) + bs + sz * 1.6)
                if (ch <= box.h * 0.85 and not any(tb.broke for tb in tbs)) or sz <= S.body_min:
                    break
                sz -= 2
            y = vcenter(ch, box, 0.42)
            slots = [(box.x + k * (cw + gap), y) for k in range(m)]
        else:
            x_off = int(u * 2.6) if rail else 0
            while True:
                cols = 2 if grid2 else 1
                cw = ((box.w - u) // 2) if grid2 else (box.w - x_off if self.g.portrait else int(box.w * 0.72) - x_off)
                per = -(-m // cols)
                bs = int(sz * 1.2)
                gap = int(sz * 0.42)
                tw = cw - bs - int(sz * 1.6)
                tbs = [D.text_block(self.fonts, t, size=sz, max_w=tw, color=pal.ink, emph=pal.primary, max_lines=3) for t in items]
                ch = int(max(max(tb.h for tb in tbs), bs) + sz * 0.8)
                total = per * ch + (per - 1) * gap
                broke = any(tb.broke for tb in tbs)
                if grid2 and broke and sz <= S.body_min:
                    grid2, sz = False, S.lead       # 두 칸이 좁으면 한 칸으로
                    continue
                if (total <= box.h and not broke) or sz <= S.body_min:
                    break
                sz -= 2
            if total > box.h:
                self.defect("카드가 많거나 길어 화면을 넘칩니다 — 카드를 줄이거나 짧게 쓰세요.")
            y = vcenter(total, box, 0.42)
            x0 = box.x + x_off if (self.g.portrait or grid2) else box.cx - (cw + x_off) / 2 + x_off
            slots = [(x0 + (k % cols) * (cw + u), y + (k // cols) * (ch + gap)) for k in range(m)] if grid2 else \
                    [(x0, y + k * (ch + gap)) for k in range(m)]
        if rail and not horizontal:
            rx = slots[0][0] - u * 1.6
            ry0, ry1 = slots[0][1] + ch / 2, slots[-1][1] + ch / 2
            self.add(D.plain(D.rounded(max(6, u // 4), int(ry1 - ry0), 3, pal.line)), rx - max(6, u // 4) / 2, ry0, show=1,
                     enter="wipe_down", delay=0.8, dur=0.6, z=-1)
        for i, t in enumerate(items):
            tb = tbs[i]
            plain_, sorted_, active = (self._card(cw, ch, tb, bs, i, mode, horizontal, sz) for mode in ("q", "n", "a"))
            sx, sy = slots[slot_of[i]]
            tx, ty = slots[i]
            keys = {1: Key(x=tx, y=ty, sprite=sorted_, delay=0.1 * i, dur=0.75)}
            if self.S is not None and self.n > self.S + 1:
                keys[2 + i] = Key(sprite=active, dur=0.3)
                keys[3 + i] = Key(sprite=sorted_, dur=0.3)
            self.add(plain_, sx, sy, show=0, enter="pop" if horizontal else "left", delay=0.12 * slot_of[i], dur=0.45, keys=keys, z=i)
            if rail and not horizontal:
                dot = int(u * 0.9)
                self.add(D.plain(D.ellipse(dot, dot, pal.primary)), slots[i][0] - u * 1.6 - dot / 2, slots[i][1] + ch / 2 - dot / 2,
                         show=1, enter="pop", delay=0.9 + 0.1 * i, z=-1)

    def _card(self, w: int, h: int, tb: D.TextBlock, bs: int, i: int, mode: str, horizontal: bool, sz: int) -> D.Sprite:
        pal = self.pal
        active = mode == "a"
        im = D.rounded(w, h, int(sz * 0.3), pal.surface, outline=pal.primary if active else pal.line, width=4 if active else 2)
        if mode == "q":
            b = badge(self, "?", bs, fill=pal.primary_soft, color=pal.primary)
        else:
            b = badge(self, str(i + 1), bs, fill=pal.primary, color=pal.on_primary)
        if horizontal:
            D.paste(im, b, (w - bs) / 2, sz * 0.5)
            D.paste(im, tb.img, (w - tb.w) / 2 - tb.ox, sz * 0.5 + bs + (h - bs - sz * 0.5 - tb.h) / 2 - tb.oy)
        else:
            D.paste(im, b, sz * 0.5, (h - bs) / 2)
            D.paste(im, tb.img, sz * 1.1 + bs - tb.ox, (h - tb.h) / 2 - tb.oy)
        return D.shadowed(im, blur=14 if active else 10, offset=(0, 6), alpha=0.2 if active else 0.08)


# ---------------------------------------------------------------- timeline

@scene("timeline")
class Timeline(SceneRenderer):
    required = ("events",)

    def validate(self) -> None:
        super().validate()
        ev = self.p.get("events")
        if not isinstance(ev, list) or not all(isinstance(e, dict) and ("label" in e or "text" in e) for e in ev):
            raise SceneError(f"장면 {self.scene.id}(timeline): events 는 [{{label, text}}, ...] 형식이어야 합니다.")

    def build(self) -> None:
        C, S, pal = self.content(), self.g.s, self.pal
        ev = [(str(e.get("label", "")), str(e.get("text", ""))) for e in self.p["events"]]
        m = len(ev)
        title = str(self.p.get("title") or "")
        steps = self.spread(m, self.n, _lead(self, m, bool(title)))
        box = header(self, C)
        if title:
            tb = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2, emph=pal.primary)
            self.add(tb, box.x, box.y, enter="left")
            _, box = box.take_top(tb.h, int(tb.size * 0.7))
        v = self.variant
        can_row = not self.g.portrait and m <= 5          # 가로 화면은 사건을 한 줄로 늘어놓을 수 있다
        if v == 1 and can_row:
            self._horizontal(box, ev, steps)
        elif v == 2:
            if not self._ladder(box, ev, steps, try_only=can_row):
                self._horizontal(box, ev, steps)
        elif not self._vertical(box, ev, steps, cards=(v == 1), try_only=can_row):
            # 세로 목록이 근거 배지·제목에 밀려 넘치면(리허설 2026-W42: 3개 사건이 근거 배지와 겹침) 가로 배치로 바꾼다
            self._horizontal(box, ev, steps)

    def _vertical(self, box: Box, ev, steps, cards: bool, *, try_only: bool = False) -> bool:
        """세로 목록. 글자를 최소 크기까지 줄이고, 그래도 넘치면 사건 사이 간격을 좁힌다.
        try_only=True 면 넘칠 때 아무것도 놓지 않고 False 를 돌려준다(다른 배치로 바꾸라는 뜻)."""
        S, pal = self.g.s, self.pal
        width = box.w if self.g.portrait else int(box.w * 0.84)
        for gap_k in ((0.32, 0.7), (0.2, 0.35)):            # (카드, 맨 목록) 간격: 기본 → 좁게
            sz = S.lead
            while True:
                dot = int(sz * 0.5)
                pad = int(sz * 0.36) if cards else 0
                tx_off = dot + int(sz * 0.8)
                tw = width - tx_off - 2 * pad
                gap = sz * (gap_k[0] if cards else gap_k[1])
                blocks = []
                for lab, txt in ev:
                    lb = D.text_block(self.fonts, lab, role="heavy", size=max(S.small, int(sz * 0.82)), color=pal.primary, max_w=tw, max_lines=1)
                    tb = D.text_block(self.fonts, txt, size=sz, max_w=tw, color=pal.ink, emph=pal.primary) if txt else None
                    blocks.append((lb, tb))
                hs = [int(lb.h + (tb.h if tb else 0) + 2 * pad) for lb, tb in blocks]
                total = sum(hs) + gap * (len(hs) - 1)
                if (total <= box.h and not any(tb and tb.broke for _, tb in blocks)) or sz <= S.body_min:
                    break
                sz -= 2
            if total <= box.h:
                break
        if total > box.h:
            if try_only:
                return False
            self.defect("사건이 많거나 길어 화면을 넘칩니다 — 사건을 줄이거나 짧게 쓰세요.")
        y = vcenter(total, box, 0.42)
        x0 = box.x if self.g.portrait else box.cx - width / 2
        rail_x = x0 + dot / 2
        tx = x0 + tx_off
        prev_c = None
        lw = max(5, dot // 4)
        for (lb, tb), h, s in zip(blocks, hs, steps):
            cy = y + pad + lb.h / 2
            if prev_c is not None:
                self.add(D.plain(D.rounded(lw, int(cy - prev_c), 2, pal.primary)), rail_x - lw / 2, prev_c, show=s, enter="wipe_down",
                         dur=0.4, z=-1)
            self.add(D.plain(D.ellipse(dot, dot, pal.surface, outline=pal.primary, width=max(4, dot // 5))), rail_x - dot / 2,
                     cy - dot / 2, show=s, enter="pop", delay=0.3, dur=0.35)
            if cards:
                self.add(card(tw + 2 * pad, h, fill=pal.surface, radius=int(sz * 0.3), border=pal.line, border_w=2), tx, y, show=s,
                         enter="left", delay=0.35)
            self.add(lb, tx + pad, y + pad, show=s, enter="left" if not cards else "fade", delay=0.4)
            if tb:
                self.add(tb, tx + pad, y + pad + lb.h, show=s, enter="left" if not cards else "fade", delay=0.5)
            prev_c = cy + dot / 2
            y += h + gap
        return True

    def _horizontal(self, box: Box, ev, steps) -> None:
        S, pal, u = self.g.s, self.pal, self.g.unit
        m = len(ev)
        cw = box.w // m
        sz = S.lead
        while True:
            dot = int(sz * 0.6)
            blocks = [(D.text_block(self.fonts, lab, role="heavy", size=max(S.h2, sz), color=pal.primary, max_w=cw - u,
                                    max_lines=2, align="center", balance=True),
                       D.text_block(self.fonts, txt, size=sz, max_w=cw - int(sz * 0.8), color=pal.ink, align="center", emph=pal.primary,
                                    balance=True))
                      for lab, txt in ev]
            top_h = max(lb.h for lb, _ in blocks)
            bot_h = max(tb.h for _, tb in blocks)
            total = top_h + sz * 0.8 + dot + sz * 0.8 + bot_h
            if (total <= box.h and not any(tb.broke for _, tb in blocks)) or sz <= S.body_min:
                break
            sz -= 2
        if total > box.h:
            self.defect("사건이 많거나 길어 화면을 넘칩니다 — 사건을 줄이거나 짧게 쓰세요.")
        y = vcenter(total, box, 0.42)
        ly = y + top_h + sz * 0.8 + dot / 2
        lh = max(6, dot // 4)
        for j, ((lb, tb), s) in enumerate(zip(blocks, steps)):
            cx = box.x + cw * j + cw / 2
            seg_w = int(cw if j else cw / 2 + dot)
            self.add(D.plain(D.rounded(seg_w, lh, 2, pal.primary)), box.x + cw * j if j else cx - dot, ly - lh / 2,
                     show=s, enter="wipe", dur=0.45, z=-1)
            self.add(D.plain(D.ellipse(dot, dot, pal.accent, outline=pal.primary, width=max(4, dot // 5))), cx - dot / 2, ly - dot / 2,
                     show=s, enter="pop", delay=0.3)
            self.add(lb, cx - lb.w / 2, y + top_h - lb.h, show=s, enter="rise", delay=0.35)
            self.add(tb, cx - tb.w / 2, ly + dot / 2 + sz * 0.8, show=s, enter="rise", delay=0.45)

    def _ladder(self, box: Box, ev, steps, *, try_only: bool = False) -> bool:
        """사다리: 아래에서 위로 한 칸씩 올라간다. 넘치면 try_only=True 일 때 False(다른 배치로)."""
        S, pal, u = self.g.s, self.pal, self.g.unit
        m = len(ev)
        shift = int(u * (1.4 if self.g.portrait else 3.0))
        w = box.w - shift * (m - 1)
        sz = S.lead
        while True:
            gap = int(sz * 0.3)
            pad = int(sz * 0.4)
            blocks = []
            for lab, txt in ev:
                lb = D.text_block(self.fonts, lab, role="heavy", size=max(S.small, int(sz * 0.82)), color=pal.on_primary, max_w=w // 2,
                                  max_lines=1)
                tb = D.text_block(self.fonts, txt, size=sz, max_w=w - lb.w - int(sz * 2.2), color=pal.ink, emph=pal.primary, max_lines=3)
                blocks.append((lb, tb))
            hs = [int(max(lb.h + pad, tb.h) + 2 * pad) for lb, tb in blocks]
            total = sum(hs) + gap * (m - 1)
            if (total <= box.h and not any(tb.broke for _, tb in blocks)) or sz <= S.body_min:
                break
            sz -= 2
        if total > box.h or any(tb.overflow for _, tb in blocks):
            if try_only:
                return False
            self.defect("사건이 많거나 길어 화면을 넘칩니다 — 사건을 줄이거나 짧게 쓰세요.")
        y = vcenter(total, box, 0.42) + total
        for j, ((lb, tb), h, s) in enumerate(zip(blocks, hs, steps)):
            y -= h
            x = box.x + shift * j
            tone = D.mix(pal.primary_soft, pal.accent_soft, j / max(1, m - 1))
            im = D.rounded(w, h, int(sz * 0.28), tone)
            lab_w = lb.w + sz * 0.8
            D.paste(im, D.rounded(int(lab_w), int(h - pad), int(sz * 0.22), pal.primary), pad / 2, pad / 2)
            D.paste(im, lb.img, pad / 2 + sz * 0.4 - lb.ox, (h - lb.h) / 2 - lb.oy)
            D.paste(im, tb.img, lab_w + sz * 0.9 - tb.ox, (h - tb.h) / 2 - tb.oy)
            self.add(D.shadowed(im, blur=10, offset=(0, 5), alpha=0.1), x, y, show=s, enter="rise", delay=0.05)
            y -= gap
        return True


# ---------------------------------------------------------------- flow

FLOW_THINK = 3.0      # hidden 공개 전 기본 생각 시간(초)
FLOW_REVEAL_HOLD = 2.0


@scene("flow")
class Flow(SceneRenderer):
    required = ("steps",)

    # ---- hidden: '?' 로 가렸다가 공개

    def _hidden_phases(self) -> None:
        m = len(self.p["steps"])
        raw = self.p.get("hidden") or []
        if isinstance(raw, int) and not isinstance(raw, bool):
            raw = [raw]
        if not isinstance(raw, list) or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < m for i in raw):
            raise SceneError(f"장면 {self.scene.id}(flow): hidden 은 숨길 단계 번호(0부터 {m - 1}) 목록이어야 합니다.")
        self.hidden = sorted(set(raw))
        if not self.hidden:
            return
        N, lines = self.n, self.scene.narration
        R = self.p.get("reveal_line")
        if not (isinstance(R, int) and not isinstance(R, bool) and 1 <= R < N):
            R = next((i for i, t in enumerate(lines) if i >= 1 and ("정답" in t or "답은" in t)), None)
            if R is None and N >= 2:
                R = N - 1
        self.R = R
        self.pre = R if R is not None else N
        cd = self.p.get("countdown")
        explicit = isinstance(cd, (int, float)) and not isinstance(cd, bool) and cd > 0
        self.think = float(cd) if explicit else (float(self.scene.hold_seconds) or FLOW_THINK)
        self.end_hold = float(self.scene.hold_seconds) if explicit else 0.0

    def plan(self) -> list[Beat]:
        if not self.hidden:
            return super().plan()
        beats = [Beat("lead", -1)]
        last_node = max(self.node_steps)
        for i in range(self.pre):
            beats.append(Beat("speech", min(i, last_node), line=i))
        if self.pre == 0:
            beats.append(Beat("hold", last_node, seconds=2.5))
        beats.append(Beat("hold", last_node, seconds=self.think, live=True, sfx="think"))
        if self.R is not None:
            for i in range(self.R, self.n):
                beats.append(Beat("speech", self.s_reveal, line=i))
        else:
            beats.append(Beat("hold", self.s_reveal, seconds=FLOW_REVEAL_HOLD))
        if self.end_hold > 0:
            beats.append(Beat("hold", self.s_reveal, seconds=self.end_hold))
        return beats

    def live(self, canvas, beat, t, dur) -> None:
        if beat.sfx == "think":
            for b in self.chip_boxes:
                pulse(canvas, b, t, self.pal.accent, self.g.unit)

    def hold_audio(self, beat, seconds):
        if beat.sfx == "think" and self.ctx.cfg.video.model_extra.get("sfx", True):
            return countdown_audio(seconds)
        return None

    def _cover_hidden(self, steps: list[int], sz: int) -> None:
        """숨길 단계의 글 자리에 '?' 칩(단계 공개 때 같이 나옴) → 공개 단계에 칩이 사라지고 상자에 고리."""
        pal, u = self.pal, self.g.unit
        self.s_reveal = max(steps) + 1
        self.basis_step = self.s_reveal
        self.chip_boxes: list[Box] = []
        for i in self.hidden:
            lr, node = self.label_rects[i], self.node_rects[i]
            cw = int(max(lr.w + u * 0.6, sz * 2.2))
            ch = int(max(lr.h + u * 0.4, sz * 1.3))
            inside = self.variant != 1          # 상자 안 글(0·2) — 번호 원 옆 글(1)은 폭 제한 없음
            if inside and node.w > 0:
                cw = min(cw, node.w - int(u * 0.4))
            cx, cy = lr.x + lr.w / 2, lr.y + lr.h / 2
            cb = Box(int(cx - cw / 2), int(cy - ch / 2), cw, ch)
            self.chip_boxes.append(cb)
            self.add(q_chip(self, cw, ch), cb.x, cb.y, show=steps[i], enter="pop", delay=0.3, z=4,
                     keys={self.s_reveal: Key(alpha=0.0, delay=0.05, dur=0.35)})
            rb = node if node.w > 0 else cb
            e = int(u * 0.35)
            rad = (rb.h // 2 if not inside else int(min(rb.w, rb.h) * 0.3)) + e
            ring = D.rounded(rb.w + 2 * e, rb.h + 2 * e, rad, None, outline=pal.accent, width=max(5, u // 4))
            self.add(D.plain(ring), rb.x - e, rb.y - e, show=self.s_reveal, enter="pop", delay=0.3, dur=0.45, z=1)

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        labels = [str(s) for s in self.p["steps"]]
        m = len(labels)
        title = str(self.p.get("title") or "")
        self._hidden_phases()
        lines = self.pre if self.hidden else self.n
        steps = self.spread(m, lines, 1 if (title and lines > m) else 0)
        self.node_steps = steps
        self.label_rects: list[Box] = [Box(0, 0, 1, 1)] * m
        self.node_rects: list[Box] = [Box(0, 0, 0, 0)] * m
        # hidden(시험 '?' 흐름도)은 5~7단계라 자리가 귀하다: 제목이 있으면 자동 머리말(챕터 제목)을 뺀다(params.kicker 는 그대로)
        box = C if (self.hidden and title and "kicker" not in self.p) else header(self, C)
        if title:
            tb = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2, emph=pal.primary)
            self.add(tb, box.x, box.y, enter="left")
            _, box = box.take_top(tb.h, int(tb.size * 0.7))
        v = self.variant
        if v == 1:
            sz = self._dots(box, labels, steps)
            if self.hidden:
                self._cover_hidden(steps, sz)
            return
        # 상자 + 화살표(가로는 한 줄 최대 4개, 넘치면 지그재그 / 세로는 한 줄 1개, 6개 이상이면 2열 지그재그)
        if self.g.portrait:
            cols = 1 if m <= 5 else 2
        else:
            cols = m if m <= 4 else 3
        rows_n = -(-m // cols)
        arrow = int(u * (2.2 if self.g.portrait else 2.8))
        bw = (box.w - arrow * (cols - 1)) // cols if cols > 1 else (box.w if self.g.portrait else int(box.w * 0.6))
        sz = S.lead
        while True:
            nb = int(sz * 0.95)
            pad = int(sz * (0.4 if self.g.portrait else 0.5))
            tw = bw - 2 * pad - (nb + int(sz * 0.3) if v == 2 else 0)
            tbs = [D.text_block(self.fonts, t, role="bold", size=sz, max_w=tw, align="center", balance=True,
                                color=pal.ink if v == 0 else pal.on_primary, emph=pal.primary if v == 0 else pal.accent, max_lines=4)
                   for t in labels]
            bh = int(max(tb.h for tb in tbs) + 2 * pad + (nb * 0.35 if v == 0 else 0))
            if v == 2:
                bh = max(bh, int(nb + 2 * pad))
            vgap = arrow if (cols == 1 or rows_n > 1) else 0
            total = rows_n * bh + (rows_n - 1) * vgap + (nb / 2 if v == 0 else 0)
            if (total <= box.h and not any(tb.broke for tb in tbs)) or sz <= S.body_min:
                if total <= box.h or sz <= S.small:     # 최소 크기에서도 넘치면 S.small 까지만 더 줄인다
                    break
            sz -= 2
        if total > box.h:
            self.defect("단계가 많거나 길어 화면을 넘칩니다 — 단계를 줄이거나 짧게 쓰세요.")
        y0 = vcenter(total, box, 0.42) + (nb / 2 if v == 0 else 0)
        x_start = box.x if (cols > 1 or self.g.portrait) else box.cx - bw / 2
        rects = []
        for i in range(m):
            r_, c = divmod(i, cols)
            if r_ % 2 == 1:
                c = cols - 1 - c
            rects.append(Box(int(x_start + c * (bw + arrow)), int(y0 + r_ * (bh + vgap)), bw, bh))
        th = max(6, sz // 9)
        for i, (rb, tb, s) in enumerate(zip(rects, tbs, steps)):
            if i > 0:
                pr = rects[i - 1]
                if pr.y == rb.y:
                    img = D.icon_arrow(arrow - u, th, pal.accent if v == 0 else pal.primary, "right")
                    if rb.x < pr.x:
                        img = img.transpose(D.Image.Transpose.FLIP_LEFT_RIGHT)
                        ax = rb.r + u / 2
                    else:
                        ax = pr.r + u / 2
                    self.add(D.plain(img), ax, rb.cy - img.height / 2, show=s, enter="wipe" if rb.x > pr.x else "fade", dur=0.35)
                else:
                    img = D.icon_arrow(int(vgap - u), th, pal.accent if v == 0 else pal.primary, "down")
                    self.add(D.plain(img), pr.cx - img.width / 2, pr.b + u / 2, show=s, enter="wipe_down", dur=0.35)
            self.node_rects[i] = rb
            if v == 0:
                self.label_rects[i] = Box(int(rb.x + (rb.w - tb.w) / 2), int(rb.y + nb * 0.35 + (rb.h - nb * 0.35 - tb.h) / 2), tb.w, tb.h)
                im = D.rounded(rb.w, rb.h, int(sz * 0.3), pal.surface, outline=pal.primary, width=3)
                D.paste(im, tb.img, (rb.w - tb.w) / 2 - tb.ox, nb * 0.35 + (rb.h - nb * 0.35 - tb.h) / 2 - tb.oy)
                sp = D.shadowed(im, blur=12, offset=(0, 6), alpha=0.1)
                self.add(sp, rb.x, rb.y, show=s, enter="rise", delay=0.25 if i else 0.05, dur=0.45)
                self.add(D.plain(badge(self, str(i + 1), nb, fill=pal.primary, color=pal.on_primary)), rb.cx - nb / 2, rb.y - nb / 2,
                         show=s, enter="pop", delay=0.4 if i else 0.2, z=2)
            else:
                self.label_rects[i] = Box(int(rb.x + pad + nb + int(sz * 0.3) + (tw - tb.w) / 2), int(rb.y + (rb.h - tb.h) / 2), tb.w, tb.h)
                tone = D.mix(pal.primary, pal.accent, 0.0 if m == 1 else i / (m - 1) * 0.35)
                im = D.rounded(rb.w, rb.h, int(sz * 0.35), tone)
                D.paste(im, badge(self, str(i + 1), nb, fill=D.rgba(pal.on_primary, 0.22), color=pal.on_primary), pad * 0.8,
                        (rb.h - nb) / 2)
                D.paste(im, tb.img, pad + nb + int(sz * 0.3) + (tw - tb.w) / 2 - tb.ox, (rb.h - tb.h) / 2 - tb.oy)
                sp = D.shadowed(im, blur=12, offset=(0, 6), alpha=0.16)
                self.add(sp, rb.x, rb.y, show=s, enter="pop", delay=0.25 if i else 0.05, dur=0.45)
        if self.hidden:
            self._cover_hidden(steps, sz)

    def _dots(self, box: Box, labels: list[str], steps: list[int]) -> int:
        """번호 원이 화살표로 이어지는 흐름(세로는 왼쪽 줄, 가로는 한 줄)."""
        S, pal, u = self.g.s, self.pal, self.g.unit
        m = len(labels)
        if self.g.portrait or m > 5:
            width = box.w if self.g.portrait else int(box.w * 0.8)
            sz = S.lead
            while True:
                nb = int(sz * 1.25)
                tw = width - nb - int(sz * 0.7)
                tbs = [D.text_block(self.fonts, t, size=sz, max_w=tw, color=pal.ink, emph=pal.primary, max_lines=3) for t in labels]
                hs = [max(nb, tb.h) for tb in tbs]
                gap = sz * (0.9 if m <= 4 else 0.6)
                total = sum(hs) + gap * (m - 1)
                if (total <= box.h and not any(tb.broke for tb in tbs)) or sz <= S.body_min:
                    if total <= box.h or sz <= S.small:     # 최소 크기에서도 넘치면 S.small 까지만 더 줄인다
                        break
                sz -= 2
            if total > box.h:
                self.defect("단계가 많거나 길어 화면을 넘칩니다 — 단계를 줄이거나 짧게 쓰세요.")
            y = vcenter(total, box, 0.42)
            x0 = box.x if self.g.portrait else box.cx - width / 2
            prev = None
            for i, (tb, h, s) in enumerate(zip(tbs, hs, steps)):
                cy = y + h / 2
                if prev is not None:
                    img = D.icon_arrow(max(8, int(cy - nb / 2 - prev - sz * 0.25)), max(5, sz // 10), pal.accent, "down")
                    self.add(D.plain(img), x0 + nb / 2 - img.width / 2, prev + sz * 0.12, show=s, enter="wipe_down", dur=0.35, z=-1)
                self.add(D.plain(badge(self, str(i + 1), nb, fill=pal.primary, color=pal.on_primary)), x0, cy - nb / 2, show=s,
                         enter="pop", delay=0.25)
                self.add(tb, x0 + nb + int(sz * 0.7), cy - tb.h / 2, show=s, enter="left", delay=0.35)
                self.label_rects[i] = Box(int(x0 + nb + int(sz * 0.7)), int(cy - tb.h / 2), tb.w, tb.h)
                self.node_rects[i] = Box(int(x0), int(cy - nb / 2), nb, nb)
                prev = cy + nb / 2
                y += h + gap
            return sz
        cw = box.w // m
        sz = S.lead
        while True:
            nb = int(sz * 1.35)
            tbs = [D.text_block(self.fonts, t, size=sz, max_w=cw - int(sz * 0.8), align="center", color=pal.ink, emph=pal.primary,
                                max_lines=4, balance=True) for t in labels]
            total = nb + sz * 0.8 + max(tb.h for tb in tbs)
            if (total <= box.h and not any(tb.broke for tb in tbs)) or sz <= S.body_min:
                if total <= box.h or sz <= S.small:     # 최소 크기에서도 넘치면 S.small 까지만 더 줄인다
                    break
            sz -= 2
        y = vcenter(total, box, 0.42)
        for i, (tb, s) in enumerate(zip(tbs, steps)):
            cx = box.x + cw * i + cw / 2
            if i:
                img = D.icon_arrow(int(cw - nb - u), max(5, sz // 10), pal.accent, "right")
                self.add(D.plain(img), cx - cw + nb / 2 + u / 2, y + nb / 2 - img.height / 2, show=s, enter="wipe", dur=0.35)
            self.add(D.plain(badge(self, str(i + 1), nb, fill=pal.primary, color=pal.on_primary)), cx - nb / 2, y, show=s, enter="pop",
                     delay=0.25)
            self.add(tb, cx - tb.w / 2, y + nb + sz * 0.8, show=s, enter="rise", delay=0.35)
            self.label_rects[i] = Box(int(cx - tb.w / 2), int(y + nb + sz * 0.8), tb.w, tb.h)
            self.node_rects[i] = Box(int(cx - nb / 2), int(y), nb, nb)
        return sz


# ---------------------------------------------------------------- bars

def fmt_value(v: float, unit: str = "") -> str:
    s = f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.1f}"
    return f"{s}{unit}"


@scene("bars")
class Bars(SceneRenderer):
    required = ("items",)

    def validate(self) -> None:
        super().validate()
        items = self.p.get("items")
        if not isinstance(items, list) or not all(isinstance(i, dict) and "label" in i and "value" in i for i in items):
            raise SceneError(f"장면 {self.scene.id}(bars): items 는 [{{label, value}}, ...] 형식이어야 합니다.")
        for i in items:
            try:
                float(i["value"])
            except (TypeError, ValueError):
                raise SceneError(f"장면 {self.scene.id}(bars): value 는 숫자여야 합니다 ({i!r}).")

    def build(self) -> None:
        C, S, pal = self.content(), self.g.s, self.pal
        items = [(str(i["label"]), float(i["value"])) for i in self.p["items"]]
        if any(v < 0 for _, v in items):
            self.warn("음수 값은 0으로 그립니다.")
        unit = str(self.p.get("unit") or "")
        title = str(self.p.get("title") or "")
        m = len(items)
        steps = self.spread(m, self.n, _lead(self, m, bool(title)))
        box = header(self, C)
        tb_title = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2,
                             emph=pal.primary) if title else None
        head_h = int(tb_title.h + tb_title.size * 0.8) if tb_title else 0
        vmax = max([v for _, v in items] + [1e-9])
        top = max(range(m), key=lambda k: items[k][1])
        v = self.variant
        if v == 1 and not self.g.portrait and m <= 7:
            area = Box(box.x, box.y + head_h, box.w, box.h - head_h)
            if tb_title:
                self.add(tb_title, box.x, box.y, enter="left")
            self._columns(area, items, steps, vmax, unit, top)
            return
        stacked = self.g.portrait and v == 1
        sz = S.lead
        while True:
            lab_w = 0 if stacked else int(box.w * (0.3 if self.g.portrait else 0.26))
            val_w = int(sz * (2.9 if self.g.portrait else 3.6))
            bar_area = box.w - lab_w - (int(sz * 0.5) if lab_w else 0) - (val_w if v != 2 else 0)
            labs = [D.text_block(self.fonts, lab, role="bold", size=sz, max_w=lab_w or box.w, color=pal.ink, max_lines=2,
                                 align="right" if lab_w else "left") for lab, _ in items]
            bar_h = int(sz * (0.9 if stacked else 1.15))
            rh = [max(lb.h, bar_h) if not stacked else lb.h + sz * 0.15 + bar_h for lb in labs]
            gap = int(sz * (0.5 if not stacked else 0.45))
            total = sum(rh) + gap * (m - 1)
            if (total <= box.h - head_h and not any(lb.broke for lb in labs)) or sz <= S.body_min:
                break
            sz -= 2
        if total > box.h - head_h:
            self.defect("막대가 많아 화면을 넘칩니다 — 항목을 줄이세요.")
        y = vcenter(head_h + total, box, 0.42)
        if tb_title:
            self.add(tb_title, box.x, y, enter="left")
            y += head_h
        x_bar = box.x + lab_w + (int(sz * 0.5) if lab_w else 0)
        for k, ((lab, val), lb, h, s) in enumerate(zip(items, labs, rh, steps)):
            hl = (k == top) if v == 2 else True
            color = pal.primary if hl else D.mix(pal.muted, pal.bg, 0.15)
            bw = max(int(bar_h * 0.6), int(bar_area * max(0.0, val) / vmax))
            if stacked:
                self.add(lb, box.x, y, show=s, enter="fade", delay=0.05)
                by = y + lb.h + sz * 0.15
            else:
                self.add(lb, box.x + lab_w - lb.w, y + (h - lb.h) / 2, show=s, enter="fade", delay=0.05)
                by = y + (h - bar_h) / 2
            self.add(D.plain(D.rounded(bw, bar_h, bar_h // 3, color)), x_bar, by, show=s, enter="wipe", delay=0.15, dur=0.8)
            label = fmt_value(val, unit)
            inside = D.text_block(self.fonts, label, role="heavy", size=sz, max_w=val_w * 2, max_lines=1,
                                  color=pal.on_primary if hl else pal.ink)
            if v == 2 and inside.w + sz * 0.6 < bw:
                self.add(inside, x_bar + bw - inside.w - sz * 0.3, by + (bar_h - inside.h) / 2, show=s, enter="fade", delay=0.85)
            else:
                vt = D.text_block(self.fonts, label, role="heavy", size=sz, max_w=val_w * 2, max_lines=1,
                                  color=pal.primary if hl else pal.ink2)
                self.add(vt, x_bar + bw + sz * 0.3, by + (bar_h - vt.h) / 2, show=s, enter="fade", delay=0.85)
            y += h + gap

    def _columns(self, box: Box, items, steps, vmax: float, unit: str, top: int) -> None:
        S, pal, u = self.g.s, self.pal, self.g.unit
        m = len(items)
        cw = box.w // m
        bar_w = int(min(cw * 0.5, u * 8))
        labs = [D.text_block(self.fonts, lab, role="bold", size=S.lead, min_size=S.body_min, max_w=cw - u, max_h=S.lead * 2.8,
                             align="center", color=pal.ink, balance=True) for lab, _ in items]
        lab_h = max(lb.h for lb in labs)
        val_h = int(S.lead * 1.5)
        area = box.h - lab_h - val_h - u * 2
        base = box.y + val_h + u + area
        self.add(D.plain(D.rounded(box.w, 3, 1, pal.line)), box.x, base, show=-1, enter="wipe", dur=0.4)
        for k, ((lab, val), lb, s) in enumerate(zip(items, labs, steps)):
            cx = box.x + cw * k + cw / 2
            bh = max(int(bar_w * 0.3), int(area * max(0.0, val) / vmax))
            color = pal.primary if k == top else D.mix(pal.primary, pal.bg, 0.45)
            bar = D.rounded(bar_w, bh + bar_w // 4, bar_w // 4, color).crop((0, 0, bar_w, bh))
            self.add(D.plain(bar), cx - bar_w / 2, base - bh, show=s, enter="grow_up", delay=0.1, dur=0.8)
            vt = D.text_block(self.fonts, fmt_value(val, unit), role="heavy", size=S.lead, max_w=cw, max_lines=1,
                              color=pal.primary if k == top else pal.ink2, align="center")
            self.add(vt, cx - vt.w / 2, base - bh - u * 0.6 - vt.h, show=s, enter="fade", delay=0.6)
            self.add(lb, cx - lb.w / 2, base + u, show=s, enter="fade", delay=0.05)

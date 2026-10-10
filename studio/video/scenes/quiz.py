"""quiz 장면: 문제·보기 → 카운트다운(실제 프레임 애니메이션) → 오답이 하나씩 흐려지고 정답 강조 → 해설·근거.

params: question, choices[2~5], answer(0부터), explanation, basis?(근거 표기), countdown?(초, 기본 hold_seconds),
        reveal_line?(정답 공개를 시작하는 narration 번호)

narration 단계 배분(문장 수 N, 정답 공개 문장 R):
  R = reveal_line → 없으면 '정답'이 처음 나오는 문장(1번 이후) → 없으면 1 (N≥2, 문제 읽기 1 + 해설 권장형) → N≤1 이면 없음
  문장 0          문제(+보기, 공개 전 문장이 1개뿐이면 같이)
  문장 1..R-1     보기(시차를 두고 하나씩)
  카운트다운       countdown 초 동안 막대·숫자가 줄어든다(효과음 째깍). 내레이션 없음
  문장 R          오답이 하나씩 흐려지고 정답 강조(O 표시)
  문장 R+1..      해설·근거 상자. 해설 문장이 없으면 읽을 시간만큼 조용히 보여 준다
  countdown 을 따로 주면 hold_seconds 는 원래 뜻(끝난 뒤 더 보여 주기)으로 쓴다.
공간이 모자라면 해설 단계에서 오답을 접고 정답을 문제 아래로 올린 뒤 해설을 붙인다.
"""

from __future__ import annotations

import math
import re

import numpy as np
from PIL import Image

from studio.video import design as D
from studio.video import media
from studio.video.design import Box
from studio.video.scenes import Beat, Key, SceneError, SceneRenderer, scene
from studio.video.scenes.common import badge, card, pill

DEFAULT_COUNTDOWN = 5.0
REVEAL_SECONDS = 2.2
WRONG_STAGGER = 0.28


def countdown_audio(seconds: float) -> np.ndarray:
    """매 초 '째깍'(마지막 3초는 조금 높게)."""
    n = int(round(seconds * media.SAMPLE_RATE))
    out = np.zeros(n, dtype=np.int32)
    k = 0
    while k < seconds - 0.4:
        left = seconds - k
        tick = media.tone(1180 if left <= 3.01 else 880, 0.09, gain_db=-24, decay=55)
        s = int(k * media.SAMPLE_RATE)
        e = min(n, s + tick.size)
        out[s:e] += tick[: e - s]
        k += 1
    return np.clip(out, -32768, 32767).astype(np.int16)


def read_seconds(text: str) -> float:
    """글을 눈으로 읽는 데 필요한 시간(초당 7자, 2.5~7초)."""
    return max(2.5, min(7.0, len(re.sub(r"\s", "", text)) / 7.0 + 1.0))


class CountdownMixin:
    """카운트다운 위젯(막대형/고리형)을 live() 에서 그린다. self.cd_box, self.cd_style 이 필요하다."""

    cd_box: Box | None = None
    cd_style: str = "bar"

    def draw_countdown(self, canvas: Image.Image, t: float, dur: float) -> None:
        if self.cd_box is None or dur <= 0:
            return
        pal, b = self.pal, self.cd_box
        left = max(0.0, dur - t)
        frac = left / dur
        appear = D.ease_out(t / 0.3)
        secs = max(1, math.ceil(left - 1e-6)) if left > 0.05 else 0
        since = (dur - left) - math.floor(dur - left)          # 초가 바뀐 뒤 지난 시간
        pulse = 1.0 + 0.14 * (1 - D.ease_out(since / 0.28)) if left > 0.05 else 1.0
        col = D.mix(pal.accent, pal.primary, 1 - frac) if frac > 0.34 else pal.primary
        if self.cd_style == "ring":
            size = b.h
            ring = D.arc_ring(size, max(8, size // 11), frac, col, track=pal.line)
            D.paste(canvas, ring, b.x + (b.w - size) / 2, b.y, appear)
            self._num(canvas, str(secs), b.x + b.w / 2, b.y + size / 2, int(size * 0.42) , pal.ink, pulse, appear)
            return
        circ = b.h
        bar_h = max(10, circ // 6)
        x0 = b.x + circ + self.g.unit
        w = b.w - circ - self.g.unit
        y = b.y + (circ - bar_h) / 2
        D.paste(canvas, D.rounded(int(w), bar_h, bar_h // 2, pal.line), x0, y, appear)
        fw = int(w * frac * appear)
        if fw >= bar_h:
            D.paste(canvas, D.rounded(fw, bar_h, bar_h // 2, col), x0, y)
        D.paste(canvas, D.ellipse(circ, circ, col), b.x, b.y, appear)
        self._num(canvas, str(secs), b.x + circ / 2, b.y + circ / 2, int(circ * 0.5), pal.on_primary, pulse, appear)

    def _num(self, canvas, text, cx, cy, size, color, scale, alpha) -> None:
        tb = D.text_block(self.fonts, text, role="heavy", size=max(8, int(size * scale)), color=color, max_w=size * 4, max_lines=1)
        D.paste(canvas, tb.img, cx - tb.w / 2 - tb.ox, cy - tb.h / 2 - tb.oy, alpha)


@scene("quiz")
class Quiz(CountdownMixin, SceneRenderer):
    required = ("question", "choices", "explanation")

    def validate(self) -> None:
        super().validate()
        ch = self.p.get("choices")
        if not isinstance(ch, list) or not 2 <= len(ch) <= 5:
            raise SceneError(f"장면 {self.scene.id}(quiz): choices 는 2~5개 목록이어야 합니다.")
        a = self.p.get("answer")
        if not isinstance(a, int) or isinstance(a, bool) or not 0 <= a < len(ch):
            raise SceneError(f"장면 {self.scene.id}(quiz): answer 는 0부터 {len(ch) - 1} 사이 정수(0부터 셈)여야 합니다.")

    # ---- 단계 배분

    def _phases(self) -> None:
        N = self.n
        R = self.p.get("reveal_line")
        if not (isinstance(R, int) and 1 <= R < N):
            R = next((i for i, s in enumerate(self.scene.narration) if i >= 1 and "정답" in s), None)
            if R is None and N >= 2:
                R = 1          # docs/SCRIPT_FORMAT.md 권장형: 문제 읽기 1문장 + 해설 문장들
        self.R = R
        self.pre = R if R is not None else N
        self.s_q = 0
        self.s_c = 1 if self.pre >= 2 else 0
        self.s_rev = self.s_c + 1
        self.s_exp = self.s_rev + 1
        cd = self.p.get("countdown")
        self.cd = float(cd) if isinstance(cd, (int, float)) and cd > 0 else float(self.scene.hold_seconds)
        if self.cd <= 0:
            self.cd = DEFAULT_COUNTDOWN
            self.warn(f"countdown·hold_seconds 가 없어 {DEFAULT_COUNTDOWN:.0f}초로 정했습니다.")
        self.end_hold = float(self.scene.hold_seconds) if isinstance(cd, (int, float)) and cd > 0 else 0.0

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        for i in range(self.pre):
            beats.append(Beat("speech", self.s_q if i == 0 else self.s_c, line=i))
        if self.pre == 0:
            beats.append(Beat("hold", self.s_c, seconds=read_seconds(str(self.p["question"]))))
        beats.append(Beat("hold", self.s_c, seconds=self.cd, live=True, sfx="countdown"))
        if self.R is not None:
            beats.append(Beat("speech", self.s_rev, line=self.R))
            rest = list(range(self.R + 1, self.n))
            for i in rest:
                beats.append(Beat("speech", self.s_exp, line=i))
            if not rest:
                beats.append(Beat("hold", self.s_exp, seconds=read_seconds(f"{self.p['explanation']} {self.p.get('basis') or ''}")))
        else:
            beats.append(Beat("hold", self.s_rev, seconds=REVEAL_SECONDS))
            beats.append(Beat("hold", self.s_exp, seconds=read_seconds(f"{self.p['explanation']} {self.p.get('basis') or ''}")))
        if self.end_hold > 0:
            beats.append(Beat("hold", self.s_exp, seconds=self.end_hold))
        return beats

    def live(self, canvas, beat, t, dur) -> None:
        if beat.sfx == "countdown":
            self.draw_countdown(canvas, t, dur)

    def hold_audio(self, beat, seconds):
        if beat.sfx == "countdown" and self.ctx.cfg.video.model_extra.get("sfx", True):
            return countdown_audio(seconds)
        return None

    # ---- 화면

    def build(self) -> None:
        self._phases()
        if self.g.portrait:
            self._build_portrait()
        else:
            self._build_landscape()

    def _fit_choices(self, v: int, choices: list[str], col_w: int, cols: int, avail: float):
        S = self.g.s
        sz = S.lead
        while True:
            rows = [self._choice(v, i, c, col_w, sz) for i, c in enumerate(choices)]
            rh = max(r[0].h for r in rows)
            per = -(-len(choices) // cols)
            gap = int(sz * 0.32)
            total = per * rh + (per - 1) * gap
            if (total <= avail and not any(r[0].meta.get("broke") for r in rows)) or sz <= S.body_min:
                break
            sz -= 2
        if total > avail:
            self.warn("문제·보기가 길어 화면을 넘칩니다 — 보기를 짧게 쓰세요.")
        for i, r in enumerate(rows):
            if r[0].meta.get("overflow"):
                self.warn(f"보기 {i + 1} 이 길어 줄였습니다.")
        return rows, rh, gap, total, sz

    def _place_choices(self, rows, pos, *, grid: bool, collapse_to: tuple[float, float] | None) -> None:
        """보기 배치 + 공개 순서(오답이 하나씩 흐려지고 정답 강조, 접기)."""
        pal = self.pal
        ans = int(self.p["answer"])
        wrong = [i for i in range(len(rows)) if i != ans]
        for k in range(len(rows)):
            normal, correct = rows[k]
            x, y = pos[k]
            delay = (0.25 if self.s_c == self.s_q else 0.05) + 0.16 * k
            keys: dict[int, Key] = {}
            if k == ans:
                keys[self.s_rev] = Key(sprite=correct, delay=WRONG_STAGGER * len(wrong) + 0.1, dur=0.35)
                if collapse_to:
                    keys[self.s_exp] = Key(x=collapse_to[0], y=collapse_to[1], delay=0.25, dur=0.5)
            else:
                keys[self.s_rev] = Key(alpha=0.3, delay=WRONG_STAGGER * wrong.index(k), dur=0.3)
                if collapse_to:
                    keys[self.s_exp] = Key(alpha=0.0, dur=0.3)
            self.add(normal, x, y, show=self.s_c, enter="left" if not grid else "rise", delay=delay, keys=keys, z=1 if k == ans else 0)
        bsz = rows[ans][0].meta["badge"]
        bx, by = rows[ans][0].meta["badge_xy"]
        o_size = int(bsz * 1.55)
        off = (o_size - bsz) / 2
        ox, oy = pos[ans][0] + bx - off, pos[ans][1] + by - off
        o_ring = D.plain(D.ellipse(o_size, o_size, None, outline=pal.accent, width=max(6, o_size // 11)))
        okeys = {self.s_exp: Key(x=collapse_to[0] + bx - off, y=collapse_to[1] + by - off, delay=0.25, dur=0.5)} if collapse_to else {}
        self.add(o_ring, ox, oy, show=self.s_rev, enter="pop", delay=WRONG_STAGGER * len(wrong) + 0.3, dur=0.45, z=2, keys=okeys)

    def _build_portrait(self) -> None:
        S, u = self.g.s, self.g.unit
        v = self.variant
        box = self.content()
        choices = [str(c) for c in self.p["choices"]]
        self.cd_style = "ring" if v == 1 else "bar"
        ring = int(S.h2 * 2.1) if self.cd_style == "ring" else 0
        q_h = self._question(v, box, ring)
        y = box.y + q_h + u * 1.4
        if self.cd_style == "bar":
            cd_h = int(S.h2 * 1.0)
            self.cd_box = Box(box.x, int(y), box.w, cd_h)
            y += cd_h + u * 1.2
        else:
            self.cd_box = Box(box.r - ring, box.y + max(0, (q_h - ring) // 2), ring, ring)
        rows, rh, gap, total, sz = self._fit_choices(v, choices, box.w, 1, box.b - y)
        y0 = y
        pos = [(box.x, y0 + i * (rh + gap)) for i in range(len(choices))]
        rows_bottom = y0 + total
        exp_full = self._explain(box.w, box.b - rows_bottom - u * 1.4, probe=True)
        if exp_full is None:      # 공간이 모자라면 오답을 접고 정답을 문제 아래로 올린다
            top = box.y + q_h + u * 1.2
            exp = self._explain(box.w, box.b - (top + rh + u), probe=False)
            exp_y, collapse = top + rh + u, (box.x, top)
        else:
            exp, exp_y, collapse = exp_full, rows_bottom + u * 1.4, None
        self._place_choices(rows, pos, grid=False, collapse_to=collapse)
        self.add(exp, box.x, exp_y, show=self.s_exp, enter="rise", delay=0.45 if collapse else 0.1, dur=0.5, z=3)

    def _build_landscape(self) -> None:
        """가로: 문제는 위 전체 폭, 아래 왼쪽에 보기, 오른쪽에 카운트다운 → 해설."""
        S, u = self.g.s, self.g.unit
        v = self.variant
        box = self.content()          # 가로 퀴즈는 공간이 빠듯해 머리말(kicker)을 생략한다
        choices = [str(c) for c in self.p["choices"]]
        q_h = self._question(v, box, 0)
        area = Box(box.x, int(box.y + q_h + u * 1.6), box.w, int(box.h - q_h - u * 1.6))
        left, right = area.split_x(0.5, int(u * 2.5))
        cell_tw = (left.w - u) // 2 - int(S.lead * 3.4)
        font = self.fonts.get("regular", S.lead)
        grid = (v == 1 and len(choices) == 4 and all(font.getlength(D.strip_emphasis(c)) <= cell_tw for c in choices))
        cols = 2 if grid else 1
        col_w = (left.w - u) // 2 if grid else left.w
        rows, rh, gap, total, sz = self._fit_choices(v, choices, col_w, cols, left.h)
        y0 = left.y + max(0, (left.h - total) * 0.25)
        pos = [(left.x + (i % cols) * (col_w + u), y0 + (i // cols) * (rh + gap)) if grid else (left.x, y0 + i * (rh + gap))
               for i in range(len(choices))]
        self.cd_style = "bar" if v == 2 else "ring"
        if self.cd_style == "ring":
            ring = int(min(right.w * 0.42, max(total, S.h1 * 2.0), S.h1 * 2.6))
            self.cd_box = Box(right.x, int(y0 + max(0, (total - ring) / 2)), right.w, ring)
        else:
            self.cd_box = Box(right.x, int(y0), right.w, int(S.h2 * 1.25))
        exp = self._explain(right.w, area.b - y0, probe=False)
        self._place_choices(rows, pos, grid=grid, collapse_to=None)
        self.add(exp, right.x, y0, show=self.s_exp, enter="left", delay=0.1, dur=0.5, z=3)

    def _question(self, v: int, box: Box, ring: int) -> int:
        S, pal, u = self.g.s, self.pal, self.g.unit
        q = str(self.p["question"])
        maxh = box.h * (0.36 if self.g.portrait else 0.34)
        q_w = box.w - (ring + u * 1.5 if ring else 0) - int(S.h1 * 1.2)
        qs = S.h1 if D.measure_lines(self.fonts, q, "heavy", S.h1, q_w) == 1 else S.h2
        if v == 0:
            qb_size = int(qs * 1.1)
            qbadge = badge(self, "Q", qb_size, fill=pal.primary, color=pal.on_primary)
            tb = self.text(q, role="heavy", size=qs, min_size=S.body_min, max_w=box.w - qb_size - u, max_h=maxh, lh=1.3,
                           emph=pal.primary, balance=True)
            h = max(qb_size, tb.h)
            self.add(D.plain(qbadge), box.x, box.y + (h - qb_size) / 2 if tb.h < qb_size * 1.4 else box.y + u * 0.2,
                     show=self.s_q, enter="pop", dur=0.4)
            self.add(tb, box.x + qb_size + u, box.y + (h - tb.h) / 2, show=self.s_q, enter="left", delay=0.1)
            return int(h)
        if v == 1:
            pad = int(u * 1.2)
            w = box.w - (ring + u * 1.5 if ring else 0)
            lab = pill(self, "문제", size=int(S.small * 0.9), fill=pal.primary, color=pal.on_primary)
            ov = lab.height // 2
            tb = self.text(q, role="heavy", size=qs, min_size=S.body_min, max_w=w - 2 * pad, max_h=maxh - ov - pad * 2,
                           lh=1.3, emph=pal.primary, balance=True)
            ph = int(tb.h + pad * 2 + ov * 0.5)
            self.add(card(w, ph, fill=pal.primary_soft, radius=u, shadow=False), box.x, box.y + ov, show=self.s_q, enter="fade", dur=0.3)
            self.add(D.plain(lab), box.x + pad, box.y, show=self.s_q, enter="pop", delay=0.05)
            self.add(tb, box.x + pad, box.y + ov + pad + ov * 0.5, show=self.s_q, enter="rise", delay=0.12)
            return ov + ph
        pad = int(u * 1.3)
        tb = self.text(q, role="heavy", size=qs, min_size=S.body_min, max_w=box.w - 2 * pad, max_h=maxh - 2 * pad, lh=1.3,
                       color=pal.on_primary, emph=pal.accent, balance=True)
        h = int(tb.h + 2 * pad)
        self.add(card(box.w, h, fill=pal.primary, radius=u, shadow_alpha=0.2), box.x, box.y, show=self.s_q, enter="wipe", dur=0.45)
        self.add(tb, box.x + pad, box.y + pad, show=self.s_q, enter="fade", delay=0.2)
        return h

    def _choice(self, v: int, i: int, text: str, w: int, sz: int) -> tuple[D.Sprite, D.Sprite]:
        """보기 한 줄의 (보통, 정답 강조) 스프라이트."""
        pal = self.pal
        bs = int(sz * 1.15)
        padx, pady, gap = int(sz * 0.42), int(sz * 0.34), int(sz * 0.45)
        tw = w - padx * 2 - bs - gap - int(sz * 1.2)
        tb = D.text_block(self.fonts, text, size=sz, max_w=tw, max_lines=3, color=pal.ink, emph=pal.primary)
        tb_c = D.text_block(self.fonts, text, role="bold", size=sz, max_w=tw, max_lines=3, color=pal.ink, emph=pal.primary)
        h = int(max(bs, tb.h) + 2 * pady)
        r = h // 2 if v == 2 else int(sz * 0.3)
        label = str(i + 1)

        def mk(correct: bool) -> D.Sprite:
            if correct:
                fill, border, bw = D.mix(pal.correct, D.WHITE, 0.86), pal.correct, 4
            else:
                fill = pal.surface
                border, bw = (pal.line, 2)
            im = D.rounded(w, h, r, fill, outline=border, width=bw)
            if correct:
                b = badge(self, label, bs, fill=pal.correct, color=D.WHITE)
            elif v == 1:
                b = D.rounded(bs, bs, int(bs * 0.28), pal.primary)
                lt = D.text_block(self.fonts, label, role="heavy", size=int(bs * 0.52), color=pal.on_primary, max_w=bs, max_lines=1)
                D.paste(b, lt.img, (bs - lt.w) / 2 - lt.ox, (bs - lt.h) / 2 - lt.oy)
            else:
                b = badge(self, label, bs, fill=None, outline=pal.primary, width=max(3, bs // 16), color=pal.primary)
            D.paste(im, b, padx, (h - bs) / 2)
            t = tb_c if correct else tb
            D.paste(im, t.img, padx + bs + gap - t.ox, (h - t.h) / 2 - t.oy)
            if correct:
                ic = int(sz * 1.0)
                D.paste(im, D.icon_check(ic, pal.correct, width=ic * 0.13), w - padx - ic, (h - ic) / 2)
            sp = D.shadowed(im, blur=12, offset=(0, 5), alpha=0.18 if correct else 0.07)
            sp.meta = {"badge": bs, "badge_xy": (padx, (h - bs) / 2), "broke": tb.broke, "overflow": tb.overflow}
            return sp

        return mk(False), mk(True)

    def _explain(self, w: int, avail: float, *, probe: bool) -> D.Sprite | None:
        """해설 상자('해설' 딱지는 위 테두리에 걸침). probe=True 면 기본 크기로 들어갈 때만 돌려준다(아니면 None)."""
        S, pal = self.g.s, self.pal
        lab = pill(self, "해설", size=int(S.small * 0.9), fill=pal.primary, color=pal.on_primary)
        ov = lab.height // 2
        basis = str(self.p.get("basis") or "")
        exp = str(self.p["explanation"])
        min_sz = S.body if probe else S.body_min
        sz = S.body

        def measure(sz: int, max_h: float | None = None):
            pad = int(sz * 0.5)
            tb = D.text_block(self.fonts, exp, size=sz, max_w=w - 2 * pad, color=pal.ink, emph=pal.primary, lh=1.32,
                              max_h=max_h, min_size=sz)
            bb = D.text_block(self.fonts, f"근거 · {basis}", size=max(S.small, int(sz * 0.8)), max_w=w - 2 * pad,
                              color=pal.ink2, max_lines=2) if basis else None
            card_h = ov + pad * 0.6 + tb.h + ((sz * 0.45 + 2 + sz * 0.3 + bb.h) if bb else 0) + pad
            return pad, tb, bb, card_h

        while True:
            pad, tb, bb, card_h = measure(sz)
            if ov + card_h <= avail or sz <= min_sz:
                break
            sz -= 2
        if ov + card_h > avail:
            if probe:
                return None
            fixed = ov + ov + pad * 0.6 + pad + ((sz * 0.45 + 2 + sz * 0.3 + bb.h) if bb else 0)
            pad, tb, bb, card_h = measure(sz, max_h=max(sz * 1.32, avail - fixed))
            self.warn("해설이 길어 화면에서 잘렸습니다 — 해설을 짧게 쓰고 자세한 설명은 내레이션으로 하세요.")
        H = int(card_h)
        im = D.blank(w, H + ov)
        D.paste(im, D.rounded(w, H, int(sz * 0.3), pal.surface, outline=pal.line, width=2), 0, ov)
        D.paste(im, D.rounded(max(8, sz // 7), int(H - ov - pad), 3, pal.accent), pad * 0.35, ov + ov + pad * 0.2)
        D.paste(im, lab, pad, 0)
        y = ov + ov + pad * 0.6
        D.paste(im, tb.img, pad - tb.ox, y - tb.oy)
        y += tb.h
        if bb:
            y += sz * 0.45
            D.paste(im, D.rounded(w - 2 * pad, 2, 1, pal.line), pad, y)
            y += 2 + sz * 0.3
            D.paste(im, bb.img, pad - bb.ox, y - bb.oy)
        return D.shadowed(im, blur=16, offset=(0, 7), alpha=0.14)

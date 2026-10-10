"""자막: 문장 → 2줄 이내 조각(글자 수 비례로 시간 배분), SRT 파일, 화면에 굽는 자막 그림.

- 조각은 화면 자막 글꼴로 실제 픽셀 폭을 재서 나눈다. 쉼표·마침표 뒤에서 끊는 것을 우선한다.
- 쇼츠: 아래 안전영역 바로 위, 2줄 이내. 롱폼: 화면 하단 2줄 이내 + subtitles.srt(업로드용).
- 화면 자막은 어두운 반투명 상자 위 흰 글자(강조 **낱말**은 accent 색).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from studio.video import design as D
from studio.video.design import Fonts, Geometry, Palette

SUB_ROLE = "bold"
MAX_LINES = 2
PREFER_BREAK = re.compile(r"[,.?!…·:;]$|(다|요|고|며|면|서|데|지만|는데|니까)$")


@dataclass
class Cue:
    start: float
    end: float
    text: str            # 줄바꿈(\n) 포함, 강조 표시(**) 유지
    scene: str = ""
    line: int | None = None


def _lines(text: str, fonts: Fonts, size: int, max_w: int) -> list[str]:
    plain, _ = D.parse_emphasis(text)
    font = fonts.get(SUB_ROLE, size)
    return [plain[s:e] for s, e in D.wrap_spans(plain, font, max_w)]


def chunk_sentence(text: str, fonts: Fonts, size: int, max_w: int, max_lines: int = MAX_LINES) -> list[str]:
    """문장을 max_lines 줄 이내 조각들로. 강조 표시(**)는 조각 안에서 짝이 맞게 유지한다."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(_lines(text, fonts, size, max_w)) <= max_lines:
        return [text]
    words = text.split(" ")
    total = len(_lines(text, fonts, size, max_w))
    n = max(2, math.ceil(total / max_lines))
    while n <= len(words):
        chunks = _split_balanced(words, n)
        if all(len(_lines(c, fonts, size, max_w)) <= max_lines for c in chunks):
            return [_fix_emph(c) for c in _carry_emph(chunks)]
        n += 1
    return [_fix_emph(w) for w in _carry_emph(words)]


def _split_balanced(words: list[str], n: int) -> list[str]:
    lens = [len(w) + 1 for w in words]
    total = sum(lens)
    out, cur, acc, k = [], [], 0, 1
    for i, w in enumerate(words):
        cur.append(w)
        acc += lens[i]
        target = total * k / n
        remaining_words = len(words) - i - 1
        if k < n and remaining_words >= (n - k):
            near = acc >= target * 0.82
            good = PREFER_BREAK.search(w.replace("**", "")) is not None
            if (near and good) or acc >= target * 1.12:
                out.append(" ".join(cur))
                cur, k = [], k + 1
    if cur:
        out.append(" ".join(cur))
    return out


def _carry_emph(chunks: list[str]) -> list[str]:
    """조각 경계에서 끊긴 ** 짝 맞추기."""
    out, open_ = [], False
    for c in chunks:
        if open_:
            c = "**" + c
        open_ = c.count("**") % 2 == 1
        if open_:
            c = c + "**"
        out.append(c)
    return out


def _fix_emph(c: str) -> str:
    return c.replace("****", "")


def wrap_cue(text: str, fonts: Fonts, size: int, max_w: int) -> str:
    """조각을 화면 줄바꿈 그대로 \n 으로 이은 문자열(강조 표시 없음) — SRT 용."""
    return "\n".join(_lines(text, fonts, size, max_w))


def weight(text: str) -> int:
    return max(1, len(re.sub(r"\s|\*", "", text)))


def sentence_cues(text: str, start: float, speech_end: float, beat_end: float, fonts: Fonts, size: int, max_w: int,
                  scene: str = "", line: int | None = None) -> list[Cue]:
    """문장 하나의 자막 조각과 시간. 마지막 조각은 문장 사이 쉼까지 유지(깜빡임 방지)."""
    chunks = chunk_sentence(text, fonts, size, max_w)
    if not chunks:
        return []
    ws = [weight(c) for c in chunks]
    dur = max(0.01, speech_end - start)
    out, t = [], start
    for i, (c, w) in enumerate(zip(chunks, ws)):
        e = t + dur * w / sum(ws)
        out.append(Cue(t, beat_end if i == len(chunks) - 1 else e, c, scene, line))
        t = e
    return out


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], path: Path, fonts: Fonts, size: int, max_w: int) -> Path:
    blocks = []
    for i, c in enumerate(cues, 1):
        blocks.append(f"{i}\n{srt_time(c.start)} --> {srt_time(c.end)}\n{wrap_cue(c.text, fonts, size, max_w)}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


class CaptionPainter:
    """화면 자막 그림(조각마다 한 번 그려 재사용)."""

    def __init__(self, geo: Geometry, pal: Palette, fonts: Fonts):
        self.g, self.pal, self.fonts = geo, pal, fonts
        self.size = geo.s.sub
        self.max_w = int(geo.sub.w * (0.92 if geo.portrait else 0.86))
        self._cache: dict[str, tuple[Image.Image, int, int]] = {}

    def sprite(self, text: str) -> tuple[Image.Image, int, int]:
        hit = self._cache.get(text)
        if hit:
            return hit
        g, pal = self.g, self.pal
        tb = D.text_block(self.fonts, text, role=SUB_ROLE, size=self.size, color=D.WHITE, max_w=self.max_w, max_lines=MAX_LINES,
                          align="center", lh=1.3, emph=pal.accent)
        padx, pady = int(self.size * 0.62), int(self.size * 0.30)
        w, h = tb.w + 2 * padx, int(tb.h + 2 * pady)
        box = D.rounded(w, h, int(self.size * 0.32), D.rgba(D.mix(pal.ink, (0, 0, 0), 0.35), 0.80))
        D.paste(box, tb.img, padx - tb.ox, pady - tb.oy)
        x = g.sub.cx - w // 2
        y = g.sub.b - h
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[text] = (box, x, y)
        return self._cache[text]

    def paint(self, canvas: Image.Image, text: str) -> None:
        img, x, y = self.sprite(text)
        D.paste(canvas, img, x, y)

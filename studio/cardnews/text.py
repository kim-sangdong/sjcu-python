"""글자 측정, 한국어 어절 단위 줄바꿈, 글자 크기 맞춤 (Pillow).

- 줄바꿈은 공백(어절) 단위로 하고 픽셀 폭을 실제로 잰다.
- 상자에 넘치면 글자 크기를 한 단계씩 줄인다. 가장 작은 크기에서도 넘치면 TextOverflow.
- 텍스트 안의 **강조** 는 강조 글꼴·색으로 그린다. 줄바꿈(\\n)은 문단 나눔이다.
- scale 은 슈퍼샘플링 배율이다. 모든 입력·출력 치수는 논리 픽셀(최종 이미지 기준)이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

from PIL import ImageDraw, ImageFont

from studio.config import font_path


class TextOverflow(ValueError):
    """가장 작은 글자 크기에서도 상자에 들어가지 않는다. suggest_chars 는 들어갈 만한 글자 수 추정치."""

    def __init__(self, message: str, *, chars: int = 0, suggest_chars: int = 0):
        super().__init__(message)
        self.chars = chars
        self.suggest_chars = suggest_chars


@lru_cache(maxsize=512)
def load_font(name: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(name)), max(1, px))


def sizes(start: int, stop: int, step: int = 2) -> list[int]:
    """start 부터 stop 까지 줄여 가는 글자 크기 목록(stop 포함)."""
    out = list(range(start, stop, -abs(step)))
    if not out or out[-1] != stop:
        out.append(stop)
    return out


# ---------------------------------------------------------------- 지원하지 않는 글자

# 나눔고딕에 없는 자주 쓰는 이모지·기호 → 있는 기호로 바꾼다.
REPLACE = {
    "✅": "√", "✔": "√", "✓": "√", "☑": "√", "👉": "→", "➡": "→", "▸": "›", "⬆": "↑", "⬇": "↓",
    "❌": "×", "✖": "×", "✕": "×", "⭕": "○", "❗": "!", "❓": "?", "💡": "※", "📌": "※", "⚠": "※",
}
_INVISIBLE = {"\ufe0f", "\ufe0e", "\u200d", "\u200b"}   # 이모지 변형 선택자·결합자·폭 없는 공백
NBSP = "\u00a0"   # 줄바꿈 금지 공백(예: "프로필\u00a0링크")


@lru_cache(maxsize=16)
def _cmap(name: str) -> frozenset[int] | None:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:  # fonttools 가 없으면 검사를 건너뛴다
        return None
    return frozenset(TTFont(str(font_path(name)), lazy=True).getBestCmap())


def sanitize(text: str, fonts: Sequence[str]) -> tuple[str, list[str]]:
    """글꼴에 없는 글자를 바꾸거나 뺀다. (정리된 글, 뺀 글자 목록)."""
    maps = [m for m in (_cmap(f) for f in fonts) if m is not None]
    out: list[str] = []
    removed: list[str] = []
    for ch in text:
        if ch in _INVISIBLE:
            continue
        ch = REPLACE.get(ch, ch)
        if ch.isspace() or ch == NBSP or all(ord(ch) in m for m in maps):
            out.append(ch)
        else:
            removed.append(ch)
    clean = "".join(out)
    if removed:
        clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean, removed


# ---------------------------------------------------------------- 서식 조각

@dataclass(frozen=True)
class Run:
    text: str
    emph: bool = False


Word = tuple[Run, ...]


def plain(text: str) -> str:
    """강조 표시(**)를 뺀 글."""
    return text.replace("**", "")


def _runs(chars: list[tuple[str, bool]]) -> Word:
    runs: list[Run] = []
    for ch, emph in chars:
        if runs and runs[-1].emph == emph:
            runs[-1] = Run(runs[-1].text + ch, emph)
        else:
            runs.append(Run(ch, emph))
    return tuple(runs)


KEEP_EMPH_MAX = 14   # keep_emph 일 때 한 줄로 묶는 강조 구절의 최대 글자 수


def _words(line: str, keep_emph: bool = False) -> list[Word]:
    parts = line.split("**")
    if len(parts) % 2 == 0:  # 짝이 안 맞는 ** → 마지막 표시는 글자 그대로 둔다
        parts = parts[:-2] + [parts[-2] + "**" + parts[-1]]
    words: list[Word] = []
    cur: list[tuple[str, bool]] = []
    for i, part in enumerate(parts):
        if keep_emph and i % 2 == 1 and len(part.strip()) <= KEEP_EMPH_MAX:
            core = part.strip()  # 짧은 강조 구절은 줄에서 갈라지지 않게(앞뒤 공백은 그대로)
            part = part.replace(core, re.sub(r"\s+", NBSP, core), 1) if core else part
        for ch in part:
            if ch == NBSP:  # 줄을 나누지 않는 공백: 어절 안의 보통 공백으로 그린다
                cur.append((" ", i % 2 == 1))
            elif ch.isspace():
                if cur:
                    words.append(_runs(cur))
                    cur = []
            else:
                cur.append((ch, i % 2 == 1))
    if cur:
        words.append(_runs(cur))
    return words


def parse_rich(text: str, keep_emph: bool = False) -> list[list[Word]]:
    """글 → 문단 목록(문단 = 어절 목록, 어절 = 서식 조각 목록). 빈 줄은 버린다.

    keep_emph=True 면 짧은 **강조 구절** 을 한 덩어리로 묶는다(제목용).
    """
    paras = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        words = _words(raw.strip(), keep_emph)
        if words:
            paras.append(words)
    return paras


# ---------------------------------------------------------------- 줄바꿈

@dataclass
class Line:
    words: list[Word]
    width: float
    para_start: bool


@dataclass
class TextBlock:
    """맞춰 놓은 글 덩어리. 치수는 논리 픽셀."""

    lines: list[Line]
    size: int
    font: str
    emph_font: str
    line_height: float
    para_gap: float
    scale: float

    @property
    def height(self) -> float:
        if not self.lines:
            return 0.0
        gaps = sum(1 for ln in self.lines[1:] if ln.para_start)
        return len(self.lines) * self.line_height + gaps * self.para_gap

    @property
    def width(self) -> float:
        return max((ln.width for ln in self.lines), default=0.0)

    def draw(
        self,
        draw: ImageDraw.ImageDraw,
        x: float,
        y: float,
        *,
        width: float,
        color,
        emph_color=None,
        align: str = "left",
    ) -> float:
        """(x, y)를 왼쪽 위로 하는 폭 width 상자 안에 그린다. 그린 덩어리의 아래쪽 y 를 돌려준다."""
        s = self.scale
        reg = load_font(self.font, round(self.size * s))
        emf = load_font(self.emph_font, round(self.size * s))
        asc, desc = reg.getmetrics()
        # 줄 높이 안에서 글자 상자(ascent+descent)를 세로 가운데에 둔다.
        base_off = (self.line_height * s - (asc + desc)) / 2 + asc
        space = reg.getlength(" ")
        cy = y * s
        for i, ln in enumerate(self.lines):
            if i and ln.para_start:
                cy += self.para_gap * s
            if align == "center":
                cx = (x + (width - ln.width) / 2) * s
            elif align == "right":
                cx = (x + width - ln.width) * s
            else:
                cx = x * s
            for wi, word in enumerate(ln.words):
                if wi:
                    cx += space
                for run in word:
                    f = emf if run.emph else reg
                    fill = (emph_color or color) if run.emph else color
                    draw.text((cx, cy + base_off), run.text, font=f, fill=fill, anchor="ls")
                    cx += f.getlength(run.text)
            cy += self.line_height * s
        return cy / s


class _Measure:
    def __init__(self, font: str, emph_font: str, size: int, scale: float):
        self.s = scale
        self.reg = load_font(font, round(size * scale))
        self.emf = load_font(emph_font, round(size * scale))
        self.space = self.reg.getlength(" ") / scale

    def run(self, r: Run) -> float:
        return (self.emf if r.emph else self.reg).getlength(r.text) / self.s

    def word(self, w: Word) -> float:
        return sum(self.run(r) for r in w)


def _split_long(word: Word, m: _Measure, max_w: float) -> list[Word]:
    """한 어절이 폭보다 길면 글자 단위로 자른다(가장 작은 크기에서만 쓴다)."""
    pieces: list[Word] = []
    cur: list[tuple[str, bool]] = []
    for r in word:
        for ch in r.text:
            trial = _runs(cur + [(ch, r.emph)])
            if cur and m.word(trial) > max_w:
                pieces.append(_runs(cur))
                cur = [(ch, r.emph)]
            else:
                cur.append((ch, r.emph))
    if cur:
        pieces.append(_runs(cur))
    return pieces


def _wrap_para(words: list[Word], m: _Measure, max_w: float, break_long: bool) -> list[Line] | None:
    lines: list[Line] = []
    cur: list[Word] = []
    cur_w = 0.0
    for word in words:
        ww = m.word(word)
        if ww > max_w + 0.01:
            if not break_long:
                return None
            if cur:
                lines.append(Line(cur, cur_w, False))
            pieces = _split_long(word, m, max_w)
            lines += [Line([p], m.word(p), False) for p in pieces[:-1]]
            cur, cur_w = [pieces[-1]], m.word(pieces[-1])
            continue
        if not cur:
            cur, cur_w = [word], ww
        elif cur_w + m.space + ww <= max_w + 0.01:
            cur.append(word)
            cur_w += m.space + ww
        else:
            lines.append(Line(cur, cur_w, False))
            cur, cur_w = [word], ww
    if cur:
        lines.append(Line(cur, cur_w, False))
    if lines:
        lines[0].para_start = True
    return lines


def _balanced(words: list[Word], m: _Measure, max_w: float, break_long: bool) -> list[Line] | None:
    """줄 수는 그대로 두고 줄 길이를 고르게(제목용). 마지막 줄에 한 어절만 남는 일을 줄인다."""
    base = _wrap_para(words, m, max_w, break_long)
    if base is None or len(base) < 2:
        return base
    n = len(base)
    lo = max(max(m.word(w) for w in words), max_w * 0.35)
    hi = max_w
    best = base
    for _ in range(14):
        mid = (lo + hi) / 2
        trial = _wrap_para(words, m, mid, False)
        if trial is not None and len(trial) <= n:
            best, hi = trial, mid
        else:
            lo = mid
    return best


def fit_text(
    text: str,
    *,
    font: str,
    emph_font: str | None = None,
    sizes: Sequence[int],
    max_width: float,
    max_height: float,
    line_spacing: float = 1.4,
    para_spacing: float = 0.45,
    max_lines: int | None = None,
    balance: bool = False,
    keep_emph: bool = False,
    scale: float = 1.0,
    label: str = "글",
) -> TextBlock:
    """가장 큰 글자 크기부터 넣어 보고, 상자(max_width × max_height)에 들어가는 첫 크기로 맞춘다."""
    emph_font = emph_font or font
    paras = parse_rich(text, keep_emph)
    if not paras:
        return TextBlock([], sizes[0], font, emph_font, sizes[0] * line_spacing, 0.0, scale)
    last: TextBlock | None = None
    for i, size in enumerate(sizes):
        m = _Measure(font, emph_font, size, scale)
        final = i == len(sizes) - 1
        lines: list[Line] = []
        for words in paras:
            part = (_balanced if balance else _wrap_para)(words, m, max_width, final)
            if part is None:
                lines = []
                break
            lines += part
        if not lines:
            continue
        block = TextBlock(lines, size, font, emph_font, size * line_spacing, size * para_spacing, scale)
        last = block
        if block.height <= max_height + 0.5 and (max_lines is None or len(lines) <= max_lines):
            return block
    chars = len(re.sub(r"\s", "", plain(text)))
    if last is None:
        raise TextOverflow(f"{label}: 띄어쓰기 없이 너무 긴 낱말이 있습니다.", chars=chars)
    ratio = max_height / last.height if last.height else 1.0
    if max_lines:
        ratio = min(ratio, max_lines / len(last.lines))
    suggest = max(1, int(chars * ratio * 0.9))
    limit = f"{max_lines}줄·" if max_lines else ""
    raise TextOverflow(
        f"{label}: 가장 작은 글자 크기({sizes[-1]}px)에서도 {len(last.lines)}줄로 상자({limit}높이 {max_height:.0f}px)를 넘습니다. "
        f"지금 {chars}자 → 약 {suggest}자 이하로 줄이세요.",
        chars=chars,
        suggest_chars=suggest,
    )


def truncate_to_fit(text: str, fit, *, ellipsis: str = "…") -> tuple[TextBlock, bool]:
    """fit(text) 가 TextOverflow 를 내면 끝 어절부터 줄이고 말줄임표를 붙인다(대표 이미지 제목 등). (덩어리, 잘렸는지)."""
    try:
        return fit(text), False
    except TextOverflow:
        pass
    words = plain(text).split()
    while len(words) > 1:
        words.pop()
        try:
            return fit(" ".join(words).rstrip(",.·:;") + ellipsis), True
        except TextOverflow:
            continue
    return fit(plain(text)[:12] + ellipsis), True

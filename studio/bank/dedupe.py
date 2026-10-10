"""중복 문항 탐지 — 정규화한 지문의 글자 3-gram 자카드 유사도.

정규화: NFKC → 소문자 → 공백·문장부호·괄호·따옴표·가운뎃점 제거(한글·영문·숫자만 남김).
유사도 ≥ 0.8(기본, channel.yaml bank.dup_threshold 로 조정)이면 같은 문항으로 보고 거부한다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

DEFAULT_THRESHOLD = 0.8
_KEEP_RE = re.compile(r"[^0-9a-z가-힣ㄱ-ㆎ一-鿿%]")


def normalize_stem(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return _KEEP_RE.sub("", t)


def trigrams(text: str) -> frozenset[str]:
    n = normalize_stem(text)
    if len(n) < 3:
        return frozenset([n]) if n else frozenset()
    return frozenset(n[i:i + 3] for i in range(len(n) - 2))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class DupHit:
    other_id: str
    score: float


class DupIndex:
    """기존 문항 지문을 들고 있다가 새 지문과 가장 비슷한 것을 찾는다."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self._grams: list[tuple[str, frozenset[str]]] = []

    def add(self, item_id: str, stem: str) -> None:
        self._grams.append((item_id, trigrams(stem)))

    def extend(self, pairs: Iterable[tuple[str, str]]) -> None:
        for i, s in pairs:
            self.add(i, s)

    def best(self, stem: str, *, exclude: str | None = None) -> DupHit | None:
        g = trigrams(stem)
        best: DupHit | None = None
        for other, og in self._grams:
            if other == exclude:
                continue
            s = jaccard(g, og)
            if best is None or s > best.score:
                best = DupHit(other, s)
        return best

    def duplicate_of(self, stem: str, *, exclude: str | None = None) -> DupHit | None:
        hit = self.best(stem, exclude=exclude)
        return hit if hit and hit.score >= self.threshold else None

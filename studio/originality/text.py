"""텍스트 지표 도구: 정규화, 문장 나누기, 문자 n-gram, MinHash, TF-IDF 코사인, 제목 규칙.

한국어는 형태소 분석 없이 공백·기호를 지운 문자 n-gram 으로 본다(띄어쓰기만 바꿔 피해 가지 못하게).
MinHash 는 외부 라이브러리 없이 직접 구현한다 — 아카이브에 저장한 서명이 라이브러리 판 올림으로
깨지지 않게(datasketch 2.0 은 기본 순열 방식이 바뀌어 저장값 복원이 깨진 전례가 있다, facts 3.2).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache

import numpy as np

SHINGLE_N = 5                # MinHash·Jaccard 용 문자 n-gram(한글 5글자 ≈ 두 어절)
TFIDF_NGRAMS = (3, 4, 5)     # TF-IDF 용 문자 n-gram 범위
NUM_PERM = 128               # 대본 MinHash 서명 길이(아카이브 저장 규격)
DESC_NUM_PERM = 64           # 설명 MinHash 서명 길이
MINHASH_SEED = "studio-originality-v1"   # 바꾸면 아카이브 서명과 호환이 깨진다 → 아카이브 version 을 올릴 것
_PRIME = np.uint64((1 << 61) - 1)
_MAX32 = 0xFFFFFFFF
EMPTY_SIG = -1               # 빈 텍스트 서명 표시

_EMPH = re.compile(r"\*\*|__")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_DROP = re.compile(r"[^0-9a-z가-힣ㄱ-ㅎㅏ-ㅣᄀ-ᇿ一-鿿]")
_SENT_SPLIT = re.compile(r"(?<=[.!?…。？！])\s+|\n+")
_WORD = re.compile(r"[0-9A-Za-z가-힣#]+")


def normalize(text: str, *, numbers: bool = False) -> str:
    """NFKC·소문자·강조표시 제거 후 글자·숫자만 남긴다(공백·기호 제거). numbers=True 면 숫자 덩어리를 0 하나로."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _EMPH.sub("", t).lower()
    if numbers:
        t = _NUM.sub("0", t)
    return _DROP.sub("", t)


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENT_SPLIT.split(text or "") if p and p.strip()]


def shingles(t: str, n: int = SHINGLE_N) -> set[str]:
    if not t:
        return set()
    if len(t) <= n:
        return {t}
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set, b: set) -> float:
    """a 가 b 에 들어 있는 비율 |a∩b|/|a|."""
    return len(a & b) / len(a) if a else 0.0


def sentence_hash(s: str) -> str:
    """정규화(숫자→0)한 문장의 짧은 해시(32비트 hex). 아카이브의 phrase_hashes 와 같은 규칙."""
    return hashlib.sha1(normalize(s, numbers=True).encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------- MinHash

def _h32(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=4).digest(), "little")


@lru_cache(maxsize=4)
def _perms(num_perm: int) -> tuple[np.ndarray, np.ndarray]:
    a, b = [], []
    for i in range(num_perm):
        d = hashlib.blake2b(f"{MINHASH_SEED}:{i}".encode(), digest_size=8).digest()
        a.append(int.from_bytes(d[:4], "little") | 1)
        b.append(int.from_bytes(d[4:], "little"))
    return np.array(a, dtype=np.uint64), np.array(b, dtype=np.uint64)


def minhash(sh: set[str], num_perm: int = NUM_PERM) -> list[int]:
    """(a·h + b) mod (2^61−1) 의 하위 32비트 최솟값. a, h < 2^32 라 곱이 uint64 를 넘지 않는다."""
    if not sh:
        return [EMPTY_SIG] * num_perm
    hv = np.fromiter((_h32(s) for s in sh), dtype=np.uint64, count=len(sh))
    a, b = _perms(num_perm)
    out = np.full(num_perm, _MAX32, dtype=np.uint64)
    for i in range(0, len(hv), 4096):
        chunk = hv[i:i + 4096, None]
        ph = ((chunk * a[None, :] + b[None, :]) % _PRIME) & np.uint64(_MAX32)
        out = np.minimum(out, ph.min(axis=0))
    return [int(x) for x in out]


def minhash_sim(a: list[int] | None, b: list[int] | None) -> float:
    """두 서명의 Jaccard 추정. 한쪽이 비었으면 0."""
    if not a or not b or len(a) != len(b) or a[0] == EMPTY_SIG or b[0] == EMPTY_SIG:
        return 0.0
    return float(np.mean(np.asarray(a, dtype=np.int64) == np.asarray(b, dtype=np.int64)))


# ---------------------------------------------------------------- TF-IDF

def _grams(t: str) -> Counter:
    c: Counter = Counter()
    for n in TFIDF_NGRAMS:
        if len(t) >= n:
            c.update(t[i:i + n] for i in range(len(t) - n + 1))
    return c


def tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    """문자 3~5-gram, sublinear tf, 평활 idf, L2 정규화(scikit-learn TfidfVectorizer 기본과 같은 식)."""
    counts = [_grams(t) for t in texts]
    df: Counter = Counter()
    for c in counts:
        df.update(c.keys())
    n = len(texts)
    out = []
    for c in counts:
        v = {g: (1.0 + math.log(tf)) * (math.log((1 + n) / (1 + df[g])) + 1.0) for g, tf in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({g: x / norm for g, x in v.items()})
    return out


def cosine(u: dict[str, float], v: dict[str, float]) -> float:
    if len(u) > len(v):
        u, v = v, u
    return sum(x * v.get(g, 0.0) for g, x in u.items())


# ---------------------------------------------------------------- 제목·태그

def title_grams(title: str) -> set[str]:
    return shingles(normalize(title, numbers=True), 2)


def title_head(title: str, words: int = 3) -> str:
    """제목 앞 n어절(괄호·기호 무시, 숫자→0, 소문자)."""
    toks = _WORD.findall(unicodedata.normalize("NFKC", title or ""))
    toks = [_NUM.sub("0", t.lower()) for t in toks if t.strip("#")]
    return " ".join(toks[:words])


SERIAL_PATTERNS = [
    re.compile(r"#\s*\d+"),                                             # #123
    re.compile(r"제\s*\d+\s*(?:탄|편|화|회|강|부|장)"),                     # 제3탄, 제12화
    re.compile(r"\d+\s*탄"),                                             # 3탄
    re.compile(r"(?i)\b(?:ep|episode|vol|part|season)\s*\.?\s*\d+"),     # EP.3, Part 2
    re.compile(r"(?:시즌|파트)\s*\d+"),
    re.compile(r"\d+\s*(?:편|화|회차|부)\s*(?=$|[\])】」:|,\-–—])"),        # 끝에 오는 3편·12화
    re.compile(r"[(\[【]\s*\d+\s*(?:/\s*\d+)?\s*[)\]】]\s*$"),             # 끝의 (3), [2/5]
    re.compile(r"\d+\s*번째\s*(?:이야기|시간|영상|퀴즈|문제|강의|수업|편)"),
]


def serial_marker(title: str) -> str | None:
    """연번 표현을 찾으면 그 부분을 돌려준다."""
    t = unicodedata.normalize("NFKC", title or "").strip()
    for p in SERIAL_PATTERNS:
        m = p.search(t)
        if m:
            return m.group(0).strip()
    return None


def tag_key(tag: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", tag or "")).lstrip("#").lower()


def tag_set(tags: list[str]) -> frozenset[str]:
    return frozenset(k for k in (tag_key(t) for t in tags) if k)


_URL = re.compile(r"https?://\S+|www\.\S+")
_HASHTAG = re.compile(r"#[^\s#]+")


def description_lines(text: str) -> list[str]:
    """설명란을 줄 단위로(링크·해시태그 제거, 빈 줄 제외)."""
    out = []
    for line in (text or "").splitlines():
        line = _HASHTAG.sub(" ", _URL.sub(" ", line)).strip()
        if normalize(line):
            out.append(line)
    return out


# ---------------------------------------------------------------- 한국어 조사

_DIGIT_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True, "8": True, "9": False}


def _has_final(word: str) -> bool:
    w = (word or "").rstrip(" )]'\"")
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return _DIGIT_FINAL[ch]
    return ch.lower() in "lmnr"   # 엘·엠·엔·알 정도만 받침으로 읽는다


def josa(word: str, pair: str) -> str:
    """josa('S04', '와/과') → 'S04와'. pair: 와/과, 은/는, 이/가, 을/를."""
    with_final, without = {"와/과": ("과", "와"), "은/는": ("은", "는"), "이/가": ("이", "가"), "을/를": ("을", "를")}[pair]
    return word + (with_final if _has_final(word) else without)

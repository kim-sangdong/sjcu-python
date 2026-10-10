"""원문 대조용 정규화 — 인용문 비교와 숫자 추출.

인용문 비교(normalize_quote)
  1) 편집 주석 제거: <개정 2023.8.8>, <신설 …>, [본조신설 …], [전문개정 …] 등
  2) 한자 병기 괄호 제거: 등기(登記) → 등기
  3) 항 번호 동그라미 숫자(①…) 제거
  4) NFKC(전각→반각, ㎡→m2) → 소문자
  5) 공백·괄호·따옴표·가운뎃점(·ㆍ・)·쉼표·마침표·쌍점·물결표 제거
  → 인용문이 원문(같은 정규화)의 부분 문자열이면 일치.

숫자 추출(extract_numbers) — (값, 종류) 쌍으로 바꿔 단위 표기가 달라도 비교한다.
  비율   : 60%, 60퍼센트, 100분의 60, 1천분의 40, 2분의 1, 삼분의 이        → ratio 0.6 / 0.04 / 0.5 …
  기간   : 3년, 6개월 = 6월(옛 법령 표기), 2주, 30일, 삼십일(=30일), 24시간    → year / month / week / day / hour
  월일   : 6월 1일                                                         → md 601
  금액   : 3억원, 1억5천만원, 20만원, 1,000원                               → money
  면적·길이: 330제곱미터 = 330㎡ = 330m², 4미터, 1킬로미터                    → area / length
  수량   : 2인, 3명, 300세대·가구·호, 5층, 2회, 1.5배, 19세, 2필지, 3개       → person / household / floor / times / multiple / age / parcel / count
  무시   : 조·항·호·목 번호(제33조 제1항, 제3자, 제1종), 연도(2026년), 날짜(2026.1.1), 회차(37회 시험)
  한글 숫자는 두 글자 이상(삼십, 이십일 …)일 때만 읽는다 — '이월'·'일부' 같은 낱말 오인을 막기 위해.
  '삼십일'처럼 끝이 '일'이고 뒤에 단위가 없으면 30일(일수)로 읽는다. 31일은 '삼십일일'.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

# ---------------------------------------------------------------- 인용문 정규화

CIRCLED: dict[str, int] = {chr(0x2460 + i): i + 1 for i in range(20)}          # ①~⑳
CIRCLED.update({chr(0x3251 + i): 21 + i for i in range(15)})                   # ㉑~㉟
CIRCLED.update({chr(0x32B1 + i): 36 + i for i in range(15)})                   # ㊱~㊿

EDITORIAL_RE = re.compile(
    r"<\s*(?:개정|신설|삭제|본조신설|전문개정|제목개정|종전|타법개정)[^<>]{0,200}>"
    r"|\[\s*(?:본조신설|전문개정|제목개정|본조제목개정|종전|시행일|제목개정|본조개정|한시조항|유효기간)[^\[\]]{0,300}\]"
)
_HAN = r"㐀-䶿一-鿿豈-﫿"
HANJA_PAREN_RE = re.compile(rf"[(（]\s*[{_HAN}][{_HAN}\s·ㆍ,、]*[)）]")
# 가운뎃점은 NFKC 뒤 모양까지 넣는다: 'ㆍ'(U+318D, 법령 본문에서 흔함) → 'ᆞ'(U+119E)
_DROP = set(" \t\r\n 　()[]{}<>「」『』〈〉《》【】〔〕\"'“”‘’`·ㆍ\u119e・‧∙•,，.。:;!?~～")


def strip_editorial(text: str) -> str:
    return EDITORIAL_RE.sub(" ", text or "")


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    s = text or ""
    removed = bytearray(len(s))
    for rx in (EDITORIAL_RE, HANJA_PAREN_RE):
        for m in rx.finditer(s):
            removed[m.start():m.end()] = b"\x01" * (m.end() - m.start())
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        if removed[i] or ch in CIRCLED:
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace() or c in _DROP:
                continue
            out.append(c.lower())
            idx.append(i)
    return "".join(out), idx


def normalize_quote(text: str) -> str:
    return _norm_with_map(text)[0]


@dataclass(frozen=True)
class QuoteMatch:
    found: bool
    score: float            # 1.0 = 정확히 포함, 아니면 가장 비슷한 구간의 유사도(0~1)
    snippet: str = ""       # 원문에서 가장 비슷한 구간(원래 표기)


def find_quote(quote: str, source: str) -> QuoteMatch:
    nq = normalize_quote(quote)
    ns, idx = _norm_with_map(source)
    if not nq:
        return QuoteMatch(False, 0.0, "")
    pos = ns.find(nq)
    if pos >= 0:
        return QuoteMatch(True, 1.0, source[idx[pos]: idx[pos + len(nq) - 1] + 1])
    if not ns:
        return QuoteMatch(False, 0.0, "")
    L = len(nq)
    if len(ns) <= L:
        return QuoteMatch(False, round(SequenceMatcher(None, nq, ns).ratio(), 3), source.strip()[:300])
    step = max(1, L // 4)
    best_i, best = 0, -1.0
    starts = list(range(0, len(ns) - L + 1, step))
    if starts[-1] != len(ns) - L:
        starts.append(len(ns) - L)
    for i in starts:
        r = SequenceMatcher(None, nq, ns[i:i + L], autojunk=False).ratio()
        if r > best:
            best_i, best = i, r
    lo, hi = max(0, best_i - step), min(len(ns) - L, best_i + step)
    for i in range(lo, hi + 1):
        r = SequenceMatcher(None, nq, ns[i:i + L], autojunk=False).ratio()
        if r > best:
            best_i, best = i, r
    snippet = source[idx[best_i]: idx[min(best_i + L, len(idx)) - 1] + 1]
    return QuoteMatch(False, round(best, 3), snippet)


# ---------------------------------------------------------------- 한글·혼합 숫자

KO_DIGITS = {"영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
SMALL = {"십": 10, "백": 100, "천": 1000}
BIG = {"만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}
_KO_CHARS = "".join(KO_DIGITS) + "".join(SMALL) + "".join(BIG)


def parse_number(token: str) -> float | None:
    """'1천500' → 1500, '3억5천만' → 350000000, '삼십' → 30, '1,000' → 1000, '0.4' → 0.4. 못 읽으면 None."""
    t = token.replace(",", "").replace(" ", "")
    if not t:
        return None
    parts = re.findall(r"\d+(?:\.\d+)?|[" + _KO_CHARS + r"]", t)
    if "".join(parts) != t:
        return None
    total = 0.0
    section = 0.0
    current: float | None = None
    prev_digit = False
    for p in parts:
        if p[0].isdigit() or p in KO_DIGITS:
            if prev_digit:            # '일이'·'12' 다음 숫자 같은 연속 숫자 → 이상한 표기
                return None
            current = float(p) if p[0].isdigit() else float(KO_DIGITS[p])
            prev_digit = True
        elif p in SMALL:
            section += (current if current is not None else 1.0) * SMALL[p]
            current, prev_digit = None, False
        elif p in BIG:
            total += (section + (current or 0.0) or 1.0) * BIG[p]
            section, current, prev_digit = 0.0, None, False
    return total + section + (current or 0.0)


# ---------------------------------------------------------------- 숫자 추출

@dataclass(frozen=True)
class Num:
    value: float
    kind: str
    raw: str
    start: int = 0
    end: int = 0

    def key(self) -> tuple[str, float]:
        return (self.kind, round(self.value, 9))

    def label(self) -> str:
        return self.raw.strip()


_AR = r"\d[\d,]*(?:\.\d+)?(?:[십백천만억조](?:\d[\d,]*(?:\.\d+)?)?)*(?:(?<=[억만조])\s(?=\d)\d[\d,]*(?:[십백천만](?:\d[\d,]*)?)*)?"
_KO = r"[" + _KO_CHARS + r"]{2,}"
_KO_ANY = r"[" + _KO_CHARS + r"]+"
NUM_TOKEN = re.compile(rf"(?P<ar>{_AR})|(?P<ko>(?<![가-힣]){_KO})")

IGNORE_RES = [
    re.compile(r"제\s*[\d" + _KO_CHARS + r"]+\s*(?:조(?:\s*의\s*\d+)?|항|호(?:\s*의\s*\d+)?|목|편|장|절|관|회|차|자|종|급)"),
    re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"),
    re.compile(r"\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\.?"),
    re.compile(r"\d{4}\s*년도?(?!\s*(?:간|이내|이상|이하|동안|마다))"),
    re.compile(r"\d+\s*회\s*(?:시험|기출|공인중개사|출제|차)"),
    re.compile(r"\d+\s*차\s*시험"),
]
MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_KO_TAIL = r"(?![가-힣])|(?=이상|이하|초과|미만|이내|을|를|의|에|로|으로|은|는|이|가|과|와|씩|만큼)"
FRAC_RE = re.compile(rf"(?P<a>{_AR}|(?<![가-힣]){_KO_ANY})\s*분\s*의\s*(?P<b>{_AR}|{_KO_ANY}(?:{_KO_TAIL}))")
PCT_RE = re.compile(rf"(?P<n>{_AR}|{_KO})\s*(?:%|퍼센트|프로(?![그젝]))")

# 단위: (정규식, 종류, 배수, 다음 글자로 오면 단위가 아닌 글자들)
UNITS: list[tuple[str, str, float, str]] = [
    ("개월", "month", 1, ""),
    ("제곱미터", "area", 1, ""), ("평방미터", "area", 1, ""), ("m2", "area", 1, ""), ("m²", "area", 1, ""),
    ("킬로미터", "length", 1000, ""), ("km", "length", 1000, ""), ("미터", "length", 1, ""),
    ("억원", "money", 1e8, ""), ("천만원", "money", 1e7, ""), ("백만원", "money", 1e6, ""),
    ("만원", "money", 1e4, ""), ("천원", "money", 1e3, ""), ("원", "money", 1, "칙인상가본리래형룸"),
    ("주일", "week", 1, ""), ("시간", "hour", 1, ""),
    ("세대", "household", 1, ""), ("가구", "household", 1, ""), ("필지", "parcel", 1, ""),
    ("개소", "count", 1, ""),
    ("년", "year", 1, ""), ("월", "month", 1, "세말초분"), ("주", "week", 1, "택식소민거권요주의체간"),
    ("일", "day", 1, "반부시정체치단"), ("명", "person", 1, "칭령단시"), ("인", "person", 1, "정가도상하계용수지"),
    ("층", "floor", 1, ""), ("회", "times", 1, "계사의원장"), ("배", "multiple", 1, "우당상제분"),
    ("세", "age", 1, "율금액법목무"), ("호", "household", 1, "의주실수"), ("개", "count", 1, "정발업설인별시선념"),
    ("m", "length", 1, ""),
]
_UNIT_RE = re.compile("|".join(re.escape(u) for u, *_ in UNITS))
_UNIT_INFO = {u: (k, mult, bad) for u, k, mult, bad in UNITS}


def _unit_at(t: str, pos: int) -> tuple[str, float, int] | None:
    """t[pos:] 가 단위로 시작하면 (종류, 배수, 끝 위치). 공백 1칸은 건너뛴다."""
    p = pos
    if p < len(t) and t[p] == " ":
        p += 1
    m = _UNIT_RE.match(t, p)
    if not m:
        return None
    u = m.group(0)
    kind, mult, bad = _UNIT_INFO[u]
    nxt = t[m.end()] if m.end() < len(t) else ""
    if nxt and nxt in bad:
        return None
    if u == "m" and nxt and (nxt.isascii() and nxt.isalpha()):
        return None
    if u == "월" and re.match(r"\s*\d{1,2}\s*일", t[m.end():]):   # '6월 1일' 은 월일(md)로 따로 읽는다
        return None
    return kind, mult, m.end()


def extract_numbers(text: str) -> list[Num]:
    t = unicodedata.normalize("NFKC", strip_editorial(text or ""))
    t = re.sub(r"[ \t ]+", " ", t)
    mask = bytearray(len(t))
    out: list[Num] = []

    def free(a: int, b: int) -> bool:
        return not any(mask[a:b])

    def take(a: int, b: int) -> None:
        mask[a:b] = b"\x01" * (b - a)

    for rx in IGNORE_RES:
        for m in rx.finditer(t):
            take(m.start(), m.end())

    for m in MD_RE.finditer(t):
        if free(m.start(), m.end()):
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.append(Num(mo * 100 + d, "md", m.group(0), m.start(), m.end()))
                take(m.start(), m.end())

    for m in FRAC_RE.finditer(t):
        if not free(m.start(), m.end()):
            continue
        a, b = parse_number(m.group("a")), parse_number(m.group("b"))
        if a and b is not None and a > 0:
            out.append(Num(b / a, "ratio", m.group(0), m.start(), m.end()))
            take(m.start(), m.end())

    for m in PCT_RE.finditer(t):
        if not free(m.start(), m.end()):
            continue
        n = parse_number(m.group("n"))
        if n is not None:
            out.append(Num(n / 100.0, "ratio", m.group(0), m.start(), m.end()))
            take(m.start(), m.end())

    for m in NUM_TOKEN.finditer(t):
        if not free(m.start(), m.end()):
            continue
        tok = m.group(0)
        if m.group("ar"):
            val = parse_number(tok)
            if val is None:
                continue
            u = _unit_at(t, m.end())
            if not u:
                continue
            kind, mult, end = u
            if kind == "year" and val >= 1900 and float(val).is_integer():
                continue      # 달력 연도
            out.append(Num(val * mult, kind, t[m.start():end], m.start(), end))
            take(m.start(), end)
        else:
            got = _korean_with_unit(t, m.start(), tok)
            if got:
                out.append(got)
                take(got.start, got.end)
    out.sort(key=lambda n: n.start)
    return out


def _korean_with_unit(t: str, start: int, tok: str) -> Num | None:
    """가장 긴 앞부분부터: (가) 앞부분 + 뒤 단위('이십년'), (나) '…일'로 끝나면 일수('삼십일' = 30일)."""
    for end in range(len(tok), 1, -1):
        pre = tok[:end]
        after = start + end
        u = _unit_at(t, after)
        if u:
            val = parse_number(pre)
            if val is not None:
                kind, mult, uend = u
                return Num(val * mult, kind, t[start:uend], start, uend)
        if pre.endswith("일"):
            val = parse_number(pre[:-1])
            if val is not None and val >= 2:
                return Num(val, "day", t[start:after], start, after)
    return None


def same(a: Num, b: Num) -> bool:
    if a.kind != b.kind:
        return False
    return math.isclose(a.value, b.value, rel_tol=1e-6, abs_tol=1e-9)


def contains(pool: Iterable[Num], n: Num) -> bool:
    return any(same(p, n) for p in pool)


def number_label(n: Num) -> str:
    return f"{n.label()}"

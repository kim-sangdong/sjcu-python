"""법령 본문 JSON(lawService.do target=law|eflaw, type=JSON) 해석과 조·항·호·목 추출.

응답 구조(실제 응답 표본 기준 — 공개 저장소의 저장본 여러 개로 확인, 공식 JSON 스키마 문서는 없음):

    {"법령": {
        "기본정보": {"법령ID": "001823", "법령명_한글": "…", "시행일자": "20260101", "공포일자": "…",
                    "법종구분": {"content": "법률", "법종구분코드": "A0002"}, …},
        "조문": {"조문단위": [                      # 한 건이면 리스트가 아니라 객체로 올 수 있다
            {"조문번호": "1", "조문여부": "전문", "조문내용": "제1장 총칙"},          # 장·절 제목(건너뜀)
            {"조문번호": "33", "조문가지번호": "2"?, "조문여부": "조문", "조문제목": "…",
             "조문시행일자": "20260101", "조문변경여부": "Y|N", "조문내용": "제33조(금지행위)",
             "항": [ {"항번호": "①", "항내용": "① …", "호": [ {"호번호": "1.", "호내용": "1. …",
                      "목": [ {"목번호": "가.", "목내용": "가. …"} ]} ]} ]}   # 항이 하나뿐이면 번호 없는 객체
        ]},
        "부칙": {…}, "별표": {"별표단위": [{"별표번호": "0001", "별표가지번호": "00", "별표제목": "…", "별표내용": [[…]]}]}
    }}

주의: '조문시행일자'는 그 조문이 마지막으로 바뀐 날이 아니라 이 법령 버전에서 그 조문의 시행일이다
(바뀌지 않은 조문도 법령 시행일과 같게 나온다). 조문 변경 감지는 본문 해시 비교로 한다(studio.bank.watch).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from studio.verify.normalize import CIRCLED, normalize_quote

# ---------------------------------------------------------------- 조문 위치 표기

ARTICLE_RE = re.compile(
    r"^\s*제\s*(?P<jo>\d+)\s*조(?:\s*의\s*(?P<jb>\d+))?"
    r"(?:\s*제\s*(?P<hang>\d+)\s*항)?"
    r"(?:\s*제\s*(?P<ho>\d+)\s*호(?:\s*의\s*(?P<hb>\d+))?)?"
    r"(?:\s*(?P<mok>[가-하])\s*목)?"
    r"(?:\s*(?P<part>본문|단서|전단|후단|각\s*호\s*외의\s*부분))?\s*$"
)
APPENDIX_RE = re.compile(r"^\s*\[?\s*별표\s*(?P<no>\d+)(?:\s*의\s*(?P<br>\d+))?\s*\]?\s*$")


class ArticleRefError(ValueError):
    pass


@dataclass(frozen=True)
class ArticleRef:
    jo: int = 0
    jo_branch: int = 0
    hang: int | None = None
    ho: int | None = None
    ho_branch: int = 0
    mok: str | None = None
    part: str | None = None
    appendix: int | None = None
    appendix_branch: int = 0

    @classmethod
    def parse(cls, text: str) -> "ArticleRef":
        t = (text or "").replace(" ", " ")
        m = ARTICLE_RE.match(t)
        if m:
            return cls(
                jo=int(m["jo"]), jo_branch=int(m["jb"] or 0),
                hang=int(m["hang"]) if m["hang"] else None,
                ho=int(m["ho"]) if m["ho"] else None, ho_branch=int(m["hb"] or 0),
                mok=m["mok"], part=re.sub(r"\s+", " ", m["part"]) if m["part"] else None,
            )
        m = APPENDIX_RE.match(t)
        if m:
            return cls(appendix=int(m["no"]), appendix_branch=int(m["br"] or 0))
        raise ArticleRefError(
            f"조문 표기를 읽지 못했습니다: {text!r} — 예) 제33조 제1항 제6호, 제10조의2 제1항, 제77조 제1항 제1호 가목, 별표 1")

    @property
    def is_appendix(self) -> bool:
        return self.appendix is not None

    def jo_code(self) -> str:
        """API 의 JO 파라미터(조번호 4자리 + 가지번호 2자리)."""
        return f"{self.jo:04d}{self.jo_branch:02d}"

    def article_label(self) -> str:
        return f"제{self.jo}조" + (f"의{self.jo_branch}" if self.jo_branch else "")

    def display(self) -> str:
        if self.is_appendix:
            return f"별표 {self.appendix}" + (f"의{self.appendix_branch}" if self.appendix_branch else "")
        s = self.article_label()
        if self.hang is not None:
            s += f" 제{self.hang}항"
        if self.ho is not None:
            s += f" 제{self.ho}호" + (f"의{self.ho_branch}" if self.ho_branch else "")
        if self.mok:
            s += f" {self.mok}목"
        if self.part:
            s += f" {self.part}"
        return s


# ---------------------------------------------------------------- 본문 구조

def flatten_text(v: Any) -> str:
    """항내용·별표내용처럼 문자열 또는 [[문자열…]] 로 오는 값을 한 문자열로."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        return flatten_text(v.get("content", ""))
    if isinstance(v, list):
        return "\n".join(t for t in (flatten_text(x) for x in v) if t)
    return str(v)


def as_list(v: Any) -> list[Any]:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def parse_ymd(v: Any) -> date | None:
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


@dataclass
class Unit:
    """항·호·목 한 단위. number 0 = 번호 없는 항(항이 하나뿐인 조문)."""

    level: str              # hang | ho | mok
    number: int
    branch: int
    label: str
    text: str
    children: list["Unit"] = field(default_factory=list)

    def full_text(self) -> str:
        parts = [self.text] + [c.full_text() for c in self.children]
        return "\n".join(p for p in parts if p)


@dataclass
class Article:
    jo: int
    branch: int
    title: str
    text: str                       # 조문내용(항이 있으면 보통 '제33조(금지행위)' 머리만)
    units: list[Unit]
    effective: date | None = None   # 조문시행일자
    changed: bool = False           # 조문변경여부 == Y (직전 버전 대비)
    note: str = ""                  # 조문참고자료([본조신설 …] 등)

    @property
    def label(self) -> str:
        return f"제{self.jo}조" + (f"의{self.branch}" if self.branch else "")

    def full_text(self) -> str:
        parts = [self.text] + [u.full_text() for u in self.units]
        return "\n".join(p for p in parts if p)

    def fingerprint(self) -> str:
        """편집 주석·공백·기호를 뺀 본문 해시(16자). 조문 내용이 바뀌었는지 비교할 때 쓴다."""
        return fingerprint_text(self.full_text())

    def is_deleted(self) -> bool:
        return bool(re.match(r"^\s*제\s*\d+\s*조(?:의\s*\d+)?\s*삭제", self.text)) and not self.units

    def delegates(self) -> bool:
        return bool(re.search(r"대통령령|총리령|부령|조례로", self.full_text()))


@dataclass
class Appendix:
    number: int
    branch: int
    title: str
    text: str


@dataclass
class LawDoc:
    law_id: str
    name: str
    effective_date: date | None
    promulgated: date | None
    kind: str
    articles: list[Article]
    appendices: list[Appendix] = field(default_factory=list)
    mst: str = ""

    def article(self, jo: int, branch: int = 0) -> Article | None:
        return next((a for a in self.articles if a.jo == jo and a.branch == branch), None)

    def locate(self, ref: ArticleRef) -> "Located":
        """조·항·호·목을 찾아 인용 단위 본문을 돌려준다. 못 찾으면 Located.error 에 이유."""
        if ref.is_appendix:
            ap = next((x for x in self.appendices if x.number == ref.appendix and x.branch == ref.appendix_branch), None)
            if ap is None:
                return Located(None, "", f"{self.name} {ref.display()} 를 찾지 못했습니다")
            return Located(None, ap.text, "", appendix=ap)
        art = self.article(ref.jo, ref.jo_branch)
        if art is None:
            return Located(None, "", f"{self.name} {ref.article_label()} 가 현행 본문에 없습니다(삭제·이동 확인)")
        if art.is_deleted():
            return Located(art, art.full_text(), f"{self.name} {ref.article_label()} 는 삭제된 조문입니다")
        if ref.hang is None and ref.ho is None:
            return Located(art, art.full_text(), "")
        hangs = art.units
        numbered = [u for u in hangs if u.number > 0]
        if ref.hang is not None:
            hang = next((u for u in numbered if u.number == ref.hang), None)
            if hang is None and not numbered and ref.hang == 1 and hangs:
                hang = hangs[0]     # 항이 하나뿐인 조문을 '제1항'으로 적은 경우
            if hang is None:
                return Located(art, art.full_text(), f"{self.name} {ref.article_label()} 제{ref.hang}항이 없습니다")
        else:
            if numbered:
                return Located(art, art.full_text(),
                               f"{self.name} {ref.article_label()} 는 항이 나뉘어 있어 호 앞에 항을 적어야 합니다")
            hang = hangs[0] if hangs else None
            if hang is None:
                return Located(art, art.full_text(), f"{self.name} {ref.article_label()} 에 호가 없습니다")
        if ref.ho is None:
            return Located(art, hang.full_text(), "")
        ho = next((c for c in hang.children if c.number == ref.ho and c.branch == ref.ho_branch), None)
        if ho is None:
            return Located(art, hang.full_text(), f"{self.name} {ref.display()} 의 호를 찾지 못했습니다")
        if ref.mok is None:
            return Located(art, ho.full_text(), "")
        mok = next((c for c in ho.children if c.label.startswith(ref.mok)), None)
        if mok is None:
            return Located(art, ho.full_text(), f"{self.name} {ref.display()} 의 목을 찾지 못했습니다")
        return Located(art, mok.full_text(), "")


@dataclass
class Located:
    article: Article | None
    text: str           # 인용 단위(조/항/호/목) 본문. 못 찾았으면 상위 단위 본문(힌트용)
    error: str = ""
    appendix: Appendix | None = None

    @property
    def ok(self) -> bool:
        return not self.error

    def fingerprint(self) -> str:
        """조문이면 조 전체, 별표면 별표 본문의 정규화 해시."""
        if self.article is not None:
            return self.article.fingerprint()
        return fingerprint_text(self.text)


def fingerprint_text(text: str) -> str:
    return hashlib.sha256(normalize_quote(text).encode("utf-8")).hexdigest()[:16]


_HO_NO_RE = re.compile(r"(\d+)\s*(?:의\s*(\d+))?")


def _hang_number(label: str) -> int:
    lab = (label or "").strip()
    if not lab:
        return 0
    if lab[0] in CIRCLED:
        return CIRCLED[lab[0]]
    m = re.match(r"\d+", lab)
    return int(m.group(0)) if m else 0


def _parse_mok(raw: dict[str, Any]) -> Unit:
    label = str(raw.get("목번호") or "").strip()
    return Unit("mok", 0, 0, label, flatten_text(raw.get("목내용")).strip())


def _parse_ho(raw: dict[str, Any]) -> Unit:
    label = str(raw.get("호번호") or "").strip()
    m = _HO_NO_RE.match(label)
    num, br = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return Unit("ho", num, br, label, flatten_text(raw.get("호내용")).strip(),
                [_parse_mok(x) for x in as_list(raw.get("목")) if isinstance(x, dict)])


def _parse_hang(raw: dict[str, Any]) -> Unit:
    label = str(raw.get("항번호") or "").strip()
    return Unit("hang", _hang_number(label), 0, label, flatten_text(raw.get("항내용")).strip(),
                [_parse_ho(x) for x in as_list(raw.get("호")) if isinstance(x, dict)])


class LawDocError(ValueError):
    pass


def parse_law(data: dict[str, Any]) -> LawDoc:
    law = data.get("법령") if isinstance(data, dict) else None
    if not isinstance(law, dict):
        raise LawDocError("법령 본문 응답에 '법령' 키가 없습니다")
    info = law.get("기본정보") or {}
    kind = info.get("법종구분")
    articles: list[Article] = []
    for u in as_list((law.get("조문") or {}).get("조문단위")):
        if not isinstance(u, dict) or str(u.get("조문여부", "조문")).strip() != "조문":
            continue
        try:
            jo = int(str(u.get("조문번호")).strip())
        except (TypeError, ValueError):
            continue
        br_raw = str(u.get("조문가지번호") or "").strip()
        articles.append(Article(
            jo=jo, branch=int(br_raw) if br_raw.isdigit() else 0,
            title=str(u.get("조문제목") or "").strip(),
            text=flatten_text(u.get("조문내용")).strip(),
            units=[_parse_hang(h) for h in as_list(u.get("항")) if isinstance(h, dict)],
            effective=parse_ymd(u.get("조문시행일자")),
            changed=str(u.get("조문변경여부") or "").strip().upper() == "Y",
            note=flatten_text(u.get("조문참고자료")).strip(),
        ))
    appendices: list[Appendix] = []
    for b in as_list((law.get("별표") or {}).get("별표단위")):
        if not isinstance(b, dict):
            continue
        no = re.sub(r"\D", "", str(b.get("별표번호") or ""))
        br = re.sub(r"\D", "", str(b.get("별표가지번호") or ""))
        if not no:
            continue
        appendices.append(Appendix(int(no), int(br or 0), str(b.get("별표제목") or "").strip(),
                                   flatten_text(b.get("별표내용"))))
    name = str(info.get("법령명_한글") or info.get("법령명한글") or "").strip()
    if not name:
        raise LawDocError("기본정보에 법령명이 없습니다")
    return LawDoc(
        law_id=str(info.get("법령ID") or "").strip(),
        name=name,
        effective_date=parse_ymd(info.get("시행일자")),
        promulgated=parse_ymd(info.get("공포일자")),
        kind=flatten_text(kind).strip() if kind else "",
        articles=articles,
        appendices=appendices,
        mst=str(info.get("법령일련번호") or ""),     # 본문에는 보통 없다 — 호출자가 목록 결과로 채운다
    )


def same_law_name(a: str, b: str) -> bool:
    return law_key(a) == law_key(b)


def law_key(name: str) -> str:
    return re.sub(r"[\s·ㆍ・「」『』]", "", name or "")

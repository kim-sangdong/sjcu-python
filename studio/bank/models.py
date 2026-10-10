"""문제은행 문항 규격.

저장: content/bank/<과목코드>.jsonl — 한 줄에 한 문항, id 순 정렬(git diff 가 문항 단위로 보이게).

    {"id": "Q-BRK-1a2b3c4d", "subject": "BRK", "unit": "중개업무", "type": "ox",
     "stem": "...", "answer": false, "explanation": "...", "trap": "숫자바꿈",
     "basis": [{"kind": "law", "law_name": "공인중개사법", "article": "제33조 제1항 제6호",
                "effective_date": "2026-01-01", "quote": "..."}],
     "difficulty": 2, "status": "draft", "verify_log": [], "created_week": "2026-W41",
     "used_in": [], "free": false, "version": 1, "updated_at": "2026-10-10T12:00:00+09:00"}

정답(answer) 규칙
  ox    : true / false
  mcq   : 정답 보기의 인덱스(0부터) — 영상 퀴즈 장면(answer 0부터)과 같은 규칙
  blank : 빈칸에 들어갈 문자열
  order : 올바른 순서대로 나열한 choices 인덱스 목록(0부터, 순열)
  calc  : 숫자. explanation 에 `calc: <식>` 줄을 두면 검증기가 식을 계산해 대조한다.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from studio import paths

# ---------------------------------------------------------------- 과목

SUBJECTS: dict[str, str] = {
    "RE": "부동산학개론",
    "CIV": "민법 및 민사특별법",
    "BRK": "공인중개사법령 및 중개실무",
    "PUB": "부동산공법",
    "REG": "부동산공시법령",
    "TAX": "부동산세법",
}
SUBJECT_SHORT: dict[str, str] = {
    "RE": "학개론", "CIV": "민법", "BRK": "중개사법", "PUB": "공법", "REG": "공시법", "TAX": "세법",
}
# 입력에서 과목 이름으로 써도 코드로 바꿔 준다.
_SUBJECT_ALIASES: dict[str, str] = {}
for _c in SUBJECTS:
    for _n in (_c, SUBJECTS[_c], SUBJECT_SHORT[_c]):
        _SUBJECT_ALIASES[re.sub(r"\s+", "", _n).lower()] = _c
_SUBJECT_ALIASES.update({
    "부동산학": "RE", "민사특별법": "CIV", "공인중개사법": "BRK", "중개실무": "BRK",
    "부동산공시법": "REG", "공간정보법": "REG", "부동산등기법": "REG", "부동산공시에관한법령": "REG",
    "부동산관련세법": "TAX",
})

SubjectCode = Literal["RE", "CIV", "BRK", "PUB", "REG", "TAX"]
ItemType = Literal["ox", "mcq", "blank", "order", "calc"]
Trap = Literal["숫자바꿈", "주체바꿈", "예외누락", "기간혼동", "요건혼동", "용어혼동", "기타"]
Status = Literal["draft", "verified", "rejected", "retired"]
BasisKind = Literal["law", "precedent", "calc"]

ITEM_TYPES: tuple[str, ...] = ("ox", "mcq", "blank", "order", "calc")
TRAPS: tuple[str, ...] = ("숫자바꿈", "주체바꿈", "예외누락", "기간혼동", "요건혼동", "용어혼동", "기타")
STATUSES: tuple[str, ...] = ("draft", "verified", "rejected", "retired")

ID_RE = re.compile(r"^Q-(RE|CIV|BRK|PUB|REG|TAX)-[0-9a-f]{8}$")
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
# 사건번호: 2019다12345, 2020도1234, 2015두49474, 2017다227516, 2019헌바123 …
CASE_NO_RE = re.compile(r"^\d{2,4}\s?[가-힣]{1,3}\s?\d{1,7}$")

MAX_CHOICES = 5            # 공인중개사 시험은 5지선다
MAX_ORDER_STEPS = 8


def _now() -> datetime:
    """KST 현재 시각(초 단위 — jsonl diff 를 짧게)."""
    return paths.now_kst().replace(microsecond=0)


def subject_code(value: str) -> str:
    """'민법' · '민법 및 민사특별법' · 'CIV' → 'CIV'. 모르는 이름이면 ValueError."""
    code = _SUBJECT_ALIASES.get(re.sub(r"\s+", "", str(value)).lower())
    if not code:
        raise ValueError(f"과목을 알 수 없습니다: {value!r} (코드: {', '.join(SUBJECTS)})")
    return code


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 근거

class Basis(_Model):
    """문항 근거 한 건.

    law       : law_name·article·effective_date·quote 필수(검증기 a 항목). law_id 는 검증기가 채운다.
    precedent : case_no·decided_at 필수. quote 를 쓰면 판시사항·판결요지와 대조한다.
    calc      : 계산 규칙 설명(note). 세율 같은 법정 숫자는 law 근거를 따로 단다.
    """

    kind: BasisKind
    law_name: str | None = None
    law_id: str | None = None
    article: str | None = None            # "제33조 제1항 제6호", "제10조의2 제1항", "제77조 제1항 제1호 가목", "별표 1"
    effective_date: date | None = None    # 이 문항이 대조한 법령 버전의 시행일
    quote: str | None = None              # 조문 원문 인용(정규화 후 부분 문자열로 대조)
    case_no: str | None = None            # 판례 사건번호
    decided_at: date | None = None        # 판례 선고일
    note: str | None = None

    @field_validator("law_name", "article", "quote", "case_no", "note", "law_id")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = re.sub(r"\s+", " ", v).strip()
        return v or None

    def badge(self) -> str:
        """화면 근거 배지 문자열. 예) [공인중개사법 제33조 제1항 · 2026.1.1 시행 기준] / [대법원 2019다12345 · 2020.5.14 선고]"""
        if self.kind == "precedent":
            d = _dot(self.decided_at) if self.decided_at else "선고일 미상"
            court = court_of(self.case_no or "")
            return f"[{court + ' ' if court else ''}{self.case_no or '사건번호 미상'} · {d} {'결정' if court == '헌법재판소' else '선고'}]"
        if self.kind == "calc":
            return f"[계산 · {self.note}]" if self.note else "[계산]"
        parts = [p for p in (self.law_name, self.article) if p]
        when = f"{_dot(self.effective_date)} 시행 기준" if self.effective_date else "시행일 미상"
        return f"[{' '.join(parts)} · {when}]"


# 사건번호의 사건 부호로 법원을 짐작한다(상고·재항고 부호 → 대법원, '헌' → 헌법재판소, 그 밖은 표기 안 함)
_SUPREME_MARKS = {"다", "두", "도", "므", "마", "그", "스", "트", "모", "카", "추", "재다", "재두", "재도"}


def court_of(case_no: str) -> str:
    m = re.match(r"^\s*\d{2,4}\s*([가-힣]{1,3})\s*\d", case_no or "")
    if not m:
        return ""
    mark = m.group(1)
    if mark.startswith("헌"):
        return "헌법재판소"
    return "대법원" if mark in _SUPREME_MARKS else ""


def _dot(d: date) -> str:
    return f"{d.year}.{d.month}.{d.day}"


class VerifyEntry(_Model):
    at: datetime
    check: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 문항

class BankItem(_Model):
    id: str
    subject: SubjectCode
    unit: str = Field(min_length=1, max_length=60)
    type: ItemType
    stem: str = Field(min_length=4, max_length=1200)
    choices: list[str] | None = None
    answer: bool | int | float | str | list[int]
    explanation: str = Field(min_length=4, max_length=3000)
    trap: Trap
    basis: list[Basis] = Field(min_length=1)
    difficulty: int = Field(ge=1, le=5)
    status: Status = "draft"
    verify_log: list[VerifyEntry] = Field(default_factory=list)
    created_week: str
    used_in: list[str] = Field(default_factory=list)
    free: bool = False
    version: int = Field(default=1, ge=1)
    updated_at: datetime

    @field_validator("subject", mode="before")
    @classmethod
    def _subject(cls, v: Any) -> Any:
        return subject_code(v) if isinstance(v, str) else v

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"id 형식은 Q-<과목코드>-<8자리 소문자 hex> 입니다: {v!r}")
        return v

    @field_validator("created_week")
    @classmethod
    def _week(cls, v: str) -> str:
        if not WEEK_RE.match(v):
            raise ValueError(f"created_week 형식은 2026-W41 입니다: {v!r}")
        return v

    @field_validator("stem", "explanation", "unit")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("빈 문자열")
        return v

    @model_validator(mode="after")
    def _shape(self) -> "BankItem":
        if self.id.split("-")[1] != self.subject:
            raise ValueError(f"id 의 과목코드({self.id.split('-')[1]})와 subject({self.subject})가 다릅니다")
        problems = answer_shape_problems(self.type, self.answer, self.choices)
        if problems:
            raise ValueError("; ".join(problems))
        self.used_in = sorted(set(self.used_in))
        return self

    # ------------------------------------------------------------ 도우미

    def law_bases(self) -> list[tuple[int, Basis]]:
        return [(i, b) for i, b in enumerate(self.basis) if b.kind == "law"]

    def badges(self) -> list[str]:
        return [b.badge() for b in self.basis if b.kind != "calc"]

    def text_fields(self) -> list[str]:
        return [self.stem, self.explanation, *(self.choices or []), *([self.answer] if isinstance(self.answer, str) else [])]

    def log(self, check: str, ok: bool, detail: str = "", at: datetime | None = None) -> None:
        self.verify_log.append(VerifyEntry(at=at or _now(), check=check, ok=ok, detail=detail))


def answer_shape_problems(type_: str, answer: Any, choices: list[str] | None) -> list[str]:
    """정답 형식 일관성(검증기 e 항목과 모델 검증이 같이 쓴다)."""
    out: list[str] = []
    n = len(choices or [])
    if type_ in ("mcq", "order"):
        if not choices:
            out.append(f"{type_} 문항은 choices 가 필요합니다")
        elif any(not str(c).strip() for c in choices):
            out.append("빈 보기가 있습니다")
        elif len({re.sub(r'\s+', '', c) for c in choices}) != n:
            out.append("같은 보기가 두 번 있습니다")
    elif choices:
        out.append(f"{type_} 문항에는 choices 를 쓰지 않습니다")
    if type_ == "ox":
        if not isinstance(answer, bool):
            out.append("ox 정답은 true/false 입니다")
    elif type_ == "mcq":
        if type(answer) is not int:
            out.append("mcq 정답은 보기 인덱스(0부터 정수)입니다")
        elif choices and not (0 <= answer < n):
            out.append(f"mcq 정답 인덱스 {answer} 가 보기 범위(0~{n - 1})를 벗어납니다")
        if choices and not (2 <= n <= MAX_CHOICES):
            out.append(f"mcq 보기는 2~{MAX_CHOICES}개입니다(지금 {n}개)")
    elif type_ == "blank":
        if not isinstance(answer, str) or not answer.strip():
            out.append("blank 정답은 빈칸에 들어갈 문자열입니다")
    elif type_ == "order":
        if not (isinstance(answer, list) and all(type(x) is int for x in answer)):
            out.append("order 정답은 choices 인덱스 목록입니다")
        elif choices and sorted(answer) != list(range(n)):
            out.append(f"order 정답은 0~{n - 1} 의 순열이어야 합니다(지금 {answer})")
        if choices and not (2 <= n <= MAX_ORDER_STEPS):
            out.append(f"order 단계는 2~{MAX_ORDER_STEPS}개입니다(지금 {n}개)")
    elif type_ == "calc":
        if isinstance(answer, bool) or not isinstance(answer, (int, float)):
            out.append("calc 정답은 숫자입니다")
    return out


class NewItem(_Model):
    """bank add 입력. id·상태·기록 필드는 비워 두면 도구가 채운다(넣어도 status 는 draft 로 시작)."""

    id: str | None = None
    subject: str
    unit: str
    type: ItemType
    stem: str
    choices: list[str] | None = None
    answer: bool | int | float | str | list[int]
    explanation: str
    trap: Trap
    basis: list[Basis] = Field(min_length=1)
    difficulty: int = Field(ge=1, le=5)
    free: bool = False
    # 아래는 받기만 하고 새 문항에서는 무시한다(다른 도구가 내보낸 JSON 을 그대로 넣어도 되게).
    status: Status | None = None
    verify_log: list[VerifyEntry] | None = None
    created_week: str | None = None
    used_in: list[str] | None = None
    version: int | None = None
    updated_at: datetime | None = None

    def to_item(self, item_id: str, *, now: datetime | None = None) -> BankItem:
        now = now or _now()
        return BankItem(
            id=item_id, subject=subject_code(self.subject), unit=self.unit, type=self.type, stem=self.stem,
            choices=self.choices, answer=self.answer, explanation=self.explanation, trap=self.trap,
            basis=self.basis, difficulty=self.difficulty, status="draft", verify_log=[],
            created_week=paths.week_of(now), used_in=[], free=self.free, version=1, updated_at=now,
        )


def asset_kind_label(asset_id: str) -> str:
    """'2026W42-S03' → '쇼츠' (studio.paths 의 콘텐츠 ID 규칙)."""
    tag = asset_id.rsplit("-", 1)[-1][:1]
    return {"L": "롱폼", "S": "쇼츠", "C": "카드뉴스", "B": "블로그"}.get(tag, "기타")


def make_id(subject: str, stem: str, salt: int = 0) -> str:
    """과목 + 정규화한 지문의 해시 8자리. 같은 지문은 같은 id(재실행에 안전), 충돌하면 salt 를 올린다."""
    from studio.bank.dedupe import normalize_stem

    code = subject_code(subject)
    h = hashlib.sha256(f"{code}|{normalize_stem(stem)}|{salt}".encode("utf-8")).hexdigest()[:8]
    return f"Q-{code}-{h}"


def item_line(item: BankItem) -> str:
    """jsonl 한 줄(필드 순서 고정, None 생략)."""
    import json

    return json.dumps(item.model_dump(mode="json", exclude_none=True), ensure_ascii=False)


now = _now

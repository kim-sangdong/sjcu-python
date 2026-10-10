"""시험·문제은행 설정 — channel.yaml 의 exam·bank 섹션(없으면 기본값).

    exam:
      name: 공인중개사
      round: 37
      next_exam_date: 2026-10-31
      subjects: [부동산학개론, 민법 및 민사특별법, ...]   # 이름 또는 코드, 또는 {code, name}
      papers:                                           # 선택. 앱 모의고사·과락 계산용 과목 묶음
        - {name: 부동산학개론, subjects: [RE], questions: 40}
    bank:                                               # 선택
      crosscheck_required: 2     # 앱 팩에 넣기 전 필요한 독립 정답 확인 횟수(ox·mcq·blank·order)
      weekly_target: 120         # 주간 신규 문항 목표
      dup_threshold: 0.8         # 지문 3-gram 자카드 중복 기준
      free_daily: 20             # 앱 무료 하루 문항 수(팩 manifest 에 실음)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from studio.bank.models import SUBJECTS, subject_code
from studio.config import Config

DEFAULT_EXAM: dict[str, Any] = {
    "name": "공인중개사",
    "round": 37,
    "next_exam_date": "2026-10-31",
}
# 1차 2과목·2차 3과목, 과목당 40문항(공시법·세법은 2차 3교시 한 과목) — 문항 수 배분은 [미확인]
DEFAULT_PAPERS: list[dict[str, Any]] = [
    {"name": "부동산학개론", "stage": 1, "subjects": ["RE"], "questions": 40},
    {"name": "민법 및 민사특별법", "stage": 1, "subjects": ["CIV"], "questions": 40},
    {"name": "공인중개사법령 및 중개실무", "stage": 2, "subjects": ["BRK"], "questions": 40},
    {"name": "부동산공법", "stage": 2, "subjects": ["PUB"], "questions": 40},
    {"name": "부동산공시법령 및 부동산세법", "stage": 2, "subjects": ["REG", "TAX"], "questions": 40},
]
DEFAULT_DISCLAIMER = "한국산업인력공단·큐넷과 무관한 비공식 학습 앱입니다. 문항은 자체 출제이며 기출 원문을 쓰지 않습니다."


@dataclass(frozen=True)
class ExamCfg:
    name: str
    round: int
    next_exam_date: date
    subjects: list[tuple[str, str]]          # (코드, 이름)
    papers: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "round": self.round,
            "next_exam_date": self.next_exam_date.isoformat(),
            "subjects": [{"code": c, "name": n} for c, n in self.subjects],
            "papers": self.papers,
        }


@dataclass(frozen=True)
class BankCfg:
    crosscheck_required: int = 2
    weekly_target: int = 120
    dup_threshold: float = 0.8
    free_daily: int = 20


def _extra(cfg: Config, key: str) -> dict[str, Any]:
    raw = (cfg.model_extra or {}).get(key) or {}
    return raw if isinstance(raw, dict) else {}


def _as_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v).strip())


def exam_cfg(cfg: Config) -> ExamCfg:
    raw = {**DEFAULT_EXAM, **_extra(cfg, "exam")}
    subjects: list[tuple[str, str]] = []
    for s in raw.get("subjects") or list(SUBJECTS):
        if isinstance(s, dict):
            code = subject_code(str(s.get("code") or s.get("name")))
            subjects.append((code, str(s.get("name") or SUBJECTS[code])))
        else:
            code = subject_code(str(s))
            subjects.append((code, SUBJECTS[code]))
    papers = raw.get("papers") or DEFAULT_PAPERS
    papers = [{**p, "subjects": [subject_code(str(x)) for x in p.get("subjects", [])]} for p in papers]
    return ExamCfg(
        name=str(raw.get("name") or DEFAULT_EXAM["name"]),
        round=int(raw.get("round") or DEFAULT_EXAM["round"]),
        next_exam_date=_as_date(raw.get("next_exam_date") or DEFAULT_EXAM["next_exam_date"]),
        subjects=subjects,
        papers=papers,
    )


def bank_cfg(cfg: Config) -> BankCfg:
    raw = _extra(cfg, "bank")
    d = BankCfg()
    return BankCfg(
        crosscheck_required=int(raw.get("crosscheck_required", d.crosscheck_required)),
        weekly_target=int(raw.get("weekly_target", d.weekly_target)),
        dup_threshold=float(raw.get("dup_threshold", d.dup_threshold)),
        free_daily=int(raw.get("free_daily", d.free_daily)),
    )


def disclaimer(cfg: Config) -> str:
    return (cfg.rules.disclaimer or "").strip() or DEFAULT_DISCLAIMER

"""앱용 문항 팩 만들기 — verified(+독립 정답 확인 통과) 문항만.

out/bank/
  manifest.json   버전(YYYYMMDD-해시8)·문항 수·과목별 파일 sha256·무료 문항 id·시험 설정·생성 시각
                  ·영상 연동 덱(decks: 콘텐츠 ID → 그 콘텐츠에 쓴 문항, 앱은 유입 utm_content 로 덱을 연다)
  RE.json CIV.json BRK.json PUB.json REG.json TAX.json   과목별 문항(내부 필드 verify_log·status·used_in·created_week 제외)

규격: studio/bank/pack_schema.json (앱 에이전트와 공유). 쓰기 전에 스키마로 검사해, 어긋나면 파일을 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from studio.bank.models import now as bank_now
from studio.bank.exam import BankCfg, ExamCfg
from studio.bank.models import SUBJECTS, BankItem
from studio.bank.schema import validate
from studio.bank.store import Bank
from studio.verify.runner import crosscheck_ok

SCHEMA_VERSION = 1
PUBLIC_FIELDS = ("id", "subject", "unit", "type", "stem", "choices", "answer", "explanation", "trap",
                 "basis", "difficulty", "free", "version", "updated_at")


@dataclass
class ExportResult:
    out_dir: Path
    version: str
    content_hash: str
    total: int
    per_subject: dict[str, int]
    free_ids: list[str]
    skipped: dict[str, list[str]] = field(default_factory=dict)   # 이유 → id 목록
    files: list[Path] = field(default_factory=list)


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n").encode("utf-8")


def item_alerts(item: BankItem) -> list[dict[str, str]]:
    """마지막 verified 판정 이후 기록된 '시험 전 개정 예정' 경고."""
    log = item.verify_log
    verdicts = [i for i, e in enumerate(log) if e.check == "verdict"]
    if not verdicts or log[verdicts[-1]].detail != "verified":
        return []
    last = verdicts[-1]
    prev = verdicts[-2] if len(verdicts) > 1 else -1
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for e in log[prev + 1:last]:          # upcoming 기록은 같은 검증 묶음(직전 판정 뒤 ~ 마지막 판정 앞)에 남는다
        if e.check == "upcoming" and e.detail not in seen:
            seen.add(e.detail)
            ef, law, art = (e.detail.split("|") + ["", "", ""])[:3]
            out.append({"kind": "upcoming_amendment", "law_name": law, "article": art, "effective_date": ef})
    return out


def public_item(item: BankItem) -> dict[str, Any]:
    d = item.model_dump(mode="json", exclude_none=True, include=set(PUBLIC_FIELDS))
    d = {k: d[k] for k in PUBLIC_FIELDS if k in d}
    d["basis"] = [{**b.model_dump(mode="json", exclude_none=True), "badge": b.badge()} for b in item.basis]
    alerts = item_alerts(item)
    if alerts:
        d["alerts"] = alerts
    return d


KIND_OF_TAG = {"L": "longform", "S": "short", "C": "cardnews", "B": "blog"}


def decks(items: list[BankItem]) -> list[dict[str, Any]]:
    """영상 연동 덱 — used_in(콘텐츠 ID)별 문항 목록. 콘텐츠 ID 형식이 아닌 기록은 건너뛴다."""
    import re

    by: dict[str, list[str]] = {}
    for it in items:
        for a in it.used_in:
            if re.match(r"^\d{4}W\d{2}-[LSCB]\d{1,2}$", a):
                by.setdefault(a, []).append(it.id)
    return [{"asset_id": a, "week": f"{a[:4]}-{a[4:7]}", "kind": KIND_OF_TAG[a.split("-")[1][0]],
             "items": sorted(set(ids))} for a, ids in sorted(by.items())]


def eligible(bank: Bank, bcfg: BankCfg, *, require_crosscheck: bool = True) -> tuple[list[BankItem], dict[str, list[str]]]:
    ok: list[BankItem] = []
    skipped: dict[str, list[str]] = {}
    for it in bank.all():
        if it.status != "verified":
            skipped.setdefault(f"상태 {it.status}", []).append(it.id)
        elif require_crosscheck and not crosscheck_ok(it, bcfg.crosscheck_required):
            skipped.setdefault(f"독립 정답 확인 {bcfg.crosscheck_required}회 미완료", []).append(it.id)
        else:
            ok.append(it)
    return ok, skipped


def build_pack(bank: Bank, exam: ExamCfg, bcfg: BankCfg, disclaimer: str, out_dir: Path, *,
               require_crosscheck: bool = True, now: datetime | None = None) -> ExportResult:
    now = now or bank_now()
    items, skipped = eligible(bank, bcfg, require_crosscheck=require_crosscheck)
    by_subject: dict[str, list[BankItem]] = {c: [] for c in SUBJECTS}
    for it in items:
        by_subject[it.subject].append(it)
    names = dict(exam.subjects)
    codes = [c for c, _ in exam.subjects] + [c for c in SUBJECTS if c not in names]

    # 내용 해시(버전 문자열에 쓰므로 먼저 계산) — 같은 내용이면 같은 해시
    payloads: dict[str, dict[str, Any]] = {}
    for c in codes:
        its = sorted(by_subject[c], key=lambda x: x.id)
        payloads[c] = {"kind": "subject", "schema_version": SCHEMA_VERSION, "version": "", "subject": c,
                       "name": names.get(c, SUBJECTS[c]), "count": len(its), "items": [public_item(i) for i in its]}
    exam_d = {"name": exam.name, "round": exam.round, "next_exam_date": exam.next_exam_date.isoformat(),
              "papers": exam.papers}
    content_hash = hashlib.sha256(json.dumps(
        {"exam": exam_d, "items": {c: p["items"] for c, p in payloads.items()}},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    version = f"{now.strftime('%Y%m%d')}-{content_hash[:8]}"

    files: dict[str, bytes] = {}
    subjects_meta: list[dict[str, Any]] = []
    for c in codes:
        p = payloads[c]
        p["version"] = version
        validate(p, "#/$defs/subjectPack")
        data = _dump(p)
        files[f"{c}.json"] = data
        subjects_meta.append({"code": c, "name": p["name"], "file": f"{c}.json", "count": p["count"],
                              "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    free_ids = sorted(i.id for i in items if i.free)
    manifest = {
        "kind": "manifest", "schema_version": SCHEMA_VERSION, "version": version, "content_hash": content_hash,
        "generated_at": now.isoformat(timespec="seconds"), "exam": exam_d, "disclaimer": disclaimer,
        "free_daily": bcfg.free_daily, "total": len(items), "subjects": subjects_meta, "free_ids": free_ids,
        "decks": decks(items),
    }
    validate(manifest, "#/$defs/manifest")
    files["manifest.json"] = _dump(manifest)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, data in files.items():
        p = out_dir / name
        tmp = p.with_suffix(".json.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        written.append(p)
    for stale in out_dir.glob("*.json"):            # 지난 내보내기에서 남은 과목 파일 정리
        if stale.name not in files:
            stale.unlink()
    return ExportResult(out_dir, version, content_hash, len(items), {c: payloads[c]["count"] for c in codes},
                        free_ids, skipped, written)

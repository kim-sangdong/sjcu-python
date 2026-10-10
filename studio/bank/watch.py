"""법령 개정 감지(watch-laws) — verified 문항이 인용한 조문이 바뀌었으면 retired 로 내리고 기록을 남긴다.

판단 순서(근거 하나마다)
  1) 현행 본문에서 조문을 찾는다. 없거나 '삭제' → 바뀜(조문 삭제·이동)
  2) 검증 때 남긴 source_hash(조문 본문 해시)와 지금 해시가 다르면 → 바뀜(조문 내용 변경)
     해시 기록이 없으면(사람이 직접 넣은 문항 등) 조문별 변경 이력 API 로 기준일 이후 변경이 있는지 본다.
  3) 시행예정 개정 중 다음 시험일까지 시행되는 것이 그 조문을 바꾸면 → 바뀜(시험 전 시행 예정)
     시험일 뒤에 시행되는 개정은 '참고'로만 적는다.
결과: content/bank/law_changes/<날짜>.md (사람·정정 쇼츠·블로그 배너 기획용) + 같은 이름 .json(다른 모듈 입력용)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from studio.bank.models import now as bank_now
from studio.bank.models import BankItem, asset_kind_label
from studio.bank.store import Bank, law_changes_dir
from studio.verify.law_api import LawApi, LawApiError, LawApiUnavailable, LawVersion
from studio.verify.lawtext import ArticleRef, ArticleRefError, LawDoc, parse_ymd
from studio.verify.normalize import find_quote


@dataclass
class Change:
    law_name: str
    law_id: str
    article: str
    kind: str                 # deleted | changed | upcoming | upcoming_after_exam | history
    detail: str
    effective: str = ""       # 바뀐 버전 시행일
    quote_kept: bool | None = None
    items: list[str] = field(default_factory=list)


@dataclass
class WatchResult:
    checked_laws: list[str] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    unavailable: str = ""


def _recorded_hash(item: BankItem, idx: int) -> tuple[str, str] | None:
    """(해시, 버전 시행일) — 같은 근거 번호의 마지막 source_hash 기록."""
    for e in reversed(item.verify_log):
        if e.check == "source_hash":
            parts = e.detail.split("|")
            if parts and parts[0] == str(idx) and len(parts) >= 4:
                return parts[3], (parts[4] if len(parts) > 4 else "")
    return None


def watch(bank: Bank, api: LawApi, *, exam_date: date, today: date) -> WatchResult:
    """바뀐 조문과 내려야 할 문항을 계산만 한다(상태 변경은 apply_retirements)."""
    res = WatchResult()
    targets = [it for it in bank.all() if it.status == "verified" and it.law_bases()]
    by_law: dict[str, list[tuple[BankItem, int]]] = {}
    for it in targets:
        for i, b in it.law_bases():
            if b.law_name:
                by_law.setdefault(b.law_name, []).append((it, i))
    changes: dict[tuple[str, str, str], Change] = {}
    retire: dict[str, list[str]] = {}

    def note(law: LawDoc, art: str, kind: str, detail: str, item: BankItem, effective: str = "",
             quote_kept: bool | None = None) -> None:
        key = (law.name, art, kind + effective)
        ch = changes.get(key)
        if ch is None:
            ch = changes[key] = Change(law.name, law.law_id, art, kind, detail, effective, quote_kept)
        if item.id not in ch.items:
            ch.items.append(item.id)
        if kind in ("deleted", "changed", "upcoming", "history"):
            retire.setdefault(item.id, []).append(f"{law.name} {art}: {detail}")

    for law_name in sorted(by_law):
        try:
            _, doc = api.law_by_name(law_name, fresh=True)
            ups = [v for v in api.upcoming(doc.law_id, fresh=True) if v.effective_date > today]
        except LawApiUnavailable as e:
            res.unavailable = str(e)
            return res
        except LawApiError as e:
            res.errors.append(f"{law_name}: {e}")
            continue
        res.checked_laws.append(f"{doc.name}({doc.law_id}, 현행 시행 {doc.effective_date})")
        version_docs: dict[str, LawDoc] = {}

        def vdoc(v: LawVersion) -> LawDoc:
            if v.mst not in version_docs:
                version_docs[v.mst] = api.law_version(v.mst, v.effective_date)
            return version_docs[v.mst]

        for item, idx in by_law[law_name]:
            b = item.basis[idx]
            try:
                ref = ArticleRef.parse(b.article or "")
            except ArticleRefError as e:
                res.errors.append(f"{item.id}: {e}")
                continue
            art = ref.display()
            rec = _recorded_hash(item, idx)
            rec_date = parse_ymd(rec[1]) if rec and rec[1] else None
            base, pending = doc, ups
            if rec_date and doc.effective_date and rec_date > doc.effective_date:
                # 시행예정 버전을 인용해 검증한 문항 — 그 버전과 비교한다
                match = [v for v in ups if v.effective_date <= rec_date]
                if not match:
                    note(doc, art, "changed", f"검증 때 인용한 {rec_date} 시행 예정 개정이 목록에 없습니다(철회·변경 확인)", item)
                    continue
                base = vdoc(match[-1])
                pending = [v for v in ups if v.effective_date > match[-1].effective_date]
            loc = base.locate(ref)
            if not loc.ok or (loc.article is not None and loc.article.is_deleted()):
                note(doc, art, "deleted", loc.error or "조문이 삭제됐습니다", item)
                continue
            if rec is not None:
                if rec[0] != loc.fingerprint():
                    kept = find_quote(b.quote or "", loc.text).found if b.quote else None
                    note(doc, art, "changed",
                         f"조문 내용이 바뀌었습니다(검증 때 버전 시행 {rec[1] or '?'} → 현행 시행 {base.effective_date})",
                         item, str(base.effective_date or ""), kept)
                    continue
            elif loc.article is not None:
                try:
                    hist = api.article_history(doc.law_id, ref.jo_code())
                except LawApiUnavailable as e:
                    res.unavailable = str(e)
                    return res
                except LawApiError:
                    hist = []
                after = [h for h in hist if h.effective and b.effective_date and b.effective_date < h.effective <= today]
                if after:
                    h = after[-1]
                    kept = find_quote(b.quote or "", loc.text).found if b.quote else None
                    note(doc, art, "history",
                         f"기준일 {b.effective_date} 이후 조문 변경({h.change or '개정'} {h.effective}"
                         f"{', ' + h.reason if h.reason else ''})", item, str(h.effective), kept)
                    continue
            for v in pending:
                newer = vdoc(v).locate(ref)
                if newer.ok and newer.fingerprint() == loc.fingerprint():
                    continue
                kept = bool(newer.ok and b.quote and find_quote(b.quote, newer.text).found)
                kind = "upcoming" if v.effective_date <= exam_date else "upcoming_after_exam"
                when = "다음 시험 전" if kind == "upcoming" else "다음 시험 뒤"
                note(doc, art, kind, f"{v.effective_date} 시행 개정({v.change or '개정'})으로 바뀝니다({when})",
                     item, v.effective_date.isoformat(), kept)
                break

    res.changes = sorted(changes.values(), key=lambda c: (c.law_name, c.article, c.kind))
    res.retired = sorted(retire)
    res.reasons = {k: retire[k] for k in res.retired}
    return res


def apply_retirements(bank: Bank, res: WatchResult, snapshot: Bank | None = None,
                      now: datetime | None = None) -> list[str]:
    """잠근 bank 에 retired 반영. 감지 뒤에 내용이 바뀐(version 이 다른) 문항은 건드리지 않는다."""
    now = now or bank_now()
    done: list[str] = []
    for item_id in res.retired:
        it = bank.get(item_id)
        before = snapshot.get(item_id) if snapshot else it
        if it is None or before is None or it.version != before.version or it.status != "verified":
            continue
        it.status = "retired"
        it.log("watch", False, ("retired: " + " / ".join(res.reasons.get(item_id, [])))[:800], at=now)
        it.log("verdict", False, "retired: 법령 개정", at=now)
        done.append(item_id)
    return done


# ---------------------------------------------------------------- 기록

KIND_KO = {
    "deleted": "조문 삭제·이동",
    "changed": "조문 내용 변경",
    "history": "조문 변경(이력)",
    "upcoming": "시험 전 시행 예정 개정",
    "upcoming_after_exam": "시험 후 시행 예정(참고)",
}


def write_report(res: WatchResult, bank: Bank, *, day: date, exam_name: str, exam_round: int,
                 exam_date: date) -> tuple[Path, Path]:
    d = law_changes_dir()
    d.mkdir(parents=True, exist_ok=True)
    md_p, js_p = d / f"{day.isoformat()}.md", d / f"{day.isoformat()}.json"
    assets: dict[str, set[str]] = {}
    for c in res.changes:
        if c.kind == "upcoming_after_exam":
            continue
        for i in c.items:
            for a in bank.require(i).used_in:
                assets.setdefault(a, set()).add(i)
    L: list[str] = [
        f"# 법령 개정 감지 — {day.isoformat()}",
        "",
        f"- 기준: 다음 시험 {exam_date.isoformat()} ({exam_name} 제{exam_round}회)",
        f"- 확인한 법령 {len(res.checked_laws)}개 · 변경 {sum(1 for c in res.changes if c.kind != 'upcoming_after_exam')}건"
        f" · retired {len(res.retired)}문항",
        "",
        "## 변경 목록",
        "",
    ]
    if not res.changes:
        L.append("변경 없음.")
    for c in res.changes:
        L.append(f"### {c.law_name} {c.article} — {KIND_KO.get(c.kind, c.kind)}")
        L.append("")
        L.append(f"- 내용: {c.detail}")
        if c.quote_kept is not None:
            L.append(f"- 인용 부분: {'그대로 있음 → 재검증 후보(내용 확인 뒤 bank update)' if c.quote_kept else '바뀜 → 문항 재작성 필요'}")
        for i in c.items:
            it = bank.require(i)
            used = ", ".join(it.used_in) or "없음"
            L.append(f"- {i} ({it.type}) {it.stem[:50]}{'…' if len(it.stem) > 50 else ''} — 사용처: {used}")
        L.append("")
    L += ["## 정정 콘텐츠 기획 입력", ""]
    if assets:
        for a in sorted(assets):
            L.append(f"- {a} ({asset_kind_label(a)}): 문항 {', '.join(sorted(assets[a]))} — "
                     + ("정정 쇼츠·고정 댓글" if asset_kind_label(a) in ("쇼츠", "롱폼") else "블로그 상단 배너·본문 정정 이력"
                        if asset_kind_label(a) == "블로그" else "카드뉴스 캡션 정정"))
    else:
        L.append("- 이미 발행한 콘텐츠에서 쓴 문항은 없습니다.")
    L += ["", "## 다음 할 일", "",
          "1. 바뀐 조문을 `python -m studio bank law --name <법령> --article <조문> --refresh` 로 읽는다.",
          "2. retired 문항은 새 버전으로 고쳐 `python -m studio bank update --file <고친 문항>.json` → verify → crosscheck.",
          "3. `python -m studio bank export && python -m studio bank upload` 로 앱 팩에서 빠지게 한다.",
          "4. 위 사용처에 24시간 안에 정정(고정 댓글·정정 쇼츠·블로그 배너)을 올린다(CONTENT 규칙 6)."]
    if res.errors:
        L += ["", "## 확인 못 한 것", ""] + [f"- {e}" for e in res.errors]
    md_p.write_text("\n".join(L) + "\n", encoding="utf-8")
    payload: dict[str, Any] = {
        "date": day.isoformat(), "exam_date": exam_date.isoformat(), "checked_laws": res.checked_laws,
        "retired": res.retired, "errors": res.errors,
        "changes": [c.__dict__ for c in res.changes],
        "corrections": [{"asset_id": a, "kind": asset_kind_label(a), "items": sorted(v)} for a, v in sorted(assets.items())],
    }
    js_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return md_p, js_p

"""검증 결과를 문항 상태에 반영하고, 독립 정답 확인(crosscheck)을 기록한다.

상태 전이
  verify : 모든 검사 통과(경고 허용) → verified
           하나라도 실패               → rejected (verify_log 에 사유)
           API 를 못 써서 판정 불가     → 상태 그대로(보류), verify_log 에 '보류'
  crosscheck : Claude 가 문항을 정답 없이 따로 푼 답을 기록. 저장된 정답과 다르면 즉시 rejected.
               앱 팩(export)은 현재 version 에서 일치 기록이 crosscheck_required(기본 2)번 이상인 문항만 넣는다
               (calc 는 식 검산으로 갈음해 면제).
  update(내용 수정) 하면 version 이 올라가 이전 crosscheck 는 세지 않는다.

verify_log 기록 규칙(check 이름)
  a_basis … g_phrases : 각 검사(ok=통과·경고, detail 이 '경고: '로 시작하면 경고)
  source_hash         : '<근거번호>|<법령ID>|<조문>|<본문해시16>|<버전시행일>' — 개정 감지(watch-laws)가 비교
  upcoming            : '<시행일>|<법령명>|<조문>' — 시험 전 시행 개정 경고(앱 경고 배지)
  llm_crosscheck      : 'v<버전> by=<누가> answer=<답> [note=…]'
  verdict             : verified | rejected: 사유 | pending: 사유
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from studio.bank.models import now as bank_now
from studio.bank.models import BankItem
from studio.verify.calc import close_enough
from studio.verify.checks import ItemReport
from studio.verify.normalize import CIRCLED, extract_numbers, normalize_quote, parse_number

CROSSCHECK_EXEMPT_TYPES = ("calc",)


def apply_report(item: BankItem, rep: ItemReport, now: datetime | None = None) -> str:
    """검증 결과를 문항에 기록하고 새 상태를 돌려준다."""
    now = now or bank_now()
    verdict = rep.verdict
    for o in rep.outcomes:
        if o.status == "skip":
            continue
        detail = o.detail if o.status != "warn" else f"경고: {o.detail}"
        item.log(o.check, o.status != "fail", detail[:500], at=now)
    if verdict == "verified":
        changed = False
        for i, field_name, _old, new in rep.updates:
            b = item.basis[i]
            if field_name == "effective_date":
                b.effective_date = new if isinstance(new, date) else date.fromisoformat(str(new))
            else:
                setattr(b, field_name, new)
            changed = True
        for s in rep.sources:
            vd = s.version_date.isoformat() if s.version_date else ""
            item.log("source_hash", True, f"{s.index}|{s.doc.law_id}|{s.ref.display()}|{s.fingerprint()}|{vd}", at=now)
        for a in rep.alerts:
            item.log("upcoming", True, f"{a['effective_date']}|{a['law_name']}|{a['article']}", at=now)
        item.status = "verified"
        if changed:
            item.updated_at = now
        item.log("verdict", True, "verified", at=now)
    elif verdict == "rejected":
        item.status = "rejected"
        reasons = " / ".join(f"{o.check}: {o.detail}" for o in rep.failures())
        item.log("verdict", False, f"rejected: {reasons}"[:800], at=now)
    else:
        why = next((o.detail for o in rep.outcomes if o.status == "skip"), "")
        item.log("verdict", False, f"pending: {why}"[:300], at=now)
    return item.status


# ---------------------------------------------------------------- crosscheck

def crosscheck_entries(item: BankItem) -> list[Any]:
    tag = f"v{item.version} "
    return [e for e in item.verify_log if e.check == "llm_crosscheck" and e.detail.startswith(tag)]


def crosscheck_state(item: BankItem, required: int) -> dict[str, Any]:
    if item.type in CROSSCHECK_EXEMPT_TYPES:
        return {"required": 0, "done": 0, "status": "n/a", "note": "calc 문항은 식 검산으로 갈음"}
    ents = crosscheck_entries(item)
    if any(not e.ok for e in ents):
        status = "failed"
    elif sum(1 for e in ents if e.ok) >= required:
        status = "passed"
    else:
        status = "pending"
    return {"required": required, "done": sum(1 for e in ents if e.ok), "status": status}


def crosscheck_ok(item: BankItem, required: int) -> bool:
    return crosscheck_state(item, required)["status"] in ("passed", "n/a")


class AnswerParseError(ValueError):
    pass


_TRUE = {"o", "○", "◯", "true", "t", "참", "맞다", "맞음", "옳다", "옳음", "y", "yes", "1"}
_FALSE = {"x", "×", "✕", "false", "f", "거짓", "틀리다", "틀림", "옳지않다", "n", "no", "0"}


def parse_answer(item: BankItem, text: str) -> Any:
    """crosscheck 로 받은 답을 문항 정답 형식으로 바꾼다.

    ox    : O/X, true/false, 참/거짓
    mcq   : 동그라미 숫자 ①~⑤ 또는 '3번'(1부터) / 맨 숫자 '2' 는 0부터 센 인덱스(파일의 answer 와 같은 규칙)
    blank : 문자열(공백·기호 무시 비교, 숫자는 단위 정규화 비교)
    order : '2,0,1,3'(0부터) 또는 '③①②④'(1부터)
    calc  : 숫자(쉼표·'원' 허용, 1억2천만 같은 한글 단위 허용)
    """
    t = (text or "").strip()
    if not t:
        raise AnswerParseError("빈 답")
    n = len(item.choices or [])
    if item.type == "ox":
        k = re.sub(r"\s+", "", t).lower()
        if k in _TRUE:
            return True
        if k in _FALSE:
            return False
        raise AnswerParseError(f"OX 답을 읽지 못했습니다: {t!r}")
    if item.type == "mcq":
        if t[0] in CIRCLED and len(t) == 1:
            idx = CIRCLED[t[0]] - 1
        elif re.fullmatch(r"\d+\s*번", t):
            idx = int(re.sub(r"\D", "", t)) - 1
        elif re.fullmatch(r"\d+", t):
            idx = int(t)
        else:
            raise AnswerParseError(f"보기 번호를 읽지 못했습니다: {t!r} (①~⑤, '3번', 또는 0부터 인덱스)")
        if not (0 <= idx < n):
            raise AnswerParseError(f"보기 범위를 벗어났습니다: {t!r}")
        return idx
    if item.type == "order":
        if all(ch in CIRCLED or ch.isspace() or ch in ",→>-" for ch in t):
            seq = [CIRCLED[ch] - 1 for ch in t if ch in CIRCLED]
        else:
            seq = [int(x) for x in re.findall(r"\d+", t)]
        if sorted(seq) != list(range(n)):
            raise AnswerParseError(f"순서 답은 0~{n - 1} 의 순열이어야 합니다: {t!r}")
        return seq
    if item.type == "calc":
        s = re.sub(r"[,\s원]", "", t)
        try:
            return float(s)
        except ValueError:
            v = parse_number(s)
            if v is None:
                raise AnswerParseError(f"숫자를 읽지 못했습니다: {t!r}") from None
            return v
    return t


def answers_equal(item: BankItem, got: Any) -> bool:
    exp = item.answer
    if item.type == "ox":
        return got is exp
    if item.type == "mcq":
        return got == exp
    if item.type == "order":
        return list(got) == list(exp)  # type: ignore[arg-type]
    if item.type == "calc":
        return close_enough(float(got), float(exp))  # type: ignore[arg-type]
    a, b = normalize_quote(str(got)), normalize_quote(str(exp))
    if a == b:
        return True
    na, nb = extract_numbers(str(got)), extract_numbers(str(exp))
    if na and len(na) == len(nb) and all(x.key() == y.key() for x, y in zip(na, nb)):
        strip = lambda s, ns: normalize_quote(re.sub("|".join(re.escape(x.raw) for x in ns), "", s))  # noqa: E731
        return strip(str(got), na) == strip(str(exp), nb)
    return False


def record_crosscheck(item: BankItem, answer_text: str, by: str, note: str = "", *,
                      required: int = 2, now: datetime | None = None) -> dict[str, Any]:
    """독립 풀이 답을 기록. 다르면 rejected. 결과 dict(ok, state)를 돌려준다."""
    now = now or bank_now()
    got = parse_answer(item, answer_text)
    ok = answers_equal(item, got)
    detail = f"v{item.version} by={by} answer={answer_text.strip()}"
    if note:
        detail += f" note={note.strip()[:200]}"
    item.log("llm_crosscheck", ok, detail, at=now)
    if not ok and item.status != "retired":
        item.status = "rejected"
        item.log("verdict", False, f"rejected: 독립 풀이 답({answer_text.strip()})이 저장된 정답과 다릅니다", at=now)
    return {"ok": ok, "parsed": got, "state": crosscheck_state(item, required)}

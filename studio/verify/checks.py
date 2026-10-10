"""문항 자동 검증 항목(a~g)과 한 문항 검증기.

  a 근거 필드   law: law_name·article·effective_date·quote / precedent: case_no·decided_at
  b 인용문 원문 정규화 후 부분 문자열. 실패하면 가장 비슷한 원문 구간과 유사도를 보여 준다.
  c 숫자 대조   '참인 진술'의 숫자·기간·비율이 인용 조문(없으면 같은 법령 시행령의 위임 조문)에 있는지
  d 시점        시행일 ≤ 다음 시험일, 시험 전에 시행될 개정이 인용 조문을 바꾸면 경고(앱 경고 배지)
  e 정답 형식   ox=bool, mcq 인덱스 범위, order 순열, blank 빈칸 표시
  f 계산        explanation 의 calc: 식을 안전 파서로 계산해 answer 와 대조
  g 금지 표현   channel.yaml rules.banned_phrases(+문제은행 기본 목록) 실패, caution_phrases 경고

결과 상태: ok · warn(통과하되 기록) · fail(반려) · skip(API 를 못 써서 판정 보류)
'참인 진술' 규칙(c)
  ox    정답 O → 지문+해설, 정답 X → 해설(지문에만 나온 숫자는 '틀린 숫자'라 면제)
  mcq   발문이 '옳은 것' → 정답 보기, '옳지 않은/틀린 것' → 나머지 보기 + 해설(틀린 보기 숫자 면제)
  blank 지문·정답·해설 전부 / order 지문·보기·해설 전부
  calc  해설에서 지문(주어진 값)·계산 중간값·정답을 뺀 나머지(세율 같은 법정 숫자)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from studio.bank.models import BankItem, Basis, CASE_NO_RE, answer_shape_problems
from studio.verify.calc import CalcError, calc_lines, close_enough, evaluate, strip_calc_lines
from studio.verify.law_api import LawApi, LawApiError, LawApiUnavailable, LawVersion
from studio.verify.lawtext import Article, ArticleRef, ArticleRefError, LawDoc, LawDocError, Located
from studio.verify.normalize import Num, contains, extract_numbers, find_quote, normalize_quote

CHECK_NAMES = {
    "a_basis": "근거 필드", "b_quote": "인용문 원문", "c_numbers": "숫자 대조", "d_dates": "시행일·시험일",
    "e_answer": "정답 형식", "f_calc": "계산 검산", "g_phrases": "금지 표현",
}
BANK_BANNED = ["족보", "복원문제", "복원 문제", "기출 원문", "적중", "합격 보장", "100% 합격", "무조건 합격",
               "큐넷 제공", "공단 제공", "공단 인증", "국가공인 앱", "정답 확정"]
MIN_QUOTE_CHARS = 8
NEG_POLARITY = re.compile(r"옳지\s*않은|틀린\s*것|아닌\s*것|잘못된|부적절한|해당하지\s*않는|없는\s*것은")
POS_POLARITY = re.compile(r"옳은\s*것|맞는\s*것|올바른|적절한\s*것|해당하는\s*것|있는\s*것은")
BLANK_MARK = re.compile(r"\(\s*\)|（\s*）|_{2,}|□|\[\s*\]|빈칸|\(\s*[ㄱ㉠]\s*\)")
EXAM_ROUND_RE = re.compile(r"\d+\s*회\s*(?:기출|시험\s*문제)")


@dataclass
class Outcome:
    check: str
    status: str            # ok | warn | fail | skip
    detail: str = ""

    def line(self) -> str:
        mark = {"ok": "통과", "warn": "경고", "fail": "실패", "skip": "보류"}[self.status]
        return f"[{mark}] {CHECK_NAMES.get(self.check, self.check)}: {self.detail}" if self.detail else \
            f"[{mark}] {CHECK_NAMES.get(self.check, self.check)}"


@dataclass
class Source:
    index: int
    basis: Basis
    ref: ArticleRef
    doc: LawDoc
    located: Located
    version_date: date | None
    via: str = "현행"

    @property
    def label(self) -> str:
        return f"{self.doc.name} {self.ref.display()}"

    def fingerprint(self) -> str:
        return self.located.fingerprint()


@dataclass
class ItemReport:
    item_id: str
    version: int
    outcomes: list[Outcome] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    updates: list[tuple[int, str, Any, Any]] = field(default_factory=list)   # (근거 번호, 필드, 이전, 새 값)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    llm_crosscheck: dict[str, Any] = field(default_factory=dict)

    def add(self, check: str, status: str, detail: str = "") -> None:
        self.outcomes.append(Outcome(check, status, detail))

    @property
    def verdict(self) -> str:
        st = {o.status for o in self.outcomes}
        if "fail" in st:
            return "rejected"
        if "skip" in st:
            return "pending"
        return "verified"

    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "fail"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "version": self.version,
            "verdict": self.verdict,
            "checks": [{"check": o.check, "status": o.status, "detail": o.detail} for o in self.outcomes],
            "sources": [{"basis": s.index, "law_id": s.doc.law_id, "law_name": s.doc.name, "article": s.ref.display(),
                         "version_date": s.version_date.isoformat() if s.version_date else None, "via": s.via,
                         "fingerprint": s.fingerprint()} for s in self.sources],
            "updates": [{"basis": i, "field": f, "from": str(a) if a is not None else None, "to": str(b)}
                        for i, f, a, b in self.updates],
            "alerts": self.alerts,
            "llm_crosscheck": self.llm_crosscheck,
        }


# ---------------------------------------------------------------- 오프라인 항목 (a e f g)

def check_basis(item: BankItem, rep: ItemReport) -> None:
    problems: list[str] = []
    warns: list[str] = []
    strong = [b for b in item.basis if b.kind in ("law", "precedent")]
    for i, b in enumerate(item.basis, 1):
        tag = f"근거{i}({b.kind})"
        if b.kind == "law":
            miss = [f for f in ("law_name", "article", "effective_date", "quote") if not getattr(b, f)]
            if miss:
                problems.append(f"{tag} 빈 필드: {', '.join(miss)}")
            if b.article:
                try:
                    ArticleRef.parse(b.article)
                except ArticleRefError as e:
                    problems.append(f"{tag} {e}")
            if b.quote and len(normalize_quote(b.quote)) < MIN_QUOTE_CHARS:
                problems.append(f"{tag} 인용문이 너무 짧습니다(기호 빼고 {MIN_QUOTE_CHARS}자 이상)")
        elif b.kind == "precedent":
            miss = [f for f in ("case_no", "decided_at") if not getattr(b, f)]
            if miss:
                problems.append(f"{tag} 빈 필드: {', '.join(miss)}")
            if b.case_no and not CASE_NO_RE.match(b.case_no):
                problems.append(f"{tag} 사건번호 형식이 아닙니다: {b.case_no} (예: 2019다12345)")
        elif b.kind == "calc" and not b.note:
            warns.append(f"{tag} note 에 계산 규칙을 적어 주세요")
    if not strong:
        theory = item.type == "calc" or item.subject == "RE"
        if theory and any(b.kind == "calc" and b.note for b in item.basis):
            warns.append("법령·판례 근거가 없는 이론·계산 문항입니다")
        else:
            problems.append("법령(law) 또는 판례(precedent) 근거가 하나 이상 있어야 합니다")
    if problems:
        rep.add("a_basis", "fail", "; ".join(problems))
    elif warns:
        rep.add("a_basis", "warn", "; ".join(warns))
    else:
        rep.add("a_basis", "ok")


def check_answer(item: BankItem, rep: ItemReport) -> None:
    problems = answer_shape_problems(item.type, item.answer, item.choices)
    if item.type == "blank" and not BLANK_MARK.search(item.stem):
        problems.append("blank 지문에 빈칸 표시( '( )' 또는 '___' )가 없습니다")
    if item.type == "ox" and item.stem.rstrip().endswith("?"):
        problems.append("ox 지문은 진술문으로 씁니다(물음표로 끝나지 않게)")
    if item.type == "mcq" and not (NEG_POLARITY.search(item.stem) or POS_POLARITY.search(item.stem)):
        rep.add("e_answer", "warn" if not problems else "fail",
                "; ".join(problems + ["발문에 '옳은 것/옳지 않은 것'이 없어 보기 숫자 대조를 줄입니다"]))
        return
    rep.add("e_answer", "fail" if problems else "ok", "; ".join(problems))


def calc_analysis(item: BankItem) -> tuple[list[float], list[str]]:
    """(계산으로 나온 값·상수 목록, 문제점). 문제점이 있으면 f 검사 실패."""
    values: list[float] = []
    problems: list[str] = []
    lines = calc_lines(item.explanation)
    if item.type == "calc" and not lines:
        return values, ["calc 문항은 해설에 'calc: <식>' 줄이 있어야 합니다"]
    last = None
    for ln in lines:
        try:
            r = evaluate(ln)
        except CalcError as e:
            problems.append(f"식 '{ln}': {e}")
            continue
        values.extend(r.steps + r.constants + [r.value])
        last = r.value
    if item.type == "calc" and last is not None and not problems:
        ans = float(item.answer)  # type: ignore[arg-type]
        if not close_enough(last, ans):
            problems.append(f"계산 결과 {last:g} 와 정답 {ans:g} 가 다릅니다")
    return values, problems


def check_calc(item: BankItem, rep: ItemReport) -> None:
    if item.type != "calc" and not calc_lines(item.explanation):
        return
    _, problems = calc_analysis(item)
    if problems:
        rep.add("f_calc", "fail" if item.type == "calc" else "warn", "; ".join(problems))
    else:
        rep.add("f_calc", "ok")


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def check_phrases(item: BankItem, rep: ItemReport, banned: list[str], caution: list[str]) -> None:
    text = "\n".join(str(x) for x in item.text_fields())
    flat = _compact(text)
    hits = sorted({p for p in [*banned, *BANK_BANNED] if p and _compact(p) in flat})
    warn = sorted({p for p in caution if p and _compact(p) in flat})
    if EXAM_ROUND_RE.search(text):
        warn.append("기출 회차 표기(기출 원문·보기 사용 금지 — 출제 포인트만)")
    if hits:
        rep.add("g_phrases", "fail", "금지 표현: " + ", ".join(hits))
    elif warn:
        rep.add("g_phrases", "warn", "주의 표현: " + ", ".join(warn))
    else:
        rep.add("g_phrases", "ok")


def offline_checks(item: BankItem, rep: ItemReport, banned: list[str], caution: list[str]) -> None:
    check_basis(item, rep)
    check_answer(item, rep)
    check_calc(item, rep)
    check_phrases(item, rep, banned, caution)


# ---------------------------------------------------------------- 숫자 '참인 진술'

def polarity(stem: str) -> str:
    if NEG_POLARITY.search(stem):
        return "neg"
    if POS_POLARITY.search(stem):
        return "pos"
    return "unknown"


@dataclass
class Claims:
    strict: list[tuple[Num, str]]          # (숫자, 어디서)
    exempt: list[Num]
    exempt_values: list[float]
    notes: list[str]


def claim_numbers(item: BankItem) -> Claims:
    strict: list[tuple[Num, str]] = []
    exempt: list[Num] = []
    exempt_values: list[float] = []
    notes: list[str] = []
    stem_n = extract_numbers(item.stem)
    expl_n = extract_numbers(strip_calc_lines(item.explanation))

    def add(nums: list[Num], where: str) -> None:
        for n in nums:
            if not contains(exempt, n) and not any(abs(n.value - v) <= 1e-6 * max(1, abs(v)) for v in exempt_values):
                strict.append((n, where))

    if item.type == "ox":
        if item.answer is True:
            add(stem_n, "지문")
        else:
            exempt.extend(stem_n)
        add(expl_n, "해설")
    elif item.type == "mcq":
        ch = item.choices or []
        pol = polarity(item.stem)
        if pol == "unknown":
            notes.append("발문 극성을 몰라 보기 숫자는 대조하지 않았습니다")
            true_idx: list[int] = []
        elif pol == "pos":
            true_idx = [int(item.answer)]  # type: ignore[arg-type]
        else:
            true_idx = [i for i in range(len(ch)) if i != item.answer]
        for i, c in enumerate(ch):
            if i not in true_idx:
                exempt.extend(extract_numbers(c))
        for i in true_idx:
            add(extract_numbers(ch[i]), f"보기{i + 1}")
        add(stem_n, "발문")
        add(expl_n, "해설")
    elif item.type == "blank":
        add(stem_n, "지문")
        add(extract_numbers(str(item.answer)), "정답")
        add(expl_n, "해설")
    elif item.type == "order":
        add(stem_n, "지문")
        for i, c in enumerate(item.choices or []):
            add(extract_numbers(c), f"단계{i + 1}")
        add(expl_n, "해설")
    elif item.type == "calc":
        values, _ = calc_analysis(item)
        exempt.extend(stem_n)
        exempt_values.extend(n.value for n in stem_n)
        exempt_values.extend(values)
        exempt_values.append(float(item.answer))  # type: ignore[arg-type]
        add(expl_n, "해설")
    seen: set[tuple[str, float]] = set()
    uniq: list[tuple[Num, str]] = []
    for n, w in strict:
        if n.key() not in seen:
            seen.add(n.key())
            uniq.append((n, w))
    return Claims(uniq, exempt, exempt_values, notes)


# ---------------------------------------------------------------- 검증기

class Verifier:
    """법령 API(없으면 오프라인 항목만) + 시험 설정으로 문항 하나를 검증한다."""

    def __init__(self, api: LawApi | None, *, exam_date: date, today: date,
                 banned: list[str] | None = None, caution: list[str] | None = None,
                 crosscheck_required: int = 2):
        self.api = api
        self.exam_date = exam_date
        self.today = today
        self.banned = list(banned or [])
        self.caution = list(caution or [])
        self.crosscheck_required = crosscheck_required
        self._docs: dict[str, tuple[Any, LawDoc]] = {}
        self._versions: dict[tuple[str, str], LawDoc] = {}
        self._upcoming: dict[str, list[LawVersion]] = {}
        self._decrees: dict[str, LawDoc | None] = {}

    # ------------------------------------------------------------ 캐시된 조회

    def law(self, name: str) -> tuple[Any, LawDoc]:
        if name not in self._docs:
            assert self.api is not None
            self._docs[name] = self.api.law_by_name(name)
        return self._docs[name]

    def upcoming(self, law_id: str) -> list[LawVersion]:
        if law_id not in self._upcoming:
            assert self.api is not None
            self._upcoming[law_id] = self.api.upcoming(law_id)
        return self._upcoming[law_id]

    def version(self, v: LawVersion) -> LawDoc:
        key = (v.mst, v.effective_date.isoformat())
        if key not in self._versions:
            assert self.api is not None
            self._versions[key] = self.api.law_version(v.mst, v.effective_date)
        return self._versions[key]

    def decree(self, name: str) -> LawDoc | None:
        if name not in self._decrees:
            assert self.api is not None
            self._decrees[name] = self.api.decree(name)
        return self._decrees[name]

    # ------------------------------------------------------------ 전체

    def run(self, item: BankItem) -> ItemReport:
        rep = ItemReport(item.id, item.version)
        offline_checks(item, rep, self.banned, self.caution)
        if self.exam_date < self.today:
            rep.add("d_dates", "warn", f"exam.next_exam_date({self.exam_date})가 지났습니다 — channel.yaml 을 다음 회차로 바꾸세요")
        if any(o.check == "a_basis" and o.status == "fail" for o in rep.outcomes):
            self._crosscheck_field(item, rep)      # 근거가 불완전하면 원문 대조는 하지 않는다(이미 반려)
            return rep
        if self.api is None or not self.api.available:
            for c in ("b_quote", "c_numbers", "d_dates"):
                rep.add(c, "skip", "법령 API 를 쓸 수 없습니다(LAW_API_OC 없음)")
            self._crosscheck_field(item, rep)
            return rep
        try:
            self._online(item, rep)
        except (LawApiError, LawDocError) as e:
            # 키·네트워크 문제, 또는 API 응답을 해석하지 못함 → 문항 잘못이 아니므로 판정 보류
            rep.outcomes = [o for o in rep.outcomes if o.check not in ("b_quote", "c_numbers", "d_dates")]
            rep.sources, rep.updates, rep.alerts = [], [], []
            why = str(e) if isinstance(e, LawApiUnavailable) else f"법령 API 응답 해석 실패: {e}"
            for c in ("b_quote", "c_numbers", "d_dates"):
                rep.add(c, "skip", why[:200])
        self._crosscheck_field(item, rep)
        return rep

    def _crosscheck_field(self, item: BankItem, rep: ItemReport) -> None:
        from studio.verify.runner import crosscheck_state
        rep.llm_crosscheck = crosscheck_state(item, self.crosscheck_required)

    # ------------------------------------------------------------ 온라인 항목 (b c d)

    def _online(self, item: BankItem, rep: ItemReport) -> None:
        sources: list[Source] = []
        source_fail: list[str] = []
        date_fail: list[str] = []
        date_warn: list[str] = []
        date_ok: list[str] = []
        for i, b in item.law_bases():
            ref = ArticleRef.parse(b.article or "")
            try:
                _, doc = self.law(b.law_name or "")
            except LawApiUnavailable:
                raise
            except LawApiError as e:
                source_fail.append(f"근거{i + 1}: {e}")
                continue
            if b.law_id and b.law_id.lstrip("0") != doc.law_id.lstrip("0"):
                source_fail.append(f"근거{i + 1}: law_id {b.law_id} 가 '{doc.name}'({doc.law_id})와 다릅니다")
                continue
            use, via, vdate = doc, "현행", doc.effective_date
            assert b.effective_date is not None
            # 기준일에 시행 중인 버전: 기준일 이전 시행 버전 중 가장 늦은 것(현행 또는 시행예정)
            if doc.effective_date and b.effective_date > doc.effective_date:
                cands = [v for v in self.upcoming(doc.law_id) if v.effective_date <= b.effective_date]
                if cands:
                    v = cands[-1]
                    use, via, vdate = self.version(v), f"시행예정 {v.effective_date}", v.effective_date
                    use.mst = v.mst
                    date_warn.append(f"근거{i + 1} 은 아직 시행 전인 {v.effective_date} 시행 버전을 인용합니다")
            if vdate and vdate > self.exam_date:
                date_fail.append(f"근거{i + 1} 이 인용한 버전의 시행일 {vdate} 이 다음 시험일 {self.exam_date} 보다 늦습니다")
            loc = use.locate(ref)
            src = Source(i, b, ref, use, loc, vdate, via)
            sources.append(src)
            if not b.law_id:
                rep.updates.append((i, "law_id", None, doc.law_id))
            if ref.display() != b.article:
                rep.updates.append((i, "article", b.article, ref.display()))
            if vdate and b.effective_date != vdate:
                # 배지의 'YYYY.M.D 시행 기준'은 대조한 버전의 시행일로 맞춘다
                rep.updates.append((i, "effective_date", b.effective_date, vdate))
                date_ok.append(f"근거{i + 1} 기준일 {b.effective_date} → 대조한 버전 시행일 {vdate} 로 맞춤")
            # 시험 전 시행 예정 개정이 인용 조문을 바꾸는지
            if via == "현행" and loc.ok:
                for v in self.upcoming(doc.law_id):
                    if not (self.today < v.effective_date <= self.exam_date):
                        continue
                    newer = self.version(v).locate(ref)
                    changed = (not newer.ok) or normalize_quote(newer.text) != normalize_quote(loc.text)
                    if changed:
                        date_warn.append(f"{src.label} 이(가) {v.effective_date} 시행 개정으로 바뀝니다(시험 전)")
                        rep.alerts.append({"kind": "upcoming_amendment", "law_name": doc.name, "article": ref.display(),
                                           "effective_date": v.effective_date.isoformat()})
        rep.sources = sources

        # b 인용문
        qfail: list[str] = []
        for s in sources:
            if not s.located.ok:
                qfail.append(s.located.error)
                continue
            m = find_quote(s.basis.quote or "", s.located.text)
            if m.found:
                continue
            whole = s.located.article.full_text() if s.located.article else ""
            if whole and find_quote(s.basis.quote or "", whole).found:
                qfail.append(f"{s.label}: 인용문이 같은 조의 다른 항·호에 있습니다 — basis.article 을 고치세요")
            else:
                qfail.append(f"{s.label}: 원문에 없음(가장 비슷한 구간 유사도 {m.score:.2f}: '{m.snippet[:80]}')")
        pfail, ptexts = self._precedents(item)
        qfail.extend(pfail)
        if source_fail or qfail:
            rep.add("b_quote", "fail", "; ".join(source_fail + qfail))
        elif not sources and not ptexts:
            rep.add("b_quote", "ok", "대조할 법령·판례 인용 없음(이론·계산 문항)")
        else:
            rep.add("b_quote", "ok", f"근거 {len(sources) + len(ptexts)}건 원문 일치")

        # c 숫자
        self._numbers(item, rep, sources, ptexts)

        # d 시점
        for _, b in [(i, b) for i, b in enumerate(item.basis) if b.kind == "precedent"]:
            if b.decided_at and b.decided_at > self.today:
                date_fail.append(f"판례 {b.case_no} 선고일 {b.decided_at} 이 미래입니다")
        if date_fail:
            rep.add("d_dates", "fail", "; ".join(date_fail))
        elif date_warn:
            rep.add("d_dates", "warn", "; ".join(date_warn + date_ok))
        else:
            rep.add("d_dates", "ok", "; ".join(date_ok))

    def _precedents(self, item: BankItem) -> tuple[list[str], list[str]]:
        fails: list[str] = []
        texts: list[str] = []
        assert self.api is not None
        for i, b in enumerate(item.basis):
            if b.kind != "precedent" or not b.case_no:
                continue
            try:
                p = self.api.find_precedent(b.case_no)
            except LawApiUnavailable:
                raise
            except LawApiError as e:
                fails.append(f"판례 {b.case_no}: {e}")
                continue
            if p is None:
                fails.append(f"판례 {b.case_no} 를 찾지 못했습니다(사건번호 확인)")
                continue
            if b.decided_at and p.decided_at and b.decided_at != p.decided_at:
                fails.append(f"판례 {b.case_no} 선고일이 {p.decided_at} 인데 {b.decided_at} 로 적혀 있습니다")
                continue
            text = self.api.precedent_text(p.prec_id) if p.prec_id else ""
            texts.append(text)
            if b.quote:
                m = find_quote(b.quote, text)
                if not m.found:
                    fails.append(f"판례 {b.case_no}: 인용문이 판시사항·판결요지에 없음(유사도 {m.score:.2f}: '{m.snippet[:80]}')")
        return fails, texts

    def _numbers(self, item: BankItem, rep: ItemReport, sources: list[Source], ptexts: list[str]) -> None:
        claims = claim_numbers(item)
        if not claims.strict:
            rep.add("c_numbers", "ok", "; ".join(claims.notes) or "대조할 숫자 없음")
            self._trap_hint(item, rep, sources)
            return
        unit_pool = [n for s in sources for n in extract_numbers(s.located.text)]
        art_pool = [n for s in sources if s.located.article for n in extract_numbers(s.located.article.full_text())]
        prec_pool = [n for t in ptexts for n in extract_numbers(t)]
        fails: list[str] = []
        warns: list[str] = list(claims.notes)
        decree_cache: list[tuple[str, list[Num]]] | None = None
        for n, where in claims.strict:
            if contains(unit_pool, n):
                continue
            if contains(art_pool, n):
                warns.append(f"{where} '{n.label()}' 는 인용한 항·호가 아닌 같은 조의 다른 부분에 있습니다")
                continue
            if contains(prec_pool, n):
                continue
            if decree_cache is None:
                decree_cache = self._decree_pools(sources)
            hit = next((lab for lab, pool in decree_cache if contains(pool, n)), None)
            if hit:
                warns.append(f"{where} '{n.label()}' 는 {hit} 에 있습니다 — 그 조문을 basis 에 추가하세요")
                continue
            delegated = any(s.located.article and s.located.article.delegates() for s in sources)
            if delegated:
                warns.append(f"{where} '{n.label()}' 를 인용 조문에서 못 찾았고 위임 법령(대통령령 등)에서도 확인하지 못했습니다 — 위임 조문을 basis 에 추가하세요")
            elif ptexts and not sources:
                warns.append(f"{where} '{n.label()}' 를 판례 요지에서 확인하지 못했습니다(판결 이유에 있을 수 있음)")
            else:
                fails.append(f"{where} '{n.label()}' 가 인용 조문에 없습니다")
        if fails:
            rep.add("c_numbers", "fail", "; ".join(fails + warns))
        elif warns:
            rep.add("c_numbers", "warn", "; ".join(warns))
        else:
            rep.add("c_numbers", "ok", f"숫자 {len(claims.strict)}개 일치")
        self._trap_hint(item, rep, sources)

    def _trap_hint(self, item: BankItem, rep: ItemReport, sources: list[Source]) -> None:
        if item.type != "ox" or item.answer is not False or item.trap != "숫자바꿈":
            return
        stem_n = extract_numbers(item.stem)
        pool = [n for s in sources if s.located.article for n in extract_numbers(s.located.article.full_text())]
        if not stem_n:
            rep.add("c_numbers", "warn", "숫자바꿈 함정인데 지문에 숫자가 없습니다")
        elif all(contains(pool, n) for n in stem_n):
            rep.add("c_numbers", "warn", "숫자바꿈 함정인데 지문 숫자가 모두 원문에 있습니다 — 함정 유형을 확인하세요")

    def _decree_pools(self, sources: list[Source]) -> list[tuple[str, list[Num]]]:
        """인용 조문(법 제N조)을 언급하는 시행령 조문들의 숫자."""
        out: list[tuple[str, list[Num]]] = []
        for s in sources:
            if s.ref.is_appendix or s.doc.name.endswith(("시행령", "시행규칙")):
                continue
            dec = self.decree(s.doc.name)
            if dec is None:
                continue
            pat = re.compile(rf"법\s*{re.escape(s.ref.article_label())}(?![의\d])")
            for a in dec.articles:
                txt = a.full_text()
                if pat.search(txt):
                    out.append((f"{dec.name} {a.label}", extract_numbers(txt)))
        return out


def summarize(rep: ItemReport) -> list[str]:
    return [o.line() for o in rep.outcomes if o.status != "ok" or o.detail]


def article_of(src: Source) -> Article | None:
    return src.located.article

"""검증 항목 a~g 와 verify 상태 전이(draft → verified / rejected / 보류). 법령 API 는 responses 로 흉내 낸다."""

from __future__ import annotations

import json
from datetime import date

import pytest
import responses

from studio.bank.models import BankItem, NewItem
from studio.bank.store import load_bank
from studio.verify.checks import ItemReport, Verifier, check_answer, check_basis, check_phrases, claim_numbers
from studio.verify.law_api import LawApi
from test_bank_helpers import NOW, LawServer, bank_main, fx, setup_root


@pytest.fixture
def root(tmp_path, monkeypatch):
    return setup_root(tmp_path, monkeypatch)


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


def _mk(row: dict, n: int) -> BankItem:
    from studio.bank.models import subject_code
    new = NewItem.model_validate(row)
    return new.to_item(f"Q-{subject_code(new.subject)}-{n:08x}", now=NOW)


ROWS = fx("bank_new_items.json")


def _added(root) -> dict[str, BankItem]:
    p = root / "new.json"
    p.write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    assert bank_main(["add", "--file", str(p)]) == 0
    bank = load_bank()
    by_unit = {}
    for it in bank.all():
        by_unit.setdefault(it.unit, []).append(it)
    return {f"{it.unit}#{i}": it for u, its in by_unit.items() for i, it in enumerate(its)}


# ---------------------------------------------------------------- 오프라인 항목

def test_basis_fields_required():
    it = _mk(ROWS[0], 1)
    it.basis[0].quote = None
    rep = ItemReport(it.id, 1)
    check_basis(it, rep)
    assert rep.outcomes[-1].status == "fail" and "quote" in rep.outcomes[-1].detail
    it2 = _mk(ROWS[5], 2)
    it2.basis[0].decided_at = None
    rep2 = ItemReport(it2.id, 1)
    check_basis(it2, rep2)
    assert rep2.outcomes[-1].status == "fail" and "decided_at" in rep2.outcomes[-1].detail
    it3 = _mk(ROWS[0], 3)
    it3.basis[0].article = "33조 1항"
    rep3 = ItemReport(it3.id, 1)
    check_basis(it3, rep3)
    assert rep3.outcomes[-1].status == "fail" and "조문 표기" in rep3.outcomes[-1].detail


def test_theory_calc_item_without_law_basis_is_warning_only():
    it = _mk(ROWS[6], 4)
    rep = ItemReport(it.id, 1)
    check_basis(it, rep)
    assert rep.outcomes[-1].status == "warn"
    it.subject = "BRK"
    it.type = "ox"
    it.answer = True
    rep2 = ItemReport(it.id, 1)
    check_basis(it, rep2)
    assert rep2.outcomes[-1].status == "fail"


def test_answer_format_checks():
    blank = _mk(ROWS[3], 5)
    blank.stem = "전속중개계약의 유효기간은 3개월이다."
    rep = ItemReport(blank.id, 1)
    check_answer(blank, rep)
    assert rep.outcomes[-1].status == "fail" and "빈칸" in rep.outcomes[-1].detail
    mcq = _mk(ROWS[2], 6)
    mcq.answer = 7
    rep = ItemReport(mcq.id, 1)
    check_answer(mcq, rep)
    assert rep.outcomes[-1].status == "fail" and "범위" in rep.outcomes[-1].detail


def test_banned_and_caution_phrases():
    it = _mk(ROWS[0], 7)
    it.explanation = "이것만 알면 합격 보장! 공식처럼 외우자."
    rep = ItemReport(it.id, 1)
    check_phrases(it, rep, ["합격 보장"], ["공식"])
    assert rep.outcomes[-1].status == "fail" and "합격 보장" in rep.outcomes[-1].detail
    it.explanation = "공식처럼 외우자. 36회 기출 지문이다."
    rep = ItemReport(it.id, 1)
    check_phrases(it, rep, ["합격 보장"], ["공식"])
    assert rep.outcomes[-1].status == "warn" and "기출 회차" in rep.outcomes[-1].detail


def test_claim_numbers_by_type():
    false_ox = _mk(ROWS[1], 8)            # 지문 5년(틀린 숫자) → 면제, 해설 3년 → 대조
    c = claim_numbers(false_ox)
    assert [n.label() for n, _ in c.strict] == ["3년"]
    mcq = _mk(ROWS[2], 9)
    mcq.choices = ["3년", "5년", "10년", "1년", "2년"]
    mcq.stem = "다음 중 옳은 것은?"
    mcq.answer = 0
    mcq.explanation = "3년이 맞다."
    c = claim_numbers(mcq)
    assert {n.label() for n, _ in c.strict} == {"3년"}
    calc = _mk(ROWS[6], 10)
    c = claim_numbers(calc)
    assert c.strict == []                 # 2천만원(주어진 값)·4억원(정답) 모두 면제


# ---------------------------------------------------------------- 온라인 검증 + 상태 전이

def test_verify_transitions_and_report(root, rsps):
    LawServer(rsps)
    items = _added(root)
    code = bank_main(["verify"])
    assert code == 2                                     # 반려가 하나 있다(인용문 틀림)
    bank = load_bank()
    st = {it.unit + ("" if it.type != "ox" else f"/{it.answer}") + f"/{it.trap}": it.status for it in bank.all()}
    by = {it.stem[:12]: it for it in bank.all()}
    ok = [it for it in bank.all() if it.status == "verified"]
    bad = [it for it in bank.all() if it.status == "rejected"]
    assert len(ok) == 7 and len(bad) == 1, st
    assert "빌려" in bad[0].stem
    verdict = [e for e in bad[0].verify_log if e.check == "verdict"][-1]
    assert not verdict.ok and "b_quote" in verdict.detail and "유사도" in verdict.detail
    # 기준일이 현행보다 이르면 검증 뒤 현행 시행일로 갱신, law_id·조문 표기 정리
    disq = next(it for it in ok if it.unit == "등록의 결격사유")
    assert disq.basis[0].effective_date == date(2026, 1, 1)
    assert disq.basis[0].law_id == "001823"
    assert disq.basis[0].article == "제10조 제1항 제4호"
    # 개정 감지용 본문 해시 기록
    assert any(e.check == "source_hash" and e.detail.startswith("0|001823|제10조 제1항 제4호|") for e in disq.verify_log)
    # 위임 숫자(3개월)는 시행령 제20조에서 찾아 경고로 통과
    blank = next(it for it in ok if it.type == "blank")
    c_log = [e for e in blank.verify_log if e.check == "c_numbers"][-1]
    assert c_log.ok and "시행령 제20조" in c_log.detail
    # 시험 전(10-20) 시행 예정 개정이 제33조 제1항 제6호를 바꾼다 → 경고 + upcoming 기록
    direct = next(it for it in ok if it.unit == "금지행위")
    assert any(e.check == "upcoming" and e.detail.startswith("2026-10-20|공인중개사법|제33조 제1항 제6호") for e in direct.verify_log)
    # 판례: 사건번호·선고일·판결요지 인용 확인
    prec = next(it for it in ok if it.subject == "CIV")
    assert [e for e in prec.verify_log if e.check == "b_quote"][-1].ok
    rep = json.loads((root / "out" / "bank-verify" / "latest.json").read_text(encoding="utf-8"))
    assert rep["counts"] == {"verified": 7, "rejected": 1}
    row = next(r for r in rep["items"] if r["id"] == direct.id)
    assert row["llm_crosscheck"] == {"required": 2, "done": 0, "status": "pending"}
    assert row["alerts"][0]["effective_date"] == "2026-10-20"
    del by, items


def test_verify_without_key_keeps_draft(root, monkeypatch, rsps):
    monkeypatch.delenv("LAW_API_OC")
    _added(root)
    code = bank_main(["verify"])
    assert code == 3
    bank = load_bank()
    assert {it.status for it in bank.all()} == {"draft"}
    last = [e for e in bank.all()[0].verify_log if e.check == "verdict"][-1]
    assert last.detail.startswith("pending:")


def test_offline_uses_cache_only(root, rsps):
    server = LawServer(rsps)
    _added(root)
    assert bank_main(["verify", "--subject", "BRK"]) == 2
    n_calls = len(server.requests)
    assert n_calls > 0
    # 같은 문항을 오프라인으로 다시(rejected 를 제외한 verified 재검증) — HTTP 없이 캐시만
    assert bank_main(["verify", "--status", "verified", "--offline"]) == 0
    assert len(server.requests) == n_calls


def test_future_effective_date_after_exam_fails(root, rsps):
    LawServer(rsps)
    api = LawApi("oc", today=NOW.date(), min_interval=0)
    v = Verifier(api, exam_date=date(2026, 10, 31), today=NOW.date())
    it = _mk(ROWS[0], 11)
    it.basis[0].effective_date = date(2027, 7, 1)          # 그날 시행 중인 버전 = 2027-06-01 시행(시험 뒤)
    rep = v.run(it)
    d = next(o for o in rep.outcomes if o.check == "d_dates")
    assert d.status == "fail" and "2027-06-01" in d.detail and "시험일" in d.detail
    assert rep.verdict == "rejected"


def test_basis_date_is_aligned_to_version_in_force(root, rsps):
    LawServer(rsps)
    api = LawApi("oc", today=NOW.date(), min_interval=0)
    v = Verifier(api, exam_date=date(2026, 10, 31), today=NOW.date())
    for when, expected in ((date(2026, 6, 1), date(2026, 1, 1)),      # 현행(2026-01-01)이 그날 시행 중
                           (date(2019, 1, 1), date(2026, 1, 1)),      # 옛 날짜 → 대조한 현행 시행일로
                           (date(2026, 10, 25), date(2026, 10, 20))): # 시행예정 버전
        it = _mk(ROWS[2], 15)
        it.basis[0].effective_date = when
        rep = v.run(it)
        assert rep.verdict == "verified", [o.line() for o in rep.outcomes]
        upd = [u for u in rep.updates if u[1] == "effective_date"]
        assert upd and upd[0][3] == expected


def test_cited_upcoming_version_is_used(root, rsps):
    LawServer(rsps)
    api = LawApi("oc", today=NOW.date(), min_interval=0)
    v = Verifier(api, exam_date=date(2026, 10, 31), today=NOW.date())
    row = dict(ROWS[4])
    row["basis"] = [{**ROWS[4]["basis"][0], "effective_date": "2026-10-20",
                     "quote": "거래당사자 쌍방을 대리하는 행위(제3자를 통하여 하는 경우를 포함한다)"}]
    it = _mk(row, 12)
    rep = v.run(it)
    assert rep.verdict == "verified", [o.line() for o in rep.outcomes]
    assert rep.sources[0].via.startswith("시행예정")


def test_wrong_hang_hint(root, rsps):
    LawServer(rsps)
    api = LawApi("oc", today=NOW.date(), min_interval=0)
    v = Verifier(api, exam_date=date(2026, 10, 31), today=NOW.date())
    it = _mk(ROWS[0], 13)
    it.basis[0].article = "제7조 제2항"          # 인용문은 제1항에 있다
    rep = v.run(it)
    b = next(o for o in rep.outcomes if o.check == "b_quote")
    assert b.status == "fail" and "다른 항" in b.detail


def test_number_not_in_law_fails(root, rsps):
    LawServer(rsps)
    api = LawApi("oc", today=NOW.date(), min_interval=0)
    v = Verifier(api, exam_date=date(2026, 10, 31), today=NOW.date())
    it = _mk(ROWS[0], 14)
    it.explanation = "공인중개사법 제7조 제1항 — 위반하면 2년 이내 자격정지라는 식의 숫자를 지어내면 안 된다."
    rep = v.run(it)
    c = next(o for o in rep.outcomes if o.check == "c_numbers")
    assert c.status == "fail" and "2년" in c.detail

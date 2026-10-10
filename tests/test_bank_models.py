"""문항 규격·저장(jsonl)·중복 탐지·add/update/mark-used/crosscheck 명령."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from studio.bank import store
from studio.bank.dedupe import DupIndex, jaccard, normalize_stem, trigrams
from studio.bank.models import BankItem, Basis, NewItem, make_id, subject_code
from studio.bank.store import BankError, editing, load_bank, subject_file
from studio.verify.runner import AnswerParseError, crosscheck_state, parse_answer, record_crosscheck
from test_bank_helpers import NOW, bank_main, fx, setup_root

ROWS = fx("bank_new_items.json")


@pytest.fixture
def root(tmp_path, monkeypatch):
    return setup_root(tmp_path, monkeypatch)


def mk(row: dict, item_id: str = "Q-BRK-00000001") -> BankItem:
    return NewItem.model_validate(row).to_item(item_id, now=NOW)


def write_rows(root, rows, name="new.json"):
    p = root / name
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- 모델

def test_subject_aliases():
    assert subject_code("민법") == "CIV" and subject_code("부동산공법") == "PUB" and subject_code("tax") == "TAX"
    assert subject_code("공인중개사법령 및 중개실무") == "BRK" and subject_code("학개론") == "RE"
    with pytest.raises(ValueError):
        subject_code("영어")


def test_item_shape_and_defaults():
    it = mk(ROWS[0])
    assert it.subject == "BRK" and it.status == "draft" and it.version == 1
    assert it.created_week == "2026-W41" and it.updated_at == NOW
    assert it.badges() == ["[공인중개사법 제7조 제1항 · 2026.1.1 시행 기준]"]
    with pytest.raises(ValidationError, match="과목코드"):
        mk(ROWS[0], "Q-CIV-00000001")
    with pytest.raises(ValidationError, match="id 형식"):
        mk(ROWS[0], "Q-BRK-XYZ")
    with pytest.raises(ValidationError):
        BankItem.model_validate({**mk(ROWS[0]).model_dump(), "extra_field": 1})   # extra=forbid


@pytest.mark.parametrize("patch,msg", [
    ({"type": "ox", "answer": 1}, "true/false"),
    ({"type": "mcq", "answer": True, "choices": ["a", "b"]}, "인덱스"),
    ({"type": "mcq", "answer": 5, "choices": ["a", "b", "c", "d", "e"]}, "범위"),
    ({"type": "mcq", "answer": 0, "choices": ["a", "b", "c", "d", "e", "f"]}, "2~5"),
    ({"type": "mcq", "answer": 0, "choices": ["a", "a"]}, "같은 보기"),
    ({"type": "order", "answer": [0, 0, 1], "choices": ["a", "b", "c"]}, "순열"),
    ({"type": "blank", "answer": ""}, "문자열"),
    ({"type": "calc", "answer": "12"}, "숫자"),
    ({"type": "ox", "answer": True, "choices": ["a", "b"]}, "choices"),
])
def test_answer_shape_errors(patch, msg):
    with pytest.raises(ValidationError, match=msg):
        mk({**ROWS[0], **patch})


def test_badges():
    assert Basis(kind="precedent", case_no="2019다299999", decided_at=date(2020, 5, 14)).badge() == "[대법원 2019다299999 · 2020.5.14 선고]"
    assert Basis(kind="precedent", case_no="2019헌바123", decided_at=date(2020, 5, 14)).badge() == "[헌법재판소 2019헌바123 · 2020.5.14 결정]"
    assert Basis(kind="precedent", case_no="2019나12345", decided_at=date(2020, 5, 14)).badge() == "[2019나12345 · 2020.5.14 선고]"
    assert Basis(kind="calc", note="직접환원법").badge() == "[계산 · 직접환원법]"


def test_make_id_deterministic():
    a = make_id("BRK", "공인중개사는  자격증을 대여할 수 없다.")
    assert a == make_id("중개사법", "공인중개사는 자격증을 대여할 수 없다")
    assert a != make_id("BRK", "공인중개사는 자격증을 대여할 수 없다", salt=1)
    assert a.startswith("Q-BRK-") and len(a) == 14


# ---------------------------------------------------------------- 저장

def test_store_roundtrip_sorted_and_minimal_writes(root):
    with editing() as bank:
        for i, row in enumerate(ROWS[:3]):
            bank.put(mk(row, f"Q-BRK-0000000{3 - i}"))
    lines = subject_file("BRK").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["id"] for x in lines] == ["Q-BRK-00000001", "Q-BRK-00000002", "Q-BRK-00000003"]
    assert not subject_file("CIV").exists()                  # 빈 과목 파일은 만들지 않는다
    assert '"choices"' not in lines[1] or "mcq" in lines[1]  # None 필드는 생략
    mtime = subject_file("BRK").stat().st_mtime_ns
    with editing() as bank:
        bank.require("Q-BRK-00000001")                       # 바꾼 것 없음 → 다시 쓰지 않는다
    assert subject_file("BRK").stat().st_mtime_ns == mtime
    assert len(load_bank().all()) == 3


def test_store_reports_broken_line(root):
    p = subject_file("TAX")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"id": "Q-TAX-00000001"}\n', encoding="utf-8")
    with pytest.raises(BankError, match=r"TAX\.jsonl:1"):
        load_bank()


def test_trim_log_keeps_needed_entries(root):
    it = mk(ROWS[0])
    it.log("source_hash", True, "0|001823|제7조 제1항|abcd|2026-01-01")
    it.log("llm_crosscheck", True, "v1 by=claude answer=O")
    for i in range(100):
        it.log("b_quote", True, f"{i}")
    store.trim_log(it, keep=10)
    checks = [e.check for e in it.verify_log]
    assert checks.count("b_quote") == 10 and "source_hash" in checks and "llm_crosscheck" in checks


# ---------------------------------------------------------------- 중복

def test_dedupe_normalization_and_threshold():
    assert normalize_stem("「공인중개사」는 자격증을 · 대여할 수 없다!") == "공인중개사는자격증을대여할수없다"
    a = "공인중개사는 다른 사람에게 자기의 공인중개사자격증을 양도하거나 대여해서는 안 된다."
    b = "공인중개사는 다른 사람에게 자기 공인중개사자격증을 양도하거나 대여해서는 안 된다."   # 조사 하나 차이(0.85)
    d = "공인중개사는 타인에게 자기의 공인중개사자격증을 양도하거나 대여해서는 안 된다."        # 낱말 바꿈(0.72)
    c = "누구든지 다른 사람의 공인중개사자격증을 빌려 사용해서는 안 된다."
    assert jaccard(trigrams(a), trigrams(b)) >= 0.8
    assert 0.7 < jaccard(trigrams(a), trigrams(d)) < 0.8
    assert jaccard(trigrams(a), trigrams(c)) < 0.5
    idx = DupIndex(0.8)
    idx.add("Q-BRK-00000001", a)
    assert idx.duplicate_of(b).other_id == "Q-BRK-00000001"
    assert idx.duplicate_of(c) is None
    assert idx.duplicate_of(a, exclude="Q-BRK-00000001") is None


# ---------------------------------------------------------------- 명령

def test_add_assigns_ids_and_rejects(root, capsys):
    bad = [
        {**ROWS[0], "stem": "공인중개사는 다른 사람에게 자기 공인중개사자격증을 양도하거나 대여해서는 안 된다."},  # 0번과 중복
        {**ROWS[2], "explanation": "이 문항만 외우면 합격 보장"},                                            # 금지 표현
        {**ROWS[0], "stem": "근거가 빠진 문항입니다.", "basis": [{"kind": "law", "law_name": "공인중개사법"}]},     # 근거 누락
        {**ROWS[0], "subject": "영어"},                                                                      # 형식 오류
    ]
    code = bank_main(["add", "--file", write_rows(root, [ROWS[0], *bad]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert len(out["added"]) == 1 and len(out["rejected"]) == 4
    reasons = {r["row"]: " ".join(r["reasons"]) for r in out["rejected"]}
    assert "중복" in reasons[2] and "합격 보장" in reasons[3] and "빈 필드" in reasons[4] and "과목" in reasons[5]
    bank = load_bank()
    assert [i.id for i in bank.all()] == [out["added"][0]["id"]]
    assert bank.all()[0].status == "draft"


def test_add_dry_run_and_explicit_id(root):
    assert bank_main(["add", "--file", write_rows(root, ROWS[:2]), "--dry-run"]) == 0
    assert load_bank().all() == []
    row = {**ROWS[0], "id": "Q-BRK-0000abcd", "status": "verified"}
    assert bank_main(["add", "--file", write_rows(root, [row])]) == 0
    it = load_bank().require("Q-BRK-0000abcd")
    assert it.status == "draft"                                  # 입력 status 는 무시
    assert bank_main(["add", "--file", write_rows(root, [{**ROWS[1], "id": "Q-BRK-0000abcd"}])]) == 2


def test_update_bumps_version(root):
    bank_main(["add", "--file", write_rows(root, ROWS[:1])])
    it = load_bank().all()[0]
    with editing() as bank:
        bank.require(it.id).status = "verified"
    fixed = {**ROWS[0], "id": it.id, "explanation": "제7조 제1항: 자격증 양도·대여 금지(고친 해설)."}
    assert bank_main(["update", "--file", write_rows(root, [fixed])]) == 0
    after = load_bank().require(it.id)
    assert after.version == 2 and after.status == "draft" and "고친 해설" in after.explanation
    assert after.created_week == it.created_week
    assert [e for e in after.verify_log if e.check == "update"][0].detail.startswith("v1 → v2")
    assert bank_main(["update", "--file", write_rows(root, [{**fixed, "subject": "CIV"}])]) == 2


def test_mark_used(root):
    bank_main(["add", "--file", write_rows(root, ROWS[:2])])
    ids = [i.id for i in load_bank().all()]
    assert bank_main(["mark-used", "--asset", "2026W42-S03", "--items", *ids]) == 1      # draft 는 거부
    assert bank_main(["mark-used", "--asset", "2026W42-S03", "--items", ids[0], "--force"]) == 0
    assert bank_main(["mark-used", "--asset", "S03", "--items", ids[0]]) == 1             # ID 형식 오류
    with editing() as bank:
        bank.require(ids[1]).status = "verified"
    assert bank_main(["mark-used", "--asset", "2026W42-B01", "--items", f"{ids[1]},{ids[0]}", "--force"]) == 0
    assert load_bank().require(ids[0]).used_in == ["2026W42-B01", "2026W42-S03"]


# ---------------------------------------------------------------- crosscheck

@pytest.mark.parametrize("row,text,expected", [
    (0, "O", True), (0, "x", False), (0, "참", True),
    (2, "④", 3), (2, "4번", 3), (2, "3", 3),
    (3, "3 개월", "3 개월"),
])
def test_parse_answer(row, text, expected):
    assert parse_answer(mk(ROWS[row]), text) == expected


def test_parse_answer_errors():
    with pytest.raises(AnswerParseError):
        parse_answer(mk(ROWS[0]), "아마도")
    with pytest.raises(AnswerParseError):
        parse_answer(mk(ROWS[2]), "⑥")


def test_crosscheck_counts_and_rejects():
    it = mk(ROWS[2])
    it.status = "verified"
    assert crosscheck_state(it, 2)["status"] == "pending"
    assert record_crosscheck(it, "④", "claude")["ok"]
    assert record_crosscheck(it, "3", "claude")["state"] == {"required": 2, "done": 2, "status": "passed"}
    assert it.status == "verified"
    it.version = 2                                    # 내용이 바뀌면 이전 확인은 세지 않는다
    assert crosscheck_state(it, 2)["done"] == 0
    res = record_crosscheck(it, "②", "claude")
    assert not res["ok"] and it.status == "rejected"
    blank = mk(ROWS[3])
    assert record_crosscheck(blank, "삼개월", "claude")["ok"] is False
    assert record_crosscheck(mk(ROWS[3]), "3개월", "claude")["ok"]
    calc = NewItem.model_validate(ROWS[6]).to_item("Q-RE-00000001", now=NOW)
    assert crosscheck_state(calc, 2)["status"] == "n/a"
    assert record_crosscheck(calc, "4억", "claude")["ok"]


def test_crosscheck_command(root, capsys):
    bank_main(["add", "--file", write_rows(root, ROWS[:1])])
    it = load_bank().all()[0]
    assert bank_main(["crosscheck", "--id", it.id, "--answer", "O", "--by", "claude"]) == 0
    assert bank_main(["crosscheck", "--id", it.id, "--answer", "모름"]) == 1
    assert bank_main(["crosscheck", "--id", it.id, "--answer", "X"]) == 2
    assert load_bank().require(it.id).status == "rejected"
    assert bank_main(["show", "--id", it.id, "--blind"]) == 0
    shown = capsys.readouterr().out
    assert it.stem in shown and it.explanation not in shown

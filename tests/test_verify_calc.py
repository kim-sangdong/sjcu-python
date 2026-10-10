"""계산 문항 안전 파서 — 허용 연산은 계산하고, 그 밖의 구문(악성 입력)은 계산 전에 거부한다."""

from __future__ import annotations

import time

import pytest

from studio.bank.models import NewItem
from studio.verify.calc import CalcError, calc_lines, evaluate, strip_calc_lines
from studio.verify.checks import ItemReport, check_calc
from test_bank_helpers import NOW


@pytest.mark.parametrize("expr,value", [
    ("300000000 * 40 / 1000", 12_000_000),
    ("300_000_000*0.4/100", 1_200_000),
    ("20000000 / 0.05", 400_000_000),
    ("floor(523456789 * 0.004 / 10) * 10", 2_093_820),
    ("round(2.5)", 3),                          # 사사오입
    ("round(1234.5678, 2)", 1234.57),
    ("round(1234567, -3)", 1_235_000),
    ("max(100, 250, 30) - min(3, 4)", 247),
    ("-(3 - 10) // 2", 3),
    ("ceil(10 / 3)", 4),
    ("trunc(-2.7)", -2),
    ("abs(-5)", 5),
])
def test_evaluate(expr, value):
    assert evaluate(expr).value == pytest.approx(value)


def test_steps_are_recorded():
    r = evaluate("(100 - 20) * 0.5")
    assert 80 in r.steps and r.value == 40
    assert 100 in r.constants


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo hi')",
    "().__class__.__bases__[0].__subclasses__()",
    "(1).__class__",
    "open('/etc/passwd').read()",
    "lambda: 1",
    "9**9**9",
    "2 ** 10",
    "x + 1",
    "[1, 2, 3]",
    "'1' + '2'",
    "1 if 1 else 2",
    "1 < 2",
    "1j * 2",
    "1e400",
    "99999999 * 99999999 * 99999999",
    "300,000,000 * 0.04",
    "min(1,234)",
    "round(1.5, ndigits=1)",
    "eval('1+1')",
    "max()",
    "1 / 0",
    "(" * 200 + "1" + ")" * 200,
    "1" + " + 1" * 200,
    "",
])
def test_rejects_malicious_or_unsupported(expr):
    t0 = time.time()
    with pytest.raises(CalcError):
        evaluate(expr)
    assert time.time() - t0 < 1.0


def test_calc_lines_and_strip():
    text = "세율은 1천분의 40이다.\ncalc: 300000000 * 40 / 1000\n계산식: 12000000"
    assert calc_lines(text) == ["300000000 * 40 / 1000", "12000000"]
    assert "calc" not in strip_calc_lines(text) and "1천분의 40" in strip_calc_lines(text)


def _calc_item(explanation: str, answer: float):
    return NewItem.model_validate({
        "subject": "RE", "unit": "수익환원법", "type": "calc",
        "stem": "순영업소득 2천만원, 환원이율 5%일 때 수익가액은?", "answer": answer,
        "explanation": explanation, "trap": "기타", "difficulty": 2,
        "basis": [{"kind": "calc", "note": "직접환원법"}],
    }).to_item("Q-RE-0000abcd", now=NOW)


def test_check_calc_matches_answer():
    rep = ItemReport("x", 1)
    check_calc(_calc_item("2천만원 ÷ 0.05\ncalc: 20000000 / 0.05", 400_000_000), rep)
    assert rep.outcomes[-1].status == "ok"
    rep = ItemReport("x", 1)
    check_calc(_calc_item("calc: 20000000 / 0.05", 40_000_000), rep)
    assert rep.outcomes[-1].status == "fail" and "다릅니다" in rep.outcomes[-1].detail
    rep = ItemReport("x", 1)
    check_calc(_calc_item("식이 없다", 400_000_000), rep)
    assert rep.outcomes[-1].status == "fail" and "calc:" in rep.outcomes[-1].detail
    rep = ItemReport("x", 1)
    check_calc(_calc_item("calc: __import__('os')", 1), rep)
    assert rep.outcomes[-1].status == "fail"

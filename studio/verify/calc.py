"""계산 문항 검산 — explanation 의 `calc:` 줄을 안전하게 계산한다(eval 을 쓰지 않는다).

표기 규칙(문항 작성자가 지킬 것)
  - 해설 안에 한 줄로 `calc: <식>` (또는 `계산식: <식>`). 여러 줄이면 마지막 줄이 정답과 같아야 하고, 앞줄은 중간 단계.
  - 숫자: 123, 1.5, 300_000_000 (쉼표 금지 — 300,000,000 은 거부). 단위·한글 금지.
  - 연산: + - * / // ( ), 단항 -.  거듭제곱(**)·비교·변수·속성·문자열·리스트는 거부.
  - 함수: floor(x), ceil(x), round(x[, 자릿수]), trunc(x), min(a, b, …), max(a, b, …), abs(x)
          round 는 사사오입(0.5 → 1). 원 단위 절사는 floor(x / 10) * 10 처럼 쓴다.
  - 법정 상수(세율 등)는 해설 본문에 조문 표기 그대로(예: '1천분의 40') 함께 적는다 — 숫자 대조 검사 대상이다.
  예) calc: 300000000 * 40 / 1000          → 12000000
      calc: floor(523456789 * 0.004 / 10) * 10
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CALC_LINE_RE = re.compile(r"^\s*(?:calc|계산식)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
MAX_LEN = 300
MAX_NODES = 120
MAX_ABS = 1e16          # 중간값 상한(원 단위 1경) — 넘으면 잘못 쓴 식으로 본다
MAX_ARGS = 10


class CalcError(ValueError):
    pass


def _round_half_up(x: float, ndigits: int = 0) -> float:
    q = Decimal(1).scaleb(-int(ndigits))
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


FUNCS = {
    "floor": (1, 1, lambda x: float(math.floor(x))),
    "ceil": (1, 1, lambda x: float(math.ceil(x))),
    "trunc": (1, 1, lambda x: float(math.trunc(x))),
    "abs": (1, 1, lambda x: abs(x)),
    "round": (1, 2, lambda x, n=0: _round_half_up(x, int(n))),
    "min": (2, MAX_ARGS, lambda *a: min(a)),
    "max": (2, MAX_ARGS, lambda *a: max(a)),
}


@dataclass
class CalcResult:
    value: float
    steps: list[float] = field(default_factory=list)   # 모든 중간값(숫자 대조에서 '계산된 값'으로 면제)
    constants: list[float] = field(default_factory=list)


def evaluate(expr: str) -> CalcResult:
    """화이트리스트 AST 계산. 허용하지 않은 구문이면 CalcError."""
    if not isinstance(expr, str):
        raise CalcError("식이 문자열이 아닙니다")
    src = expr.strip()
    if not src:
        raise CalcError("빈 식")
    if len(src) > MAX_LEN:
        raise CalcError(f"식이 너무 깁니다({len(src)}자 > {MAX_LEN})")
    if re.search(r",\d{3}(?![\d.])", src):
        raise CalcError("숫자에 쉼표를 쓰지 않습니다(300000000 또는 300_000_000). 함수 인자 쉼표 뒤에는 공백을 둡니다")
    if not re.fullmatch(r"[0-9_.+\-*/()\s,a-z]*", src):
        raise CalcError("허용하지 않는 글자가 있습니다(숫자·+-*/()·함수 이름만)")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"식 문법 오류: {e.msg}") from e
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise CalcError(f"식이 너무 복잡합니다(노드 {len(nodes)} > {MAX_NODES})")
    res = CalcResult(0.0)
    res.value = _eval(tree.body, res)
    return res


def _check(v: float) -> float:
    if not math.isfinite(v) or abs(v) > MAX_ABS:
        raise CalcError("계산 값이 범위를 벗어납니다")
    return v


def _eval(node: ast.AST, res: CalcResult) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("숫자 외 상수는 쓸 수 없습니다")
        v = _check(float(node.value))
        res.constants.append(v)
        return v
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, res)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)):
        a, b = _eval(node.left, res), _eval(node.right, res)
        if isinstance(node.op, (ast.Div, ast.FloorDiv)) and b == 0:
            raise CalcError("0 으로 나눌 수 없습니다")
        op = node.op
        v = (a + b if isinstance(op, ast.Add) else a - b if isinstance(op, ast.Sub) else a * b if isinstance(op, ast.Mult)
             else a / b if isinstance(op, ast.Div) else float(a // b))
        v = _check(v)
        res.steps.append(v)
        return v
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            name = getattr(node.func, "id", type(node.func).__name__)
            raise CalcError(f"허용하지 않는 함수: {name}")
        if node.keywords:
            raise CalcError("함수에 이름 붙은 인자는 쓸 수 없습니다")
        lo, hi, fn = FUNCS[node.func.id]
        if not (lo <= len(node.args) <= hi):
            raise CalcError(f"{node.func.id} 인자 수가 맞지 않습니다")
        args = [_eval(a, res) for a in node.args]
        if node.func.id == "round" and len(args) == 2 and (not float(args[1]).is_integer() or abs(args[1]) > 12):
            raise CalcError("round 자릿수는 -12~12 정수")
        v = _check(float(fn(*args)))
        res.steps.append(v)
        return v
    raise CalcError(f"허용하지 않는 구문: {type(node).__name__}")


def calc_lines(explanation: str) -> list[str]:
    return [m.group(1) for m in CALC_LINE_RE.finditer(explanation or "")]


def strip_calc_lines(explanation: str) -> str:
    return CALC_LINE_RE.sub(" ", explanation or "")


def close_enough(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)

"""앱 팩 JSON Schema(pack_schema.json) 읽기와 검증.

외부 의존성 없이 쓰려고 JSON Schema 2020-12 의 일부만 구현했다:
type, enum, const, properties, required, additionalProperties, items, minItems, maxItems, uniqueItems,
minLength, maxLength, pattern, minimum, maximum, $ref(#/...), allOf, anyOf, oneOf, if/then/else, format(date, date-time).
테스트에서는 jsonschema 패키지(설치돼 있으면)로도 같은 스키마를 한 번 더 검증한다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("pack_schema.json")


@lru_cache(maxsize=1)
def pack_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class SchemaError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:5]) + (f" … 외 {len(errors) - 5}건" if len(errors) > 5 else ""))
        self.errors = errors


def validate(instance: Any, ref: str, schema: dict[str, Any] | None = None) -> None:
    """ref 예: '#/$defs/manifest'. 어긋나면 SchemaError(경로별 오류 목록)."""
    root = schema or pack_schema()
    errors: list[str] = []
    _validate(instance, _resolve(root, ref), root, "$", errors)
    if errors:
        raise SchemaError(errors)


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#"):
        raise ValueError(f"외부 $ref 는 지원하지 않습니다: {ref}")
    node: Any = root
    for part in [p for p in ref[1:].split("/") if p]:
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _type_ok(v: Any, t: str) -> bool:
    return {
        "object": isinstance(v, dict),
        "array": isinstance(v, list),
        "string": isinstance(v, str),
        "boolean": isinstance(v, bool),
        "integer": isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer()),
        "number": isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": v is None,
    }[t]


def _valid(v: Any, s: dict[str, Any], root: dict[str, Any]) -> bool:
    errs: list[str] = []
    _validate(v, s, root, "$", errs)
    return not errs


def _validate(v: Any, s: Any, root: dict[str, Any], path: str, errs: list[str]) -> None:
    if s is True or s == {}:
        return
    if s is False:
        errs.append(f"{path}: 허용되지 않는 값")
        return
    if "$ref" in s:
        _validate(v, _resolve(root, s["$ref"]), root, path, errs)
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_type_ok(v, t) for t in types):
            errs.append(f"{path}: 형식이 {'/'.join(types)} 가 아닙니다({type(v).__name__})")
            return
    if "const" in s and v != s["const"]:
        errs.append(f"{path}: 값은 {s['const']!r} 이어야 합니다")
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {v!r} 는 허용 값 {s['enum']} 이 아닙니다")
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            errs.append(f"{path}: 길이 {len(v)} < {s['minLength']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errs.append(f"{path}: 길이 {len(v)} > {s['maxLength']}")
        if "pattern" in s and not re.search(s["pattern"], v):
            errs.append(f"{path}: 패턴 {s['pattern']} 불일치")
        fmt = s.get("format")
        if fmt == "date":
            try:
                date.fromisoformat(v)
            except ValueError:
                errs.append(f"{path}: 날짜(YYYY-MM-DD) 형식이 아닙니다")
        elif fmt == "date-time":
            try:
                datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                errs.append(f"{path}: 날짜시각(ISO 8601) 형식이 아닙니다")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errs.append(f"{path}: {v} < 최소 {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errs.append(f"{path}: {v} > 최대 {s['maximum']}")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errs.append(f"{path}: 항목 {len(v)}개 < {s['minItems']}")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errs.append(f"{path}: 항목 {len(v)}개 > {s['maxItems']}")
        if s.get("uniqueItems"):
            seen = [json.dumps(x, sort_keys=True, ensure_ascii=False) for x in v]
            if len(seen) != len(set(seen)):
                errs.append(f"{path}: 중복 항목이 있습니다")
        if "items" in s:
            for i, x in enumerate(v):
                _validate(x, s["items"], root, f"{path}[{i}]", errs)
    if isinstance(v, dict):
        props = s.get("properties", {})
        for k in s.get("required", []):
            if k not in v:
                errs.append(f"{path}: 필수 키 '{k}' 가 없습니다")
        for k, x in v.items():
            if k in props:
                _validate(x, props[k], root, f"{path}.{k}", errs)
            elif "additionalProperties" in s:
                ap = s["additionalProperties"]
                if ap is False:
                    errs.append(f"{path}: 규격에 없는 키 '{k}'")
                elif isinstance(ap, dict):
                    _validate(x, ap, root, f"{path}.{k}", errs)
    for sub in s.get("allOf", []):
        _validate(v, sub, root, path, errs)
    if "anyOf" in s and not any(_valid(v, sub, root) for sub in s["anyOf"]):
        errs.append(f"{path}: anyOf 중 맞는 것이 없습니다")
    if "oneOf" in s:
        n = sum(1 for sub in s["oneOf"] if _valid(v, sub, root))
        if n != 1:
            errs.append(f"{path}: oneOf 중 정확히 하나가 맞아야 합니다(지금 {n}개)")
    if "if" in s:
        if _valid(v, s["if"], root):
            if "then" in s:
                _validate(v, s["then"], root, path, errs)
        elif "else" in s:
            _validate(v, s["else"], root, path, errs)

"""인스타그램·페이스북 Graph API 인사이트 공통 도우미.

지표 이름이 버전·미디어 유형마다 달라 한 번에 요청하다 하나만 틀려도 전체가 거절된다(code 100).
그래서 거절되면 지표를 하나씩 다시 받아 되는 것만 모으고, 거절된 지표는 경고로 남긴 뒤 같은 유형에서는 건너뛴다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from studio.analytics.common import HttpError

try:   # 버전·호스트는 발행 모듈과 같은 값을 쓴다
    from studio.publish.meta_auth import FB_GRAPH, GRAPH_VERSION, IG_GRAPH
except ImportError:  # pragma: no cover - 발행 모듈이 없어도 분석은 돈다
    GRAPH_VERSION, FB_GRAPH, IG_GRAPH = "v26.0", "https://graph.facebook.com", "https://graph.instagram.com"

# Graph 오류 코드 분류 [기억·미확인]
TOKEN_CODES = {190, 102, 463, 467}
PERMISSION_CODES = {3, 10} | set(range(200, 300))
RATE_CODES = {4, 17, 32, 613, 80001, 80002, 80004}
INVALID_CODE = 100

Fetch = Callable[[str, list[str]], dict[str, Any]]


class GraphAbort(RuntimeError):
    """토큰·권한 문제 — 같은 원천의 나머지 호출도 모두 실패하므로 원천 전체를 실패로 기록한다."""


class GraphRateLimited(RuntimeError):
    """호출 한도 — 지금까지 받은 것만 쓰고 멈춘다."""


class MediaFailed(RuntimeError):
    """게시물 하나 실패(지워짐·ID 오류 등)."""


def graph_url(host: str, path: str) -> str:
    return f"{host}/{GRAPH_VERSION}{path if path.startswith('/') else '/' + path}"


def error_info(e: HttpError) -> tuple[str, int | None, str]:
    """(종류, code, message). 종류: token | permission | rate_limit | invalid | other."""
    err = e.payload.get("error") if isinstance(e.payload, dict) else None
    err = err if isinstance(err, dict) else {}
    try:
        code = int(err.get("code")) if err.get("code") is not None else None
    except (TypeError, ValueError):
        code = None
    msg = str(err.get("message") or e)[:300]
    if code in TOKEN_CODES or e.status == 401:
        return "token", code, msg
    if code in RATE_CODES or e.status == 429:
        return "rate_limit", code, msg
    if code in PERMISSION_CODES or e.status == 403:
        return "permission", code, msg
    if code == INVALID_CODE or e.status == 400:
        return "invalid", code, msg
    return "other", code, msg


def _abort(kind: str, msg: str, reauth: str) -> Exception:
    if kind == "token":
        return GraphAbort(f"토큰이 만료·취소되었습니다({msg}). `python -m studio auth {reauth}` 로 다시 발급하고 GitHub Secret 을 바꾸세요.")
    if kind == "permission":
        return GraphAbort(f"인사이트 권한이 없습니다({msg}). 앱 권한(instagram_business_manage_insights / read_insights·"
                          f"pages_read_engagement)을 확인한 뒤 `python -m studio auth {reauth}` 로 토큰을 다시 받으세요.")
    return GraphRateLimited(f"호출 한도에 걸렸습니다({msg}). 다음 실행에서 다시 받습니다.")


def parse_insights(payload: dict[str, Any]) -> dict[str, Any]:
    """{"data": [{"name", "values": [{"value"}]} | {"name", "total_value": {"value"}}]} → {이름: 값}."""
    out: dict[str, Any] = {}
    for m in payload.get("data") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        if isinstance(m.get("total_value"), dict):
            out[m["name"]] = m["total_value"].get("value")
        else:
            vals = m.get("values") or []
            out[m["name"]] = vals[-1].get("value") if vals and isinstance(vals[-1], dict) else None
    return out


def fetch_insights(fetch: Fetch, media_id: str, metrics: tuple[str, ...], *, skip: set[str], reauth: str,
                   warnings: list[str], label: str) -> dict[str, Any]:
    want = [m for m in metrics if m not in skip]
    if not want:
        raise MediaFailed("요청할 지표가 없습니다(모든 지표가 거절 목록에 있음 — 상수 확인).")
    try:
        return parse_insights(fetch(media_id, want))
    except HttpError as e:
        kind, _, msg = error_info(e)
        if kind in ("token", "permission", "rate_limit"):
            raise _abort(kind, msg, reauth) from None
        if kind != "invalid" or len(want) == 1:
            raise MediaFailed(msg) from None
    out: dict[str, Any] = {}
    bad: list[str] = []
    last = ""
    for m in want:
        try:
            out.update(parse_insights(fetch(media_id, [m])))
        except HttpError as e:
            kind, _, msg = error_info(e)
            if kind in ("token", "permission", "rate_limit"):
                raise _abort(kind, msg, reauth) from None
            bad.append(m)
            last = msg
    if not out:
        raise MediaFailed(f"모든 지표가 거절되었습니다(게시물이 지워졌거나 ID 가 다를 수 있음): {last}")
    if bad:
        skip.update(bad)
        warnings.append(f"{label}: 지표 {', '.join(bad)} 가 거절되어 이후 같은 유형에서는 빼고 받습니다 — 상수를 확인하세요({last}).")
    return out

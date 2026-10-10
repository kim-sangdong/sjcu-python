"""국가법령정보 공동활용 Open API(DRF) 클라이언트 — 조문 원문·시행일·연혁·판례.

인증: 환경변수 LAW_API_OC = open.law.go.kr 에 신청한 계정의 이메일 ID(g4c@korea.kr → g4c).
      공동활용 신청 때 호출 서버의 IP·도메인을 등록해야 응답이 온다(GitHub Actions 처럼 IP 가 바뀌는
      러너에서의 허용 여부는 [미확인] — 막히면 캐시를 커밋하거나 고정 IP 러너를 쓴다).

쓰는 엔드포인트(공식 활용가이드 요청변수 기준, JSON 응답 모양은 공개 저장소의 실제 응답 저장본으로 확인):
  lawSearch.do  target=law          query=법령명                현행법령 목록 → 법령ID·법령일련번호(MST)·시행일자
  lawService.do target=law          ID=법령ID                   현행 본문(조·항·호·목, 조문시행일자)
  lawSearch.do  target=eflaw        LID=법령ID nw=2             시행예정 버전 목록(시행일자)
  lawService.do target=eflaw        MST=..&efYd=YYYYMMDD        특정 시행일 버전 본문
  lawService.do target=lsJoHstInf   ID=..&JO=6자리              조문별 변경 이력(JSON 모양 [미확인] → 관대하게 읽음)
  lawSearch.do  target=prec         nb=사건번호                 판례 목록 → 판례일련번호·사건번호·선고일자
  lawService.do target=prec         ID=판례일련번호              판례 본문(판시사항·판결요지) — 루트 키 [미확인]

응답은 out/cache/law/<종류>/<요청 해시>.json 에 저장한다(OC 는 저장하지 않는다).
  법령 본문·목록 7일, 시행예정·연혁 1일, 판례 30일. --offline 은 기한과 무관하게 캐시만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from studio import paths
from studio.config import env
from studio.verify.lawtext import LawDoc, as_list, flatten_text, law_key, parse_law, parse_ymd

LAW_API_BASE = "https://www.law.go.kr/DRF"   # 문서 예시는 http:// — https 동작은 다른 클라이언트 구현에서 확인 [미확인: 공식 보장]
SEARCH = "lawSearch.do"
SERVICE = "lawService.do"
TIMEOUT = (10, 40)
RETRIES = 3
MIN_INTERVAL = 0.3        # 요청 사이 최소 간격(초) — 공동활용 서버 부담을 줄인다
TTL = {
    "law_search": timedelta(days=7),
    "law_body": timedelta(days=7),
    "eflaw_list": timedelta(days=1),
    "eflaw_body": timedelta(days=7),
    "jo_history": timedelta(days=1),
    "prec_search": timedelta(days=30),
    "prec_body": timedelta(days=30),
}

_sleep: Callable[[float], None] = time.sleep   # 테스트에서 바꿔 끼운다


class LawApiError(RuntimeError):
    """응답 해석 실패·법령 없음 등(문항 쪽 문제일 수 있음)."""


class LawApiUnavailable(LawApiError):
    """API 를 쓸 수 없음(키 없음·네트워크·오프라인 캐시 없음) — 문항 잘못이 아니므로 판정을 보류한다."""


def cache_root() -> Path:
    return paths.OUT / "cache" / "law"


@dataclass(frozen=True)
class LawRef:
    law_id: str
    mst: str
    name: str
    effective_date: date | None
    kind: str = ""
    status: str = ""          # 현행연혁코드: 현행 | 연혁 | 시행예정


@dataclass(frozen=True)
class LawVersion:
    mst: str
    effective_date: date
    status: str
    change: str = ""          # 제개정구분명


@dataclass(frozen=True)
class Precedent:
    prec_id: str
    case_no: str
    name: str
    decided_at: date | None
    court: str = ""


@dataclass(frozen=True)
class HistoryEntry:
    effective: date | None
    changed_at: date | None
    reason: str
    change: str
    mst: str


def norm_case_no(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class LawApi:
    def __init__(self, oc: str | None = None, *, cache_dir: Path | None = None, offline: bool = False,
                 refresh: bool = False, session: requests.Session | None = None, base: str | None = None,
                 min_interval: float = MIN_INTERVAL, today: date | None = None):
        self.oc = oc
        self.cache_dir = cache_dir or cache_root()
        self.offline = offline
        self.refresh = refresh
        self.session = session or requests.Session()
        self.base = (base or os.environ.get("LAW_API_BASE") or LAW_API_BASE).rstrip("/")
        self.min_interval = min_interval
        self.today = today or paths.now_kst().date()
        self._last_call = 0.0
        self._mem: dict[str, Any] = {}
        self.calls = 0          # 실제 HTTP 호출 수(보고용)

    @classmethod
    def from_env(cls, *, offline: bool = False, refresh: bool = False, **kw: Any) -> "LawApi":
        oc = None if offline else env("LAW_API_OC", required=False)
        return cls(oc, offline=offline, refresh=refresh, **kw)

    @property
    def available(self) -> bool:
        return self.offline or bool(self.oc)

    # ------------------------------------------------------------ 저수준

    def _cache_file(self, kind: str, endpoint: str, params: dict[str, Any]) -> Path:
        key = json.dumps({"e": endpoint, **{k: str(v) for k, v in sorted(params.items())}}, ensure_ascii=False)
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / kind / f"{h}.json"

    def get(self, kind: str, endpoint: str, params: dict[str, Any], *, fresh: bool = False) -> Any:
        """JSON 응답(dict). 캐시 → (기한 지남·refresh) → HTTP. OC 는 캐시 키와 저장 파일에 넣지 않는다."""
        params = {**params, "type": "JSON"}
        cf = self._cache_file(kind, endpoint, params)
        mem_key = str(cf)
        if mem_key in self._mem:
            return self._mem[mem_key]
        cached = None
        if cf.exists():
            try:
                cached = json.loads(cf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        if cached is not None:
            if self.offline:
                self._mem[mem_key] = cached["data"]
                return cached["data"]
            if not (self.refresh or fresh):
                saved = datetime.fromisoformat(cached.get("saved_at", "1970-01-01T00:00:00+00:00"))
                if datetime.now(timezone.utc) - saved < TTL.get(kind, timedelta(days=1)):
                    self._mem[mem_key] = cached["data"]
                    return cached["data"]
        if self.offline:
            raise LawApiUnavailable(f"오프라인 모드인데 캐시가 없습니다: {endpoint} {params}")
        if not self.oc:
            raise LawApiUnavailable("환경변수 LAW_API_OC 가 없습니다(open.law.go.kr 신청 이메일 ID). .env 또는 GitHub Secrets 에 넣어 주세요.")
        data = self._http(endpoint, params)
        cf.parent.mkdir(parents=True, exist_ok=True)
        tmp = cf.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "request": {"endpoint": endpoint, "params": params}, "data": data},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(cf)
        self._mem[mem_key] = data
        return data

    def _http(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = f"{self.base}/{endpoint}"
        shown = f"{endpoint}?" + "&".join(f"{k}={v}" for k, v in params.items())   # OC 없는 표시용
        last: Exception | None = None
        for attempt in range(RETRIES):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                _sleep(wait)
            if attempt:
                _sleep(min(2 ** attempt, 10))
            self._last_call = time.monotonic()
            try:
                self.calls += 1
                r = self.session.get(url, params={"OC": self.oc, **params}, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = LawApiUnavailable(f"법령 API 네트워크 오류({type(e).__name__}): {shown}")
                continue
            if r.status_code >= 500 or r.status_code == 429:
                last = LawApiUnavailable(f"법령 API HTTP {r.status_code}: {shown}")
                continue
            if r.status_code != 200:
                raise LawApiUnavailable(f"법령 API HTTP {r.status_code}: {shown} — {r.text[:150]}")
            try:
                data = r.json()
            except ValueError:
                text = r.text.strip()
                if "사용자" in text and ("검증" in text or "인증" in text) or "IP" in text:
                    raise LawApiUnavailable(f"법령 API 사용자 검증 실패 — OC 값과 등록한 서버 IP·도메인을 확인하세요: {text[:150]}")
                raise LawApiError(f"법령 API 응답이 JSON 이 아닙니다: {shown} — {text[:150]}")
            err = _api_error(data)
            if err:
                raise LawApiUnavailable(f"법령 API 오류: {err} ({shown})")
            return data
        assert last is not None
        raise last

    # ------------------------------------------------------------ 법령

    def search_laws(self, query: str, *, target: str = "law") -> list[dict[str, Any]]:
        data = self.get("law_search", SEARCH, {"target": target, "query": query, "display": 100})
        return [x for x in as_list(_root(data, "LawSearch").get("law")) if isinstance(x, dict)]

    def resolve(self, name: str, law_id: str | None = None) -> LawRef:
        """법령명(또는 약칭) → 현행 법령. 이름이 정확히 같은 것만 받는다('상법'으로 '국가배상법'이 걸리지 않게)."""
        rows = self.search_laws(name)
        key = law_key(name)
        exact = [r for r in rows if law_key(str(r.get("법령명한글", ""))) == key
                 or (r.get("법령약칭명") and law_key(str(r.get("법령약칭명"))) == key)]
        if law_id:
            exact = [r for r in exact if str(r.get("법령ID", "")).lstrip("0") == str(law_id).lstrip("0")] or exact
        if not exact:
            cands = ", ".join(sorted({str(r.get('법령명한글')) for r in rows})[:6]) or "없음"
            raise LawApiError(f"'{name}' 과(와) 이름이 정확히 같은 현행 법령이 없습니다(검색 후보: {cands})")
        cur = [r for r in exact if str(r.get("현행연혁코드", "현행")).strip() in ("현행", "")] or exact
        cur.sort(key=lambda r: (parse_ymd(r.get("시행일자")) or date.min), reverse=True)
        r = cur[0]
        return LawRef(law_id=str(r.get("법령ID", "")).strip(), mst=str(r.get("법령일련번호", "")).strip(),
                      name=str(r.get("법령명한글", "")).strip(), effective_date=parse_ymd(r.get("시행일자")),
                      kind=str(r.get("법령구분명", "")).strip(), status=str(r.get("현행연혁코드", "")).strip())

    def law(self, law_id: str, *, fresh: bool = False) -> LawDoc:
        data = self.get("law_body", SERVICE, {"target": "law", "ID": law_id}, fresh=fresh)
        doc = parse_law(data)
        doc.law_id = doc.law_id or law_id
        return doc

    def law_by_name(self, name: str, *, fresh: bool = False) -> tuple[LawRef, LawDoc]:
        ref = self.resolve(name)
        doc = self.law(ref.law_id, fresh=fresh)
        doc.mst = doc.mst or ref.mst
        return ref, doc

    def upcoming(self, law_id: str, *, fresh: bool = False) -> list[LawVersion]:
        """시행예정 버전(오늘 이후 시행) — 시행일 오름차순."""
        data = self.get("eflaw_list", SEARCH, {"target": "eflaw", "LID": law_id, "nw": 2, "display": 100,
                                              "sort": "efasc"}, fresh=fresh)
        out: list[LawVersion] = []
        for r in as_list(_root(data, "LawSearch").get("law")):
            if not isinstance(r, dict):
                continue
            ef = parse_ymd(r.get("시행일자"))
            if ef and ef > self.today:
                out.append(LawVersion(mst=str(r.get("법령일련번호", "")).strip(), effective_date=ef,
                                      status=str(r.get("현행연혁코드", "")).strip(),
                                      change=str(r.get("제개정구분명", "")).strip()))
        out.sort(key=lambda v: v.effective_date)
        return out

    def law_version(self, mst: str, effective: date) -> LawDoc:
        data = self.get("eflaw_body", SERVICE, {"target": "eflaw", "MST": mst, "efYd": effective.strftime("%Y%m%d")})
        doc = parse_law(data)
        doc.mst = mst
        return doc

    def decree(self, law_name: str) -> LawDoc | None:
        """같은 법령의 시행령(위임 숫자 대조용). 없으면 None."""
        if law_name.endswith(("시행령", "시행규칙")):
            return None
        try:
            _, doc = self.law_by_name(f"{law_name} 시행령")
        except LawApiError as e:
            if isinstance(e, LawApiUnavailable):
                raise
            return None
        return doc

    def article_history(self, law_id: str, jo_code: str) -> list[HistoryEntry]:
        """조문별 변경 이력. 응답 모양이 공식 문서에 없어 '시행일자'가 있는 객체를 모두 모아 읽는다 [미확인]."""
        data = self.get("jo_history", SERVICE, {"target": "lsJoHstInf", "ID": law_id, "JO": jo_code, "display": 100})
        return parse_history(data)

    # ------------------------------------------------------------ 판례

    def find_precedent(self, case_no: str) -> Precedent | None:
        want = norm_case_no(case_no)
        data = self.get("prec_search", SEARCH, {"target": "prec", "nb": want, "display": 20})
        for r in as_list(_root(data, "PrecSearch").get("prec")):
            if isinstance(r, dict) and norm_case_no(str(r.get("사건번호", ""))) == want:
                return Precedent(prec_id=str(r.get("판례일련번호", "")).strip(), case_no=str(r.get("사건번호", "")).strip(),
                                 name=str(r.get("사건명", "")).strip(), decided_at=parse_ymd(r.get("선고일자")),
                                 court=str(r.get("법원명", "")).strip())
        return None

    def precedent_text(self, prec_id: str) -> str:
        """판시사항 + 판결요지(+ 참조조문). HTML 태그는 지운다."""
        data = self.get("prec_body", SERVICE, {"target": "prec", "ID": prec_id})
        body = data.get("PrecService") if isinstance(data, dict) else None
        body = body if isinstance(body, dict) else (data if isinstance(data, dict) else {})
        parts = [flatten_text(body.get(k)) for k in ("판시사항", "판결요지", "참조조문")]
        text = "\n".join(p for p in parts if p)
        return re.sub(r"<[^>]+>", " ", text)


def _root(data: Any, key: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise LawApiError("법령 API 응답 형식이 객체가 아닙니다")
    root = data.get(key)
    if root is None:
        # 결과 0건이면 루트만 있고 목록 키가 없거나, 루트 자체가 빠지는 경우가 있다
        return {}
    if not isinstance(root, dict):
        raise LawApiError(f"법령 API 응답의 {key} 가 객체가 아닙니다")
    return root


def _api_error(data: Any) -> str:
    """사용자 검증 실패 같은 오류 응답을 알아본다(모양 [미확인] — 흔히 result/msg 키)."""
    if not isinstance(data, dict):
        return ""
    known = {"법령", "LawSearch", "PrecSearch", "PrecService", "Law", "LawService"}
    if known & set(data):
        return ""
    for k in ("result", "msg", "message", "error", "faultInfo"):
        if k in data:
            v = data[k]
            return flatten_text(v)[:200] if not isinstance(v, dict) else json.dumps(v, ensure_ascii=False)[:200]
    return ""


def parse_history(data: Any) -> list[HistoryEntry]:
    out: list[HistoryEntry] = []

    def scalars(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if not isinstance(v, (dict, list))}

    def walk(node: Any, ctx: dict[str, Any]) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x, ctx)
            return
        if not isinstance(node, dict):
            return
        here = {**ctx, **scalars(node)}
        info = node.get("법령정보")
        if isinstance(info, dict):
            here.update(scalars(info))
        jos = node.get("jo") if "jo" in node else node.get("조문정보")
        if jos is not None:
            for j in as_list(jos):
                if isinstance(j, dict):
                    _emit({**here, **scalars(j)})
            return
        if ("변경사유" in node or "조문변경일" in node or "조문개정일" in node) and "시행일자" in here:
            _emit(here)
            return
        for k, v in node.items():
            if isinstance(v, (dict, list)) and k != "법령정보":
                walk(v, here)

    def _emit(d: dict[str, Any]) -> None:
        out.append(HistoryEntry(
            effective=parse_ymd(d.get("조문시행일") or d.get("시행일자")),
            changed_at=parse_ymd(d.get("조문변경일") or d.get("조문개정일") or d.get("공포일자")),
            reason=str(d.get("변경사유") or "").strip(),
            change=str(d.get("제개정구분명") or "").strip(),
            mst=str(d.get("법령일련번호") or "").strip(),
        ))

    walk(data, {})
    out.sort(key=lambda h: (h.effective or date.min))
    return out

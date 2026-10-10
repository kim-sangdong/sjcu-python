"""문제은행·법령 검증 테스트 공용 도우미(이 파일에는 테스트가 없다).

- law_root: STUDIO_ROOT 를 tmp_path 로 돌리고, 오늘을 2026-10-10 12:00 KST 로 고정, channel.yaml(exam·rules) 작성.
- LawServer: responses 로 law.go.kr DRF 를 흉내 낸다. 요청 파라미터(target·query·ID·LID·MST·nb)로 픽스처를 고른다.
  픽스처의 법령ID·일련번호·공포번호는 테스트용 자리값이고, 시행예정·개정본(_20261020·_20270601·_amended)은 가상 개정이다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from studio import cliutil, config, paths

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=paths.KST)
CHANNEL_YAML = """
exam:
  name: 공인중개사
  round: 37
  next_exam_date: 2026-10-31
rules:
  banned_phrases: [합격 보장, 적중, 100% 합격]
  caution_phrases: [공식, 무조건]
  disclaimer: 한국산업인력공단·큐넷과 무관한 비공식 학습 앱입니다.
storage:
  bucket: media
bank:
  crosscheck_required: 2
"""


def fx(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def setup_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, oc: str | None = "testoc") -> Path:
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"),
                      ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(paths, "now_kst", lambda: NOW)
    (tmp_path / "channel.yaml").write_text(CHANNEL_YAML, encoding="utf-8")
    if oc:
        monkeypatch.setenv("LAW_API_OC", oc)
    else:
        monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("LAW_API_BASE", raising=False)
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    from studio.bank import upload
    from studio.verify import law_api
    monkeypatch.setattr(law_api, "_sleep", lambda s: None)
    monkeypatch.setattr(upload, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    cliutil._GROUPS.clear()
    return tmp_path


def bank_main(argv: list[str]) -> int:
    from studio.bank.__main__ import main

    cliutil._GROUPS.clear()
    return main(argv)


class LawServer:
    """법령 API 흉내. body·decree·upcoming 등을 바꿔 끼워 개정 상황을 만든다."""

    SEARCH = re.compile(r"https://www\.law\.go\.kr/DRF/lawSearch\.do.*")
    SERVICE = re.compile(r"https://www\.law\.go\.kr/DRF/lawService\.do.*")

    def __init__(self, rsps: responses.RequestsMock, *, body: str = "law_service_brokerage.json",
                 upcoming: str = "law_eflaw_upcoming.json"):
        self.body = body
        self.upcoming = upcoming
        self.requests: list[dict[str, str]] = []
        self.override: dict[tuple[str, str], Callable[[dict[str, str]], Any]] = {}
        rsps.add_callback(responses.GET, self.SEARCH, callback=self._search)
        rsps.add_callback(responses.GET, self.SERVICE, callback=self._service)

    def _params(self, req: Any) -> dict[str, str]:
        q = parse_qs(urlparse(req.url).query, keep_blank_values=True)
        p = {k: v[0] for k, v in q.items()}
        self.requests.append(p)
        return p

    @staticmethod
    def _ok(data: Any) -> tuple[int, dict[str, str], str]:
        return 200, {"Content-Type": "application/json;charset=UTF-8"}, json.dumps(data, ensure_ascii=False)

    def _search(self, req: Any):
        p = self._params(req)
        t = p.get("target")
        if t == "law":
            q = p.get("query", "")
            if q == "공인중개사법":
                return self._ok(fx("law_search_brokerage.json"))
            if q == "공인중개사법 시행령":
                return self._ok(fx("law_search_decree.json"))
            return self._ok({"LawSearch": {"target": "law", "키워드": q, "totalCnt": "0", "page": "1"}})
        if t == "eflaw":
            if p.get("LID") == "001823":
                return self._ok(fx(self.upcoming))
            return self._ok(fx("law_eflaw_none.json"))
        if t == "prec":
            if p.get("nb") == "2019다299999":
                return self._ok(fx("law_prec_search.json"))
            return self._ok({"PrecSearch": {"target": "prec", "totalCnt": "0", "page": "1"}})
        return 404, {}, "not mocked"

    def _service(self, req: Any):
        p = self._params(req)
        t = p.get("target")
        if t == "law":
            if p.get("ID") == "001823":
                return self._ok(fx(self.body))
            if p.get("ID") == "003822":
                return self._ok(fx("law_service_decree.json"))
        if t == "eflaw":
            if p.get("MST") == "900101":
                return self._ok(fx("law_service_brokerage_20261020.json"))
            if p.get("MST") == "900102":
                return self._ok(fx("law_service_brokerage_20270601.json"))
        if t == "lsJoHstInf":
            return self._ok(fx("law_jo_history.json"))
        if t == "prec" and p.get("ID") == "777001":
            return self._ok(fx("law_prec_service.json"))
        return 404, {}, "not mocked"

    def count(self, **match: str) -> int:
        return sum(1 for r in self.requests if all(r.get(k) == v for k, v in match.items()))

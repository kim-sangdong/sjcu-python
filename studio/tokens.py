"""회전하는 OAuth 토큰(틱톡 refresh token, 인스타 장기 토큰 등) 보관소.

GitHub Secrets 는 실행 중에 고칠 수 없으므로, 갱신되는 토큰은 여기에 저장한다.
- 로컬:   .secrets/tokens.json (gitignore)
- CI:     Supabase 테이블 oauth_tokens (STUDIO_TOKEN_BACKEND=supabase)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Protocol

import requests

from studio import paths
from studio.config import env


class TokenStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any]) -> None: ...


class FileTokenStore:
    def __init__(self, path=None):
        self.path = path or (paths.SECRETS / "tokens.json")

    def _all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key: str) -> dict[str, Any] | None:
        return self._all().get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        data = self._all()
        data[key] = {**value, "_updated_at": datetime.now(timezone.utc).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


class SupabaseTokenStore:
    """PostgREST 로 oauth_tokens(key text primary key, value jsonb, updated_at timestamptz) 를 읽고 쓴다."""

    def __init__(self, url: str | None = None, service_key: str | None = None):
        self.url = (url or env("SUPABASE_URL")).rstrip("/")
        self.key = service_key or env("SUPABASE_SERVICE_ROLE_KEY")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def get(self, key: str) -> dict[str, Any] | None:
        r = requests.get(
            f"{self.url}/rest/v1/oauth_tokens",
            params={"key": f"eq.{key}", "select": "value"},
            headers=self._headers,
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0]["value"] if rows else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        r = requests.post(
            f"{self.url}/rest/v1/oauth_tokens",
            headers={**self._headers, "Prefer": "resolution=merge-duplicates"},
            json={"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=30,
        )
        r.raise_for_status()


def token_store() -> TokenStore:
    backend = os.environ.get("STUDIO_TOKEN_BACKEND", "file")
    if backend == "supabase":
        return SupabaseTokenStore()
    return FileTokenStore()

"""문항 팩 업로드 — Supabase Storage 공개 버킷(channel.yaml storage.bucket, 기본 media).

  bank/<version>/manifest.json, <코드>.json   버전 고정 사본 — 내용이 바뀌지 않으므로 오래 캐시(1년)
  bank/latest/<코드>.json, manifest.json      최신 — 짧게 캐시(max-age=300). manifest 는 맨 마지막에 올린다
                                              (앱이 새 manifest 를 보고 받으러 갔을 때 파일이 이미 있게).
환경변수: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY. 리허설: --dry-run 또는 STUDIO_DRY_RUN=1.
요청 형식은 studio/publish/storage.py 와 같다(POST /storage/v1/object/<bucket>/<path> + x-upsert + cache-control).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests

from studio.bank.schema import validate

OBJECT_PATH = "/storage/v1/object/{bucket}/{path}"
PUBLIC_PATH = "/storage/v1/object/public/{bucket}/{path}"
LATEST_MAX_AGE = 300
VERSIONED_MAX_AGE = 31_536_000
TIMEOUT = (10, 60)
RETRIES = 3
_sleep: Callable[[float], None] = time.sleep


class UploadError(RuntimeError):
    pass


@dataclass
class PlannedUpload:
    local: Path
    path: str
    max_age: int
    sha256: str
    size: int


def env_dry_run() -> bool:
    return os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def plan(pack_dir: Path, prefix: str = "bank", *, allow_empty: bool = False) -> tuple[str, list[PlannedUpload]]:
    mp = pack_dir / "manifest.json"
    if not mp.exists():
        raise UploadError(f"{mp} 가 없습니다. 먼저 `python -m studio bank export` 를 실행하세요.")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    validate(manifest, "#/$defs/manifest")
    version = manifest["version"]
    if not manifest.get("total") and not allow_empty:
        # 빈 팩을 latest/ 에 올리면 앱이 받아서 지금 쓰는 문항(시드·캐시)을 0문항으로 바꾼다 — 리허설 2026-W42 에서 확인
        raise UploadError("팩이 비어 있습니다(0문항) — 올리면 앱의 문항이 모두 사라집니다. verify·crosscheck 로 문항을 통과시킨 뒤 "
                          "다시 export 하세요(정말 비운 팩을 올려야 하면 --allow-empty).")
    subjects: list[PlannedUpload] = []
    for s in manifest["subjects"]:
        p = pack_dir / s["file"]
        if not p.exists():
            raise UploadError(f"manifest 에 있는 {s['file']} 가 없습니다")
        data = p.read_bytes()
        h = hashlib.sha256(data).hexdigest()
        if h != s["sha256"]:
            raise UploadError(f"{s['file']} 의 sha256 이 manifest 와 다릅니다 — export 를 다시 하세요")
        subjects.append(PlannedUpload(p, s["file"], 0, h, len(data)))
    mdata = mp.read_bytes()
    mh = hashlib.sha256(mdata).hexdigest()
    out: list[PlannedUpload] = []
    for u in subjects + [PlannedUpload(mp, "manifest.json", 0, mh, len(mdata))]:
        out.append(PlannedUpload(u.local, f"{prefix}/{version}/{u.path}", VERSIONED_MAX_AGE, u.sha256, u.size))
    for u in subjects:
        out.append(PlannedUpload(u.local, f"{prefix}/latest/{u.path}", LATEST_MAX_AGE, u.sha256, u.size))
    out.append(PlannedUpload(mp, f"{prefix}/latest/manifest.json", LATEST_MAX_AGE, mh, len(mdata)))
    return version, out


def public_url(base_url: str, bucket: str, path: str) -> str:
    return base_url.rstrip("/") + PUBLIC_PATH.format(bucket=bucket, path=quote(path, safe="/"))


def _headers(key: str) -> dict[str, str]:
    h = {"apikey": key}
    if key.startswith("eyJ"):          # 옛 JWT 형식 service_role 키만 Bearer 로(새 sb_secret_ 키는 apikey 헤더)
        h["Authorization"] = f"Bearer {key}"
    return h


def upload(items: list[PlannedUpload], *, url: str, key: str, bucket: str,
           session: requests.Session | None = None) -> list[str]:
    s = session or requests.Session()
    done: list[str] = []
    for u in items:
        target = url.rstrip("/") + OBJECT_PATH.format(bucket=bucket, path=quote(u.path, safe="/"))
        hdr = {**_headers(key), "Content-Type": "application/json; charset=utf-8", "x-upsert": "true",
               "cache-control": f"max-age={u.max_age}"}
        data = u.local.read_bytes()
        last = ""
        for attempt in range(RETRIES):
            if attempt:
                _sleep(min(2 ** attempt, 10))
            try:
                r = s.post(target, headers=hdr, data=data, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = f"네트워크 오류 {type(e).__name__}"
                continue
            if r.status_code in (200, 201):
                done.append(u.path)
                break
            last = f"HTTP {r.status_code} {r.text[:200]}"
            if r.status_code in (400, 401, 403, 404, 413):
                break
        else:
            raise UploadError(f"업로드 실패 {u.path}: {last}")
        if done[-1:] != [u.path]:
            raise UploadError(f"업로드 실패 {u.path}: {last}")
    return done

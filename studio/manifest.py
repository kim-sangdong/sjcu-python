"""렌더 결과 목록(content/weeks/<week>/manifest.json) 읽기·갱신. 여러 프로세스가 동시에 써도 안전하게 잠근다."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from typing import Iterator

from studio import paths
from studio.models import RenderedAsset, RenderManifest, read_model, write_model


def manifest_path(week: str):
    return paths.content_dir(week) / "manifest.json"


def load_manifest(week: str) -> RenderManifest:
    p = manifest_path(week)
    return read_model(p, RenderManifest) if p.exists() else RenderManifest(week=week)


@contextmanager
def editing(week: str) -> Iterator[RenderManifest]:
    """with editing(week) as m: m.upsert(...)  — 잠금 상태에서 읽고, 블록이 끝나면 저장한다."""
    p = manifest_path(week)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            m = load_manifest(week)
            yield m
            write_model(p, m)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def upsert_asset(week: str, asset: RenderedAsset) -> None:
    with editing(week) as m:
        old = m.get(asset.asset_id)
        if old and not asset.remote_urls:
            asset.remote_urls = old.remote_urls  # 다시 렌더해도 기존 업로드 URL 정보는 유지(파일이 바뀌면 업로드 단계가 갱신)
        m.upsert(asset)

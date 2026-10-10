"""모든 플랫폼 발행기가 따르는 공통 접점.

발행기는 Publisher 프로토콜을 구현하고 registry.PUBLISHERS 에 등록된다.
- publish(ctx): 업로드(또는 예약)하고 PublishResult 를 돌려준다. 실패하면 PublishError 를 던진다.
- check(ctx):   처리 중(processing)인 항목의 상태를 다시 확인한다.
- dry_run=True 이면 네트워크를 쓰지 않고, 무엇을 보낼지 기록만 한 뒤 가짜 ID 를 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Union

from studio import paths
from studio.config import Config
from studio.models import AssetKind, BlogPost, CardNews, PublishStatus, PublishTarget, RenderedAsset, VideoScript

Content = Union[VideoScript, CardNews, BlogPost]


class PublishError(RuntimeError):
    """발행 실패. retryable=False 면 재시도하지 않는다(권한·형식 오류 등)."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class PublishContext:
    week: str
    target: PublishTarget
    asset: RenderedAsset
    content: Content
    cfg: Config
    dry_run: bool = False
    log: list[str] = field(default_factory=list)

    def file(self, role: str) -> Path:
        """렌더 결과 파일의 절대경로. 예: ctx.file('video')"""
        rel = self.asset.files.get(role)
        if not rel:
            raise PublishError(f"{self.asset.asset_id}: '{role}' 파일이 렌더 결과에 없습니다.", retryable=False)
        p = paths.ROOT / rel
        if not p.exists():
            raise PublishError(f"{self.asset.asset_id}: 파일이 없습니다 {p} (렌더를 다시 하거나 스토리지에서 받아야 합니다)")
        return p

    def files_with_prefix(self, prefix: str) -> list[Path]:
        """slide01, slide02 … 처럼 접두어가 같은 파일들(이름순)."""
        keys = sorted(k for k in self.asset.files if k.startswith(prefix))
        return [paths.ROOT / self.asset.files[k] for k in keys]

    def remote_url(self, role: str) -> str:
        url = self.asset.remote_urls.get(role)
        if not url:
            raise PublishError(f"{self.asset.asset_id}: '{role}' 공개 URL 이 없습니다. `publish upload-media` 를 먼저 실행하세요.")
        return url


@dataclass
class PublishResult:
    status: PublishStatus            # scheduled | published | processing | skipped
    remote_id: str | None = None
    url: str | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Publisher(Protocol):
    platform: str
    kinds: tuple[AssetKind, ...]     # 이 발행기가 다루는 콘텐츠 종류

    def publish(self, ctx: PublishContext) -> PublishResult: ...

    def check(self, ctx: PublishContext) -> PublishResult: ...

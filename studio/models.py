"""모든 모듈이 공유하는 데이터 규격.

Claude Code(슬래시 명령어)가 JSON을 쓰고, 파이썬이 이 규격으로 검증·렌더링·발행한다.
파일 위치는 studio.paths 참고:
  content/weeks/<week>/plan.json                       WeekPlan
  content/weeks/<week>/longform/<id>.json              VideoScript (format=longform)
  content/weeks/<week>/shorts/<id>.json                VideoScript (format=short)
  content/weeks/<week>/cardnews/<id>.json              CardNews
  content/weeks/<week>/blog/<id>.json                  BlogPost
  content/weeks/<week>/manifest.json                   RenderManifest (렌더 결과 + 원격 URL)
  content/weeks/<week>/originality.json                OriginalityReport
  content/weeks/<week>/queue.json                      PublishQueue
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

AssetKind = Literal["longform", "short", "cardnews", "blog"]
VideoFormat = Literal["longform", "short"]
Platform = Literal["youtube", "instagram", "facebook", "tiktok", "tistory"]
Verdict = Literal["pass", "warn", "block"]
PublishStatus = Literal["pending", "uploading", "processing", "scheduled", "published", "failed", "skipped"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 주간 계획

class PlanItem(_Model):
    id: str
    kind: AssetKind
    topic: str
    angle: str = ""            # 지난 회차와 달라지는 지점(독창성 근거)
    keyword: str = ""          # 블로그 SEO 롱테일 키워드
    source_id: str | None = None  # 이 콘텐츠가 파생된 원본(예: 쇼츠 ← 롱폼)
    publish_at: datetime | None = None


class WeekPlan(_Model):
    week: str
    theme: str
    summary: str = ""
    brief_applied: str = ""    # 지난주 /channel-revenue·/ad-strategy 보고에서 반영한 점
    items: list[PlanItem]

    def of_kind(self, kind: AssetKind) -> list[PlanItem]:
        return [i for i in self.items if i.kind == kind]


# ---------------------------------------------------------------- 영상 대본

class Scene(_Model):
    """화면 한 장면.

    kind 는 studio.video.scenes 레지스트리에 등록된 이름이어야 한다.
    narration 은 문장 단위 목록 — 문장마다 TTS 한 클립과 자막 한 줄이 만들어진다.
    hold_seconds 는 내레이션이 끝난 뒤 화면을 더 보여 주는 시간(문제 푸는 시간 등).
    """

    id: str
    kind: str
    narration: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    hold_seconds: float = Field(default=0.0, ge=0, le=180)


class Chapter(_Model):
    title: str
    scenes: list[Scene] = Field(min_length=1)


class Thumbnail(_Model):
    headline: str = Field(max_length=40)
    subline: str = Field(default="", max_length=60)
    style: str = "default"
    params: dict[str, Any] = Field(default_factory=dict)


class VideoScript(_Model):
    id: str
    week: str
    format: VideoFormat
    title: str = Field(max_length=100)
    description: str = Field(max_length=4900)
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(min_length=1)
    thumbnail: Thumbnail | None = None
    pinned_comment: str = ""
    cta: str = ""
    synthetic_media: bool = True   # 합성 음성(TTS)·생성 화면 사용 → 플랫폼 AI 표시
    made_for_kids: bool = False
    notes: str = ""                # 제작 메모(발행하지 않음)

    @field_validator("tags")
    @classmethod
    def _tags_len(cls, v: list[str]) -> list[str]:
        if sum(len(t) for t in v) > 450:
            raise ValueError("태그 총 길이는 450자 이하여야 합니다(YouTube 500자 제한 여유분).")
        return v

    def scenes(self) -> list[Scene]:
        return [s for c in self.chapters for s in c.scenes]

    def narration_sentences(self) -> list[str]:
        return [line for s in self.scenes() for line in s.narration]

    def narration_text(self) -> str:
        return "\n".join(self.narration_sentences())


# ---------------------------------------------------------------- 카드뉴스

class CardSlide(_Model):
    kind: Literal["cover", "body", "list", "quiz", "answer", "cta"]
    title: str = ""
    body: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class CardNews(_Model):
    id: str
    week: str
    title: str
    caption: str = Field(max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    slides: list[CardSlide] = Field(min_length=2, max_length=10)
    source_id: str | None = None
    synthetic_media: bool = False


# ---------------------------------------------------------------- 블로그

class BlogPost(_Model):
    id: str
    week: str
    title: str
    keyword: str
    tags: list[str] = Field(default_factory=list)
    meta_description: str = Field(default="", max_length=160)
    body_markdown: str
    related_video_id: str | None = None


# ---------------------------------------------------------------- 렌더 결과

class RenderedAsset(_Model):
    asset_id: str
    kind: AssetKind
    files: dict[str, str] = Field(default_factory=dict)        # 역할 → ROOT 기준 상대경로 (video, thumbnail, subtitles, slide01 ...)
    remote_urls: dict[str, str] = Field(default_factory=dict)  # 역할 → 공개 URL(스토리지 업로드 후)
    duration_seconds: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class RenderManifest(_Model):
    week: str
    assets: list[RenderedAsset] = Field(default_factory=list)

    def get(self, asset_id: str) -> RenderedAsset | None:
        return next((a for a in self.assets if a.asset_id == asset_id), None)

    def upsert(self, asset: RenderedAsset) -> None:
        self.assets = [a for a in self.assets if a.asset_id != asset.asset_id] + [asset]


# ---------------------------------------------------------------- 독창성 검사

class CheckResult(_Model):
    metric: str
    value: float
    warn_at: float | None = None
    block_at: float | None = None
    verdict: Verdict
    detail: str = ""
    compared_with: str | None = None


class AssetOriginality(_Model):
    asset_id: str
    verdict: Verdict
    checks: list[CheckResult] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)   # 다시 쓸 때 따를 구체 지시


class OriginalityReport(_Model):
    week: str
    generated_at: datetime
    stage: Literal["script", "render"] | None = None
    verdict: Verdict
    assets: list[AssetOriginality] = Field(default_factory=list)

    def blocked(self) -> list[AssetOriginality]:
        return [a for a in self.assets if a.verdict == "block"]


# ---------------------------------------------------------------- 발행 대기열

class PublishTarget(_Model):
    asset_id: str
    asset_kind: AssetKind
    platform: Platform
    publish_at: datetime
    status: PublishStatus = "pending"
    remote_id: str | None = None
    url: str | None = None
    attempts: int = 0
    last_error: str | None = None
    updated_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.asset_id}"


class PublishQueue(_Model):
    week: str
    targets: list[PublishTarget] = Field(default_factory=list)

    def find(self, platform: str, asset_id: str) -> PublishTarget | None:
        return next((t for t in self.targets if t.platform == platform and t.asset_id == asset_id), None)


# ---------------------------------------------------------------- 입출력

M = TypeVar("M", bound=BaseModel)


def read_model(path: Path, model: type[M]) -> M:
    return model.model_validate_json(Path(path).read_text(encoding="utf-8"))


def write_model(path: Path, obj: BaseModel) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(obj.model_dump_json(indent=2, exclude_none=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def asset_kind_of(asset_id: str) -> AssetKind:
    """'2026W42-S03' → 'short'. 접미 글자 규칙은 studio.paths 의 *_id 함수와 같다."""
    tag = asset_id.rsplit("-", 1)[-1][:1]
    return {"L": "longform", "S": "short", "C": "cardnews", "B": "blog"}[tag]  # type: ignore[return-value]

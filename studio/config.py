"""channel.yaml(채널 설정)과 환경변수(비밀값) 로딩.

비밀값은 저장소에 두지 않는다. 로컬은 .env, GitHub Actions 는 Secrets 로 넣는다.
필요한 환경변수 목록은 .env.example 과 docs/SETUP.md 에 있다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from studio import paths


class _Section(BaseModel):
    # 하위 모듈이 자기 섹션에 키를 추가해도 깨지지 않도록 extra 허용.
    model_config = ConfigDict(extra="allow")


class ChannelCfg(_Section):
    name: str = "채널 이름"
    handle: str = ""
    topic: str = ""
    audience: str = ""
    tone: str = "존댓말, 천천히, 따뜻하게"
    language: str = "ko"
    timezone: str = "Asia/Seoul"


class ScheduleCfg(_Section):
    weekly_run: str = "SUN 12:00"            # 정기 제작·발행 실행 시각(KST)
    longform_publish: str = "SUN 19:00"      # 롱폼 공개 시각
    shorts_per_week: int = 14
    shorts_times: list[str] = Field(default_factory=lambda: ["12:00", "19:00"])
    cardnews_per_week: int = 3
    cardnews_days: list[str] = Field(default_factory=lambda: ["TUE", "THU", "SAT"])
    cardnews_time: str = "20:00"
    blog_per_week: int = 3
    blog_days: list[str] = Field(default_factory=lambda: ["MON", "WED", "FRI"])
    blog_time: str = "08:00"


class YouTubeCfg(_Section):
    enabled: bool = True
    category_id: str = "27"                  # 27 = Education
    default_language: str = "ko"
    playlist_longform: str | None = None
    playlist_shorts: str | None = None
    notify_subscribers: bool = True
    audited: bool = False                    # API 감사 통과 전에는 업로드가 비공개로 잠긴다


class InstagramCfg(_Section):
    enabled: bool = True
    share_reels_to_feed: bool = True


class FacebookCfg(_Section):
    enabled: bool = True


class TikTokCfg(_Section):
    enabled: bool = True
    privacy_level: str = "PUBLIC_TO_EVERYONE"
    audited: bool = False                    # 감사 전에는 SELF_ONLY 로만 올라간다


class TistoryCfg(_Section):
    enabled: bool = True
    blog_name: str = ""                      # https://<blog_name>.tistory.com
    category: str = ""
    visibility: Literal["public", "private", "protected"] = "public"


class PlatformsCfg(_Section):
    youtube: YouTubeCfg = Field(default_factory=YouTubeCfg)
    instagram: InstagramCfg = Field(default_factory=InstagramCfg)
    facebook: FacebookCfg = Field(default_factory=FacebookCfg)
    tiktok: TikTokCfg = Field(default_factory=TikTokCfg)
    tistory: TistoryCfg = Field(default_factory=TistoryCfg)


class TTSCfg(_Section):
    provider: Literal["google", "edge", "elevenlabs", "openai"] = "google"
    voice: str = "ko-KR-Chirp3-HD-Aoede"
    speaking_rate: float = 0.95
    fallback: Literal["google", "edge", "elevenlabs", "openai", "none"] = "edge"
    fallback_voice: str = "ko-KR-SunHiNeural"


class VideoCfg(_Section):
    long_size: tuple[int, int] = (1920, 1080)
    short_size: tuple[int, int] = (1080, 1920)
    fps: int = 30
    short_max_seconds: int = 59
    longform_target_minutes: int = 28
    bgm_dir: str = "assets/bgm"
    bgm_volume_db: float = -26.0
    loudness_lufs: float = -14.0


class BrandCfg(_Section):
    background: str = "#FFF8F2"
    text: str = "#1A1717"
    primary: str = "#8C2129"
    accent: str = "#F09E6B"
    muted: str = "#C7C7C7"
    font_regular: str = "NanumGothic.ttf"
    font_bold: str = "NanumGothicExtraBold.ttf"
    logo: str | None = None


class AppCfg(_Section):
    name: str = ""
    android_package: str = ""
    ios_app_id: str = ""
    play_url: str = ""
    appstore_url: str = ""
    link_base: str = ""                      # 추적 리디렉트 주소 (Supabase Edge Function /go)


class StorageCfg(_Section):
    provider: Literal["supabase", "none"] = "supabase"
    bucket: str = "media"


class RulesCfg(_Section):
    banned_phrases: list[str] = Field(default_factory=list)
    caution_phrases: list[str] = Field(default_factory=list)
    disclaimer: str = ""


class Config(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel: ChannelCfg = Field(default_factory=ChannelCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)
    platforms: PlatformsCfg = Field(default_factory=PlatformsCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    video: VideoCfg = Field(default_factory=VideoCfg)
    brand: BrandCfg = Field(default_factory=BrandCfg)
    app: AppCfg = Field(default_factory=AppCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    rules: RulesCfg = Field(default_factory=RulesCfg)
    originality: dict[str, Any] = Field(default_factory=dict)   # 임계값 덮어쓰기 (studio.originality 기본값 위에)


CONFIG_FILE = "channel.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> Config:
    load_env()
    p = Path(path) if path else paths.ROOT / CONFIG_FILE
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return Config.model_validate(data or {})


_env_loaded = False


def load_env() -> None:
    global _env_loaded
    if not _env_loaded:
        load_dotenv(paths.ROOT / ".env", override=False)
        _env_loaded = True


class MissingSecret(RuntimeError):
    pass


def env(name: str, default: str | None = None, *, required: bool = True) -> str | None:
    """비밀값 읽기. required=True 인데 없으면 어떤 설정이 필요한지 알려 주는 예외를 낸다."""
    load_env()
    val = os.environ.get(name, default)
    if required and not val:
        raise MissingSecret(f"환경변수 {name} 가 없습니다. .env 또는 GitHub Secrets 에 넣어 주세요 (docs/SETUP.md 참고).")
    return val


def font_path(name: str) -> Path:
    """폰트 파일 경로. assets/fonts → STUDIO_FONT_DIR 순서로 찾는다."""
    candidates = [paths.FONTS / name]
    if os.environ.get("STUDIO_FONT_DIR"):
        candidates.append(Path(os.environ["STUDIO_FONT_DIR"]) / name)
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"폰트 {name} 를 찾지 못했습니다. `python -m studio setup fonts` 를 먼저 실행하세요.")

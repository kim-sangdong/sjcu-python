"""플랫폼별 설명문·캡션 조립 (앱 링크, 챕터, 해시태그, AI 사용 고지 포함)."""

from __future__ import annotations

from studio.config import Config
from studio.models import CardNews, RenderedAsset, VideoScript
from studio.publish.utm import app_link

AI_NOTICE = "※ 이 영상의 내레이션은 AI 음성(TTS)으로 만들었습니다."


def _hashtags(tags: list[str], limit: int) -> str:
    out = []
    for t in tags[:limit]:
        t = t.strip().replace(" ", "")
        if t:
            out.append(t if t.startswith("#") else f"#{t}")
    return " ".join(out)


def _chapters(asset: RenderedAsset | None) -> str:
    """렌더 결과 meta['chapters'] = [[초, 제목], ...] → YouTube 챕터 표기(첫 줄은 0:00 이어야 함)."""
    chapters = (asset.meta.get("chapters") if asset else None) or []
    if len(chapters) < 3:
        return ""
    lines = []
    for sec, title in chapters:
        sec = int(sec)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        stamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        lines.append(f"{stamp} {title}")
    return "\n".join(lines)


def youtube_description(script: VideoScript, cfg: Config, asset: RenderedAsset | None) -> str:
    parts = [script.description.strip()]
    link = app_link(cfg, platform="youtube", medium="description" if script.format == "longform" else "shorts", week=script.week, asset_id=script.id)
    if link:
        label = cfg.app.name or "앱"
        parts.append(f"▶ {label} 내려받기: {link}")
    ch = _chapters(asset) if script.format == "longform" else ""
    if ch:
        parts.append(ch)
    if cfg.rules.disclaimer:
        parts.append(cfg.rules.disclaimer)
    if script.synthetic_media:
        parts.append(AI_NOTICE)
    tags = _hashtags(script.hashtags, 15)
    if tags:
        parts.append(tags)
    text = "\n\n".join(p for p in parts if p)
    # YouTube 설명 5000바이트 제한(여유 두고 4800)
    while len(text.encode("utf-8")) > 4800:
        text = text[:-50]
    return text


def reel_caption(script: VideoScript, cfg: Config, platform: str) -> str:
    parts = [script.description.strip().split("\n\n")[0]]
    if cfg.app.name:
        parts.append(f"{cfg.app.name}은(는) 프로필 링크에서 받을 수 있어요.")
    if platform == "facebook":
        link = app_link(cfg, platform="facebook", medium="reels", week=script.week, asset_id=script.id)
        if link:
            parts.append(link)
    if script.synthetic_media:
        parts.append(AI_NOTICE)
    parts.append(_hashtags(script.hashtags, 5 if platform == "instagram" else 8))
    return "\n\n".join(p for p in parts if p)[:2200]


def carousel_caption(card: CardNews, cfg: Config) -> str:
    parts = [card.caption.strip()]
    if cfg.app.name:
        parts.append(f"{cfg.app.name}은(는) 프로필 링크에서 받을 수 있어요.")
    parts.append(_hashtags(card.hashtags, 5))
    return "\n\n".join(p for p in parts if p)[:2200]

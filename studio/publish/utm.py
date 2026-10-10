"""채널별 추적 링크 만들기.

모든 앱 링크는 Supabase Edge Function `/go` 를 거친다. 함수가 클릭을 기록한 뒤
안드로이드는 Play 스토어(referrer=utm_...)로, iOS 는 App Store 캠페인 링크(pt/ct)로 보낸다.
utm_content 에는 콘텐츠 ID(예: 2026W42-S03)를 넣어 어떤 콘텐츠가 설치·결제를 만들었는지 잇는다.
"""

from __future__ import annotations

from urllib.parse import urlencode

from studio.config import Config

# 플랫폼 → utm_source 값
SOURCES = {
    "youtube": "youtube",
    "instagram": "instagram",
    "facebook": "facebook",
    "tiktok": "tiktok",
    "tistory": "tistory",
}


def app_link(cfg: Config, *, platform: str, medium: str, week: str, asset_id: str) -> str:
    """예) https://<ref>.supabase.co/functions/v1/go?s=youtube&m=description&c=2026-W42&a=2026W42-L1"""
    base = cfg.app.link_base.rstrip("/")
    if not base:
        return ""
    q = urlencode({"s": SOURCES.get(platform, platform), "m": medium, "c": week, "a": asset_id})
    return f"{base}?{q}"


def utm_params(platform: str, medium: str, week: str, asset_id: str) -> dict[str, str]:
    """스토어 referrer 에 실릴 표준 UTM 값(/go 함수와 같은 규칙)."""
    return {
        "utm_source": SOURCES.get(platform, platform),
        "utm_medium": medium,
        "utm_campaign": week,
        "utm_content": asset_id,
    }

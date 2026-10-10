"""플랫폼 이름 → 발행기 클래스(지연 import)."""

from __future__ import annotations

import importlib

from studio.publish.base import Publisher

PUBLISHERS: dict[str, str] = {
    "youtube": "studio.publish.youtube:YouTubePublisher",
    "instagram": "studio.publish.instagram:InstagramPublisher",
    "facebook": "studio.publish.facebook:FacebookPublisher",
    "tiktok": "studio.publish.tiktok:TikTokPublisher",
    "tistory": "studio.publish.tistory:TistoryPublisher",
}


def get_publisher(platform: str) -> Publisher:
    mod_name, cls_name = PUBLISHERS[platform].split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    return cls()

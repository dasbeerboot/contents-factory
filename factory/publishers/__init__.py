"""Publisher registry."""
from __future__ import annotations

from pathlib import Path

from ..errors import FactoryError
from .base import Publisher, PublishRequest, PublishResult
from .instagram import InstagramPublisher
from .naver_blog import NaverBlogPublisher
from .threads import ThreadsPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

REGISTRY: dict[str, type[Publisher]] = {
    ThreadsPublisher.platform: ThreadsPublisher,
    InstagramPublisher.platform: InstagramPublisher,
    TikTokPublisher.platform: TikTokPublisher,
    YouTubePublisher.platform: YouTubePublisher,
    NaverBlogPublisher.platform: NaverBlogPublisher,
}


def publisher_for(platform: str, home: Path, *, env: dict[str, str] | None = None) -> Publisher:
    cls = REGISTRY.get(platform)
    if cls is None:
        raise FactoryError("PLATFORM_UNKNOWN", str(platform)[:32])
    return cls(home, env=env)


def required_env(platform: str) -> tuple[str, ...]:
    cls = REGISTRY.get(platform)
    if cls is None:
        raise FactoryError("PLATFORM_UNKNOWN", str(platform)[:32])
    return cls.required_env


__all__ = ["InstagramPublisher", "NaverBlogPublisher", "Publisher", "PublishRequest",
           "PublishResult", "REGISTRY", "ThreadsPublisher", "TikTokPublisher",
           "YouTubePublisher", "publisher_for", "required_env"]

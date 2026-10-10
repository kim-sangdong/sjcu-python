"""저장소 안의 표준 경로와 주차(week) 계산.

주차 규칙: 일요일 정기 실행이 만드는 묶음은 "다음 날(월요일)이 속한 ISO 주차"로 부른다.
예) 2026-10-11(일) 12:00 실행 → 2026-W42 (10-12 월 ~ 10-18 일 발행분).
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def _find_root() -> Path:
    env = os.environ.get("STUDIO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve().parent
    for p in (here, *here.parents):
        if (p / "pyproject.toml").exists() and (p / "studio").is_dir():
            return p
    return Path.cwd().resolve()


ROOT = _find_root()
CONTENT = ROOT / "content"
OUT = ROOT / "out"
ARCHIVE = ROOT / "archive"
REPORTS = ROOT / "reports"
SECRETS = ROOT / ".secrets"
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"


def now_kst() -> datetime:
    return datetime.now(KST)


def week_of(d: date | datetime) -> str:
    """주어진 날짜가 속한 ISO 주차 문자열."""
    if isinstance(d, datetime):
        d = d.astimezone(KST).date() if d.tzinfo else d.date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def publishing_week(at: datetime | None = None) -> str:
    """정기 실행 시점 기준으로 이번에 만들 묶음의 주차(실행일 다음 날이 속한 주)."""
    at = at or now_kst()
    return week_of(at.astimezone(KST) + timedelta(days=1))


def week_monday(week: str) -> date:
    m = WEEK_RE.match(week)
    if not m:
        raise ValueError(f"주차 형식이 아닙니다: {week!r} (예: 2026-W42)")
    return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)


def previous_week(week: str) -> str:
    return week_of(week_monday(week) - timedelta(days=7))


def compact(week: str) -> str:
    """'2026-W42' → '2026W42' (콘텐츠 ID 접두어)."""
    return week.replace("-", "")


def content_dir(week: str) -> Path:
    return CONTENT / "weeks" / week


def out_dir(week: str) -> Path:
    return OUT / "weeks" / week


def reports_dir(week: str) -> Path:
    return REPORTS / week


def archive_index() -> Path:
    return ARCHIVE / "index.json"


# 콘텐츠 ID 규칙 — UTM의 utm_content 값으로도 그대로 쓰인다.
def longform_id(week: str, n: int = 1) -> str:
    return f"{compact(week)}-L{n}"


def short_id(week: str, n: int) -> str:
    return f"{compact(week)}-S{n:02d}"


def cardnews_id(week: str, n: int) -> str:
    return f"{compact(week)}-C{n:02d}"


def blog_id(week: str, n: int) -> str:
    return f"{compact(week)}-B{n:02d}"

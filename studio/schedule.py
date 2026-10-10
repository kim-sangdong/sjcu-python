"""주차별 발행 시각 계산.

- 정기 실행은 일요일 12:00(KST)이고, 그날 만든 묶음의 주차는 다음 날(월)이 속한 ISO 주차다.
- 롱폼은 실행 당일(그 주 월요일의 전날 일요일) `schedule.longform_publish` 시각에 공개된다.
- 쇼츠·카드뉴스·블로그는 해당 ISO 주(월~일) 안의 요일·시각에 공개된다.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from studio import paths
from studio.config import Config

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _hm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def run_datetime(week: str, cfg: Config) -> datetime:
    """이 주차 묶음을 만드는 정기 실행 시각(직전 일요일)."""
    _, hm = cfg.schedule.weekly_run.split()
    d = paths.week_monday(week) - timedelta(days=1)
    return datetime.combine(d, _hm(hm), tzinfo=paths.KST)


def longform_slot(week: str, cfg: Config) -> datetime:
    _, hm = cfg.schedule.longform_publish.split()
    d = paths.week_monday(week) - timedelta(days=1)
    return datetime.combine(d, _hm(hm), tzinfo=paths.KST)


def _week_day(week: str, day: str) -> datetime:
    return datetime.combine(paths.week_monday(week) + timedelta(days=DAYS.index(day.upper())), time(0), tzinfo=paths.KST)


def shorts_slots(week: str, cfg: Config) -> list[datetime]:
    slots = []
    for i in range(7):
        for hm in cfg.schedule.shorts_times:
            d = paths.week_monday(week) + timedelta(days=i)
            slots.append(datetime.combine(d, _hm(hm), tzinfo=paths.KST))
    n = cfg.schedule.shorts_per_week
    if n > len(slots):
        raise ValueError(f"쇼츠 {n}편을 넣을 시간대가 부족합니다(shorts_times 를 늘리세요).")
    return slots[:n]


def day_slots(week: str, days: list[str], hm: str, n: int) -> list[datetime]:
    base = [_week_day(week, d).replace(hour=_hm(hm).hour, minute=_hm(hm).minute) for d in days]
    if n > len(base):
        # 요일보다 편수가 많으면 같은 요일에 2시간 간격으로 더 넣는다.
        extra = [b + timedelta(hours=2 * (k // len(base) + 1)) for k, b in enumerate(base * ((n // len(base)) + 1))]
        base = base + extra
    return sorted(base)[:n]


def cardnews_slots(week: str, cfg: Config) -> list[datetime]:
    s = cfg.schedule
    return day_slots(week, s.cardnews_days, s.cardnews_time, s.cardnews_per_week)


def blog_slots(week: str, cfg: Config) -> list[datetime]:
    s = cfg.schedule
    return day_slots(week, s.blog_days, s.blog_time, s.blog_per_week)

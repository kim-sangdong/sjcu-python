"""--fake 합성 데이터. 보고서 형식 확인·리허설용이며 실제 성과가 아니다(모든 결과에 fake=true 표시).

같은 주차면 언제 돌려도 같은 숫자가 나온다(주차로 시드). 실제 API 응답과 같은 모양을 만들어
수집기의 해석 코드를 그대로 통과시킨다. 귀속 규칙의 모든 갈래(콘텐츠 귀속, 플랫폼 귀속, utm 없음,
스토어 자연 유입, 7일 창 밖, 체험→유료, 환불, SANDBOX, 기간 전 설치의 갱신 결제)가 한 번씩은 나오게 만든다.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import Any

from studio import paths, schedule
from studio.analytics.common import Period, Settings, Target
from studio.config import Config

PLATFORMS_BY_KIND = {
    "longform": ("youtube",),
    "short": ("youtube", "instagram", "facebook", "tiktok"),
    "cardnews": ("instagram",),
    "blog": ("tistory",),
}
TRAFFIC_TYPES = (("SHORTS", 0.46), ("SUGGESTED", 0.2), ("BROWSE", 0.12), ("YT_SEARCH", 0.1), ("SUBSCRIBER", 0.07), ("EXT_URL", 0.05))
UNMAPPED_VIDEO = "fakeytunmapped01"
PRICE_USD, PRICE_KRW = 9.99, 14000


def _rng(*keys: Any) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, keys)).encode()).hexdigest()[:16], 16))


def _remote_id(platform: str, asset_id: str) -> str:
    digest = hashlib.sha256(f"{platform}:{asset_id}".encode()).hexdigest()
    if platform == "tiktok":
        return str(7_000_000_000_000_000_000 + int(digest[:12], 16))    # 틱톡 게시물 ID 처럼 숫자
    return f"fake{platform[:2]}{digest[:10]}"


def synth_targets(week: str, cfg: Config, period: Period) -> list[Target]:
    """queue.json 이 없을 때 쓰는 가상 게시물(이번 주 계획 규칙대로)."""
    slots: list[tuple[str, str, datetime]] = [(paths.longform_id(week), "longform", schedule.longform_slot(week, cfg))]
    slots += [(paths.short_id(week, i + 1), "short", t) for i, t in enumerate(schedule.shorts_slots(week, cfg))]
    slots += [(paths.cardnews_id(week, i + 1), "cardnews", t) for i, t in enumerate(schedule.cardnews_slots(week, cfg))]
    slots += [(paths.blog_id(week, i + 1), "blog", t) for i, t in enumerate(schedule.blog_slots(week, cfg))]
    out = []
    for asset_id, kind, at in slots:
        at = max(at, period.start)        # 롱폼은 전날(일) 공개 → 기간 안에 넣는다
        if at >= period.end:
            continue
        for platform in PLATFORMS_BY_KIND[kind]:
            out.append(Target(asset_id, kind, platform, _remote_id(platform, asset_id), at, "published", None, week))
    return out


class FakeWorld:
    def __init__(self, week: str, period: Period, targets: list[Target], st: Settings):
        self.week, self.period, self.st = week, period, st
        self.targets = [t for t in targets if t.publish_at < period.end]
        self.stats: dict[tuple[str, str], dict[str, float]] = {}
        for t in self.targets:
            r = _rng(week, t.platform, t.asset_id)
            if t.platform == "youtube":
                views = r.randint(800, 6000) if t.kind == "short" else r.randint(500, 2500)
                avg = r.uniform(18, 40) if t.kind == "short" else r.uniform(240, 600)
                self.stats[(t.platform, t.remote_id)] = {
                    "views": views, "avg_sec": avg, "avg_pct": min(r.uniform(35, 95), 100) if t.kind == "short" else r.uniform(20, 45),
                    "subs_gained": r.randint(0, max(1, views // 300)), "subs_lost": r.randint(0, 3),
                    "likes": int(views * r.uniform(0.02, 0.06)), "comments": int(views * r.uniform(0.001, 0.006)),
                    "shares": int(views * r.uniform(0.001, 0.01)), "revenue": round(views / 1000 * r.uniform(0.1, 0.6), 2)}
            elif t.platform in ("instagram", "facebook", "tiktok"):
                views = r.randint(200, 4000)
                self.stats[(t.platform, t.remote_id)] = {
                    "views": views, "reach": int(views * r.uniform(0.6, 0.9)), "likes": int(views * r.uniform(0.02, 0.08)),
                    "comments": int(views * r.uniform(0.001, 0.008)), "shares": int(views * r.uniform(0.002, 0.02)),
                    "saves": int(views * r.uniform(0.005, 0.04)), "avg_ms": r.randint(6000, 25000)}
        self.stats[("youtube", UNMAPPED_VIDEO)] = {"views": 350, "avg_sec": 120.0, "avg_pct": 30.0, "subs_gained": 1, "subs_lost": 0,
                                                   "likes": 9, "comments": 1, "shares": 0, "revenue": 0.1}

    # ------------------------------------------------------------ 유튜브

    def youtube(self, params: dict[str, Any]) -> dict[str, Any]:
        metrics = str(params["metrics"]).split(",")
        dim = params.get("dimensions")
        vids = [k[1] for k in self.stats if k[0] == "youtube"]
        filt = str(params.get("filters") or "")
        if filt.startswith("video=="):
            vids = [v for v in vids if v in set(filt[len("video=="):].split(","))]
        if dim == "video":
            vids.sort(key=lambda v: -self.stats[("youtube", v)]["views"])
            start, size = int(params.get("startIndex", 1)), int(params.get("maxResults", 200))
            rows = [[v, *[self._yt_metric(v, m) for m in metrics]] for v in vids[start - 1:start - 1 + size]]
            heads = ["video", *metrics]
        else:
            total = sum(self.stats[("youtube", v)]["views"] for v in vids)
            rows = [[name, round(total * share), round(total * share * 0.6, 1)][: 1 + len(metrics)] for name, share in TRAFFIC_TYPES]
            heads = [str(dim), *metrics]
        return {"kind": "youtubeAnalytics#resultTable", "columnHeaders": [{"name": h} for h in heads], "rows": rows}

    def _yt_metric(self, vid: str, m: str) -> float:
        s = self.stats[("youtube", vid)]
        return {
            "views": s["views"], "estimatedMinutesWatched": round(s["views"] * s["avg_sec"] / 60, 1),
            "averageViewDuration": round(s["avg_sec"]), "averageViewPercentage": round(s["avg_pct"], 1),
            "subscribersGained": s["subs_gained"], "subscribersLost": s["subs_lost"], "likes": s["likes"],
            "comments": s["comments"], "shares": s["shares"], "estimatedRevenue": s["revenue"],
            "estimatedAdRevenue": round(s["revenue"] * 0.9, 2), "cpm": 1.2,
        }.get(m, 0)

    # ------------------------------------------------------------ 인스타·페북·틱톡

    def instagram(self, media_id: str, metrics: list[str]) -> dict[str, Any]:
        s = self.stats.get(("instagram", media_id), {})
        names = {"views": "views", "reach": "reach", "likes": "likes", "comments": "comments", "shares": "shares",
                 "saved": "saves", "ig_reels_avg_watch_time": "avg_ms"}
        data = []
        for m in metrics:
            v = s.get(names.get(m, ""), None)
            if m == "total_interactions":
                v = sum(int(s.get(k, 0)) for k in ("likes", "comments", "shares", "saves"))
            data.append({"name": m, "period": "lifetime", "values": [{"value": v if v is not None else 0}]})
        return {"data": data}

    def facebook(self, video_id: str, metrics: list[str]) -> dict[str, Any]:
        s = self.stats.get(("facebook", video_id), {})
        vals = {"blue_reels_play_count": s.get("views", 0), "post_impressions_unique": s.get("reach", 0),
                "post_video_avg_time_watched": s.get("avg_ms", 0),
                "post_video_social_actions": {"REACTION": s.get("likes", 0), "COMMENT": s.get("comments", 0), "SHARE": s.get("shares", 0)}}
        return {"data": [{"name": m, "period": "lifetime", "values": [{"value": vals.get(m, 0)}]} for m in metrics]}

    def tiktok(self, ids: list[str]) -> dict[str, Any]:
        videos = []
        for vid in ids:
            s = self.stats.get(("tiktok", vid))
            if s:
                videos.append({"id": vid, "title": "", "view_count": s["views"], "like_count": s["likes"],
                               "comment_count": s["comments"], "share_count": s["shares"]})
        return {"data": {"videos": videos, "cursor": 0, "has_more": False}, "error": {"code": "ok", "message": ""}}

    # ------------------------------------------------------------ 클릭·설치·결제

    def funnel(self) -> dict[str, Any]:
        p, r = self.period, _rng(self.week, "funnel")
        clicks: list[dict[str, Any]] = []
        installs: list[dict[str, Any]] = []
        purchases: list[dict[str, Any]] = []
        n = {"click": 0, "user": 0, "event": 0}

        def at(lo: datetime, frac: float) -> datetime:
            return lo + timedelta(seconds=max(0.0, (p.end - lo).total_seconds()) * frac)

        def click(t: datetime, source: str, content: str, medium: str) -> None:
            n["click"] += 1
            clicks.append({"id": n["click"], "created_at": t.isoformat(), "source": source, "medium": medium,
                           "campaign": self.week, "content": content, "os": r.choice(("android", "android", "ios"))})

        def user() -> str:
            n["user"] += 1
            return "u_" + hashlib.sha256(f"{self.week}:{n['user']}".encode()).hexdigest()[:16]

        def install(t: datetime, *, source: str | None, content: str | None, os_: str = "android", medium: str | None = None) -> str:
            uid = user()
            installs.append({"id": n["user"], "created_at": t.isoformat(), "app_user_id": uid, "os": os_, "utm_source": source,
                             "utm_medium": medium, "utm_campaign": self.week if content else None, "utm_content": content,
                             "app_version": "1.0.0"})
            return uid

        def event(t: datetime, uid: str, etype: str, *, period_type: str = "NORMAL", usd: float = PRICE_USD,
                  krw: float = PRICE_KRW, env: str = "PRODUCTION", conv: bool = False, cancel_reason: str | None = None) -> None:
            if t >= p.end:
                return
            n["event"] += 1
            purchases.append({"id": n["event"], "created_at": (t + timedelta(minutes=1)).isoformat(),
                              "event_id": f"fake-{self.week}-{n['event']}", "event_type": etype, "app_user_id": uid,
                              "product_id": "premium_monthly", "period_type": period_type, "price": krw, "currency": "KRW",
                              "price_in_usd": usd, "store": "PLAY_STORE", "environment": env, "event_timestamp": t.isoformat(),
                              "is_trial_conversion": "true" if conv else "false", "cancel_reason": cancel_reason})

        def journey(uid: str, t: datetime) -> None:
            x = r.random()
            if x < 0.30:
                trial = t + timedelta(hours=r.uniform(0.2, 6))
                event(trial, uid, "INITIAL_PURCHASE", period_type="TRIAL", usd=0, krw=0)
                if r.random() < 0.5:
                    event(trial + timedelta(days=3), uid, "RENEWAL", conv=True)
            elif x < 0.40:
                event(t + timedelta(hours=r.uniform(0.5, 30)), uid, "INITIAL_PURCHASE")

        # 콘텐츠 링크(유튜브 설명·고정댓글, 블로그 본문): content = 콘텐츠 ID
        for t in self.targets:
            if t.platform == "youtube":
                views = self.stats[(t.platform, t.remote_id)]["views"]
                k = int(views * r.uniform(0.004, 0.03))
            elif t.platform == "tistory":
                k = r.randint(4, 40)
            else:
                continue
            medium = "description" if t.kind == "longform" else ("shorts" if t.kind == "short" else "post")
            for _ in range(k):
                ct = at(max(t.publish_at, p.start), r.random() * 0.95)
                click(ct, t.platform, t.asset_id, medium)
                if r.random() < 0.22:
                    it = ct + timedelta(minutes=r.uniform(2, 60 * 24 * 2))
                    if it < p.end:
                        journey(install(it, source=t.platform, content=t.asset_id, medium=medium), it)
        # 인스타·페북·틱톡은 프로필 링크(content=profile) → 플랫폼 귀속
        for platform in ("instagram", "facebook", "tiktok"):
            for _ in range(r.randint(15, 60)):
                ct = at(p.start, r.random() * 0.95)
                click(ct, platform, "profile", "profile")
                if r.random() < 0.2:
                    it = ct + timedelta(minutes=r.uniform(2, 600))
                    if it < p.end:
                        journey(install(it, source=platform, content="profile", medium="profile"), it)
        # iOS(utm 없음)·스토어 자연 유입
        for _ in range(max(2, len(installs) // 6)):
            it = at(p.start, r.random())
            journey(install(it, source=None, content=None, os_="ios"), it)
        for _ in range(2):
            it = at(p.start, r.random())
            install(it, source="google-play", content=None, medium="organic")
        # 7일 창 밖: 지난주 콘텐츠의 클릭이 10일 전 → 이번 주 설치
        prev = paths.compact(paths.previous_week(self.week))
        old_click = p.start - timedelta(days=10)
        click(old_click, "youtube", f"{prev}-S05", "shorts")
        uid = install(at(p.start, 0.1), source="youtube", content=f"{prev}-S05", medium="shorts")
        event(at(p.start, 0.2), uid, "INITIAL_PURCHASE")
        # 기간 전 설치(3주 전 롱폼) → 이번 주 갱신 결제
        old = paths.compact(paths.week_of(p.start - timedelta(days=21)))
        for i in range(3):
            ct = p.start - timedelta(days=20 - i)
            click(ct, "youtube", f"{old}-L1", "description")
            uid = install(ct + timedelta(hours=3), source="youtube", content=f"{old}-L1", medium="description")
            event(at(p.start, 0.1 + 0.2 * i), uid, "RENEWAL")
        # 환불 1건(첫 유료 결제자) · SANDBOX 1건(제외 대상)
        paid = next((e for e in purchases if e["event_type"] == "INITIAL_PURCHASE" and e["period_type"] == "NORMAL"
                     and e["environment"] == "PRODUCTION"), None)
        if paid:
            t = datetime.fromisoformat(paid["event_timestamp"]) + timedelta(hours=12)
            event(min(t, p.end - timedelta(seconds=1)), paid["app_user_id"], "CANCELLATION", usd=-PRICE_USD, krw=-PRICE_KRW,
                  cancel_reason="CUSTOMER_SUPPORT")
        if installs:
            event(at(p.start, 0.5), installs[0]["app_user_id"], "INITIAL_PURCHASE", env="SANDBOX")
        return {"link_clicks": clicks, "app_installs": installs, "purchases": purchases}

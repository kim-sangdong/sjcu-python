"""매출 귀속 — 클릭(link_clicks.content = 콘텐츠 ID) → 설치(app_installs.utm_content) → 결제(purchases, app_user_id 로 설치와 조인).

규칙
- 설치 귀속: utm_content 가 콘텐츠 ID 이고, 같은 원천(utm_source)·같은 content 의 클릭이 설치 전 귀속 창(기본 7일) 안에 있으면
  그 (콘텐츠, 플랫폼)으로 귀속한다. content 가 콘텐츠 ID 가 아니면(예: 프로필 링크 'profile') 플랫폼까지만 귀속한다.
- 미귀속 설치: utm 없음(iOS 등) · 스토어 자연 유입(google-play/organic) · 창 밖(창 안 클릭 없음) · 원천 불명.
- 결제: 이벤트 시각(event_timestamp, 없으면 created_at)이 기간 안인 것만. 설치 귀속을 그대로 따른다(결제 시점 제한 없음).
  설치 기록이 없는 사용자의 결제는 미귀속(설치 기록 없음).
- RevenueCat 이벤트 처리(EVENT_KIND): 첫 결제(체험이면 체험 시작) · 갱신(체험→유료 전환이면 유료 전환) · 단건 · 환불(차감) ·
  환불 취소(복구) · 그 밖의 상태 변화(해지·만료·결제 문제 등)는 건수만. TEST 와 SANDBOX(기본) 는 제외.
- 금액: price_in_usd 우선. 없으면 currency 가 USD 면 price, KRW 면 price ÷ 환율. 그 밖의 통화는 금액 모름(경고).
"""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from studio.analytics.common import FUNNEL_PLATFORMS, ORGANIC_SOURCES, Period, is_asset_id, norm_source, num, parse_time

# ---------------------------------------------------------------- 상수 (RevenueCat 이벤트 — docs/research/facts-app-stack.md 2-3)

EVENT_KIND = {
    "INITIAL_PURCHASE": "initial",          # period_type=TRIAL 이면 체험 시작, 아니면 첫 유료 결제
    "RENEWAL": "renewal",                   # is_trial_conversion=true 면 체험→유료 전환
    "NON_RENEWING_PURCHASE": "one_time",    # 단건 구매
    "PRODUCT_CHANGE": "info",               # 상품 변경 알림 — 실제 청구는 갱신 이벤트로 온다고 보고 금액을 넣지 않음 [미확인]
    "CANCELLATION": "cancellation",         # 해지. 환불이면 price 가 음수이거나 cancel_reason=CUSTOMER_SUPPORT [기억·미확인]
    "REFUND": "refund",                     # 제3자 처리기 형식 [미확인]
    "REFUND_REVERSED": "refund_reversed",
    "UNCANCELLATION": "info",
    "EXPIRATION": "info",
    "BILLING_ISSUE": "info",
    "SUBSCRIPTION_PAUSED": "info",
    "SUBSCRIPTION_EXTENDED": "info",
    "TRANSFER": "info",
    "TEMPORARY_ENTITLEMENT_GRANT": "info",
    "INVOICE_ISSUANCE": "info",
    "VIRTUAL_CURRENCY_TRANSACTION": "info",
    "TEST": "test",
    # RevenueCat 표준이 아닌 별칭(다른 결제 백엔드·수동 입력 대비). RevenueCat 은 체험 시작을 INITIAL_PURCHASE+TRIAL 로 보낸다.
    "TRIAL_STARTED": "trial_start",
    "TRIAL_CONVERTED": "trial_conversion",
    "TRIAL_CANCELLED": "info",
}
TRIAL_PERIOD_TYPES = {"TRIAL"}
FREE_PERIOD_TYPES = {"PROMOTIONAL"}         # 무료 부여 — 유료 전환으로 세지 않음
REFUND_CANCEL_REASONS = {"CUSTOMER_SUPPORT"}
CLICK_TOLERANCE = timedelta(minutes=10)     # 기기·서버 시계 차이 허용(설치가 클릭보다 살짝 앞서 기록돼도 인정)

UNATTRIBUTED_LABEL = {
    "no_utm": "utm 없음(iOS 등)",
    "organic": "스토어 자연 유입",
    "window_expired": "귀속 창 밖(창 안 클릭 없음)",
    "unknown_source": "원천 불명",
    "no_install": "설치 기록 없는 결제",
}


# ---------------------------------------------------------------- 결과 구조

@dataclass
class Funnel:
    clicks: int = 0
    installs: int = 0
    trials: int = 0
    paid: int = 0            # 유료 전환(첫 유료 결제 + 체험→유료 + 단건)
    renewals: int = 0
    cancellations: int = 0
    refunds: int = 0
    gross_usd: float = 0.0   # 결제 합(환불 전)
    refund_usd: float = 0.0

    @property
    def revenue_usd(self) -> float:
        return self.gross_usd - self.refund_usd

    def add(self, o: Funnel) -> None:
        for f in ("clicks", "installs", "trials", "paid", "renewals", "cancellations", "refunds", "gross_usd", "refund_usd"):
            setattr(self, f, getattr(self, f) + getattr(o, f))

    def to_dict(self, usd_krw: float) -> dict[str, Any]:
        return {
            "clicks": self.clicks, "installs": self.installs, "trials": self.trials, "paid": self.paid,
            "renewals": self.renewals, "cancellations": self.cancellations, "refunds": self.refunds,
            "gross_usd": round(self.gross_usd, 4), "refund_usd": round(self.refund_usd, 4),
            "revenue_usd": round(self.revenue_usd, 4), "revenue_krw": round(self.revenue_usd * usd_krw),
            "refund_krw": round(self.refund_usd * usd_krw),
        }


@dataclass
class InstallAttribution:
    uid: str
    at: datetime | None
    status: str                  # content | platform | no_utm | organic | window_expired | unknown_source
    asset_id: str | None = None
    platform: str | None = None
    in_period: bool = False


@dataclass
class Attribution:
    period: Period
    window_days: int
    usd_krw: float
    by_content: dict[tuple[str, str], Funnel] = field(default_factory=dict)   # (콘텐츠 ID, 플랫폼)
    by_platform: dict[str, Funnel] = field(default_factory=dict)              # 귀속분 합(콘텐츠 + 플랫폼 수준)
    unattributed: Funnel = field(default_factory=Funnel)
    total: Funnel = field(default_factory=Funnel)
    unattributed_installs: Counter = field(default_factory=Counter)           # 사유 → 기간 안 설치 수
    unattributed_events: Counter = field(default_factory=Counter)             # 사유 → 결제 이벤트 수
    excluded: Counter = field(default_factory=Counter)                        # sandbox | test | out_of_period | duplicate | unknown_price ...
    event_types: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def content(self, asset_id: str, platform: str) -> Funnel:
        return self.by_content.setdefault((asset_id, platform), Funnel())

    def platform(self, platform: str) -> Funnel:
        return self.by_platform.setdefault(platform, Funnel())

    def to_dict(self) -> dict[str, Any]:
        r = self.usd_krw
        return {
            "window_days": self.window_days,
            "usd_krw": self.usd_krw,
            "total": self.total.to_dict(r),
            "by_platform": {k: v.to_dict(r) for k, v in sorted(self.by_platform.items())},
            "by_content": [{"asset_id": a, "platform": p, **f.to_dict(r)} for (a, p), f in sorted(self.by_content.items())],
            "unattributed": self.unattributed.to_dict(r),
            "unattributed_installs": dict(self.unattributed_installs),
            "unattributed_events": dict(self.unattributed_events),
            "excluded": dict(self.excluded),
            "event_types": dict(self.event_types),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------- 계산

def _text(v: Any) -> str:
    return str(v or "").strip()


def _flag(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return _text(v).lower() in ("true", "1", "yes", "t")


def amount_usd(p: dict[str, Any], usd_krw: float) -> float | None:
    v = num(p.get("price_in_usd"))
    if v is not None:
        return v
    price, cur = num(p.get("price")), _text(p.get("currency")).upper()
    if price is None:
        return None
    if cur in ("", "USD"):
        return price
    if cur == "KRW":
        return price / usd_krw
    return None


class _ClickIndex:
    def __init__(self, clicks: Iterable[dict[str, Any]]):
        by_key: dict[tuple[str, str], list[datetime]] = defaultdict(list)
        by_content: dict[str, list[datetime]] = defaultdict(list)
        by_source: dict[str, list[datetime]] = defaultdict(list)
        for c in clicks:
            t = parse_time(c.get("created_at"))
            if t is None:
                continue
            source, content = norm_source(c.get("source")), _text(c.get("content"))
            by_source[source].append(t)
            if content:
                by_key[(source, content)].append(t)
                by_content[content].append(t)
        self.by_key = {k: sorted(v) for k, v in by_key.items()}
        self.by_content = {k: sorted(v) for k, v in by_content.items()}
        self.by_source = {k: sorted(v) for k, v in by_source.items()}

    def within(self, source: str, content: str, at: datetime, window: timedelta) -> bool:
        """설치 시각 at 기준 [at − window, at + 허용오차] 안에 맞는 클릭이 있는지.
        content 가 있으면 (원천, content) — 원천을 모르면 content 만, content 가 없으면 원천만 본다."""
        if content:
            times = self.by_key.get((source, content)) if source else self.by_content.get(content)
        else:
            times = self.by_source.get(source)
        if not times:
            return False
        i = bisect.bisect_right(times, at + CLICK_TOLERANCE)
        return i > 0 and times[i - 1] >= at - window


def classify_install(inst: dict[str, Any], clicks: _ClickIndex, window: timedelta, *, require_click: bool,
                     period: Period) -> InstallAttribution:
    uid = _text(inst.get("app_user_id"))
    at = parse_time(inst.get("created_at"))
    out = InstallAttribution(uid=uid, at=at, status="no_utm", in_period=period.contains(at))
    raw_src, content = _text(inst.get("utm_source")).lower(), _text(inst.get("utm_content"))
    medium = _text(inst.get("utm_medium")).lower()
    if not raw_src and not content:
        return out
    if raw_src in ORGANIC_SOURCES and (not content or medium in ("", "organic")):
        out.status = "organic"
        return out
    src = norm_source(raw_src)
    platform = src if src in FUNNEL_PLATFORMS else None
    asset = content if is_asset_id(content) else None
    if not asset and not platform:
        out.status = "unknown_source"
        return out
    if require_click and (at is None or not clicks.within(platform or "", content, at, window)):
        out.status = "window_expired"
        return out
    out.asset_id, out.platform = asset, platform or "other"
    out.status = "content" if asset else "platform"
    return out


def attribute(clicks: list[dict[str, Any]], installs: list[dict[str, Any]], purchases: list[dict[str, Any]],
              period: Period, *, window_days: int = 7, usd_krw: float = 1400.0, require_click: bool = True,
              include_sandbox: bool = False) -> Attribution:
    res = Attribution(period=period, window_days=window_days, usd_krw=usd_krw)
    window = timedelta(days=window_days)
    index = _ClickIndex(clicks)

    # 1) 기간 안 클릭
    for c in clicks:
        t = parse_time(c.get("created_at"))
        if not period.contains(t):
            continue
        content, platform = _text(c.get("content")), norm_source(c.get("source")) or "other"
        platform = platform if platform in FUNNEL_PLATFORMS else "other"
        for f in _funnels(res, is_asset_id(content) and content or None, platform, attributed=True):
            f.clicks += 1

    # 2) 설치 귀속(사용자당 가장 이른 설치 한 건)
    users: dict[str, InstallAttribution] = {}
    for inst in sorted(installs, key=lambda r: parse_time(r.get("created_at")) or period.end):
        uid = _text(inst.get("app_user_id"))
        if not uid or uid in users:
            if uid:
                res.excluded["duplicate_install"] += 1
            continue
        a = classify_install(inst, index, window, require_click=require_click, period=period)
        users[uid] = a
        if not a.in_period:
            continue
        attributed = a.status in ("content", "platform")
        for f in _funnels(res, a.asset_id, a.platform, attributed=attributed):
            f.installs += 1
        if not attributed:
            res.unattributed_installs[a.status] += 1
    utm_installs = sum(1 for a in users.values() if a.in_period and a.status in ("content", "platform", "window_expired"))
    if utm_installs and not clicks:
        res.warnings.append("utm 이 있는 설치가 있는데 클릭 기록(link_clicks)이 하나도 없습니다 — /go 함수의 클릭 저장을 확인하세요."
                            " 이 상태에서는 모두 '귀속 창 밖'으로 집계됩니다.")

    # 3) 결제
    seen: set[str] = set()
    unknown_price = 0
    for p in sorted(purchases, key=lambda r: parse_time(r.get("event_timestamp")) or parse_time(r.get("created_at")) or period.end):
        eid = _text(p.get("event_id")) or f"row-{p.get('id')}"
        if eid in seen:
            res.excluded["duplicate_event"] += 1
            continue
        seen.add(eid)
        etype = _text(p.get("event_type")).upper()
        t = parse_time(p.get("event_timestamp")) or parse_time(p.get("created_at"))
        if not period.contains(t):
            res.excluded["out_of_period"] += 1
            continue
        kind = EVENT_KIND.get(etype, "info")
        if kind == "test":
            res.excluded["test"] += 1
            continue
        if _text(p.get("environment")).upper() == "SANDBOX" and not include_sandbox:
            res.excluded["sandbox"] += 1
            continue
        res.event_types[etype or "(없음)"] += 1
        uid = _text(p.get("app_user_id"))
        a = users.get(uid)
        attributed = bool(a and a.status in ("content", "platform"))
        if not attributed:
            res.unattributed_events[a.status if a else "no_install"] += 1
        targets = _funnels(res, a.asset_id if a else None, a.platform if a else None, attributed=attributed)
        if not _apply(p, kind, targets, usd_krw):
            unknown_price += 1
    if unknown_price:
        res.excluded["unknown_price"] = unknown_price
        res.warnings.append(f"금액을 알 수 없는 결제 이벤트 {unknown_price}건(price_in_usd 없음·통화 미지원)은 건수만 세고 매출에서 뺐습니다.")
    if etypes := [k for k in res.event_types if k not in EVENT_KIND]:
        res.warnings.append(f"처리 규칙이 없는 결제 이벤트 종류 {', '.join(etypes)} 는 건수만 셌습니다(EVENT_KIND 확인).")
    return res


def _funnels(res: Attribution, asset_id: str | None, platform: str | None, *, attributed: bool) -> list[Funnel]:
    if not attributed:
        return [res.unattributed, res.total]
    out = [res.platform(platform or "other"), res.total]
    if asset_id:
        out.insert(0, res.content(asset_id, platform or "other"))
    return out


def _apply(p: dict[str, Any], kind: str, funnels: list[Funnel], usd_krw: float) -> bool:
    """이벤트 하나를 깔때기들에 반영. 금액이 필요한데 모르면 False."""
    amt = amount_usd(p, usd_krw)
    period_type = _text(p.get("period_type")).upper()
    needs_amount = False
    for f in funnels:
        if kind == "initial":
            if period_type in TRIAL_PERIOD_TYPES:
                f.trials += 1
            elif period_type not in FREE_PERIOD_TYPES:
                f.paid += 1
                needs_amount = True
                f.gross_usd += amt or 0.0
        elif kind == "renewal":
            if _flag(p.get("is_trial_conversion")):
                f.paid += 1
            else:
                f.renewals += 1
            needs_amount = True
            f.gross_usd += amt or 0.0
        elif kind == "trial_start":
            f.trials += 1
        elif kind in ("trial_conversion", "one_time"):
            f.paid += 1
            needs_amount = True
            f.gross_usd += amt or 0.0
        elif kind == "cancellation":
            if (amt is not None and amt < 0) or _text(p.get("cancel_reason")).upper() in REFUND_CANCEL_REASONS:
                f.refunds += 1
                needs_amount = True
                f.refund_usd += abs(amt or 0.0)
            else:
                f.cancellations += 1
        elif kind == "refund":
            f.refunds += 1
            needs_amount = True
            f.refund_usd += abs(amt or 0.0)
        elif kind == "refund_reversed":
            needs_amount = True
            f.refund_usd -= abs(amt or 0.0)
    return not (needs_amount and amt is None)

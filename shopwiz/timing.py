"""Buy now or wait? Judges a wishlist item against today's price, other stores, your own price history and the sale calendar.

The sale calendar and the typical discounts below are rules of thumb from public sale-calendar guides, not
measurements, so every verdict says how sure it is. The price history is the part that is real: ShopWiz records
the price each time you check an item, and after a few checks the verdict leans on that instead of the rules.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

WAIT_HORIZON_DAYS = 45  # a sale further away than this isn't worth holding a purchase for
WAIT_IF_EXTRA_DROP = 0.07  # wait only when the sale is likely to beat today's price by at least 7%


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month (Monday=0). n=-1 means the last one."""
    days = [d for d in calendar.Calendar().itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    return days[n - 1] if n > 0 else days[-1]


def black_friday(year: int) -> date:
    return _nth_weekday(year, 11, 3, 4) + timedelta(days=1)  # the day after the 4th Thursday of November


# key: (name, date for a year, whether the date is only approximate)
EVENTS = {
    "super_bowl": ("Super Bowl TV sales", lambda y: _nth_weekday(y, 2, 6, 2) - timedelta(days=5), True),
    "presidents_day": ("Presidents Day sales", lambda y: _nth_weekday(y, 2, 0, 3), False),
    "memorial_day": ("Memorial Day sales", lambda y: _nth_weekday(y, 5, 0, -1), False),
    "prime_day": ("Prime Day (mid-July)", lambda y: date(y, 7, 12), True),
    "back_to_school": ("Back-to-school sales", lambda y: date(y, 7, 25), True),
    "labor_day": ("Labor Day sales", lambda y: _nth_weekday(y, 9, 0, 1), False),
    "prime_fall": ("Prime Big Deal Days (early October)", lambda y: date(y, 10, 7), True),
    "black_friday": ("Black Friday", black_friday, False),
    "cyber_monday": ("Cyber Monday", lambda y: black_friday(y) + timedelta(days=3), False),
}

# Typical share off the list price at each sale, by kind of product. Conservative rules of thumb.
# Each entry is (keywords, label, {event: typical cut}).
CATEGORIES = [
    (r"\b(iphone|ipad|macbook|airpods|apple watch|apple)\b", "Apple products", {"black_friday": 0.12, "back_to_school": 0.08}),
    (r"\b(tv|television|oled|qled|soundbar)\b", "TVs", {"black_friday": 0.30, "super_bowl": 0.25, "prime_fall": 0.2}),
    (r"\b(laptop|chromebook|notebook|monitor|desktop)\b", "laptops and monitors", {"black_friday": 0.25, "back_to_school": 0.18, "prime_day": 0.2}),
    (r"\b(headphones?|earbuds?|buds|earphones?|speaker)\b", "headphones and speakers", {"black_friday": 0.30, "prime_day": 0.25, "prime_fall": 0.2}),
    (r"\b(tablet|kindle|e-reader|fire hd)\b", "tablets", {"black_friday": 0.25, "prime_day": 0.25, "prime_fall": 0.2}),
    (r"\b(phone|galaxy|pixel)\b", "phones", {"black_friday": 0.15}),
    (r"\b(playstation|ps5|xbox|nintendo|switch|console|controller)\b", "game consoles", {"black_friday": 0.15}),
    (r"\b(watch|fitbit|garmin|oura|smartwatch)\b", "watches and trackers", {"black_friday": 0.30, "prime_day": 0.25}),
    (r"\b(camera|gopro|drone)\b", "cameras", {"black_friday": 0.25}),
    (r"\b(vacuum|roomba|robot|mop)\b", "vacuums", {"black_friday": 0.35, "prime_day": 0.3, "prime_fall": 0.25}),
    (r"\b(air fryer|blender|instant pot|coffee|espresso|mixer|toaster|kettle|cooker)\b", "small kitchen appliances",
     {"black_friday": 0.40, "prime_day": 0.3, "prime_fall": 0.3}),
    (r"\b(refrigerator|fridge|washer|dryer|dishwasher|range|oven|microwave|freezer)\b", "large appliances",
     {"memorial_day": 0.25, "labor_day": 0.25, "black_friday": 0.25}),
    (r"\b(mattress|bed)\b", "mattresses", {"presidents_day": 0.35, "memorial_day": 0.35, "labor_day": 0.35, "black_friday": 0.35}),
    (r"\b(lego|toy|board game)\b", "toys", {"black_friday": 0.25, "prime_day": 0.2}),
    (r"\b(drill|saw|tool)\b", "tools", {"black_friday": 0.30, "memorial_day": 0.2}),
]
DEFAULT_CATEGORY = ("most products", {"black_friday": 0.20})


def categorize(title: str) -> tuple[str, dict[str, float]]:
    text = (title or "").lower()
    for pattern, label, cuts in CATEGORIES:
        if re.search(pattern, text):
            return label, cuts
    return DEFAULT_CATEGORY


def upcoming(today: date, count: int = 4) -> list[dict]:
    """The next few sale events, soonest first."""
    found = []
    for key, (name, when, approx) in EVENTS.items():
        d = when(today.year)
        if d < today:
            d = when(today.year + 1)
        found.append({"key": key, "name": name, "date": d.isoformat(), "days": (d - today).days, "approx": approx})
    return sorted(found, key=lambda e: e["days"])[:count]


@dataclass
class Timing:
    verdict: str  # buy_now, wait or skip
    headline: str
    reasons: list[str]
    score: float  # 0-100, how good buying today is, used to rank a wishlist
    confidence: str  # low, medium or high
    target_price: float | None = None  # a price worth buying at, when the verdict is wait
    wait_until: str | None = None
    event: str | None = None
    deal_pct: float | None = None  # how far below the list price today's price is
    category: str = ""
    history: dict | None = None
    extras: dict = field(default_factory=dict)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _history_facts(net: float, history: list[dict]) -> dict | None:
    pts = [(h["d"], float(h["p"])) for h in history if h.get("d") and h.get("p")]
    if len(pts) < 2:
        return None
    pts.sort()
    prices = [p for _, p in pts]
    days = (date.fromisoformat(pts[-1][0]) - date.fromisoformat(pts[0][0])).days
    return {"low": min(prices), "high": max(prices), "avg": sum(prices) / len(prices), "first": prices[0],
            "points": len(pts), "days": days, "at_low": net <= min(prices) * 1.005 and len(pts) >= 3}


def evaluate(title: str, net: float, nets: list[float], list_price: float | None, rating: float | None,
             rating_count: int, history: list[dict], need_by: str | None, today: date) -> Timing:
    category, cuts = categorize(title)
    reasons: list[str] = []
    score = 50.0
    confidence = "low"

    # 1. How good is today's price against the list price and against the other stores?
    ref, ref_kind = None, ""
    if list_price and list_price > net:
        ref, ref_kind = list_price, "list price"
    elif len(nets) >= 4:
        ref, ref_kind = sorted(nets)[int(0.75 * (len(nets) - 1))], "typical store price"
    deal_pct = (ref - net) / ref if ref else None
    if deal_pct is not None:
        score += min(30, max(-10, deal_pct * 100))
        if deal_pct >= 0.03:
            reasons.append(f"{deal_pct:.0%} below the {ref_kind} ({_money(ref)}).")
        else:
            reasons.append(f"About the same as the {ref_kind}, so no discount right now.")
        confidence = "medium" if ref_kind == "list price" else "low"
    if len(nets) >= 3:
        median = sorted(nets)[len(nets) // 2]
        gap = (median - net) / median if median else 0
        score += min(12, max(0, gap * 100 * 0.8))
        if gap >= 0.08:
            reasons.append(f"The best store beats the middle of {len(nets)} stores by {gap:.0%}.")

    # 2. What have you seen this item cost? This is real data, so it counts for more than the calendar.
    hist = _history_facts(net, history)
    if hist:
        confidence = "high" if hist["points"] >= 4 and hist["days"] >= 5 else "medium"
        if hist["at_low"]:
            score += 12
            reasons.append(f"Lowest price you've seen in {hist['days']} days of checking.")
        elif net > hist["avg"] * 1.05:
            score -= 8
            reasons.append(f"Above the {_money(hist['avg'])} average you've seen. It was {_money(hist['low'])} at its lowest.")
        elif net < hist["first"] * 0.97:
            reasons.append(f"Down {(hist['first'] - net) / hist['first']:.0%} since you first checked.")

    # 3. Is a sale coming that would probably beat today's price?
    event = next((e for e in upcoming(today, 9) if e["key"] in cuts and 0 < e["days"] <= WAIT_HORIZON_DAYS), None)
    extra_drop, target, wait_until, wait_event = 0.0, None, None, None
    if event:
        base = ref if ref else net / 0.92  # without a list price, assume today's price is already ~8% off
        expected = base * (1 - cuts[event["key"]])
        extra_drop = max(0.0, (net - expected) / net)
        if hist and hist["at_low"]:
            extra_drop *= 0.5  # a price this low is hard to beat
        need_before = need_by and date.fromisoformat(need_by) < date.fromisoformat(event["date"])
        if need_before:
            reasons.append(f"You need it by {date.fromisoformat(need_by):%b} {date.fromisoformat(need_by).day}, before {event['name']}.")
        elif extra_drop >= WAIT_IF_EXTRA_DROP:
            target = float(round(min(net * 0.97, expected)))
            wait_until, wait_event = event["date"], event["name"]
            when = "in " + str(event["days"]) + " days" if event["days"] > 1 else "tomorrow"
            reasons.append(f"{event['name']} is {when}. {category.capitalize()} are often around {cuts[event['key']]:.0%} off then, "
                           f"so aim for {_money(target)} or less.")
            score -= min(30, extra_drop * 100 * 1.4)
        else:
            reasons.append(f"Already close to what {event['name']} usually brings ({cuts[event['key']]:.0%} off).")
            score += 6
    else:
        nxt = next((e for e in upcoming(today, 9) if e["key"] in cuts), None)
        if nxt and deal_pct is not None and deal_pct < 0.03:
            reasons.append(f"No big sale for {category} until {nxt['name']} in about {nxt['days']} days.")

    # 4. Is the product itself well rated?
    weak = bool(rating and rating < 3.7 and rating_count >= 20)
    if weak:
        score -= 25
        reasons.append(f"Reviews are weak ({rating:.1f} of 5 from {rating_count:,} ratings).")

    score = max(0.0, min(100.0, score))
    if weak:
        verdict, headline = "skip", "Skip it. The reviews are weak."
    elif wait_until:
        verdict, headline = "wait", f"Wait for {wait_event}. Aim for {_money(target)}."
    else:
        verdict = "buy_now"
        headline = "Buy now." if (deal_pct is None or deal_pct >= 0.05 or (hist and hist["at_low"])) else "Fine to buy, but it isn't a deal."
    return Timing(verdict, headline, reasons[:5], round(score, 1), confidence, target, wait_until, wait_event,
                  None if deal_pct is None else round(deal_pct, 3), category, hist)


def plan(items: list[dict], budget: float | None = None) -> dict:
    """Orders evaluated wishlist items and, given a budget, picks what to buy today.

    Each item needs id, verdict, score, net_price and optionally saves (dollars below the list price) and wait_until.
    """
    buy = sorted((i for i in items if i["verdict"] == "buy_now"), key=lambda i: -i["score"])
    wait = sorted((i for i in items if i["verdict"] == "wait"), key=lambda i: i.get("wait_until") or "9999")
    skip = [i for i in items if i["verdict"] == "skip"]
    cart, spent = [], 0.0
    for i in buy:
        if budget is None or spent + i["net_price"] <= budget:
            cart.append(i["id"])
            spent += i["net_price"]
    return {
        "order": [i["id"] for i in buy + wait + skip],
        "buy_now": [i["id"] for i in buy],
        "wait": [i["id"] for i in wait],
        "skip": [i["id"] for i in skip],
        "cart": cart,
        "cart_total": round(spent, 2),
        "cart_saves": round(sum(i.get("saves") or 0 for i in buy if i["id"] in cart), 2),
        "left_over": None if budget is None else round(budget - spent, 2),
        "wait_savings": round(sum(max(0, i["net_price"] - (i.get("target_price") or i["net_price"])) for i in wait), 2),
    }

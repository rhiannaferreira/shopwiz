"""Sale Day: what to do before, during and after a big sale, and whether a "deal" is real.

Sale dates come from shopwiz.timing (Black Friday and Cyber Monday are exact, the Amazon events are approximate).
The tips are general practice, not store-by-store promises. Always read a store's own policy before relying on it.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from . import timing

# key: (days before the main day the sale starts, days after it ends, one line on what the sale is good for)
WINDOWS = {
    "black_friday": (7, 3, "The biggest week of the year for TVs, headphones, small kitchen gear, vacuums, laptops and toys. It runs through Cyber Monday."),
    "cyber_monday": (1, 1, "Online-only deals, strongest on laptops, tablets, smart-home gear and anything Amazon sells."),
    "presidents_day": (3, 0, "A long weekend of mattress, appliance and TV sales."),
    "memorial_day": (3, 0, "Appliance, mattress and outdoor sales, plus a tools push."),
    "labor_day": (3, 0, "Appliance and mattress sales, and the last summer clearances."),
    "prime_day": (0, 2, "Amazon's summer event, plus matching sales at Target, Walmart and Best Buy. The exact dates are announced about a month ahead."),
    "prime_fall": (0, 1, "Amazon's early holiday event. Other stores answer with their own early deals."),
    "back_to_school": (20, 36, "Laptop, tablet and backpack sales from mid-July through August."),
    "super_bowl": (10, 0, "TV sales peak in the two weeks before the game."),
}

TRAPS = [
    ("Inflated \"was\" prices", "Many sales mark a price up before cutting it. Check a deal with Sale Shield on any product's page before you trust the percentage."),
    ("Doorbuster models", "Some TVs and laptops sold at doorbuster prices are cheaper versions made for the sale. Compare the model number, not just the name."),
    ("Bundles that look like discounts", "A bundle with a stand or case can cost more than buying the product alone. ShopWiz lists plain products first."),
    ("Return windows", "Check the return window and any restocking fee on big electronics before you buy. Some stores lengthen returns for holiday purchases, so read the store's own policy."),
    ("Lookalike shops and fake ads", "Deals far below every other store, especially on social media ads, are a common scam. Stick to stores you know, and pay with a credit card."),
    ("Stacking", "A coupon, your best card and a cash back portal can add up. Portals often raise their rates on big sale days, so check before you click through."),
]

CHECKLIST = [
    ("cards", "Add the cards you carry", "ShopWiz picks the best one at each store."),
    ("inbox", "Load your inbox coupons", "Connect Gmail or try the sample inbox, so codes count in every price."),
    ("wishlist", "Save what you want", "Open a product's deal and press Save to wishlist."),
    ("checked", "Check your wishlist today", "Each check records a price, so you can tell a real deal from a fake one later."),
    ("budget", "Set a spending limit", "On the Wishlist tab, so the plan picks what fits."),
]


@dataclass
class Sale:
    key: str
    name: str
    start: date
    peak: date
    end: date
    approx: bool
    blurb: str

    def status(self, today: date) -> str:
        if self.start <= today <= self.end:
            return "live"
        if today < self.start:
            return "soon" if (self.start - today).days <= 21 else "later"
        return "ended"


def sales(today: date) -> list[Sale]:
    """Every sale's next (or current) occurrence, soonest first."""
    out = []
    for key, (before, after, blurb) in WINDOWS.items():
        name, when, approx = timing.EVENTS[key]
        for year in (today.year - 1, today.year, today.year + 1):
            peak = when(year)
            s = Sale(key, name, peak - timedelta(days=before), peak, peak + timedelta(days=after), approx, blurb)
            if s.end >= today:
                out.append(s)
                break
    return sorted(out, key=lambda s: s.start)


def focus(today: date) -> Sale:
    """The sale to show first: the one that's on now (the closest to ending), else the next to start."""
    live = [s for s in sales(today) if s.status(today) == "live"]
    return min(live, key=lambda s: s.end) if live else sales(today)[0]


def sale_json(s: Sale, today: date) -> dict:
    return {"key": s.key, "name": s.name, "start": s.start.isoformat(), "peak": s.peak.isoformat(), "end": s.end.isoformat(),
            "approx": s.approx, "blurb": s.blurb, "status": s.status(today),
            "days_to_start": max(0, (s.start - today).days), "days_to_peak": (s.peak - today).days,
            "days_left": max(0, (s.end - today).days)}


def best_for(key: str) -> dict:
    """Categories that usually see their deepest cuts at this sale, and ones better saved for another sale."""
    here, later = [], []
    for _, label, cuts in timing.CATEGORIES:
        if key in cuts:
            elsewhere = max(((k, c) for k, c in cuts.items() if k != key), key=lambda kc: kc[1], default=None)
            if elsewhere is None or cuts[key] >= elsewhere[1] - 0.02:
                here.append({"category": label, "typical_cut": cuts[key]})
            else:
                later.append({"category": label, "typical_cut": cuts[key], "better_at": timing.EVENTS[elsewhere[0]][0],
                              "better_cut": elsewhere[1]})
    return {"here": sorted(here, key=lambda c: -c["typical_cut"]), "better_later": later}


def timeline(s: Sale, today: date) -> list[dict]:
    """Dated steps for one sale. Steps whose day has passed are marked so the page can fade them."""
    plan = [
        (s.start - timedelta(days=7), "Get ready", "Add your cards, save your wishlist and check it, so ShopWiz has a baseline price for each item."),
        (s.start - timedelta(days=2), "Set your targets", "Look at each wait verdict, set a need-by date for gifts, and load your inbox coupons."),
        (s.start, "Early deals open" if s.start < s.peak else "The sale opens", "Buy anything marked Buy now. Check the rest against its target price."),
    ]
    if s.start < s.peak:
        plan.append((s.peak, "Main day", "Re-check your wishlist and run Sale Shield on anything that looks too good. Doorbusters go fast."))
    plan.append((s.end, "Last call", "Buy what's left on your list. If a price is still above target, decide whether you need it before the next sale."))
    plan.append((s.end + timedelta(days=3), "Afterwards", "Keep receipts. Check your stores' policies in case a price drops after you buy."))
    seen, out = set(), []
    for d, title, text in plan:
        if (d, title) in seen:
            continue
        seen.add((d, title))
        out.append({"date": d.isoformat(), "title": title, "text": text,
                    "when": "past" if d < today else "today" if d == today else "next"})
    return out


def playbook(key: str, today: date) -> dict | None:
    match = next((s for s in sales(today) if s.key == key), None)
    if not match:
        return None
    return {"sale": sale_json(match, today), "timeline": timeline(match, today), "best_for": best_for(key),
            "traps": [{"title": t, "text": x} for t, x in TRAPS],
            "checklist": [{"id": i, "title": t, "text": x} for i, t, x in CHECKLIST]}


def run_sheet(items: list[dict], s: Sale, today: date) -> dict:
    """An ordered shopping list for a sale from checked wishlist items.

    Each item needs id, title, net_price, verdict, merchant, optional target_price and wait_until.
    Buy-now items go first. Wait items follow, soonest target date first.
    """
    steps = []
    for it in items:
        price, target = it["net_price"], it.get("target_price")
        if it["verdict"] == "skip":
            steps.append({**_base(it), "bucket": "skip", "action": "Skip this one. The reviews are weak.", "trigger": None, "buy_by": None})
        elif it["verdict"] == "buy_now":
            steps.append({**_base(it), "bucket": "now", "trigger": price,
                          "action": f"Buy at {it['merchant']} now. It's already a good price.", "buy_by": None})
        else:
            by = it.get("wait_until") or s.peak.isoformat()
            steps.append({**_base(it), "bucket": "sale", "trigger": target,
                          "action": f"Buy at {it['merchant']} when it drops to {_dollars(target or price)} or less. If it hasn't by {_pretty(s.end)}, "
                                    f"decide whether to pay {_dollars(price)} or wait for the next sale.",
                          "buy_by": min(by, s.end.isoformat()) if by >= s.start.isoformat() else s.end.isoformat()})
    order = {"now": 0, "sale": 1, "skip": 2}
    steps.sort(key=lambda x: (order[x["bucket"]], x["buy_by"] or "", -(x.get("saves") or 0)))
    return {"sale": sale_json(s, today), "steps": steps,
            "buy_now_total": round(sum(x["net_price"] for x in steps if x["bucket"] == "now"), 2),
            "sale_total_if_target": round(sum((x["trigger"] or x["net_price"]) for x in steps if x["bucket"] == "sale"), 2)}


def _base(it: dict) -> dict:
    return {"id": it["id"], "title": it["title"], "net_price": it["net_price"], "merchant": it["merchant"], "saves": it.get("saves") or 0}


def _dollars(x: float) -> str:
    return f"${x:,.2f}"


def _pretty(d: date) -> str:
    return f"{d:%b} {d.day}"


def ics(events: list[tuple[date, str, str]]) -> str:
    """A calendar file with all-day reminders. Each event is (day, title, note)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ShopWiz//Sale Day//EN", "CALSCALE:GREGORIAN"]
    for i, (d, title, note) in enumerate(events):
        lines += ["BEGIN:VEVENT", f"UID:shopwiz-{d:%Y%m%d}-{i}@shopwiz", f"DTSTAMP:{d:%Y%m%d}T120000Z",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_esc(title)}", f"DESCRIPTION:{_esc(note)}", "END:VEVENT"]
    return "\r\n".join(lines + ["END:VCALENDAR"]) + "\r\n"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


# Sale Shield: is the advertised discount real?
def deal_check(price: float, was: float | None, nets: list[float], history: list[dict]) -> dict:
    """Compares a sale price with what other stores charge and what you've seen it cost.

    nets are the prices after your cards at stores selling the item new. history is [{"d", "p"}] from your own checks.
    Returns a verdict of real, good, meh or inflated, with the reasons.
    """
    reasons: list[str] = []
    claimed = (was - price) / was if was and was > price else None
    mid = median(nets) if len(nets) >= 3 else None
    lowest = min(nets) if nets else None
    hist = [float(h["p"]) for h in history if h.get("p")]
    hist_avg = sum(hist) / len(hist) if len(hist) >= 3 else None
    hist_low = min(hist) if len(hist) >= 3 else None

    vs_mid = (mid - price) / mid if mid else None
    vs_hist = (hist_avg - price) / hist_avg if hist_avg else None
    if claimed is not None:
        reasons.append(f"The ad claims {claimed:.0%} off (was {_dollars(was)}).")
    if vs_mid is not None:
        reasons.append(f"Other stores sell it for about {_dollars(mid)}, so this is {abs(vs_mid):.0%} {'below' if vs_mid > 0 else 'above'} the middle price.")
    if vs_hist is not None:
        reasons.append(f"You've seen it at {_dollars(hist_avg)} on average and {_dollars(hist_low)} at its lowest.")

    real_gap = max([g for g in (vs_mid, vs_hist) if g is not None], default=None)
    if real_gap is None and claimed is None:
        verdict, headline = "unknown", "Not enough to judge yet. Check the item a few times and compare more stores."
    elif real_gap is None:
        verdict, headline = "unknown", "There's nothing to compare the ad against yet, so ShopWiz can't confirm the discount."
    elif claimed is not None and claimed >= 0.2 and real_gap < 0.05:
        verdict, headline = "inflated", f"The \"was\" price looks inflated. You'd really save about {max(0, real_gap):.0%}, not {claimed:.0%}."
    elif real_gap >= 0.15 or (hist_low and price <= hist_low * 1.01 and real_gap >= 0.08):
        verdict, headline = "real", "A real deal. It beats other stores and your own price history."
    elif real_gap >= 0.07:
        verdict, headline = "good", "A decent price, a little better than usual."
    else:
        verdict, headline = "meh", "Not much of a deal. You can find about this price anytime."
    cheaper = lowest is not None and lowest < price * 0.995
    if cheaper:
        reasons.append(f"Right now another store has it for {_dollars(lowest)} after your card.")
    return {"verdict": verdict, "headline": headline, "reasons": reasons, "claimed_pct": None if claimed is None else round(claimed, 3),
            "real_pct": None if real_gap is None else round(real_gap, 3), "cheaper_elsewhere": cheaper,
            "store_low": None if lowest is None else round(lowest, 2)}

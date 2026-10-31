"""A trust-adjusted rating: pools ratings across stores and discounts reviews that look fake."""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

FULL_SAMPLE = 30  # reviews needed before the fake-review adjustment counts fully
PRIOR_RATING, PRIOR_WEIGHT = 4.0, 50  # a product with few ratings is pulled toward an average product
GENERIC = re.compile(r"^(great|good|nice|love it|excellent|awesome|perfect|amazing|works great|five stars|5 stars)[.! ]*(product|item)?[.! ]*$", re.I)


@dataclass
class TrustRating:
    rating: float | None  # trust-adjusted, out of 5
    raw_rating: float | None
    total_reviews: int
    sources: int
    flagged_share: float | None  # share of sampled reviews that look suspicious
    flags: list[str]


def pooled(listings: list[dict]) -> tuple[float | None, int, int]:
    """Weighted average of the star ratings different stores show for the same product."""
    pairs = [(float(l["rating"]), int(l.get("reviews") or 0)) for l in listings if l.get("rating")]
    pairs = [(r, n) for r, n in pairs if 0 < r <= 5 and n > 0]
    total = sum(n for _, n in pairs)
    if not total:
        return None, 0, 0
    return sum(r * n for r, n in pairs) / total, total, len(pairs)


def suspicious(reviews: list[dict]) -> tuple[set[int], Counter]:
    """Indices of reviews that look fake, and the count of each reason."""
    flagged, reasons = set(), Counter()
    texts = Counter(_norm(r.get("text", "")) for r in reviews)
    days = Counter(_day(r.get("date")) for r in reviews if _day(r.get("date")))
    burst_days = {d for d, n in days.items() if len(reviews) >= 20 and n >= 0.15 * len(reviews)}
    for i, r in enumerate(reviews):
        text, stars = r.get("text", "") or "", r.get("rating") or 0
        why = []
        if stars >= 5 and r.get("verified") is False:
            why.append("5 stars without a verified purchase")
        if stars >= 5 and (len(text.split()) < 4 or GENERIC.match(text.strip())):
            why.append("short, generic 5-star text")
        if text and texts[_norm(text)] > 1:
            why.append("same text as another review")
        if stars >= 5 and _day(r.get("date")) in burst_days:
            why.append("posted in a burst of reviews on one day")
        if why:
            flagged.add(i)
            reasons.update(why)
    return flagged, reasons


def trust_rating(listings: list[dict], reviews: list[dict] | None = None) -> TrustRating:
    raw, total, sources = pooled(listings)
    if raw is None:
        return TrustRating(None, None, 0, 0, None, [])
    rating, share, flags = raw, None, []
    if reviews:
        flagged, reasons = suspicious(reviews)
        share = len(flagged) / len(reviews)
        clean = [r["rating"] for i, r in enumerate(reviews) if i not in flagged and r.get("rating")]
        if clean and share > 0:
            # Shift the pooled rating by how much the suspicious reviews inflated the sample.
            # A small sample only moves the rating part of the way, so 8 odd reviews can't sink a product.
            sample = sum(r["rating"] for r in reviews if r.get("rating")) / len(reviews)
            rating = raw - (sample - sum(clean) / len(clean)) * min(1, len(reviews) / FULL_SAMPLE)
        flags = [f"{why} ({n})" for why, n in reasons.most_common()]
    adjusted = (rating * total + PRIOR_RATING * PRIOR_WEIGHT) / (total + PRIOR_WEIGHT)
    return TrustRating(round(min(5, max(1, adjusted)), 2), round(raw, 2), total, sources,
                       round(share, 2) if share is not None else None, flags)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _day(value) -> date | None:
    """Read a review date such as "2026-03-05" or "Reviewed in the United States on March 5, 2026"."""
    s = str(value or "")
    if m := re.search(r"\d{4}-\d{2}-\d{2}", s):
        return date.fromisoformat(m.group(0))
    if m := re.search(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})", s):
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(" ".join(m.groups()), fmt).date()
            except ValueError:
                pass
    return None


# What reviewers talk about, and whether the people who mention it are happy. Words are matched loosely.
ASPECTS = {
    "Comfort and fit": r"comfort|uncomfortable|fit|hurt|tight|ears? (?:get )?hot|heavy|light ?weight",
    "Sound": r"sound|audio|bass|treble|\beq\b",
    "Noise cancelling": r"noise[- ]?cancel|anc\b|blocks? (?:out )?(?:the )?noise",
    "Battery": r"battery|charge|charging",
    "Build and durability": r"build|broke|broken|crack|hinge|cheap plastic|durab|premium|sturdy|flimsy|stopped working|died",
    "Value for money": r"price|worth|value|overpriced|expensive",
    "Calls and mic": r"\bmic\b|microphone|calls?\b",
    "Connection and app": r"bluetooth|pairing|connect|\bapp\b|wi-?fi|disconnect|dropping",
    "Size and portability": r"bulky|case|portable|compact|huge|size",
    "Ease of use": r"easy|controls?|setup|set up|intuitive|confusing",
    "Cleaning": r"clean|wash|dishwasher",
    "Noise level": r"\bloud\b|quiet|noisy",
    "Speed and performance": r"fast|slow|lag|performance|speed",
    "Screen": r"screen|display|bright",
    "Customer service and warranty": r"warranty|customer service|support|refund|replacement",
}


def aspects(reviews: list[dict], skip: set[int] = frozenset(), top: int = 6) -> list[dict]:
    """For each topic reviewers bring up, how many mention it and what share of them rated 4 stars or more."""
    out = []
    for name, pattern in ASPECTS.items():
        rx = re.compile(pattern, re.I)
        hits = [r for i, r in enumerate(reviews) if i not in skip and r.get("rating") and rx.search(r.get("text") or "")]
        if len(hits) >= 2:
            happy = sum(1 for r in hits if r["rating"] >= 4) / len(hits)
            quote = max(hits, key=lambda r: len(r.get("text") or "") if (r["rating"] >= 4) == (happy >= .5) else 0)["text"]
            out.append({"aspect": name, "mentions": len(hits), "positive": round(happy, 2), "quote": quote[:160]})
    return sorted(out, key=lambda a: -a["mentions"])[:top]

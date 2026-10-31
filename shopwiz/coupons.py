"""Finds coupon codes and personal offers in promotional emails, and works out what they save on a purchase.

Only the extracted offer (store, discount, code, expiry) leaves this module. Email text is never stored.
"""

import re
from datetime import date, datetime

from .cards import Catalog

PERCENT = re.compile(r"(?<![\d.])(\d{1,2})\s?%\s?off\b", re.I)
AMOUNT = re.compile(r"\$(\d{1,4})\s?off(?:\s+(?:your\s+|any\s+)?(?:order|purchase)s?)?(?:\s+of\s+\$(\d{1,5})\s?(?:\+|or more|and up)?)?", re.I)
REWARD = re.compile(r"\$(\d{1,4})\s(?:[A-Za-z]+\s)?(?:reward|credit|bonus cash|gift)", re.I)
MIN_SPEND = re.compile(r"(?:orders?|purchases?|spend)\s+(?:of\s+)?\$(\d{1,5})\s?(?:\+|or more|and up)", re.I)
CODE = re.compile(r"(?i:\b(?:promo\s+code|coupon\s+code|use\s+code|enter\s+code|with\s+code|code))\s*[:\-]?\s*[\"“']?([A-Z0-9][A-Z0-9\-]{3,19})\b")
EXPIRES = re.compile(r"(?i:\b(?:expires?|ends?|valid\s+(?:through|until)|through|until|by))\s+(?:on\s+)?(?:\w+day,?\s+)?"
                     r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)")
PERSONAL = re.compile(r"\b(birthday|just for you|for you,|picked for you|your (?:\$\d+ )?(?:\w+ )?reward|rewards? (?:is|are) (?:ready|waiting)|"
                      r"points? (?:are )?(?:expiring|expire)|member exclusive|exclusive offer for|we miss you|thanks for being a member)\b", re.I)
NOT_CODES = {"HTTP", "HTTPS", "WWW", "SHOP", "HERE", "TODAY", "NOW", "BELOW", "ONLINE", "STORE", "APPLY", "VALID", "REQUIRED"}


def parse_date(text: str, today: date) -> date | None:
    text = text.replace(".", "").replace(",", " ")
    parts = text.split()
    try:
        if "/" in text:
            m, d, *y = (int(x) for x in text.split("/"))
            year = (y[0] + 2000 if y and y[0] < 100 else y[0]) if y else today.year
            found = date(year, m, d)
        else:
            month = datetime.strptime(parts[0][:3], "%b").month
            year = int(parts[2]) if len(parts) > 2 else today.year
            found = date(year, month, int(parts[1]))
    except (ValueError, IndexError):
        return None
    if len(parts) <= 2 and "/" not in text and found < today and (today - found).days > 180:
        found = found.replace(year=today.year + 1)  # "ends Jan 3" seen in December
    return found


def extract(message: dict, catalog: Catalog, today: date | None = None) -> dict | None:
    """One email to one offer, or None if it doesn't hold a usable discount or reward.

    message: {"id", "sender", "sender_email", "subject", "body", "date"}
    """
    today = today or date.today()
    text = f"{message.get('subject', '')}\n{message.get('body', '')}"[:20000]
    percent = amount = min_spend = None
    if m := AMOUNT.search(text):
        amount, min_spend = float(m.group(1)), float(m.group(2)) if m.group(2) else None
    elif m := REWARD.search(text):
        amount = float(m.group(1))
    if not amount and (m := PERCENT.search(text)):
        percent = float(m.group(1))
    if not (percent or amount):
        return None
    if min_spend is None and (m := MIN_SPEND.search(text)):
        min_spend = float(m.group(1))
    code = next((c for c in CODE.findall(text) if _looks_like_code(c)), None)
    expires = None
    if m := EXPIRES.search(text):
        expires = parse_date(m.group(1), today)
        if expires and expires < today:
            return None  # already expired
    store = _store(message, catalog)
    return {
        "id": message.get("id"),
        "store_key": store.key if store else None,
        "store": store.key.title() if store else (message.get("sender") or "").split("<")[0].strip()[:40],
        "percent": percent, "amount": amount, "min_spend": min_spend, "code": code,
        "expires": expires.isoformat() if expires else None,
        "personal": bool(PERSONAL.search(text)),
        "subject": (message.get("subject") or "")[:140],
    }


def _looks_like_code(c: str) -> bool:
    return c.upper() == c and c not in NOT_CODES and (any(ch.isdigit() for ch in c) or len(c) >= 5)


def _store(message: dict, catalog: Catalog):
    """Match the sender to a store ShopWiz knows, by display name first and then by email domain."""
    name = (message.get("sender") or "").split("<")[0].strip().strip('"')
    domain = (message.get("sender_email") or "").split("@")[-1].lower()
    for candidate in (name, domain.split(".")[-2] if domain.count(".") >= 1 else domain):
        store = catalog.store(candidate) if candidate else None
        if store and store.key in catalog.known_stores:
            return store
    return None


def coupon_off(coupon: dict | None, price: float) -> float:
    """Dollars a coupon takes off this price, or 0 if it doesn't apply (spend minimum not met)."""
    if not coupon:
        return 0.0
    if coupon.get("min_spend") and price < coupon["min_spend"]:
        return 0.0
    if coupon.get("amount"):
        return round(min(coupon["amount"], price), 2)
    if coupon.get("percent"):
        return round(price * coupon["percent"] / 100, 2)
    return 0.0


def best_by_store(coupons: list[dict], prices: dict[str, float]) -> dict[str, dict]:
    """For each store, the coupon that saves the most on that store's price."""
    best = {}
    for c in coupons:
        key = c.get("store_key")
        if key in prices and coupon_off(c, prices[key]) > coupon_off(best.get(key), prices[key]):
            best[key] = c
    return best

"""Ranks store offers by what you actually pay after the best card in your wallet."""

import re
from dataclasses import dataclass

from .cards import Card, Catalog
from .coupons import coupon_off

CATEGORY_LABELS = {
    "dining": "Restaurants", "supermarket": "Groceries (supermarkets)", "online_grocery": "Online groceries",
    "gas": "Gas", "drugstore": "Drugstores", "online_retail": "Online shopping", "amazon": "Amazon",
    "walmart": "Walmart (in store)", "walmart_online": "Walmart.com", "target": "Target", "costco": "Costco",
    "warehouse": "Warehouse clubs", "travel": "Travel", "flights": "Flights", "transit": "Transit and rideshare",
    "streaming": "Streaming", "entertainment": "Entertainment", "home_improvement": "Home improvement",
    "apple": "Apple", "whole_foods": "Whole Foods", "superstore": "Superstores (Walmart, Target)",
    "electronics": "Electronics stores",
}
WALLET_CATEGORIES = ["dining", "supermarket", "gas", "online_retail", "amazon", "walmart_online", "target",
                     "costco", "drugstore", "travel", "streaming", "home_improvement"]


@dataclass
class CardPick:
    card: Card
    rate: float
    back: float
    why: str
    note: str = ""


@dataclass
class RankedOffer:
    merchant: str
    price: float
    link: str | None
    pick: CardPick | None  # None if no card in the wallet works here
    net_price: float
    perks: list[str]
    store_key: str = ""
    portal_back: float = 0.0  # estimated cash back portal dollars, included in net_price when turned on
    condition: str = "new"
    return_days: int | None = None
    return_note: str = ""
    member_note: str = ""
    needs_membership: bool = False  # you'd have to join to buy here, so it's never the headline pick
    coupon: dict | None = None  # an offer from your inbox that applies here
    coupon_off: float = 0.0


def parse_price(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value or ""))
    return float(m.group(0).replace(",", "")) if m else None


def best_card(cards: list[Card], categories: list[str], price: float, networks=None, rotating=frozenset()) -> CardPick | None:
    picks = []
    for card in cards:
        if networks and card.network not in networks:
            continue
        e = card.best_earning(categories, rotating)
        why = f"{e.rate:g}% on {CATEGORY_LABELS.get(e.category, e.category)}" if e.category else f"{e.rate:g}% on everything"
        picks.append(CardPick(card, e.rate * card.point_value, card.value_back(price, e), why, e.note))
    # Ties go to the card with purchase protection or a longer warranty, which matters on big purchases.
    return max(picks, key=lambda p: (p.back, p.card.perks.get("extended_warranty_years", 0)), default=None)


def perk_notes(card: Card, price: float) -> list[str]:
    notes = []
    if years := card.perks.get("extended_warranty_years"):
        notes.append(f"Adds {years} year to the manufacturer's warranty, so you can skip the store's 1-year protection plan")
    if days := card.perks.get("purchase_protection_days"):
        notes.append(f"Covers theft or damage for {days} days")
    return notes if price >= 50 else []


def condition(offer: dict) -> str:
    text = f"{offer.get('condition') or ''} {offer.get('product_title') or offer.get('title') or ''}".lower()
    for word in ("refurbished", "renewed", "used", "open box"):
        if word in text:
            return word
    return "new"


ELECTRONICS = re.compile(r"\b(headphones?|earbuds?|airpods|tv|television|laptop|macbook|chromebook|phone|iphone|tablet|ipad|camera|"
                         r"speaker|monitor|console|playstation|xbox|switch|smartwatch|watch|drone|projector|router|gpu|ssd)\b", re.I)


def is_electronics(title: str) -> bool:
    return bool(ELECTRONICS.search(title or ""))


def return_window(store, card: Card | None, electronics: bool) -> tuple[int | None, str]:
    """Days you have to return it here, or (None, "") if the store has no set limit."""
    r = store.returns or {}
    if not r:
        return None, "Check the store's return policy"
    days = r.get("electronics_days") if electronics else r.get("days")
    if days is None:
        return None, "No set return limit"
    note = f"{days}-day returns"
    if card and card.id == "target-circle" and store.key == "target":
        days += 30
        note = f"{days}-day returns with your Target Circle Card"
    return days, note


def rank_offers(offers: list[dict], cards: list[Card], catalog: Catalog, rotating=frozenset(), use_portal=False,
                members=frozenset(), electronics=False, coupons: dict | None = None) -> list[RankedOffer]:
    """coupons maps a store key to the inbox offer to use there. Card rewards are earned on the price after the coupon."""
    ranked = []
    for o in offers:
        price = parse_price(o.get("price_value", o.get("price")))
        if price is None or price <= 0 or not o.get("merchant"):
            continue
        store = catalog.store(o["merchant"])
        member_note, blocked = "", False
        if store.membership and store.key not in members:
            if store.membership.get("surcharge_pct"):
                price = round(price * (1 + store.membership["surcharge_pct"] / 100), 2)
            else:
                blocked = True
            member_note = store.membership["note"]
        coupon = (coupons or {}).get(store.key)
        off = coupon_off(coupon, price)
        charged = round(price - off, 2)
        pick = best_card(cards, store.categories, charged, store.networks, rotating)
        portal = round(charged * catalog.portal_rates.get(store.key, 0) / 100, 2)
        net = round(charged - (pick.back if pick else 0) - (portal if use_portal else 0), 2)
        days, rnote = return_window(store, pick.card if pick else None, electronics)
        ranked.append(RankedOffer(o["merchant"], price, o.get("link"), pick, net,
                                  perk_notes(pick.card, price) if pick else [], store.key, portal, condition(o),
                                  days, rnote, member_note, blocked, coupon if off else None, off))
    return sorted(ranked, key=lambda r: (r.needs_membership, r.net_price, r.price))


def wallet_guide(cards: list[Card], rotating=frozenset()) -> list[dict]:
    """For each everyday category, which of your cards earns the most, and the runner-up."""
    guide = []
    for cat in WALLET_CATEGORIES:
        options = sorted(cards, key=lambda c: -c.best_earning([cat], rotating).rate * c.point_value)
        if not options:
            continue
        top = options[0].best_earning([cat], rotating)
        runner = options[1] if len(options) > 1 else None
        guide.append({
            "category": CATEGORY_LABELS[cat],
            "card": options[0].name,
            "card_id": options[0].id,
            "rate": top.rate * options[0].point_value,
            "note": top.note,
            "runner_up": runner.name if runner else None,
            "runner_up_rate": runner.best_earning([cat], rotating).rate * runner.point_value if runner else None,
        })
    return guide

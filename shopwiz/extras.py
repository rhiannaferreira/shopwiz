"""The extra tricks on top of the basic ranking: price matching, sign-up bonuses and plain-English search."""

import json
import os
import re

import requests

from .cards import Card, Catalog
from .optimize import RankedOffer, best_card

MIN_SAVING = 0.50  # don't suggest a price match for pocket change


def price_match_tip(ranked: list[RankedOffer], cards: list[Card], catalog: Catalog, rotating=frozenset()) -> dict | None:
    """Find a store that matches a cheaper competitor's price where one of your cards earns more.

    Example: Amazon has it for $299, Target for $329. Target matches Amazon, and your Target Circle Card
    earns 5% at Target, so asking Target to match $299 beats buying at Amazon with a 1% card.
    """
    ranked = [r for r in ranked if r.condition == "new" and not r.needs_membership]  # stores only match new items
    if not ranked:
        return None
    best_net = ranked[0].net_price
    tip = None
    for offer in ranked:
        store = catalog.store(offer.merchant)
        for rival in ranked:
            if rival.store_key not in store.price_match or rival.price >= offer.price:
                continue
            pick = best_card(cards, store.categories, rival.price, store.networks, rotating)
            if not pick:
                continue
            net = round(rival.price - pick.back, 2)
            saves = round(best_net - net, 2)
            if saves >= MIN_SAVING and (tip is None or net < tip["net_price"]):
                tip = {"store": offer.merchant, "match_to": rival.merchant, "matched_price": rival.price,
                       "card": pick.card.name, "card_id": pick.card.id, "back": pick.back, "why": pick.why,
                       "net_price": net, "saves": saves}
    return tip


def bonus_note(best: RankedOffer, bonus: dict | None, cards: list[Card], catalog: Catalog) -> dict | None:
    """If you're working toward a card's welcome bonus, say whether this purchase can go on it and what it costs you."""
    if not bonus or not bonus.get("card_id"):
        return None
    card = next((c for c in cards if c.id == bonus["card_id"]), None)
    if not card:
        return None
    store = catalog.store(best.merchant)
    if store.networks and card.network not in store.networks:
        return {"card": card.name, "fits": False, "text": f"{best.merchant} doesn't take {card.network.title()}, so this can't count toward your bonus."}
    earning = card.best_earning(store.categories)
    back = card.value_back(best.price, earning)
    given_up = round((best.pick.back if best.pick else 0) - back, 2)
    left = max(0.0, float(bonus.get("remaining") or 0) - best.price)
    text = f"Putting this on {card.name} counts ${best.price:,.2f} toward the bonus"
    text += f" and leaves ${left:,.2f} to go." if left else " and finishes it."
    if given_up > 0:
        text += f" You give up ${given_up:,.2f} in rewards versus {best.pick.card.name}, which a welcome bonus usually far outweighs."
    return {"card": card.name, "fits": True, "remaining_after": round(left, 2), "given_up": max(0, given_up), "text": text}


PRICE = re.compile(r"\b(?:under|below|less than|max|up to|<)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\b", re.I)
STARS = re.compile(r"\b(?:at least\s*)?(\d(?:\.\d)?)\s*\+?\s*stars?\s*(?:\+|or (?:more|better|higher))?", re.I)
FILLER = re.compile(r"\b(?:i want|i need|i'm looking for|im looking for|looking for|find me|show me|get me|can you find|please|some|a good|good|the best|best|cheap|cheapest|deal on|a|an)\b", re.I)


def parse_query(text: str) -> dict:
    """Turn "best noise cancelling headphones under $300, 4.5 stars+" into a search and filters, with simple rules."""
    max_price = min_rating = None
    if m := PRICE.search(text):
        max_price = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        text = text.replace(m.group(0), " ")
    if m := STARS.search(text):
        if 1 <= float(m.group(1)) <= 5:
            min_rating = float(m.group(1))
            text = text.replace(m.group(0), " ")
    query = " ".join(FILLER.sub(" ", text).replace(",", " ").split()).strip(" .?!")
    return {"query": query or text.strip(), "max_price": max_price, "min_rating": min_rating, "use": None, "by": "rules"}


AI_MODEL = os.environ.get("SHOPWIZ_AI_MODEL", "")  # a small, fast model id from your provider
AI_PROMPT = """You turn a shopper's request into a product search. Reply with JSON only:
{"query": short search words for Google Shopping, "max_price": number or null, "min_rating": number or null,
 "use": one short phrase about what it's for, or null}
Request: """


def _ask_ai(prompt: str, max_tokens: int) -> dict | None:
    """One short call to a small model, returning its JSON reply, or None if there's no key or anything fails."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not AI_MODEL:
        return None
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=15, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": AI_MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        reply = r.json()["content"][0]["text"]
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except Exception:
        return None


VERDICT_PROMPT = """Here are recent owner reviews of "{title}". Reply with JSON only:
{{"best_for": one short phrase, "skip_if": one short phrase}}
Base it only on the reviews. Reviews:
{reviews}"""


def review_verdict(title: str, reviews: list[dict], skip: set[int] = frozenset()) -> dict | None:
    """Who the product suits and who should skip it, from trustworthy reviews. Needs ANTHROPIC_API_KEY."""
    texts = [f"{r['rating']:g}★ {r['text'][:300]}" for i, r in enumerate(reviews) if i not in skip and r.get("text") and r.get("rating")]
    if len(texts) < 5:
        return None
    data = _ask_ai(VERDICT_PROMPT.format(title=title[:150], reviews="\n".join(texts[:40])), 120)
    if not data or not data.get("best_for"):
        return None
    return {"best_for": str(data["best_for"])[:120], "skip_if": str(data.get("skip_if") or "")[:120]}


def understand(text: str) -> dict:
    """Plain-English request to search terms. Uses a small AI model when ANTHROPIC_API_KEY is set, rules otherwise."""
    rules = parse_query(text)
    try:
        data = _ask_ai(AI_PROMPT + text[:300], 150)
        if not data:
            return rules
        if not str(data.get("query") or "").strip():
            return rules
        return {"query": str(data["query"])[:120], "max_price": _num(data.get("max_price")),
                "min_rating": _num(data.get("min_rating")), "use": data.get("use"), "by": "ai"}
    except Exception:
        return rules  # never let the optional AI step break search


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

"""ShopWiz web API. The website in public/ calls these routes.

Run locally:  uvicorn app:app --reload   then open http://localhost:8000
Without APIFY_TOKEN the app runs on built-in sample data so you can try the interface.
"""

import hashlib
import json
import os
import re
import secrets
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shopwiz import apify, extras, gmail, timing
from shopwiz.coupons import best_by_store, extract
from shopwiz.cards import Catalog
from shopwiz.extras import bonus_note, parse_query, price_match_tip, review_verdict, understand
from shopwiz.optimize import is_electronics, parse_price, rank_offers, wallet_guide
from shopwiz.ratings import aspects, suspicious, trust_rating

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
SAMPLES = ROOT / "tests" / "fixtures"
CACHE_SECONDS = 3600  # prices move, so cached results expire after an hour
CACHE_MAX = 500  # a public site sees endless different searches, so the cache can't grow without limit
BUNDLE = re.compile(r"\b(bundle|kit|combo|protection plan|with (?:power bank|stand|case)|\+)|\s\+\s", re.I)

app = FastAPI(title="ShopWiz")

# The same headers are set in vercel.json for the static files, which Vercel serves without this app.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src https://fonts.gstatic.com; img-src 'self' https: data:; connect-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response
catalog = Catalog()
_cache: dict[tuple, tuple[float, object]] = {}


def live() -> bool:
    return bool(os.environ.get("APIFY_TOKEN"))


# Spending guards for a public site: every uncached Apify call costs money, so cap how many
# one visitor and the whole site can make. Cached results are free and don't count.
# These counters live in memory, so they reset when the server restarts. Also set a
# monthly usage limit in your Apify account settings as the hard cap.
PER_VISITOR_HOUR = int(os.environ.get("SHOPWIZ_VISITOR_LIMIT", "30"))
SITE_PER_DAY = int(os.environ.get("SHOPWIZ_DAILY_LIMIT", "300"))
_spend: dict[str, list[float]] = {}


def visitor(request: Request | None) -> str:
    if request is None:
        return "local"
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def spend(who: str) -> None:
    now = time.time()
    mine = [t for t in _spend.get(who, []) if now - t < 3600]
    site = [t for t in _spend.get("*", []) if now - t < 86400]
    if len(site) >= SITE_PER_DAY:
        raise HTTPException(429, "ShopWiz has hit its daily limit for live price checks. Try again tomorrow.")
    if len(mine) >= PER_VISITOR_HOUR:
        raise HTTPException(429, "You've done a lot of live checks this hour. Try again in a bit.")
    _spend[who], _spend["*"] = mine + [now], site + [now]
    if len(_spend) > 5000:  # forget visitors who haven't made a live call in the last hour
        for k in [k for k, v in _spend.items() if k != "*" and not any(now - x < 3600 for x in v)]:
            del _spend[k]


def cached(key: tuple, fetch, who: str | None = None):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if who is not None:
        spend(who)
    value = fetch()
    if len(_cache) >= CACHE_MAX:
        for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[: CACHE_MAX // 5]:
            del _cache[k]  # drop the oldest fifth
    _cache[key] = (time.time(), value)
    return value


def sample(name: str):
    return json.loads((SAMPLES / f"sample_{name}.json").read_text())


def wallet_or_400(ids: list[str]):
    try:
        return catalog.wallet(ids)
    except KeyError as e:
        raise HTTPException(400, str(e.args[0]))


class Coupon(BaseModel):
    store_key: str | None = Field(default=None, max_length=40)
    percent: float | None = Field(default=None, ge=0, le=90)
    amount: float | None = Field(default=None, ge=0, le=2000)
    min_spend: float | None = Field(default=None, ge=0)
    code: str | None = Field(default=None, max_length=24)
    expires: str | None = Field(default=None, max_length=10)
    personal: bool = False
    store: str | None = Field(default=None, max_length=40)
    subject: str | None = Field(default=None, max_length=140)
    id: str | None = Field(default=None, max_length=40)


class DealRequest(BaseModel):
    catalog_id: str = Field(default="", max_length=200)
    product_id: str = Field(default="", max_length=200)
    # The search listing's own rating, because Google's offers list doesn't include ratings.
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0)
    cards: list[str] = Field(min_length=1, max_length=20)
    rotating: list[str] = Field(default_factory=list, max_length=20)
    review_url: str | None = Field(default=None, max_length=500)
    use_portal: bool = False
    members: list[str] = Field(default_factory=list, max_length=5)  # warehouse clubs you belong to, e.g. ["costco"]
    title: str = Field(default="", max_length=300)
    coupons: list[Coupon] = Field(default_factory=list, max_length=60)  # offers found in your inbox
    bonus: dict | None = None  # {"card_id": ..., "remaining": dollars of spend left for a welcome bonus}


class WalletRequest(BaseModel):
    cards: list[str] = Field(min_length=1, max_length=20)
    rotating: list[str] = Field(default_factory=list, max_length=20)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(PUBLIC / "index.html")


@app.get("/api/config")
def config():
    return {"live": live(), "cards_as_of": catalog.as_of}


@app.get("/api/cards")
def cards():
    return [{"id": c.id, "name": c.name, "network": c.network, "rotating": bool(c.rotating)} for c in catalog.cards.values()]


@app.get("/api/search")
def search(q: str, request: Request):
    q = q.strip()
    if not q or len(q) > 300:
        raise HTTPException(400, "Type what you're shopping for.")
    try:  # the optional AI step costs money too, so it counts toward the same limits
        intent = cached(("intent", q.lower()), lambda: understand(q), visitor(request) if extras.ai_enabled() else None)
    except HTTPException:
        intent = parse_query(q)  # over the limit: fall back to the free rules instead of failing the search
    if not live():
        products = sample("search")
    else:
        try:
            products = cached(("search", intent["query"].lower(), intent.get("max_price")),
                              lambda: apify.search_products(intent["query"], max_price=intent.get("max_price")),
                              visitor(request))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "Couldn't search stores right now. Try again in a moment.")
    return {"sample": not live(), "intent": intent, "products": _filter(products, intent)}


def _filter(products: list[dict], intent: dict) -> list[dict]:
    """Apply the price and star limits from the request, and show one listing per product."""
    seen, out = set(), []
    for p in products:
        price, rating = parse_price(p.get("price_value", p.get("price"))), p.get("rating")
        if intent.get("max_price") and price and price > intent["max_price"]:
            continue
        if intent.get("min_rating") and rating and float(rating) < intent["min_rating"]:
            continue
        key = p.get("catalog_id") or p.get("product_id") or p.get("title")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    # Listings without a Google catalog id can't be compared across stores, so keep them only if nothing else is left.
    priced = [p for p in out if p.get("catalog_id")]
    out = priced or out
    # Bundles and kits ("+ earbuds", "with stand", "protection plan") go below the plain product,
    # unless the shopper asked for a bundle.
    if not BUNDLE.search(intent.get("query", "")):
        out.sort(key=lambda p: bool(BUNDLE.search(p.get("title", ""))))
    return out


def amazon_url(url) -> bool:
    """Only real Amazon product pages go to the reviews scraper, so a visitor can't point it at other sites."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "amazon.com" or host.endswith(".amazon.com"))


def _live_offers(req, request: Request) -> list[dict]:
    if not (req.catalog_id or req.product_id):
        raise HTTPException(400, "Pick a product first.")
    try:
        return cached(("offers", req.catalog_id, req.product_id),
                      lambda: apify.product_offers(req.catalog_id, req.product_id), visitor(request))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Couldn't get store prices right now. Try again in a moment.")


@app.post("/api/deal")
def deal(req: DealRequest, request: Request):
    wallet = wallet_or_400(req.cards)
    rotating = set(req.rotating)
    reviews, review_url = None, None
    if not live():
        more = sample("offers_more")
        offers, reviews = (more[req.catalog_id], None) if req.catalog_id in more else (sample("offers"), sample("reviews"))
    else:
        offers = _live_offers(req, request)
        review_url = next((u for u in [req.review_url, *[o.get("link") for o in offers]] if amazon_url(u)), None)
        if review_url:
            try:
                reviews = cached(("reviews", review_url), lambda: apify.amazon_reviews(review_url), visitor(request))
            except Exception:
                reviews = None  # the rating still works from store ratings alone
    prices = {catalog.store(o["merchant"]).key: parse_price(o.get("price_value", o.get("price"))) or 0
              for o in offers if o.get("merchant")}
    coupons = best_by_store([c.model_dump() for c in req.coupons], prices)
    ranked = rank_offers(offers, wallet, catalog, rotating, req.use_portal, set(req.members),
                         is_electronics(req.title or (offers[0].get("product_title", "") if offers else "")), coupons)
    if not ranked:
        raise HTTPException(404, "No store prices found for this product.")
    # Used and refurbished listings are shown, but the headline pick is the best new one when there is one.
    best = next((r for r in ranked if r.condition == "new" and not r.needs_membership), ranked[0])
    listing = [{"rating": req.rating, "reviews": req.reviews}] if req.rating and req.reviews else []
    rating = trust_rating(offers + listing if not any(o.get("rating") for o in offers) else offers, reviews)
    flagged = suspicious(reviews)[0] if reviews else set()
    title = req.title or (offers[0].get("product_title", "") if offers else "")
    verdict = None
    if reviews:
        verdict = sample("verdict") if not live() else cached(("verdict", review_url), lambda: review_verdict(title, reviews, flagged))
    return {
        "sample": not live(),
        "best": _offer_json(best),
        "aspects": aspects(reviews, flagged) if reviews else [],
        "verdict": verdict,
        "offers": [_offer_json(r) for r in ranked],
        "rating": rating.__dict__,
        "saved_vs_sticker": round(max(r.price for r in ranked) - best.net_price, 2),
        "price_match": price_match_tip(ranked, wallet, catalog, rotating),
        "bonus": bonus_note(best, req.bonus, wallet, catalog),
        "portal": {"name": catalog.portal, "as_of": catalog.portal_as_of, "on": req.use_portal},
    }


class HistoryPoint(BaseModel):
    d: str = Field(max_length=10)  # the day the price was seen, YYYY-MM-DD
    p: float = Field(ge=0, le=100000)


class WishCheck(DealRequest):
    list_price: float | None = Field(default=None, ge=0, le=100000)  # the listing's "was" price, if it had one
    history: list[HistoryPoint] = Field(default_factory=list, max_length=60)
    need_by: str | None = Field(default=None, max_length=10)


class WishItem(BaseModel):
    id: str = Field(max_length=60)
    verdict: str = Field(pattern="^(buy_now|wait|skip)$")
    score: float = Field(ge=0, le=100)
    net_price: float = Field(ge=0)
    saves: float | None = None
    target_price: float | None = None
    wait_until: str | None = Field(default=None, max_length=10)


class WishPlan(BaseModel):
    items: list[WishItem] = Field(max_length=40)
    budget: float | None = Field(default=None, ge=0, le=1000000)


@app.get("/api/market")
def market():
    """The sale calendar: what's coming up and how many days away it is."""
    today = date.today()
    return {"today": today.isoformat(), "events": timing.upcoming(today, 4)}


@app.post("/api/wishlist/check")
def wishlist_check(req: WishCheck, request: Request):
    """Prices one wishlist item across stores and says whether to buy now or wait. No review scraping, so it stays cheap."""
    wallet = wallet_or_400(req.cards)
    for value in (req.need_by, *[h.d for h in req.history]):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(400, "Dates need the form YYYY-MM-DD.")
    if not live():
        more = sample("offers_more")
        offers = more[req.catalog_id] if req.catalog_id in more else sample("offers")
    else:
        offers = _live_offers(req, request)
    prices = {catalog.store(o["merchant"]).key: parse_price(o.get("price_value", o.get("price"))) or 0
              for o in offers if o.get("merchant")}
    coupons = best_by_store([c.model_dump() for c in req.coupons], prices)
    title = req.title or (offers[0].get("product_title", "") if offers else "")
    ranked = rank_offers(offers, wallet, catalog, set(req.rotating), req.use_portal, set(req.members),
                         is_electronics(title), coupons)
    buyable = [r for r in ranked if r.condition == "new" and not r.needs_membership]
    if not buyable:
        raise HTTPException(404, "No new-condition store prices found for this product.")
    best = buyable[0]
    listing = [{"rating": req.rating, "reviews": req.reviews}] if req.rating and req.reviews else []
    rating = trust_rating(offers + listing if not any(o.get("rating") for o in offers) else offers, None)
    t = timing.evaluate(title, best.net_price, [r.net_price for r in buyable], req.list_price, rating.rating,
                        rating.total_reviews, [h.model_dump() for h in req.history], req.need_by, date.today())
    return {
        "sample": not live(), "checked": date.today().isoformat(),
        "best": _offer_json(best), "stores": len(buyable),
        "saves": round(req.list_price - best.net_price, 2) if req.list_price and req.list_price > best.net_price else 0,
        "timing": {**t.__dict__, "extras": None},
    }


@app.post("/api/wishlist/plan")
def wishlist_plan(req: WishPlan):
    """Orders the checked wishlist and, with a budget, picks what to buy today. No store lookups, so it's free."""
    return timing.plan([i.model_dump() for i in req.items], req.budget)


@app.post("/api/wallet")
def wallet(req: WalletRequest):
    return {"guide": wallet_guide(wallet_or_400(req.cards), set(req.rotating)), "cards_as_of": catalog.as_of}


# Gmail: sign in, read promotions, sign out. The token stays in an http-only cookie and nothing is stored here.
TOKEN_COOKIE, STATE_COOKIE = "shopwiz_gmail", "shopwiz_state"


def _redirect_uri(request: Request) -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI") or str(request.url_for("google_callback"))


@app.get("/auth/google")
def google_login(request: Request):
    if not gmail.configured():
        raise HTTPException(400, "Gmail isn't set up on this server yet.")
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(gmail.auth_url(_redirect_uri(request), state))
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


@app.get("/auth/google/callback", name="google_callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or not state or not secrets.compare_digest(state, request.cookies.get(STATE_COOKIE, "")):
        return RedirectResponse("/#inbox-error")
    try:
        token = gmail.exchange(code, _redirect_uri(request))
    except Exception:
        return RedirectResponse("/#inbox-error")
    resp = RedirectResponse("/#inbox")
    resp.set_cookie(TOKEN_COOKIE, token["access_token"], max_age=min(3600, int(token.get("expires_in", 3600))),
                    httponly=True, samesite="lax", secure=request.url.scheme == "https")
    resp.delete_cookie(STATE_COOKIE)
    return resp


@app.post("/auth/google/logout")
def google_logout(request: Request):
    if token := request.cookies.get(TOKEN_COOKIE):
        gmail.revoke(token)
        _cache.pop(("coupons", hashlib.sha256(token.encode()).hexdigest()), None)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(TOKEN_COOKIE)
    return resp


@app.get("/api/coupons")
def coupons(request: Request, demo: bool = False):
    """Coupons and personal offers from your Gmail promotions. demo=true shows sample offers instead."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        if demo:
            return {"connected": False, "sample": True, "available": gmail.configured(), "coupons": sample("coupons")}
        return {"connected": False, "sample": False, "available": gmail.configured(), "coupons": []}
    key = ("coupons", hashlib.sha256(token.encode()).hexdigest())
    try:
        found = cached(key, lambda: [c for c in (extract(m, catalog) for m in gmail.promo_messages(token)) if c])
    except Exception:
        raise HTTPException(502, "Couldn't read Gmail right now. Try signing in again.")
    found.sort(key=lambda c: (c["store_key"] is None, not c["personal"], c["expires"] or "9999"))
    return {"connected": True, "sample": False, "available": True, "coupons": found}


def _offer_json(r):
    return {
        "merchant": r.merchant, "price": r.price, "net_price": r.net_price, "link": r.link,
        "card": r.pick.card.name if r.pick else None, "card_id": r.pick.card.id if r.pick else None,
        "back": r.pick.back if r.pick else 0, "why": r.pick.why if r.pick else "None of your cards are accepted here",
        "note": r.pick.note if r.pick else "", "perks": r.perks,
        "portal_back": r.portal_back, "condition": r.condition, "return_days": r.return_days,
        "return_note": r.return_note, "member_note": r.member_note, "needs_membership": r.needs_membership,
        "coupon": r.coupon, "coupon_off": r.coupon_off,
    }


if not os.environ.get("VERCEL"):
    app.mount("/", StaticFiles(directory=PUBLIC), name="public")

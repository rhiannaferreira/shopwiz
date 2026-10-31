"""Fetches store prices and reviews by running Apify scrapers (https://apify.com).

Set APIFY_TOKEN. The scrapers are configurable with environment variables in case you want to
swap in a different one from the Apify Store.
"""

import os

import requests

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
SHOPPING_ACTOR = os.environ.get("SHOPWIZ_SHOPPING_ACTOR", "experthasan~google-shopping-scraper")
REVIEWS_ACTOR = os.environ.get("SHOPWIZ_REVIEWS_ACTOR", "junglee~amazon-reviews-scraper")
TIMEOUT = 120  # Apify keeps sync runs open up to 300 s; shopping searches usually take 20-60 s


class ApifyError(Exception):
    pass


def run_actor(actor: str, payload: dict, timeout: float = TIMEOUT) -> list[dict]:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ApifyError("APIFY_TOKEN is not set")
    resp = requests.post(API.format(actor=actor), json=payload, timeout=timeout,
                         headers={"Authorization": f"Bearer {token}"})
    if resp.status_code >= 400:
        raise ApifyError(f"{actor} failed with HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def search_products(query: str, limit: int = 20, max_price: float | None = None) -> list[dict]:
    """Products matching a search, each with its best price, store, rating and Google catalog id.

    The actor bills per product and per results page, so asking for 20 (two pages) keeps a search near $0.03.
    """
    payload = {"action": "search", "queries": [query], "country": "us", "language": "en",
               "maxProductsPerQuery": max(10, limit)}
    if max_price:
        payload["maxPrice"] = str(int(max_price))  # filter at the source so we don't pay for products we'd hide
    items = run_actor(SHOPPING_ACTOR, payload)
    return [i for i in items if i.get("type") not in {"empty", "error"} and i.get("title")][:limit]


def product_offers(catalog_id: str | None = None, product_id: str | None = None) -> list[dict]:
    """Every store's offer for one product (Google Shopping's 'compare prices' list)."""
    product = {"catalogId": catalog_id} if catalog_id else {"productId": product_id}
    items = run_actor(SHOPPING_ACTOR, {"action": "offers", "products": [product], "country": "us", "language": "en"})
    return [i for i in items if i.get("merchant") and i.get("type") not in {"empty", "error"}]


def amazon_reviews(product_url: str, limit: int = 50) -> list[dict]:
    """A sample of Amazon reviews, normalized to {rating, text, date, verified}. 50 reviews cost about $0.15."""
    items = run_actor(REVIEWS_ACTOR, {"productUrls": [{"url": product_url}], "maxReviews": limit,
                                      "includeGdprSensitive": False, "scrapeProductDetails": False})
    return [normalize_review(i) for i in items if not i.get("error")]


def normalize_review(item: dict) -> dict:
    """The reviews scraper's field names vary between versions, so accept the common spellings."""
    rating = item.get("ratingScore", item.get("rating", item.get("stars")))
    verified = item.get("isVerified", item.get("verified", item.get("verifiedPurchase")))
    return {
        "rating": float(rating) if rating not in (None, "") else None,
        "text": item.get("reviewDescription") or item.get("text") or item.get("body") or "",
        "date": item.get("date") or item.get("reviewedIn") or item.get("reviewDate"),
        "verified": bool(verified) if verified is not None else None,
    }

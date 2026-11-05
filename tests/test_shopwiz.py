import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from shopwiz.cards import Catalog
from shopwiz.extras import bonus_note, parse_query, price_match_tip
from shopwiz.optimize import best_card, parse_price, rank_offers, wallet_guide
from shopwiz.ratings import suspicious, trust_rating

FIX = Path(__file__).parent / "fixtures"
catalog = Catalog()
client = TestClient(app)


def offers():
    return json.loads((FIX / "sample_offers.json").read_text())


def test_store_matching():
    assert catalog.store("Best Buy").key == "best buy"
    assert catalog.store("bestbuy.com - Marketplace").key == "best buy"
    assert catalog.store("Amazon.com").key == "amazon"
    assert catalog.store("Some Tiny Shop").categories == ["online_retail"]


def test_card_earning_and_value():
    prime = catalog.cards["prime-visa"]
    e = prime.best_earning(catalog.store("Amazon.com").categories)
    assert e.rate == 5 and prime.value_back(100, e) == 5.0
    assert prime.best_earning(["electronics"]).rate == 1


def test_costco_skips_amex():
    pick = best_card(catalog.wallet(["amex-blue-cash-everyday", "citi-double-cash"]),
                     catalog.store("Costco").categories, 300, catalog.store("Costco").networks)
    assert pick.card.id == "citi-double-cash"


def test_rotating_only_when_activated():
    flex = catalog.wallet(["chase-freedom-flex"])
    assert best_card(flex, ["online_retail"], 100).rate == 1
    assert best_card(flex, ["online_retail"], 100, rotating={"chase-freedom-flex"}).rate == 5


def test_rank_offers_by_net_price():
    ranked = rank_offers(offers(), catalog.wallet(["prime-visa", "citi-double-cash"]), catalog)
    assert ranked == sorted(ranked, key=lambda r: r.net_price)
    amazon = next(r for r in ranked if r.store_key == "amazon")
    assert amazon.pick.card.id == "prime-visa" and amazon.net_price == round(299.99 - 15.0, 2)
    assert next(r for r in ranked if r.store_key == "ebay").condition == "used"


def test_portal_only_when_turned_on():
    wallet = catalog.wallet(["citi-double-cash"])
    off = {r.store_key: r for r in rank_offers(offers(), wallet, catalog)}
    on = {r.store_key: r for r in rank_offers(offers(), wallet, catalog, use_portal=True)}
    assert on["best buy"].net_price == round(off["best buy"].net_price - on["best buy"].portal_back, 2)
    assert on["best buy"].portal_back > 0


def test_perks_suggest_skipping_protection_plan():
    ranked = rank_offers(offers(), catalog.wallet(["chase-freedom-unlimited"]), catalog)
    assert any("protection plan" in p for p in ranked[0].perks)


def test_wallet_guide():
    guide = {g["category"]: g for g in wallet_guide(catalog.wallet(["prime-visa", "amex-blue-cash-preferred", "citi-double-cash"]))}
    assert guide["Amazon"]["card"] == "Prime Visa"
    assert guide["Groceries (supermarkets)"]["card"] == "Amex Blue Cash Preferred"
    assert guide["Online shopping"]["card"] == "Citi Double Cash"


def test_price_match_tip():
    wallet = catalog.wallet(["target-circle", "citi-double-cash"])
    tip = price_match_tip(rank_offers(offers(), wallet, catalog), wallet, catalog)
    assert tip["store"] == "Target" and tip["match_to"] == "Amazon.com" and tip["saves"] > 0


def test_no_price_match_when_nothing_beats_best():
    wallet = catalog.wallet(["prime-visa"])
    assert price_match_tip(rank_offers(offers(), wallet, catalog), wallet, catalog) is None


def test_bonus_note():
    wallet = catalog.wallet(["citi-double-cash", "amex-gold"])
    ranked = rank_offers(offers(), wallet, catalog)
    costco = next(r for r in ranked if r.store_key == "costco")
    assert bonus_note(costco, {"card_id": "amex-gold", "remaining": 1000}, wallet, catalog)["fits"] is False
    amazon = next(r for r in ranked if r.store_key == "amazon")
    note = bonus_note(amazon, {"card_id": "amex-gold", "remaining": 200}, wallet, catalog)
    assert note["fits"] and note["remaining_after"] == 0


def test_parse_price_and_query():
    assert parse_price("$1,299.99") == 1299.99 and parse_price(None) is None
    q = parse_query("find me the best air fryer under $120, 4.5 stars+")
    assert q["query"] == "air fryer" and q["max_price"] == 120 and q["min_rating"] == 4.5
    assert parse_query("laptop under 1.5k")["max_price"] == 1500


def test_suspicious_reviews():
    reviews = [
        {"rating": 5, "text": "Great product!", "verified": True},
        {"rating": 5, "text": "Comfortable for long flights and the noise cancelling is excellent.", "verified": False},
        {"rating": 4, "text": "Solid sound, a bit tight on my head after a few hours.", "verified": True},
    ]
    flagged, reasons = suspicious(reviews)
    assert flagged == {0, 1}
    assert reasons["5 stars without a verified purchase"] == 1


def test_trust_rating_discounts_fakes_and_shrinks_small_counts():
    listings = [{"rating": 5.0, "reviews": 4}]
    assert trust_rating(listings).rating < 4.2  # four 5-star ratings aren't proof
    big = [{"rating": 4.6, "reviews": 10000}]
    reviews = json.loads((FIX / "sample_reviews.json").read_text())
    from shopwiz.apify import normalize_review
    t = trust_rating(big, [normalize_review(r) for r in reviews])
    assert t.rating < t.raw_rating and t.flags
    assert trust_rating([]).rating is None


def test_api_flow():
    assert client.get("/api/config").json()["live"] is False
    assert any(c["id"] == "prime-visa" for c in client.get("/api/cards").json())
    s = client.get("/api/search", params={"q": "headphones under $310"}).json()
    assert s["sample"] and s["intent"]["max_price"] == 310
    assert all((p.get("price_value") or 0) <= 310 for p in s["products"])
    d = client.post("/api/deal", json={"catalog_id": "sample-sony-xm5", "cards": ["target-circle", "citi-double-cash"]}).json()
    assert d["best"]["condition"] == "new" and d["price_match"]["store"] == "Target"
    assert client.post("/api/wallet", json={"cards": ["prime-visa"]}).json()["guide"]


def test_api_rejects_bad_input():
    assert client.post("/api/deal", json={"catalog_id": "x", "cards": ["not-a-card"]}).status_code == 400
    assert client.post("/api/deal", json={"catalog_id": "x", "cards": []}).status_code == 422
    assert client.get("/api/search", params={"q": "  "}).status_code == 400


def test_live_path_with_mocked_apify(monkeypatch):
    import app as app_module
    from shopwiz import apify
    calls = []

    def fake_run(actor, payload, timeout=0):
        calls.append(payload)
        if payload["action"] == "search":
            return [{"type": "product", "title": "Air Fryer", "price": "$99.00", "price_value": 99.0,
                     "merchant": "Target", "rating": 4.7, "reviews": 900, "catalog_id": "123"},
                    {"type": "empty"}]
        return [{"product_title": "Air Fryer", "merchant": "Target", "price": "$99.00", "link": "https://www.target.com/x"},
                {"product_title": "Air Fryer", "merchant": "Amazon.com", "price": "$104.00", "link": "https://www.google.com/url?q=x"}]

    monkeypatch.setenv("APIFY_TOKEN", "test")
    monkeypatch.setattr(apify, "run_actor", fake_run)
    app_module._cache.clear()
    s = client.get("/api/search", params={"q": "air fryer under $150"}).json()
    assert not s["sample"] and len(s["products"]) == 1
    assert calls[0]["maxPrice"] == "150"
    d = client.post("/api/deal", json={"catalog_id": "123", "rating": 4.7, "reviews": 900, "cards": ["target-circle"]}).json()
    assert calls[1]["products"] == [{"catalogId": "123"}]
    assert d["best"]["merchant"] == "Target" and d["rating"]["raw_rating"] == 4.7
    app_module._cache.clear()


def test_membership_surcharge_and_returns():
    from shopwiz.optimize import is_electronics
    wallet = catalog.wallet(["target-circle", "citi-double-cash"])
    guest = {r.store_key: r for r in rank_offers(offers(), wallet, catalog, electronics=True)}
    member = {r.store_key: r for r in rank_offers(offers(), wallet, catalog, members={"costco"}, electronics=True)}
    assert guest["costco"].price == round(309.99 * 1.05, 2) and guest["costco"].member_note
    assert member["costco"].price == 309.99 and not member["costco"].member_note
    assert guest["best buy"].return_days == 15
    assert guest["target"].return_days == 60  # 30 for electronics + 30 with the Target Circle Card
    assert is_electronics("Sony WH-1000XM5 Headphones") and not is_electronics("Ninja Air Fryer")


def test_review_aspects():
    from shopwiz.apify import normalize_review
    from shopwiz.ratings import aspects
    reviews = [normalize_review(r) for r in json.loads((FIX / "sample_reviews.json").read_text())]
    flagged, _ = suspicious(reviews)
    found = {a["aspect"]: a for a in aspects(reviews, flagged)}
    assert found["Noise cancelling"]["positive"] == 1.0
    assert found["Build and durability"]["positive"] < 0.5


def test_coupon_extraction_from_emails():
    from datetime import date
    from shopwiz.coupons import extract
    today = date(2026, 10, 31)
    bb = extract({"id": "1", "sender": "Best Buy <BestBuyInfo@emailinfo.bestbuy.com>", "sender_email": "BestBuyInfo@emailinfo.bestbuy.com",
                  "subject": "Your $25 reward is ready", "body": "Take $25 off orders of $250 or more. Use code TECH25 at checkout. Offer ends Nov 15."},
                 catalog, today)
    assert bb["store_key"] == "best buy" and bb["amount"] == 25 and bb["min_spend"] == 250
    assert bb["code"] == "TECH25" and bb["expires"] == "2026-11-15" and bb["personal"]
    tg = extract({"sender": "Target <target@e.target.com>", "sender_email": "target@e.target.com",
                  "subject": "Save 15% off home this weekend", "body": "Shop now. Valid through 11/2/2026."}, catalog, today)
    assert tg["store_key"] == "target" and tg["percent"] == 15 and tg["code"] is None
    old = extract({"sender": "Walmart", "sender_email": "a@walmart.com", "subject": "$10 off", "body": "Expires Oct 1, 2026"}, catalog, today)
    assert old is None
    assert extract({"sender": "News", "sender_email": "x@news.com", "subject": "Weekly digest", "body": "Nothing to save here"}, catalog, today) is None
    other = extract({"sender": "Sephora <hi@beauty.sephora.com>", "sender_email": "hi@beauty.sephora.com",
                     "subject": "Happy birthday! 20% off", "body": "Use code BDAY20"}, catalog, today)
    assert other["store_key"] is None and other["store"] == "Sephora" and other["code"] == "BDAY20"


def test_coupons_change_the_best_deal():
    sample_coupons = json.loads((FIX / "sample_coupons.json").read_text())
    d = client.post("/api/deal", json={"catalog_id": "sample-sony-xm5", "cards": ["citi-double-cash"], "coupons": sample_coupons}).json()
    assert d["best"]["merchant"] == "Best Buy" and d["best"]["coupon"]["code"] == "TECH25"
    assert d["best"]["net_price"] == round((319.99 - 25) * 0.98, 2)
    walmart = next(o for o in d["offers"] if o["merchant"] == "Walmart")
    assert walmart["coupon_off"] == 10
    assert client.get("/api/coupons", params={"demo": True}).json()["sample"] is True
    assert client.get("/api/coupons").json() == {"connected": False, "sample": False, "available": False, "coupons": []}


def test_spending_guard_caps_uncached_live_calls(monkeypatch):
    import app as shop
    from fastapi import HTTPException
    monkeypatch.setattr(shop, "PER_VISITOR_HOUR", 2)
    monkeypatch.setattr(shop, "_spend", {})
    monkeypatch.setattr(shop, "_cache", {})
    calls = []
    for i in range(2):
        shop.cached(("t", i), lambda: calls.append(1), "1.2.3.4")
    shop.cached(("t", 0), lambda: calls.append(1), "1.2.3.4")  # cached, so free
    try:
        shop.cached(("t", 9), lambda: calls.append(1), "1.2.3.4")
        raise AssertionError("expected the limit")
    except HTTPException as e:
        assert e.status_code == 429
    assert len(calls) == 2
    shop.cached(("t", 9), lambda: calls.append(1), "5.6.7.8")  # a different visitor still works

"""Checks ShopWiz's card data against the issuers' own web pages.

Run:  python -m shopwiz.refresh            (needs APIFY_TOKEN; scrapes every card's official page)
      python -m shopwiz.refresh --only amex-gold chase-freedom-flex
      python -m shopwiz.refresh --from-dir saved_pages/   (checks text files named <card-id>.txt, no scraping)

It never edits data/cards.json. Issuer pages are marketing copy, so a script can't be trusted to rewrite the rules
on its own. Instead it reads each page, looks for every reward rate ShopWiz stores, and writes a report saying which
rates the page backs up, which it contradicts and which it never mentions. You review the report and update
cards.json (and its as_of date) yourself.
"""

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from . import apify

DATA = Path(__file__).resolve().parent.parent / "data"
CRAWLER = "apify~website-content-crawler"

# How issuers word each category we store. The words are matched near a rate on the page.
CATEGORY_WORDS = {
    "dining": r"dining|restaurants?|takeout|delivery",
    "supermarket": r"supermarkets?|grocery stores?|groceries|grocery",
    "online_grocery": r"groceries|grocery",
    "drugstore": r"drug ?stores?|pharmac",
    "gas": r"\bgas\b|fuel|gas stations?",
    "transit": r"transit|rideshare|taxis?|trains?|buses",
    "travel": r"travel|hotels?|airfare|flights?|cruises?",
    "flights": r"flights?|airfare|airlines?",
    "streaming": r"streaming",
    "entertainment": r"entertainment|movies?",
    "online_retail": r"online (?:retail|shopping|purchases)|online",
    "home_improvement": r"home improvement",
    "amazon": r"amazon",
    "whole_foods": r"whole foods",
    "walmart": r"walmart",
    "walmart_online": r"walmart\.com|walmart",
    "target": r"target",
    "costco": r"costco",
    "warehouse": r"warehouse|wholesale",
    "apple": r"\bapple\b",
    "apple_pay": r"apple pay",
}
BASE_WORDS = r"every purchase|other purchases|all (?:other )?purchases|everything else|all other|everywhere|on everything|unlimited|flat"

# Not a rate: 100,000 points, $200, a year. Only 0.5 to 20 count as reward rates.
RATE = re.compile(r"(?<![\d,.$])(\d{1,2}(?:\.\d+)?)\s*(%|x\b|percent|points?\b|miles?\b)", re.I)
BLOCKED = re.compile(r"access denied|unusual traffic|captcha|verify you are (?:a )?human|enable javascript|request blocked", re.I)


@dataclass
class Check:
    category: str  # a category name, "base", or "rotating"
    stored: float
    status: str  # confirmed, different or missing
    detail: str = ""


@dataclass
class CardReport:
    card_id: str
    name: str
    status: str  # ok, review, blocked or nopage
    checks: list[Check] = field(default_factory=list)
    other_rates: list[float] = field(default_factory=list)
    note: str = ""


def clean(text: str) -> str:
    """Lowercase, drop footnote marks (3%*, 3%¹) and collapse whitespace."""
    text = re.sub(r"[*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]+", " ", text)
    return re.sub(r"\s+", " ", text).lower()


def mentions(text: str) -> list[tuple[float, str]]:
    """Every reward-rate-looking number on the page with the words around it."""
    out = []
    for m in RATE.finditer(text):
        rate = float(m.group(1))
        if 0.5 <= rate <= 20:
            out.append((rate, text[max(0, m.start() - 110): m.end() + 150]))
    return out


def _near(found, rate, words):
    return any(abs(r - rate) < 0.01 and re.search(words, w) for r, w in found)


def check_card(card: dict, page_text: str) -> CardReport:
    name = card["name"]
    text = clean(page_text)
    if len(text) < 400 or BLOCKED.search(text[:3000]):
        return CardReport(card["id"], name, "blocked", note="The page came back empty or blocked, so nothing was checked.")
    found = mentions(text)
    checks: list[Check] = []

    for rule in card.get("earn", []):
        for cat in rule["categories"]:
            words = CATEGORY_WORDS.get(cat, re.escape(cat))
            if _near(found, rule["rate"], words):
                checks.append(Check(cat, rule["rate"], "confirmed"))
                continue
            other = sorted({r for r, w in found if re.search(words, w)} - {rule["rate"]})
            if other:
                shown = ", ".join(f"{r:g}" for r in other[:4])
                checks.append(Check(cat, rule["rate"], "different", f"the page shows {shown} near {cat.replace('_', ' ')}"))
            else:
                checks.append(Check(cat, rule["rate"], "missing", "not mentioned on the page"))

    base = card.get("base")
    if base:
        ok = _near(found, base, BASE_WORDS) or (not card.get("earn") and any(abs(r - base) < 0.01 for r, _ in found))
        checks.append(Check("base", base, "confirmed" if ok else "missing", "" if ok else "no matching everyday rate on the page"))

    rot = card.get("rotating")
    if rot:
        ok = _near(found, rot["rate"], r"quarter|rotating|categories|activate")
        checks.append(Check("rotating", rot["rate"], "confirmed" if ok else "missing", "" if ok else "no matching rotating rate on the page"))

    stored = {c.stored for c in checks}
    other_rates = sorted({r for r, _ in found} - stored)[:6]
    status = "ok" if checks and all(c.status == "confirmed" for c in checks) else "review"
    return CardReport(card["id"], name, status, checks, other_rates)


def load_sources() -> dict[str, list[str]]:
    data = json.loads((DATA / "card_sources.json").read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def crawl(urls: list[str], run_actor=apify.run_actor) -> dict[str, str]:
    """Scrapes the pages with Apify's website content crawler. Returns text keyed by the URL that was asked for."""
    items = run_actor(CRAWLER, {
        "startUrls": [{"url": u} for u in urls], "maxCrawlDepth": 0, "maxCrawlPages": len(urls),
        "crawlerType": "playwright:adaptive", "removeCookieWarnings": True, "saveMarkdown": False,
    }, timeout=280)
    pages: dict[str, str] = {}
    for item in items:
        got = (item.get("url") or "").split("#")[0].rstrip("/")
        text = item.get("text") or ""
        for u in urls:
            want = u.rstrip("/")
            if got == want or (urlparse(got).netloc == urlparse(want).netloc and urlparse(got).path.rstrip("/") == urlparse(want).path.rstrip("/")):
                pages[u] = pages.get(u, "") + "\n" + text
    return pages


def run(cards: list[dict], texts: dict[str, str], sources: dict[str, list[str]]) -> list[CardReport]:
    reports = []
    for card in cards:
        if card["id"] not in texts:
            reports.append(CardReport(card["id"], card["name"], "nopage", note="No page text was available for this card."))
        else:
            reports.append(check_card(card, texts[card["id"]]))
    return reports


def render(reports: list[CardReport], as_of: str, today: date) -> str:
    ok = [r for r in reports if r.status == "ok"]
    lines = [f"# Card data check, {today.isoformat()}", "",
             f"Compared the rates in data/cards.json (as of {as_of}) with each issuer's own page.",
             f"**{len(ok)} of {len(reports)} cards fully confirmed.** "
             f"{sum(r.status == 'review' for r in reports)} need a look, {sum(r.status in ('blocked', 'nopage') for r in reports)} couldn't be read.", "",
             "A rate counts as confirmed when the page shows that number near the category's words. This is a text check, "
             "not a reading of the terms, so open the page before you change anything.", ""]
    for r in sorted(reports, key=lambda r: (r.status == "ok", r.name)):
        mark = {"ok": "OK", "review": "CHECK", "blocked": "BLOCKED", "nopage": "NO PAGE"}[r.status]
        lines.append(f"## {r.name}: {mark}")
        if r.note:
            lines.append(r.note)
        for c in r.checks:
            if c.status != "confirmed":
                lines.append(f"- {c.category.replace('_', ' ')} at {c.stored:g}: {c.status}. {c.detail}")
        if r.checks and r.status == "ok":
            lines.append(f"All {len(r.checks)} stored rates found on the page.")
        if r.other_rates and r.status != "blocked":
            lines.append(f"- Other rates on the page (could be new tiers or bonuses): {', '.join(f'{x:g}' for x in r.other_rates)}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check ShopWiz's card rewards against the issuers' pages.")
    ap.add_argument("--only", nargs="*", help="card ids to check (default: all)")
    ap.add_argument("--from-dir", help="read <card-id>.txt files from this folder instead of scraping")
    ap.add_argument("--out", default="reports/card-check.md", help="where to write the report")
    args = ap.parse_args(argv)

    data = json.loads((DATA / "cards.json").read_text())
    cards = [c for c in data["cards"] if not args.only or c["id"] in args.only]
    sources = load_sources()
    texts: dict[str, str] = {}
    if args.from_dir:
        for c in cards:
            f = Path(args.from_dir) / f"{c['id']}.txt"
            if f.exists():
                texts[c["id"]] = f.read_text()
    else:
        urls = sorted({u for c in cards for u in sources.get(c["id"], [])})
        print(f"Scraping {len(urls)} pages with Apify ({CRAWLER})...")
        pages = crawl(urls)
        for c in cards:
            got = [pages[u] for u in sources.get(c["id"], []) if u in pages]
            if got:
                texts[c["id"]] = "\n".join(got)
    reports = run(cards, texts, sources)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(reports, data["as_of"], date.today()))
    for r in reports:
        print(f"{r.status.upper():8} {r.name}")
    print(f"\nReport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

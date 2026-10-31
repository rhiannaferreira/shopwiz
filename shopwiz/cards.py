"""Card rules and store categories, and the math for how much each card earns on a purchase."""

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Earning:
    rate: float  # percent back (or points per dollar for points cards)
    category: str | None  # None means the card's base rate
    note: str = ""


@dataclass
class Card:
    id: str
    name: str
    network: str
    base: float
    point_value: float  # cents per point; 1.0 for cash back
    earn: list[dict] = field(default_factory=list)
    rotating: dict | None = None
    perks: dict = field(default_factory=dict)
    cap_note: str = ""

    def best_earning(self, categories: list[str], rotating_active: set[str] = frozenset()) -> Earning:
        """The highest rate this card earns at a store with these categories."""
        best = Earning(self.base, None)
        for rule in self.earn:
            hit = next((c for c in rule["categories"] if c in categories), None)
            if hit and rule["rate"] > best.rate:
                note = rule.get("cap_note") or rule.get("note") or self.cap_note
                best = Earning(rule["rate"], hit, note)
        if self.rotating and self.id in rotating_active and self.rotating["rate"] > best.rate:
            best = Earning(self.rotating["rate"], "this quarter's 5% category", self.rotating["cap_note"])
        return best

    def value_back(self, price: float, earning: Earning) -> float:
        """Dollars back on a purchase: rate x price, converted from points at point_value."""
        return round(price * earning.rate / 100 * self.point_value, 2)


@dataclass
class Store:
    key: str
    categories: list[str]
    networks: list[str] | None = None  # None means every network is accepted
    price_match: list[str] = field(default_factory=list)  # store keys whose prices this store matches
    returns: dict | None = None  # {"days": general window, "electronics_days": ...}; None days means no set limit
    membership: dict | None = None  # warehouse clubs: {"name", "surcharge_pct", "note"}


class Catalog:
    def __init__(self, data_dir: Path = DATA):
        cards = json.loads((data_dir / "cards.json").read_text())
        self.as_of = cards["as_of"]
        self.cards = {c["id"]: Card(**c) for c in cards["cards"]}
        merchants = json.loads((data_dir / "merchants.json").read_text())
        self._default = merchants["default"]["categories"]
        self._stores = {}
        self.known_stores = set(merchants["merchants"])
        for key, m in merchants["merchants"].items():
            store = Store(key, m["categories"], m.get("networks"), m.get("price_match", []), m.get("returns"), m.get("membership"))
            for name in [key, *m["names"]]:
                self._stores[_norm(name)] = store

        portals = json.loads((data_dir / "portals.json").read_text())
        self.portal, self.portal_as_of, self.portal_rates = portals["portal"], portals["as_of"], portals["rates"]

    def store(self, merchant: str) -> Store:
        """Match a merchant name from a listing ("Best Buy", "bestbuy.com - Seller") to a known store."""
        n = _norm(merchant)
        if n in self._stores:
            return self._stores[n]
        for name, store in sorted(self._stores.items(), key=lambda kv: -len(kv[0])):
            if name and name in n:
                return store
        return Store(merchant, list(self._default))

    def wallet(self, ids: list[str]) -> list[Card]:
        unknown = [i for i in ids if i not in self.cards]
        if unknown:
            raise KeyError(f"Unknown card(s): {', '.join(unknown)}")
        return [self.cards[i] for i in ids]


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("’", "'").split())

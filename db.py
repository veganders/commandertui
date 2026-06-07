"""In-memory card database built from Scryfall bulk data."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"

# Layouts where oracle_text and mana_cost live inside card_faces, not at top level.
_SPLIT_LAYOUTS = {"transform", "modal_dfc", "flip", "split", "adventure", "battle"}


@dataclass
class Card:
    oracle_id: str
    name: str
    type_line: str
    oracle_text: str
    mana_cost: str
    cmc: float
    colors: list[str]
    color_identity: list[str]
    keywords: list[str]
    rarity: str
    layout: str
    power: Optional[str]
    toughness: Optional[str]
    loyalty: Optional[str]
    image_uri: Optional[str]
    printings: list["Printing"] = field(default_factory=list)


# Price sources present in Scryfall data keyed without finish suffix.
_PRICE_SOURCES = ("usd", "eur", "tix")
_FINISH_SUFFIX = {"nonfoil": "", "foil": "_foil", "etched": "_etched"}


@dataclass
class Printing:
    set_code: str
    set_name: str
    collector_number: str
    finish: str
    prices: dict[str, float]  # source -> price, e.g. {"usd": 0.31, "eur": 0.29}


def _extract_printings(raw: dict) -> list[Printing]:
    raw_prices = raw.get("prices", {})
    printings = []
    for finish in raw.get("finishes", ["nonfoil"]):
        suffix = _FINISH_SUFFIX.get(finish, "")
        prices = {}
        for src in _PRICE_SOURCES:
            val = raw_prices.get(f"{src}{suffix}")
            if val is not None:
                prices[src] = float(val)
        printings.append(Printing(
            set_code=raw.get("set", ""),
            set_name=raw.get("set_name", ""),
            collector_number=raw.get("collector_number", ""),
            finish=finish,
            prices=prices,
        ))
    return printings


def _parse_card(raw: dict) -> Optional[Card]:
    layout = raw.get("layout", "normal")

    if layout in _SPLIT_LAYOUTS and "card_faces" in raw:
        faces = raw["card_faces"]
        oracle_text = " // ".join(f.get("oracle_text", "") for f in faces)
        mana_cost = faces[0].get("mana_cost", raw.get("mana_cost", ""))
        image_uri = (
            faces[0].get("image_uris", {}).get("normal")
            or raw.get("image_uris", {}).get("normal")
        )
    else:
        oracle_text = raw.get("oracle_text", "")
        mana_cost = raw.get("mana_cost", "")
        image_uri = raw.get("image_uris", {}).get("normal")

    return Card(
        oracle_id=raw["oracle_id"],
        name=raw["name"],
        type_line=raw.get("type_line", ""),
        oracle_text=oracle_text,
        mana_cost=mana_cost,
        cmc=raw.get("cmc", 0.0),
        colors=raw.get("colors", []),
        color_identity=raw.get("color_identity", []),
        keywords=raw.get("keywords", []),
        rarity=raw.get("rarity", ""),
        layout=layout,
        power=raw.get("power"),
        toughness=raw.get("toughness"),
        loyalty=raw.get("loyalty"),
        image_uri=image_uri,
        printings=_extract_printings(raw),
    )


@dataclass
class CardDB:
    cards: dict[str, Card] = field(default_factory=dict)
    rulings: dict[str, list[str]] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)

    def search(
        self,
        name: str = "",
        colors: Optional[list[str]] = None,
        type_line: str = "",
        tag: str = "",
    ) -> list[Card]:
        results = self.cards.values()

        if name:
            q = name.lower()
            results = (c for c in results if q in c.name.lower())

        if colors is not None:
            color_set = set(colors)
            results = (c for c in results if set(c.color_identity) <= color_set)

        if type_line:
            q = type_line.lower()
            results = (c for c in results if q in c.type_line.lower())

        if tag:
            q = tag.lower()
            results = (
                c for c in results
                if any(q in t.lower() for t in self.tags.get(c.oracle_id, []))
            )

        return list(results)

    def get_rulings(self, oracle_id: str) -> list[str]:
        return self.rulings.get(oracle_id, [])

    def get_tags(self, oracle_id: str) -> list[str]:
        return self.tags.get(oracle_id, [])


def load_db() -> CardDB:
    """Load all bulk data and return a fully indexed CardDB."""
    db = CardDB()

    print("Loading cards...")
    with open(DATA_DIR / "default_cards.json") as f:
        raw_cards: list[dict] = json.load(f)

    # First printing wins for card text/image; all printings accumulate prices.
    for raw in raw_cards:
        oid = raw.get("oracle_id")
        if not oid:
            continue
        if oid in db.cards:
            db.cards[oid].printings.extend(_extract_printings(raw))
        else:
            card = _parse_card(raw)
            if card:
                db.cards[oid] = card

    print(f"  {len(db.cards)} commander-legal cards loaded")

    print("Loading rulings...")
    with open(DATA_DIR / "rulings.json") as f:
        raw_rulings: list[dict] = json.load(f)

    for r in raw_rulings:
        oid = r.get("oracle_id")
        if oid and oid in db.cards:
            db.rulings.setdefault(oid, []).append(r["comment"])

    print(f"  {len(db.rulings)} cards with rulings")

    print("Loading oracle tags...")
    with open(DATA_DIR / "oracle_tags.json") as f:
        raw_tags: list[dict] = json.load(f)

    for tag in raw_tags:
        label = tag.get("label", "")
        for tagging in tag.get("taggings", []):
            oid = tagging.get("oracle_id")
            if oid and oid in db.cards:
                db.tags.setdefault(oid, []).append(label)

    tagged_count = sum(1 for v in db.tags.values() if v)
    print(f"  {tagged_count} cards with oracle tags")

    return db


if __name__ == "__main__":
    db = load_db()
    # Quick sanity check
    results = db.search(name="sol ring")
    for c in results:
        print(f"{c.name} | {c.mana_cost} | {c.type_line}")
        for t in db.get_tags(c.oracle_id):
            print(f"  tag: {t}")

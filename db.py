"""In-memory card database built from Scryfall bulk data."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

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

    def allows_multiple(self) -> bool:
        return (
            "Basic" in self.type_line
            or "a deck can have any number of cards named" in self.oracle_text.lower()
        )


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
        oracle_text: str = "",
        rarity: str = "",
        cmc: Optional[tuple[str, float]] = None,
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

        if oracle_text:
            q = oracle_text.lower()
            results = (c for c in results if q in c.oracle_text.lower())

        if rarity:
            q = rarity.lower()
            results = (c for c in results if c.rarity.lower() == q)

        if cmc is not None:
            op, val = cmc
            _ops: dict = {
                "=": float.__eq__, "<": float.__lt__, ">": float.__gt__,
                "<=": float.__le__, ">=": float.__ge__,
            }
            fn = _ops.get(op, float.__eq__)
            results = (c for c in results if fn(c.cmc, val))

        return list(results)

    def query(self, node: "QueryNode") -> list[Card]:
        """Evaluate a parsed AST against all cards and return matches."""
        return [
            card for card in self.cards.values()
            if _eval_node(node, card, self.tags.get(card.oracle_id, []))
        ]

    def get_rulings(self, oracle_id: str) -> list[str]:
        return self.rulings.get(oracle_id, [])

    def get_tags(self, oracle_id: str) -> list[str]:
        return self.tags.get(oracle_id, [])


# ── Query AST ─────────────────────────────────────────────────────────────────

@dataclass
class Atom:
    """Single predicate, e.g. Atom('o', 'draw a card')."""
    key: str
    value: str


@dataclass
class And:
    children: list  # list[QueryNode]


@dataclass
class Or:
    children: list  # list[QueryNode]


QueryNode = Union[Atom, And, Or]

_FILTER_CMP_RE = re.compile(r'^(cmc|mv)([<>]=?|=)(.+)$', re.IGNORECASE)
_VALUE_CMP_RE = re.compile(r'^([<>]=?|=)(.+)$')
_CMP_OPS: dict = {
    '=': float.__eq__, '<': float.__lt__, '>': float.__gt__,
    '<=': float.__le__, '>=': float.__ge__,
}


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(query)
    while i < n:
        if query[i].isspace():
            i += 1
            continue
        if query[i] in '()':
            tokens.append(query[i])
            i += 1
            continue
        start = i
        while i < n and not query[i].isspace() and query[i] not in '()':
            if query[i] == '"':
                i += 1
                while i < n and query[i] != '"':
                    i += 1
                if i < n:
                    i += 1  # closing quote
            else:
                i += 1
        tok = query[start:i]
        if tok:
            tokens.append(tok)
    return tokens


def _parse_filter(token: str) -> Atom:
    m = _FILTER_CMP_RE.match(token)
    if m:
        _, op, val = m.groups()
        return Atom(key='cmc', value=f'{op}{val}')
    if ':' in token:
        key, _, rest = token.partition(':')
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1]
        return Atom(key=key.lower(), value=rest)
    return Atom(key='name', value=token)


def parse_query(query: str) -> QueryNode:
    """Parse a Scryfall-like query string into a boolean AST.

    Supported syntax:
      bare words         — name substring (implicit AND)
      t:type             — type line substring
      o:"draw a card"    — oracle text substring (quotes allow spaces)
      c:wubrg / ci:      — color identity subset
      tag:ramp           — oracle tag substring
      r:rare             — exact rarity
      cmc:3 / cmc>=2     — mana value comparison
      AND / OR           — explicit boolean operators
      ( ... )            — grouping; AND has higher precedence than OR
    """
    tokens = _tokenize(query)
    pos = [0]

    def peek() -> Optional[str]:
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def consume() -> str:
        tok = tokens[pos[0]]
        pos[0] += 1
        return tok

    def parse_or() -> QueryNode:
        children = [parse_and()]
        while peek() and peek().lower() == 'or':
            consume()
            children.append(parse_and())
        return children[0] if len(children) == 1 else Or(children)

    def parse_and() -> QueryNode:
        children = [parse_atom()]
        while True:
            p = peek()
            if p is None or p == ')' or p.lower() == 'or':
                break
            if p.lower() == 'and':
                consume()
                if peek() is None or peek() == ')' or peek().lower() == 'or':
                    break
            children.append(parse_atom())
        return children[0] if len(children) == 1 else And(children)

    def parse_atom() -> QueryNode:
        if peek() == '(':
            consume()
            node = parse_or()
            if peek() == ')':
                consume()
            return node
        return _parse_filter(consume())

    return parse_or() if tokens else And([])


def _eval_atom(atom: Atom, card: Card, tags: list[str]) -> bool:
    key, value = atom.key, atom.value
    match key:
        case 'o' | 'oracle':
            return value.lower() in card.oracle_text.lower()
        case 't' | 'type':
            return value.lower() in card.type_line.lower()
        case 'c' | 'ci' | 'id':
            color_set = {ch.upper() for ch in value if ch.isalpha()}
            return set(card.color_identity) <= color_set
        case 'tag':
            return any(value.lower() in t.lower() for t in tags)
        case 'r' | 'rarity':
            return card.rarity.lower() == value.lower()
        case 'cmc' | 'mv':
            m = _VALUE_CMP_RE.match(value)
            op, num = (m.group(1), m.group(2)) if m else ('=', value)
            try:
                return _CMP_OPS.get(op, float.__eq__)(card.cmc, float(num))
            except ValueError:
                return True
        case _:
            return value.lower() in card.name.lower()


def _eval_node(node: QueryNode, card: Card, tags: list[str]) -> bool:
    if isinstance(node, Atom):
        return _eval_atom(node, card, tags)
    if isinstance(node, And):
        return all(_eval_node(c, card, tags) for c in node.children)
    if isinstance(node, Or):
        return any(_eval_node(c, card, tags) for c in node.children)


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

"""In-memory card database built from Scryfall bulk data."""

import json
import re
from dataclasses import dataclass, field
from rich.text import Text
from pathlib import Path
from typing import Optional, Union

from scryfall import load_bulk

DATA_DIR = Path(__file__).parent / "data"

# Layouts where oracle_text and mana_cost live inside card_faces, not at top level.
_SPLIT_LAYOUTS = {"transform", "modal_dfc", "flip", "split", "adventure", "battle", "prepare"}


@dataclass
class CardFace:
    name: str
    mana_cost: str
    oracle_text: str
    type_line: str
    power: Optional[str] = None
    toughness: Optional[str] = None
    loyalty: Optional[str] = None


@dataclass
class Card:
    oracle_id: str
    name: str
    cmc: float
    colors: list[str]
    color_identity: list[str]
    keywords: list[str]
    rarity: str
    layout: str
    printings: list["Printing"] = field(default_factory=list)
    faces: list[CardFace] = field(default_factory=list)

    def has_type(self, text: str) -> bool:
        return any(text in f.type_line for f in self.faces)

    def has_oracle(self, text: str) -> bool:
        return any(text in f.oracle_text for f in self.faces)

    def allows_multiple(self) -> bool:
        return (
            self.has_type("basic")
            or self.has_oracle("a deck can have any number of cards named")
        )

    def display_label(self, currency: str, printing_idx: int) -> Text:
        """Returns a rich Text: mana cost, name, price — brackets are literal, not markup."""
        t = Text()
        multi_mana = len(self.faces) > 1 and any(f.mana_cost for f in self.faces[1:])
        if multi_mana:
            for i, face in enumerate(self.faces):
                if i > 0:
                    t.append(" // ")
                mana = re.sub(r'[{}]', '', face.mana_cost) if face.mana_cost else ""
                if mana:
                    t.append(f"[{mana}]", style="dim")
                    t.append(" ")
                t.append(face.name)
        else:
            mana = re.sub(r'[{}]', '', self.faces[0].mana_cost) if self.faces and self.faces[0].mana_cost else ""
            if mana:
                t.append(f"[{mana}]", style="dim")
                t.append(" ")
            t.append(self.name)
        if 0 <= printing_idx < len(self.printings):
            price = self.printings[printing_idx].prices.get(currency)
            if price is not None:
                t.append(f" [{currency.upper()}: {price:.2f}]", style="dim")
        return t


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
    scryfall_id: str = ""


def _extract_printings(raw: dict) -> list[Printing]:
    raw_prices = raw.get("prices", {})
    scryfall_id = raw.get("id", "")
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
            scryfall_id=scryfall_id,
        ))
    return printings


def _parse_card(raw: dict) -> Optional[Card]:
    layout = raw.get("layout", "normal")

    if layout in _SPLIT_LAYOUTS and "card_faces" in raw:
        faces = [
            CardFace(
                name=f.get("name", ""),
                mana_cost=f.get("mana_cost", ""),
                oracle_text=f.get("oracle_text", ""),
                type_line=f.get("type_line", "").lower(),
                power=f.get("power"),
                toughness=f.get("toughness"),
                loyalty=f.get("loyalty"),
            )
            for f in raw["card_faces"]
        ]
    else:
        faces = [
            CardFace(
                name=raw.get("name", ""),
                mana_cost=raw.get("mana_cost", ""),
                oracle_text=raw.get("oracle_text", ""),
                type_line=raw.get("type_line", "").lower(),
                power=raw.get("power"),
                toughness=raw.get("toughness"),
                loyalty=raw.get("loyalty"),
            )
        ]

    return Card(
        oracle_id=raw["oracle_id"],
        name=raw["name"],
        cmc=raw.get("cmc", 0.0),
        colors=raw.get("colors", []),
        color_identity=raw.get("color_identity", []),
        keywords=[kw.lower() for kw in raw.get("keywords", [])],
        rarity=raw.get("rarity", ""),
        layout=layout,
        printings=_extract_printings(raw),
        faces=faces,
    )


@dataclass
class CardDB:
    cards: dict[str, Card] = field(default_factory=dict)
    rulings: dict[str, list[str]] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)        # expanded (incl. ancestors)
    tags_norm: dict[str, frozenset[str]] = field(default_factory=dict)  # normalized expanded tags
    leaf_tags: dict[str, list[str]] = field(default_factory=dict)   # direct tags only

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
            results = (c for c in results if any(q in f.type_line for f in c.faces))

        if tag:
            q = tag.lower()
            results = (
                c for c in results
                if any(q in t.lower() for t in self.tags.get(c.oracle_id, []))
            )

        if oracle_text:
            q = oracle_text.lower()
            results = (c for c in results if any(q in f.oracle_text.lower() for f in c.faces))

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
            if _eval_node(node, card,
                          self.tags.get(card.oracle_id, []),
                          self.tags_norm.get(card.oracle_id, frozenset()))
        ]

    def get_rulings(self, oracle_id: str) -> list[str]:
        return self.rulings.get(oracle_id, [])

    def get_tags(self, oracle_id: str) -> list[str]:
        return self.tags.get(oracle_id, [])

    def get_leaf_tags(self, oracle_id: str) -> list[str]:
        return self.leaf_tags.get(oracle_id, [])


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


@dataclass
class Not:
    child: "QueryNode"


QueryNode = Union[Atom, And, Or, Not]

_FILTER_CMP_RE = re.compile(r'^(mv|eur|usd|tix|power|toughness|id)([<>]=?|=)(.+)$', re.IGNORECASE)
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
                    if query[i] == '\\' and i + 1 < n and query[i + 1] == '"':
                        i += 2  # \" — not a closing delimiter
                    else:
                        i += 1
                if i < n:
                    i += 1  # closing quote
            elif query[i] == '/':
                i += 1
                while i < n and query[i] != '/':
                    if query[i] == '\\' and i + 1 < n and query[i + 1] == '/':
                        i += 2  # \/ — not a closing delimiter
                    else:
                        i += 1
                if i < n:
                    i += 1  # closing /
            else:
                i += 1
        tok = query[start:i]
        if tok:
            tokens.append(tok)
    return tokens


def _parse_filter(token: str) -> QueryNode:
    if token.startswith('-') and len(token) > 1:
        return Not(_parse_filter(token[1:]))
    m = _FILTER_CMP_RE.match(token)
    if m:
        key, op, val = m.groups()
        return Atom(key=key.lower(), value=f'{op}{val}')
    if ':' in token:
        key, _, rest = token.partition(':')
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1].replace('\\"', '"')          # unescape \" inside quoted strings
        elif rest.startswith('/') and rest.endswith('/') and len(rest) >= 2:
            inner = rest[1:-1].replace('\\/', '/')         # unescape \/ → / inside regex
            rest = f'/{inner}/'                            # store with /…/ marker
            # Validity checked in _validate_atom; parse_query() stays non-raising.
        return Atom(key=key.lower(), value=rest)
    return Atom(key='name', value=token)


def parse_query(query: str) -> QueryNode:
    """Parse a Scryfall-like query string into a boolean AST.

    Supported syntax:
      bare words         — name substring (implicit AND)
      t:type             — type line substring
      o:"draw a card"    — oracle text substring (quotes allow spaces)
      id:wubrg           — color identity is subset of given colors
      c:rg               — card colors include at least the given colors
      otag:ramp          — oracle tag exact match
      r:rare             — exact rarity
      mv:3 / mv>=2       — mana value comparison
      eur<=1 / usd>=5    — price comparison (cheapest printing)
      -t:creature        — negate any filter
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
        tok = peek()
        if tok is None or tok == ')':
            return And([])
        consume()
        return _parse_filter(tok)

    return parse_or() if tokens else And([])


def _norm_tag(s: str) -> str:
    """Normalize a tag string for fuzzy matching: strip spaces and hyphens, lowercase."""
    return s.replace(" ", "").replace("-", "").lower()


def _eval_atom(atom: Atom, card: Card, tags: list[str], tags_norm: frozenset[str]) -> bool:
    key, value = atom.key, atom.value
    match key:
        case 'o' | 'oracle':
            if value.startswith('/') and value.endswith('/') and len(value) >= 2:
                try:
                    pat = re.compile(value[1:-1], re.IGNORECASE)
                    return any(pat.search(f.oracle_text) for f in card.faces)
                except re.error:
                    return False
            q = value.lower()
            return any(q in f.oracle_text.lower() for f in card.faces)
        case 't' | 'type':
            pat = re.compile(r'\b' + re.escape(value.lower()) + r'\b')
            return any(pat.search(f.type_line) for f in card.faces)
        case 'id':
            m = _VALUE_CMP_RE.match(value)
            op, raw = (m.group(1), m.group(2)) if m else (None, value)
            target = set() if raw.lower() == 'c' else {ch.upper() for ch in raw if ch.upper() in "WUBRG"}
            if not target and raw.lower() != 'c':
                return False
            card_ci = set(card.color_identity)
            return card_ci == target if op == '=' else card_ci <= target
        case 'c':
            color_set = {ch.upper() for ch in value if ch.isalpha()}
            return color_set <= set(card.colors)
        case 'otag':
            return _norm_tag(value) in tags_norm
        case 'kw' | 'keyword':
            return value.lower() in card.keywords
        case 'r' | 'rarity':
            return card.rarity.lower() == value.lower()
        case 'mv':
            m = _VALUE_CMP_RE.match(value)
            op, num = (m.group(1), m.group(2)) if m else ('=', value)
            try:
                return _CMP_OPS.get(op, float.__eq__)(card.cmc, float(num))
            except ValueError:
                return False
        case 'power' | 'toughness':
            m = _VALUE_CMP_RE.match(value)
            op, num = (m.group(1), m.group(2)) if m else ('=', value)
            try:
                threshold = float(num)
            except ValueError:
                return False
            fn = _CMP_OPS.get(op, float.__eq__)
            for face in card.faces:
                stat = face.power if key == 'power' else face.toughness
                if stat is None:
                    continue
                try:
                    stat_val = float(stat)
                except ValueError:
                    stat_val = 0.0  # non-numeric (e.g. "*") counts as 0
                if fn(stat_val, threshold):
                    return True
            return False
        case 'eur' | 'usd' | 'tix':
            m = _VALUE_CMP_RE.match(value)
            op, num = (m.group(1), m.group(2)) if m else ('=', value)
            try:
                threshold = float(num)
            except ValueError:
                return False
            fn = _CMP_OPS.get(op, float.__eq__)
            prices = [p.prices[key] for p in card.printings if key in p.prices]
            return bool(prices) and fn(min(prices), threshold)
        case _:
            return value.lower() in card.name.lower()


def _eval_node(node: QueryNode, card: Card, tags: list[str], tags_norm: frozenset[str]) -> bool:
    if isinstance(node, Atom):
        return _eval_atom(node, card, tags, tags_norm)
    if isinstance(node, And):
        return all(_eval_node(c, card, tags, tags_norm) for c in node.children)
    if isinstance(node, Or):
        return any(_eval_node(c, card, tags, tags_norm) for c in node.children)
    if isinstance(node, Not):
        return not _eval_node(node.child, card, tags, tags_norm)


def _validate_atom(atom: Atom) -> bool:
    if atom.key in ('mv', 'power', 'toughness', 'eur', 'usd', 'tix'):
        m = _VALUE_CMP_RE.match(atom.value)
        _, num = (m.group(1), m.group(2)) if m else ('=', atom.value)
        try:
            float(num)
        except ValueError:
            return False
    if atom.key in ('o', 'oracle'):
        v = atom.value
        if v.startswith('/') and v.endswith('/') and len(v) >= 2:
            try:
                re.compile(v[1:-1], re.IGNORECASE)
            except re.error:
                return False
    return True


def validate_query(node: QueryNode) -> bool:
    """Returns False if any numeric filter atom has an unparseable value."""
    if isinstance(node, Atom):
        return _validate_atom(node)
    if isinstance(node, (And, Or)):
        return all(validate_query(c) for c in node.children)
    if isinstance(node, Not):
        return validate_query(node.child)
    return True


def load_db() -> CardDB:
    """Load all bulk data and return a fully indexed CardDB."""
    db = CardDB()

    print("Loading cards...")
    raw_cards: list[dict] = load_bulk("default_cards")

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
    raw_rulings: list[dict] = load_bulk("rulings")

    for r in raw_rulings:
        oid = r.get("oracle_id")
        if oid and oid in db.cards:
            db.rulings.setdefault(oid, []).append(r["comment"])

    print(f"  {len(db.rulings)} cards with rulings")

    print("Loading oracle tags...")
    raw_tags: list[dict] = load_bulk("oracle_tags")

    # Leaf tags: direct tag labels before ancestor expansion
    for tag in raw_tags:
        for tagging in tag.get("taggings", []):
            oid = tagging.get("oracle_id")
            if oid and oid in db.cards:
                db.leaf_tags.setdefault(oid, [])
                if tag["label"] not in db.leaf_tags[oid]:
                    db.leaf_tags[oid].append(tag["label"])

    tag_by_id: dict[str, dict] = {t["id"]: t for t in raw_tags}
    _memo: dict[str, frozenset] = {}

    def _all_labels(tid: str) -> frozenset:
        if tid in _memo:
            return _memo[tid]
        tag = tag_by_id.get(tid)
        if not tag:
            _memo[tid] = frozenset()
            return _memo[tid]
        result: frozenset = frozenset({tag["label"]})
        for pid in tag.get("parent_ids", []):
            result |= _all_labels(pid)
        _memo[tid] = result
        return result

    tag_sets: dict[str, set] = {}
    for tag in raw_tags:
        labels = _all_labels(tag["id"])
        for tagging in tag.get("taggings", []):
            oid = tagging.get("oracle_id")
            if oid and oid in db.cards:
                tag_sets.setdefault(oid, set()).update(labels)

    for oid, labels in tag_sets.items():
        db.tags[oid] = list(labels)
    db.tags_norm = {
        oid: frozenset(_norm_tag(l) for l in labels)
        for oid, labels in db.tags.items()
    }

    tagged_count = sum(1 for v in db.tags.values() if v)
    print(f"  {tagged_count} cards with oracle tags")

    return db


if __name__ == "__main__":
    db = load_db()
    # Quick sanity check
    results = db.search(name="sol ring")
    for c in results:
        print(f"{c.name} | {c.faces[0].mana_cost} | {c.faces[0].type_line}")
        for t in db.get_tags(c.oracle_id):
            print(f"  tag: {t}")

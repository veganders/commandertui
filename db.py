"""In-memory card database built from Scryfall bulk data."""

import json
import re
from dataclasses import dataclass, field
from datetime import date  # used by _card_passes_filter
from rich.text import Text
from pathlib import Path
from typing import Callable, Optional, Union

from scryfall import load_bulk
from settings import Settings

DATA_DIR = Path(__file__).parent / "data"

# Layouts where oracle_text and mana_cost live inside card_faces, not at top level.
_SPLIT_LAYOUTS = {"transform", "modal_dfc", "flip", "split", "adventure", "battle", "prepare"}

# Type-line token parsing (used for card_type_tokens index and t: search).
_MULTIWORD_TYPES: list[str] = ["time lord"]


def extract_type_words(type_line: str) -> frozenset[str]:
    """Parse a card type line into individual type tokens.

    Preserves multi-word types (e.g. "time lord") as single tokens.
    Handles // separators and the — supertype/subtype divider.
    """
    result: set[str] = set()
    line = type_line.replace("//", " ")
    for mwt in _MULTIWORD_TYPES:
        if mwt in line:
            result.add(mwt)
            line = line.replace(mwt, " ")
    line = line.replace("—", " ")
    result.update(t for t in line.split() if t)
    return frozenset(result)


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
    game_changer: bool = False
    commander_legal: str = "legal"   # from legalities.commander: "legal" / "not_legal" / "banned"
    released_at: str = ""            # ISO date string, e.g. "2024-11-08"; empty for old data

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
        if self.game_changer:
            t.append(" [GC]", style="bold yellow")
        if 0 <= printing_idx < len(self.printings):
            price = self.printings[printing_idx].prices.get(currency)
            if price is not None:
                t.append(f" [{currency.upper()}: {price:.2f}]", style="dim")
        return t


# Predicates for the `is:` search filter.  Values are matched case-insensitively.
_IS_PREDICATES: dict[str, Callable[["Card"], bool]] = {
    "permanent":   lambda c: any(c.has_type(t) for t in ("creature", "artifact", "enchantment", "planeswalker", "land", "battle")),
    "historic":    lambda c: any(c.has_type(t) for t in ("artifact", "legendary", "saga")),
    "party":       lambda c: any(c.has_type(t) for t in ("cleric", "rogue", "warrior", "wizard")),
    "commander":   lambda c: (c.has_type("legendary") and c.has_type("creature")) or c.has_type("background") or c.has_oracle("can be your commander"),
    "gamechanger": lambda c: c.game_changer,
    "outlaw":      lambda c: any(c.has_type(t) for t in ("assassin", "mercenary", "pirate", "rogue", "warlock")),
    "vanilla":     lambda c: c.has_type("creature") and all(not f.oracle_text.strip() for f in c.faces),
    "split":       lambda c: c.layout == "split",
    "transform":   lambda c: c.layout == "transform",
}


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
        game_changer=raw.get("game_changer", False),
        commander_legal=raw.get("commander_legal", "legal"),
        released_at=raw.get("released_at", ""),
    )


@dataclass
class CardDB:
    cards: dict[str, Card] = field(default_factory=dict)
    rulings: dict[str, list[str]] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)             # expanded (incl. ancestors)
    tags_norm: dict[str, frozenset[str]] = field(default_factory=dict)   # normalized expanded tags
    leaf_tags: dict[str, list[str]] = field(default_factory=dict)        # direct tags only
    tag_ancestors: dict[str, frozenset[str]] = field(default_factory=dict)   # tag label → ancestor labels
    tag_descriptions: dict[str, str] = field(default_factory=dict)          # tag label → description (may be "")
    tag_parents: dict[str, list[str]] = field(default_factory=dict)         # tag label → direct parent labels
    card_type_tokens: dict[str, frozenset[str]] = field(default_factory=dict)  # oracle_id → type tokens
    settings: Settings = field(default_factory=Settings)

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

    def matches(self, card: "Card", node: "QueryNode") -> bool:
        """Return True if *card* satisfies *node* (no card-pool filter applied)."""
        return _eval_node(
            node, card,
            self.tags.get(card.oracle_id, []),
            self.tags_norm.get(card.oracle_id, frozenset()),
            self.card_type_tokens.get(card.oracle_id, frozenset()),
        )

    def query(self, node: "QueryNode") -> list[Card]:
        """Evaluate a parsed AST against all cards and return matches.

        Cards excluded by db.settings card-pool toggles are omitted automatically.
        """
        today = date.today().isoformat()
        return [
            card for card in self.cards.values()
            if _eval_node(node, card,
                          self.tags.get(card.oracle_id, []),
                          self.tags_norm.get(card.oracle_id, frozenset()),
                          self.card_type_tokens.get(card.oracle_id, frozenset()))
            and _card_passes_filter(card, self.settings, today)
        ]

    def get_rulings(self, oracle_id: str) -> list[str]:
        return self.rulings.get(oracle_id, [])

    def get_tags(self, oracle_id: str) -> list[str]:
        return self.tags.get(oracle_id, [])

    def get_leaf_tags(self, oracle_id: str) -> list[str]:
        return self.leaf_tags.get(oracle_id, [])

    def get_type_tokens(self, oracle_id: str) -> frozenset[str]:
        return self.card_type_tokens.get(oracle_id, frozenset())


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


@dataclass
class AtLeastN:
    """Threshold node: at least `n` of `children` must be True."""
    children: list  # list[QueryNode]
    n: int


QueryNode = Union[Atom, And, Or, Not, AtLeastN]

_FILTER_CMP_RE = re.compile(r'^(mv|eur|usd|tix|power|toughness|id)([<>]=?|=)(.+)$', re.IGNORECASE)
_VALUE_CMP_RE = re.compile(r'^([<>]=?|=)(.+)$')
_CMP_OPS: dict = {
    '=': float.__eq__, '<': float.__lt__, '>': float.__gt__,
    '<=': float.__le__, '>=': float.__ge__,
}


def _skip_delimited(s: str, i: int, delim: str) -> int:
    """Advance i past a delimited span (quote or regex). i points at the opening delimiter."""
    i += 1
    n = len(s)
    while i < n and s[i] != delim:
        if s[i] == '\\' and i + 1 < n and s[i + 1] == delim:
            i += 2  # escaped delimiter — not a closer
        else:
            i += 1
    return i + 1 if i < n else i  # step past closing delimiter if present


def _split_threshold_parts(s: str) -> list[str]:
    """Split a threshold inner string on commas, skipping commas inside
    nested brackets, quoted strings, and regex literals."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '[':
            depth += 1; i += 1
        elif c == ']':
            depth -= 1; i += 1
        elif c == '"' and depth == 0:
            i = _skip_delimited(s, i, '"')
        elif c == '/' and depth == 0 and i > 0 and s[i - 1] == ':':
            i = _skip_delimited(s, i, '/')
        elif c == ',' and depth == 0:
            parts.append(s[start:i])
            start = i + 1
            i += 1
        else:
            i += 1
    parts.append(s[start:])
    return parts


def _scan_bracket_token(query: str, i: int, n: int) -> int:
    """Consume a `[…]{N}` span. `i` points at the opening `[`.

    Returns the position after the closing `}` (or `]` when no `{N}` suffix is
    present).  Handles nested brackets and delimited spans inside the brackets
    so that inner `"…"` and `/…/` content does not confuse the depth counter.
    """
    i += 1  # skip opening [
    depth = 1
    while i < n and depth > 0:
        if query[i] == '[':
            depth += 1; i += 1
        elif query[i] == ']':
            depth -= 1; i += 1
        elif query[i] == '"':
            i = _skip_delimited(query, i, '"')
        elif query[i] == '/' and i > 0 and query[i - 1] == ':':
            i = _skip_delimited(query, i, '/')
        else:
            i += 1
    # optionally consume trailing {N}
    if i < n and query[i] == '{':
        i += 1
        while i < n and query[i] != '}':
            i += 1
        if i < n:
            i += 1
    return i


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
        if query[i] == '[' or (query[i] == '-' and i + 1 < n and query[i + 1] == '['):
            # consume (optional leading -)[sub-query, sub-query, ...]{N} as a single token
            start = i
            if query[i] == '-':
                i += 1  # skip the negation prefix
            i = _scan_bracket_token(query, i, n)
            tokens.append(query[start:i])
            continue
        start = i
        while i < n and not query[i].isspace() and query[i] not in '()':
            if query[i] == '"':
                i = _skip_delimited(query, i, '"')
            elif query[i] == '/' and i > start and query[i - 1] == ':':
                i = _skip_delimited(query, i, '/')
            else:
                i += 1
        tok = query[start:i]
        if tok:
            tokens.append(tok)
    return tokens


def _parse_threshold(token: str) -> AtLeastN:
    """Parse a `[q1, q2, …]{N}` token into an `AtLeastN` node."""
    try:
        inner_end = token.rindex(']')
    except ValueError:
        inner_end = len(token)  # unclosed bracket — treat rest as inner content
    inner = token[1:inner_end]
    suffix = token[inner_end + 1:]  # e.g. "{2}" or ""
    if suffix.startswith('{') and suffix.endswith('}'):
        try:
            n = int(suffix[1:-1])
        except ValueError:
            n = 1
    else:
        n = 1  # no {N} — OR semantics
    parts = _split_threshold_parts(inner)
    children = [parse_query(p.strip()) for p in parts if p.strip()]
    return AtLeastN(children=children, n=n)


def _parse_colon_atom(key: str, rest: str) -> Atom:
    """Parse the value side of a `key:value` token into an `Atom`.

    Handles three forms: quoted (`"…"` with `\"` escape), regex (`/…/` with
    `\\/` escape), and plain.  Regex validity is checked later in
    `_validate_atom`; this function is non-raising.
    """
    if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
        rest = rest[1:-1].replace('\\"', '"')
    elif rest.startswith('/') and rest.endswith('/') and len(rest) >= 2:
        rest = '/' + rest[1:-1].replace('\\/', '/') + '/'
    return Atom(key=key.lower(), value=rest)


def _parse_filter(token: str) -> QueryNode:
    if token.startswith('-') and len(token) > 1:
        return Not(_parse_filter(token[1:]))
    if token.startswith('['):
        return _parse_threshold(token)
    m = _FILTER_CMP_RE.match(token)
    if m:
        key, op, val = m.groups()
        return Atom(key=key.lower(), value=f'{op}{val}')
    if ':' in token:
        key, _, rest = token.partition(':')
        return _parse_colon_atom(key, rest)
    return Atom(key='name', value=token)


def parse_query(query: str) -> QueryNode:
    """Parse a Scryfall-like query string into a boolean AST.

    Supported syntax:
      bare words              — name substring (implicit AND)
      t:type                  — type line substring
      o:"draw a card"         — oracle text substring (quotes allow spaces)
      id:wubrg                — color identity is subset of given colors
      c:rg                    — card colors include at least the given colors
      otag:ramp               — oracle tag exact match
      r:rare                  — exact rarity
      mv:3 / mv>=2            — mana value comparison
      eur<=1 / usd>=5         — price comparison (cheapest printing)
      -t:creature             — negate any filter
      AND / OR                — explicit boolean operators
      ( ... )                 — grouping; AND has higher precedence than OR
      [q1, q2, q3]{N}         — at least N of the comma-separated sub-queries
                                must match; {N} defaults to 1 if omitted.
                                Filters outside the brackets are AND-ed normally,
                                so [otag:ramp, otag:draw]{2} eur<1 finds cheap
                                cards that do both.
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


def _eval_atom(atom: Atom, card: Card, tags: list[str], tags_norm: frozenset[str],
               type_tokens: frozenset[str] = frozenset()) -> bool:
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
            return value.lower() in type_tokens
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
        case 'is':
            pred = _IS_PREDICATES.get(value.lower())
            return pred(card) if pred is not None else False
        case _:
            return value.lower() in card.name.lower()


def _eval_node(node: QueryNode, card: Card, tags: list[str], tags_norm: frozenset[str],
               type_tokens: frozenset[str] = frozenset()) -> bool:
    if isinstance(node, Atom):
        return _eval_atom(node, card, tags, tags_norm, type_tokens)
    if isinstance(node, And):
        return all(_eval_node(c, card, tags, tags_norm, type_tokens) for c in node.children)
    if isinstance(node, Or):
        return any(_eval_node(c, card, tags, tags_norm, type_tokens) for c in node.children)
    if isinstance(node, Not):
        return not _eval_node(node.child, card, tags, tags_norm, type_tokens)
    if isinstance(node, AtLeastN):
        count = sum(1 for c in node.children if _eval_node(c, card, tags, tags_norm, type_tokens))
        return count >= node.n


def _validate_atom(atom: Atom) -> bool:
    if atom.key in ('mv', 'power', 'toughness', 'eur', 'usd', 'tix'):
        m = _VALUE_CMP_RE.match(atom.value)
        _, num = (m.group(1), m.group(2)) if m else ('=', atom.value)
        try:
            float(num)
        except ValueError:
            return False
    if atom.key == 'is':
        return atom.value.lower() in _IS_PREDICATES
    if atom.key in ('o', 'oracle'):
        v = atom.value
        if v.startswith('/') and v.endswith('/') and len(v) >= 2:
            try:
                re.compile(v[1:-1], re.IGNORECASE)
            except re.error:
                return False
    return True


def validate_query(node: QueryNode) -> bool:
    """Returns False if any numeric filter atom has an unparseable value,
    or if an AtLeastN node has an out-of-range threshold."""
    if isinstance(node, Atom):
        return _validate_atom(node)
    if isinstance(node, (And, Or)):
        return all(validate_query(c) for c in node.children)
    if isinstance(node, Not):
        return validate_query(node.child)
    if isinstance(node, AtLeastN):
        if not node.children or not (1 <= node.n <= len(node.children)):
            return False
        return all(validate_query(c) for c in node.children)
    return True


def _card_passes_filter(card: "Card", settings: Settings, today: str) -> bool:
    """Return True if card should be included given current settings."""
    is_unreleased = bool(card.released_at) and card.released_at > today
    if is_unreleased:
        return settings.show_unreleased
    if card.commander_legal == "legal":
        return settings.show_legal
    if card.commander_legal == "banned":
        return settings.show_banned
    return settings.show_not_legal  # "not_legal", "restricted", etc.


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
            # Keep the earliest known release date so a future reprint doesn't
            # make an already-released card appear unreleased.
            new_date = raw.get("released_at", "")
            card = db.cards[oid]
            if new_date and (not card.released_at or new_date < card.released_at):
                card.released_at = new_date
        else:
            card = _parse_card(raw)
            if card:
                db.cards[oid] = card

    print(f"  {len(db.cards)} cards loaded")

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

    # tag label → frozenset of ancestor labels (memo already warm, so this is O(n))
    db.tag_ancestors = {
        tag["label"]: _all_labels(tag["id"]) - {tag["label"]}
        for tag in raw_tags
    }

    db.tag_descriptions = {t["label"]: t.get("description", "") for t in raw_tags}
    db.tag_parents = {
        t["label"]: [
            tag_by_id[pid]["label"]
            for pid in t.get("parent_ids", [])
            if pid in tag_by_id
        ]
        for t in raw_tags
    }

    db.tags_norm = {
        oid: frozenset(_norm_tag(l) for l in labels)
        for oid, labels in db.tags.items()
    }

    tagged_count = sum(1 for v in db.tags.values() if v)
    print(f"  {tagged_count} cards with oracle tags")

    print("Indexing type tokens...")
    for oracle_id, card in db.cards.items():
        tokens: set[str] = set()
        for face in card.faces:
            tokens |= extract_type_words(face.type_line)
        db.card_type_tokens[oracle_id] = frozenset(tokens)
    print(f"  {len(db.card_type_tokens)} cards indexed")

    return db


if __name__ == "__main__":
    db = load_db()
    # Quick sanity check
    results = db.search(name="sol ring")
    for c in results:
        print(f"{c.name} | {c.faces[0].mana_cost} | {c.faces[0].type_line}")
        for t in db.get_tags(c.oracle_id):
            print(f"  tag: {t}")

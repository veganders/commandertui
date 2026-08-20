# Data model

## CardFace

`CardFace` (in `db.py`) holds all per-face data: `name`, `mana_cost`, `oracle_text`, `type_line`, `power`, `toughness`, `loyalty`. Every `Card` has at least one face; multi-face cards (split, transform, modal_dfc, adventure, etc.) have one `CardFace` per Scryfall face.

`type_line` is stored **lowercase** (normalized at parse time). `oracle_text` keeps its original case — it is displayed to the user and searched case-insensitively at query time.

## Card

`Card` stores only card-level fields: `oracle_id`, `name`, `cmc`, `colors`, `color_identity`, `keywords`, `rarity`, `layout`, `printings`, `faces`. Per-face data lives exclusively in `card.faces` — there are no `oracle_text`, `type_line`, `power`, `toughness`, `loyalty`, or `mana_cost` fields on `Card` itself.

`keywords` are stored **lowercase** (normalized at parse time). Pass lowercase strings when checking membership.

`Card.has_type(text) -> bool` returns True if `text` appears in any face's `type_line`. Since `type_line` is lowercase, always pass a lowercase argument. `Card.has_oracle(text) -> bool` returns True if `text` appears in any face's `oracle_text`. Use these instead of accessing face fields directly when checking a simple substring across all faces.

`Card.allows_multiple() -> bool` returns True for basic lands and cards whose oracle text contains `"a deck can have any number of cards named"`. Uses `has_type` and `has_oracle` internally. Do not duplicate this check elsewhere.

`_CARD_KEEP_FIELDS` and `_CARD_FACE_KEEP_FIELDS` in `scryfall.py` are whitelists applied to `default_cards` during download (via `_strip_card`). Only fields actually used by `_parse_card` / `_extract_printings` are kept; everything else (artist, flavor text, image URIs, legalities, purchase URIs, etc.) is discarded at sync time to keep the on-disk file small. `_CARD_FACE_KEEP_FIELDS` includes `name`, `mana_cost`, `oracle_text`, `type_line`, `power`, `toughness`, `loyalty`. If a new field is needed, add it to the appropriate whitelist and re-sync with `python scryfall.py --force`.

`_SPLIT_LAYOUTS` in `db.py` lists layouts where face data lives inside `card_faces` rather than at the top level. Currently: `transform`, `modal_dfc`, `flip`, `split`, `adventure`, `battle`, `prepare`. For these layouts `_parse_card` builds one `CardFace` per Scryfall face. For all other layouts it builds a single `CardFace` from the top-level fields. When Scryfall introduces a new multi-face layout and cards show empty oracle text, add it here.

`Card.display_label(currency, printing_idx) -> rich.text.Text` returns a formatted label: `[mana cost] Name [EUR: 1.23]`. For multi-face cards where multiple faces carry their own mana cost it renders `[1R] Fire // [1U] Ice [EUR: 0.50]`; for transform/flip cards (only front face has a cost) it renders `[U] Delver of Secrets // Insectile Aberration`. Returns a `Text` object (not a string) so brackets are always literal, never parsed as Rich markup.

Search filters `o:`, `t:`, `power`, `toughness` loop over `card.faces` and match if **any** face satisfies the condition. This means `power>=10` matches a DFC whose back face is a 13/13.

## CardRole / CardEntry / Group / Deck

`CardEntry` is the single source of truth for all per-deck card state. Commander and partner are also stored as `CardEntry` (not bare `Card`).

```python
class CardRole(Enum):
    MAIN = auto()
    COMMANDER = auto()
    PARTNER = auto()

@dataclass
class CardEntry:
    card: Card
    count: int = 1
    groups: set[str] = field(default_factory=set)
    printing_idx: int = 0          # index into card.printings; stored here, not in a separate dict
    role: CardRole = CardRole.MAIN
    color_identity_override: Optional[list[str]] = None  # set for color-choosing commanders
    # property: color_identity -> override if set, else card.color_identity
    # helpers: in_group(name), join_group(name), leave_group(name), is_maybe()
    # method:  price(currency: str) -> float | None

@dataclass
class Group:
    name: str
    permanent: bool = False  # if True, d-key clears memberships but never removes the group

@dataclass
class Deck:
    commander: Optional[CardEntry] = None
    partner: Optional[CardEntry] = None
    groups: list[Group] = field(default_factory=list)
    entries: dict[str, CardEntry] = field(default_factory=dict)  # oracle_id → CardEntry
    selected_printings: dict[str, int] = field(default_factory=dict)  # cache for non-deck cards only
    name: Optional[str] = None
    save_path: Optional[Path] = None
```

`CardEntry.price(currency)` returns the price for `card.printings[printing_idx]`, or `None` if unavailable.

`Deck.get_entry_for_card(oracle_id)` searches commander, partner, and entries — use this when you need to find any entry regardless of role (e.g. when handling a printing selection event).

`Deck.get_entry(oracle_id)` only looks in `entries` (not commander/partner) — use this for main-deck operations.

`selected_printings` is kept only as a temporary cache for cards browsed in the search screen but not yet added to the deck. When a card is added via `Deck.add()`, its cached printing_idx is moved into the new `CardEntry` and removed from the cache.

Key `Deck` helpers: `add(card)`, `remove_one(oracle_id)`, `remove_all(oracle_id)`, `count_of(oracle_id)`, `get_entry(oracle_id)`, `get_entry_for_card(oracle_id)`, `entries_for_group(name)`, `uncategorized_entries()`.

`Deck.all_entries() -> list[CardEntry]` returns commander + partner + all non-maybeboard entries, deduped by oracle_id. `card_count()`, `mana_curve()`, and `total_cost()` all use this, so maybeboard cards are automatically excluded from all three. `total_cost` uses `entry.price(currency)` directly.

Mana curve excludes cards where every face is a land (`all("land" in f.type_line for f in card.faces)`). MDFCs with a non-land face (e.g. a Sorcery // Land) are included.

Cards with no group memberships appear in a dynamic **Uncategorized** node at the bottom of the tree (not a real Group — its tree node has `data=None`).

## Oracle tag hierarchy

Tags in `oracle_tags.json` form a parent–child hierarchy via `parent_ids`. During `load_db()` each card's tag set is expanded to include all ancestor labels (e.g. "mana rock" → "mana producer" → "ramp"). This means `otag:ramp` correctly matches mana rocks, mana dorks, land ramp spells, etc. without hardcoding child tag names.

The expansion is done once at load time via a memoised recursive `_all_labels(tag_id)` helper defined inside `load_db()`. The stored `db.tags[oracle_id]` list already contains all ancestor labels. Leaf-only tags are stored separately in `db.leaf_tags[oracle_id]`.

`db.tags_norm[oracle_id]` is a pre-built `frozenset[str]` of the same expanded labels with spaces and hyphens stripped and lowercased (via `_norm_tag`). Used by `otag:` evaluation for O(1) normalized lookup — no per-query normalization cost.

`db.tag_ancestors` is a `dict[str, frozenset[str]]` mapping each **tag label** to the frozenset of its ancestor labels (not including itself). Built in `load_db()` after `_all_labels` is fully memoised, so it's O(n). Used by `similar.py` to derive the full set of descendant labels for any blacklisted parent category without scanning every card.

`db.tag_descriptions` maps tag label → description string (may be `""`). `db.tag_parents` maps tag label → list of direct parent labels. Both are populated at load time from `oracle_tags.json`.

`CardDB.tags[oracle_id]` holds all ancestor-expanded tag labels (used for search). `CardDB.leaf_tags[oracle_id]` holds only the directly-assigned tags. `db.get_tags(oracle_id)` returns the expanded list. `db.get_leaf_tags(oracle_id)` returns the leaf list.

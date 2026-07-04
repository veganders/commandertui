# Commander Deckbuilder — development notes

## Partner / background commander logic

The `p` keybinding and any "Partner" label in the top bar must only be shown and functional when the primary commander actually supports a second commander. Everything needed is in the card's `keywords` list and `oracle_text` — no external lookup required.

### Detection — all known cases

| Case | Detection | Valid partners |
|---|---|---|
| **Generic Partner** | `"Partner" in keywords` AND no `"Partner with"` in keywords AND no `Partner—` in oracle_text | Any other generic-Partner card (same three conditions) |
| **Partner with (specific)** | `"Partner with" in keywords` | Exactly one card; extract name with `re.search(r"Partner with ([^(\n]+)", oracle_text)` |
| **Partner—X (pool variant)** | `re.search(r"Partner—([^(]+)", oracle_text)` matches | Any card whose oracle_text contains the **same** `Partner—X` string |
| **Doctor's companion (Doctor side)** | `"Time Lord Doctor" in type_line` | Any card with `"Doctor's companion" in keywords` |
| **Doctor's companion (Companion side)** | `"Doctor's companion" in keywords` | Any card with `"Time Lord Doctor" in type_line` |
| **Choose a Background** | `"Choose a background" in keywords` (lowercase b — that's how Scryfall stores it) | Any card with `"Background" in type_line` |

**Known Partner—X values in the current dataset** (detected automatically; do not hardcode):

| Mechanic name | Set |
|---|---|
| Friends forever | Unfinity / Doctor Who adjacent |
| Character select | Teenage Mutant Ninja Turtles |
| Survivors | (recent set) |
| Father & son | (recent set) |

Because new sets can introduce new `Partner—X` names, the implementation must extract and match the name dynamically rather than checking for specific strings.

### Required behaviour

- Show `p` binding / Partner label **only** when `partner_mode(commander)` returns non-None.
- If a partner is already set, pressing `p` clears it (toggle).
- When opening the partner search screen, pre-filter candidates to the valid pool for this commander (see table above).
- These filters must be applied in the search post-filter, not just in placeholder text.
- **Partner with**: do not open a search screen — look up the named card directly in the DB and set it immediately. Show a notification if the card is not found.

### Helper function to implement

Add `partner_mode(card: Card) -> dict | None` (in `app.py` or a shared helpers module). Returns one of:

```python
None                                          # no second commander allowed
{"type": "partner"}                           # generic partner pool
{"type": "partner_with", "name": str}         # exactly one named card
{"type": "partner_variant", "mechanic": str}  # Partner—X pool (extract X from oracle)
{"type": "doctors_companion"}                 # Doctor ↔ companion pairing
{"type": "background"}                        # Background enchantments
```

Use this in both the top bar rendering (show/hide the label) and the `action_search_partner` handler so the logic is not duplicated.

### Detection priority (in order — stop at first match)

1. `"Partner with" in keywords` → `partner_with`
2. `"Doctor's companion" in keywords` → `doctors_companion`
3. `"Time Lord Doctor" in type_line` → `doctors_companion`
4. `"Choose a background" in keywords` → `background`
5. `re.search(r"Partner—([^(]+)", oracle_text)` → `partner_variant` (extract mechanic name)
6. `"Partner" in keywords` → `partner`

---

## Data model

### Card

`Card.allows_multiple() -> bool` lives on the `Card` class in `db.py`. It returns True for basic lands (`"Basic" in type_line`) and cards whose oracle text contains `"a deck can have any number of cards named"`. Do not duplicate this check elsewhere.

`_SPLIT_LAYOUTS` in `db.py` lists layouts where `oracle_text` and `mana_cost` live inside `card_faces` rather than at the top level. Currently: `transform`, `modal_dfc`, `flip`, `split`, `adventure`, `battle`, `prepare`. When Scryfall introduces a new multi-face layout and cards show empty oracle text, add it here.

`Card.display_label(currency, printing_idx) -> rich.text.Text` returns a formatted label: `[mana cost] Name [EUR: 1.23]`. For multi-face cards with mana costs on multiple faces it renders `[1R] Fire // [1U] Ice [EUR: 0.50]`. Returns a `Text` object (not a string) so brackets are always literal, never parsed as Rich markup.

### CardRole / CardEntry / Group / Deck

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

Mana curve excludes cards where every face is a land (`all("Land" in face for face in type_line.split(" // "))`). MDFCs with a non-land face (e.g. `"Sorcery // Land"`) are included.

Cards with no group memberships appear in a dynamic **Uncategorized** node at the bottom of the tree (not a real Group — its tree node has `data=None`).

---

## Oracle tag hierarchy

Tags in `oracle_tags.json` form a parent–child hierarchy via `parent_ids`. During `load_db()` each card's tag set is expanded to include all ancestor labels (e.g. "mana rock" → "mana producer" → "ramp"). This means `otag:ramp` correctly matches mana rocks, mana dorks, land ramp spells, etc. without hardcoding child tag names.

The expansion is done once at load time via a memoised recursive `_all_labels(tag_id)` helper defined inside `load_db()`. The stored `db.tags[oracle_id]` list already contains all ancestor labels. Leaf-only tags are stored separately in `db.leaf_tags[oracle_id]`. See also the [Oracle tag leaf vs. ancestor tags](#oracle-tag-leaf-vs-ancestor-tags) section below.

---

## Search syntax

| Filter | Meaning |
|---|---|
| bare word | name substring |
| `t:type` | type line substring |
| `o:"text"` | oracle text substring (quotes allow spaces) |
| `id:wubrg` | color identity is a **subset** of the given colors |
| `c:rg` | card colors include **at least** red and green |
| `otag:ramp` | oracle tag exact match (matches ancestors — see above) |
| `kw:partner` | keyword substring (matches entries in `card.keywords`) |
| `r:rare` | exact rarity |
| `mv>=3` | mana value comparison (`=` `<` `>` `<=` `>=`) |
| `power>=3` / `toughness<=5` | power/toughness comparison; `:` means `=`. Non-numeric values (e.g. `*`) count as 0, consistent with Scryfall. |
| `eur<=1` / `usd>=5` / `tix=0` | price comparison against cheapest printing |
| `-t:creature` | negate any filter |
| `AND` / `OR` / `( )` | explicit boolean; AND has higher precedence than OR |

Implied `id:` filter is applied automatically in group-search mode based on the commander + partner color identity.

---

## Auto-routing when adding cards

When a card is toggled or incremented into the deck from the search screen (`space` or `+`), it is automatically routed to the correct permanent group rather than the group that was active when `s` was pressed:

| Condition | Target group |
|---|---|
| any face of `type_line` contains `"land"` (case-insensitive) | **Lands** |
| oracle tag exactly equals `"ramp"` | **Ramp** |
| oracle tag exactly equals `"draw"` | **Draw** |
| oracle tag exactly equals `"removal"` | **Interaction** |
| none of the above | fallback to the currently open group |

A card can match multiple conditions and land in multiple groups. Lookup is by group name (case-insensitive); if no matching group exists, that condition is skipped.

Tag routes are defined in `SearchScreen._TAG_ROUTES` as a list of `(tag, group_name)` tuples — add new routes there. Lands remain a special case (type-line check, not a tag). Tag matching is exact (not substring) to avoid false matches like "drawback" → Draw.

The `+` key on a card already in the deck increments its count in whichever group(s) already hold it, rather than re-routing.

Toggling off (space when card is already in deck) removes the card from **all** groups.

---

## Oracle tag leaf vs. ancestor tags

`CardDB.tags[oracle_id]` holds all ancestor-expanded tag labels (used for search and the histogram's "all tags" mode). `CardDB.leaf_tags[oracle_id]` holds only the directly-assigned tags (used for the histogram's "leaf tags" mode). Both are populated at load time in `load_db()`.

`db.get_tags(oracle_id)` returns the expanded list. `db.get_leaf_tags(oracle_id)` returns the leaf list.

---

## Tag histogram (`h`)

`TagHistogramScreen` in `histogram.py`. Shows all tags present in the current deck with counts, sorted descending. Toggle between leaf-only and ancestor-expanded modes with `t`. Method is named `_build_content` — do **not** rename to `_render` (Textual uses that internally).

---

## Save / load (`deck_io.py`)

Decks are saved as JSON to `data/decks/`. Filename is a slugified deck name; collisions get `_2`, `_3` appended.

Save format:
```json
{
  "name": "My Deck",
  "groups": [{"name": "Ramp", "permanent": true}, ...],
  "commander": {"oracle_id": "...", "printing": {"set_code": "m21", "collector_number": "123", "finish": "nonfoil"}},
  "partner": null,
  "cards": [
    {"oracle_id": "...", "printing": {...}, "count": 1, "groups": ["Ramp"]}
  ]
}
```

`finish` is included in the printing key because Scryfall can have distinct foil/nonfoil entries with the same set + collector number.

`_printing_dict(entry)` takes a `CardEntry` and serialises its `printing_idx` to `{set_code, collector_number, finish}`. `_find_printing_idx(card, printing_data)` is the inverse — scans `card.printings` and returns the matching index (0 if not found).

`load_deck(path, db)` returns a new `Deck` with commander/partner as `CardEntry(role=CardRole.COMMANDER/PARTNER)`. In `app.py`, `action_open_deck` mutates `self._deck` in-place (copies all fields) so `TopBar` and other live widget references stay valid without needing updates. After loading, `_ensure_permanent_groups(deck)` is called to add any permanent groups missing from older saves (e.g. Maybeboard added after the deck was created).

`Deck.name` and `Deck.save_path` are set after the first save or after opening a file. Subsequent `ctrl+s` saves skip the name prompt and write directly to `save_path`.

---

## Key bindings (main window)

| Key | Action |
|---|---|
| `c` | Search / set commander |
| `p` | Search / set partner (hidden when commander has no partner mode) |
| `s` | Open card search for the current group |
| `g` | Create a new group (prompts for name via `GroupNameModal`) |
| `d` | On a card leaf: remove card entirely. On a group: remove group memberships + delete group (permanent groups: clear memberships only). |
| `e` | On a card leaf: open `CardGroupEditorScreen` to toggle group memberships and adjust count |
| `m` | On a card leaf: toggle maybeboard status (adds/removes from the Maybeboard group) |
| `h` | Open tag histogram screen |
| `o` | Cycle sort order within groups (Name → MV → Price → Name …) |
| `ctrl+n` | New deck — resets to initial state (five permanent groups, no cards) |
| `ctrl+s` | Save deck (prompts for name on first save, then saves in place) |
| `ctrl+o` | Open saved deck (shows list sorted by most-recently-modified) |
| `+` | Increment copy count for the focused card (only if `card.allows_multiple()`) |
| `-` | Decrement copy count for the focused card |
| `q` | Quit |

---

## Otag autocomplete (`search.py`)

When the user types `otag:` in the search input, a suggestion dropdown appears below showing matching tag names. Selecting a tag completes the token in-place.

### Implementation

- **`_SmartInput(Input)`** subclass handles `"` auto-pairing. When `"` is typed:
  - If the character at the cursor is already `"`, jump the cursor over it.
  - Otherwise insert `""` and place the cursor between them.
  - Uses `event.prevent_default()` (not `event.stop()`) to break Textual's MRO dispatch loop so `Input._on_key` doesn't also run and double-insert the character. `event.stop()` only prevents widget-tree bubbling; `prevent_default()` sets `_no_default_action` which is checked at the top of each MRO iteration.
  - `select_on_focus=False` must be set on the input to prevent `inp.focus()` from selecting all text after autocomplete.

- **`_otag_context(value, pos) -> tuple[int, int, str] | None`** — scans left from cursor to find the current token, checks for `otag:` prefix (with optional leading `-`), handles both quoted (`otag:"card draw`) and unquoted (`otag:ramp`) forms. Returns `(token_start, token_end, partial)` or `None` if not in an otag token or the token is already complete (closing `"` present).

- **`_update_suggestions(value, pos)`** — called from `on_input_changed`. Filters `_all_tags` by the partial string, populates the `#srch-suggest` ListView, sets `margin-left` to align the dropdown with the token position, and sets `sugg.index = 0` so the first item is pre-highlighted. Hides when no matches or not in otag context.

- **`_apply_suggestion(tag)`** — replaces the typed partial with the completed tag. `inp.replace(text, start, end)` uses **exclusive** `end` (Python slice semantics: `value[end:]` is the preserved tail). So `replace_end = token_end` (no closing quote) or `replace_end = token_end + 1` (consume auto-paired closing `"`).

- Tags with spaces are wrapped in quotes: `otag:"card draw"`. Tags without spaces are unquoted: `otag:ramp`.

- `#srch-suggest` ListView is positioned below the search bar (not inline). Navigation while suggestions are visible (focus stays in input throughout):
  - `enter` — apply the currently highlighted suggestion
  - `tab` — cycle highlight forward (wraps around)
  - `shift+tab` — cycle highlight backward (wraps around)
  - `escape` — close the dropdown
  Both `tab` and `shift+tab` use `event.stop()` to suppress Textual's default focus-cycling behaviour.

---

## Search screen (`search.py`) — misc

`SearchScreen` is typed `Screen[str]` and dismisses with the current query string. `DeckbuilderApp` stores this in `_last_search_query` and passes it back as `initial_query` the next time the group search screen is opened, so the query is remembered for the session. Commander and partner searches always open with an empty query.

`_sync_detail_to_cursor()` is called via `call_after_refresh` at the end of every `_rebuild_tree()`. This ensures the `CardDetail` panel always reflects the actual cursor position after a rebuild — Textual's `NodeHighlighted` event does not re-fire when the cursor index is unchanged but a different card is now at that position (e.g. after a card moves to another group).

---

## CardDetail printing select (`widgets.py`)

The printing `Select` in `CardDetail` encodes both the oracle_id and the printing index in the option value using a `_PrintingKey(oracle_id, idx)` NamedTuple. This means `on_select_changed` is entirely self-contained — it reads `event.value.oracle_id` and `event.value.idx` directly rather than relying on any widget-level mutable state.

This matters because Textual fires `Select.Changed` asynchronously: by the time the handler runs, the user may have already navigated to a different card, making any `_current_oracle_id` field stale. Encoding the identity in the value avoids that race entirely.

`isinstance(event.value, _PrintingKey)` is the guard — blank/reset events from `set_options` produce `Select.BLANK`, which is not a `_PrintingKey` and is silently ignored.

---

## Sorting (`sorting.py`)

Cards within each group are sorted by the current sort order, cycled with `o`. The sort is purely cosmetic — it never affects the deck data.

```python
class CardSorter(ABC):
    label: str = ""
    @abstractmethod
    def key(self, entry: CardEntry) -> Any: ...

class NameSorter(CardSorter):   label = "Name";  key → entry.card.name.lower()
class MVSorter(CardSorter):     label = "MV";    key → entry.card.cmc
class PriceSorter(CardSorter):  label = "Price"; key → entry.price(currency) or inf
```

`PriceSorter(currency)` takes currency in its constructor — no deck reference needed since `entry.price()` is self-contained.

`DeckbuilderApp._sorters()` returns `[NameSorter(), MVSorter(), PriceSorter(currency)]`. `_sort_idx` cycles through them. `action_cycle_sort` rebuilds the tree and shows a notify with the new label. Add new sorters by appending to `_sorters()`.

Commander/partner nodes are not sorted (they're always shown first, in commander-then-partner order).

---

## Maybeboard

A card is in the maybeboard when it belongs to the `"Maybeboard"` group (constant `MAYBEBOARD` in `models.py`). The Maybeboard group is a permanent group added to every new deck and injected into older loaded decks via `_ensure_permanent_groups`.

`CardEntry.is_maybe() -> bool` returns `MAYBEBOARD in self.groups`. This is the single check used everywhere:
- `Deck.all_entries()` excludes `is_maybe()` entries → `card_count`, `mana_curve`, `total_cost` automatically exclude maybeboard cards.
- `_rebuild_tree()` filters `is_maybe()` entries from all non-Maybeboard group nodes (and from Uncategorized).
- Search screen shows `[M]` prefix instead of `[+]` for maybeboard cards; `[M]` takes priority over count in the display logic.

`m` in the main tree toggles maybeboard on the focused card. `m` in the search screen adds a card to the maybeboard if not already there, or removes it if it is (toggle). In the search screen, `[M]` is checked before `count` so maybeboard cards never incorrectly show `[+]`.

Maybeboard cards are stored in the JSON save format like any other card (as a group membership), so save/load requires no special handling.

---

## CSS conventions

Input fields and Select dropdowns use no border — a background tint signals interactivity instead. The global rules live in `DeckbuilderApp.CSS` and apply to all screens:

- Resting: `background: $surface`, `border: none`, `height: 1`
- Focused: `background: $panel`, `border: none`

Target `SelectCurrent` (not `Select`) to style the visible trigger of a dropdown.

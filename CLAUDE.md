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

`Card.display_label(currency, printing_idx) -> rich.text.Text` returns a formatted label: `[mana cost] Name [EUR: 1.23]`. For multi-face cards with mana costs on multiple faces it renders `[1R] Fire // [1U] Ice [EUR: 0.50]`. Returns a `Text` object (not a string) so brackets are always literal, never parsed as Rich markup.

### CardEntry / Group / Deck

Cards have a single deck-level entry with a count and a set of group names they belong to. Groups are just named categories — they hold no card list themselves.

```python
@dataclass
class CardEntry:
    card: Card
    count: int = 1
    groups: set[str] = field(default_factory=set)
    # helpers: in_group(name), join_group(name), leave_group(name)

@dataclass
class Group:
    name: str
    permanent: bool = False  # if True, d-key clears memberships but never removes the group

@dataclass
class Deck:
    commander: Optional[Card] = None
    partner: Optional[Card] = None
    groups: list[Group] = field(default_factory=list)
    entries: dict[str, CardEntry] = field(default_factory=dict)  # oracle_id → CardEntry
    selected_printings: dict[str, int] = field(default_factory=dict)
    name: Optional[str] = None
    save_path: Optional[Path] = None
```

Key `Deck` helpers: `add(card)`, `remove_one(oracle_id)`, `remove_all(oracle_id)`, `count_of(oracle_id)`, `get_entry(oracle_id)`, `entries_for_group(name)`, `uncategorized_entries()`.

`Deck.all_entries() -> list[tuple[Card, int]]` returns commander + partner (count 1 each) + all entries, deduped by oracle_id. `card_count()`, `mana_curve()`, and `total_cost()` all use this.

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
| `otag:ramp` | oracle tag substring (matches ancestors — see above) |
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
| any oracle tag contains `"ramp"` | **Ramp** |
| any oracle tag contains `"draw"` | **Draw** |
| none of the above | fallback to the currently open group |

A card can match multiple conditions and land in multiple groups (e.g. a card tagged both ramp and draw). Lookup is by group name (case-insensitive); if no group named "Lands" / "Ramp" / "Draw" exists, that condition is skipped.

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

`load_deck(path, db)` returns a new `Deck`. In `app.py`, `action_open_deck` mutates `self._deck` in-place (copies all fields) so `TopBar` and other live widget references stay valid without needing updates.

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
| `h` | Open tag histogram screen |
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

- **`_update_suggestions(value, pos)`** — called from `on_input_changed`. Filters `_all_tags` by the partial string, populates the `#srch-suggest` ListView, and sets `margin-left` to align the dropdown with the token position. Hides when no matches or not in otag context.

- **`_apply_suggestion(tag)`** — replaces the typed partial with the completed tag. `inp.replace(text, start, end)` uses **exclusive** `end` (Python slice semantics: `value[end:]` is the preserved tail). So `replace_end = token_end` (no closing quote) or `replace_end = token_end + 1` (consume auto-paired closing `"`).

- Tags with spaces are wrapped in quotes: `otag:"card draw"`. Tags without spaces are unquoted: `otag:ramp`.

- `#srch-suggest` ListView is positioned below the search bar (not inline). Navigation: `down` from input focuses the list; `up` from index 0 or `escape` returns focus to input.

---

## CardDetail printing select (`widgets.py`)

The printing `Select` in `CardDetail` encodes both the oracle_id and the printing index in the option value using a `_PrintingKey(oracle_id, idx)` NamedTuple. This means `on_select_changed` is entirely self-contained — it reads `event.value.oracle_id` and `event.value.idx` directly rather than relying on any widget-level mutable state.

This matters because Textual fires `Select.Changed` asynchronously: by the time the handler runs, the user may have already navigated to a different card, making any `_current_oracle_id` field stale. Encoding the identity in the value avoids that race entirely.

`isinstance(event.value, _PrintingKey)` is the guard — blank/reset events from `set_options` produce `Select.BLANK`, which is not a `_PrintingKey` and is silently ignored.

---

## CSS conventions

Input fields and Select dropdowns use no border — a background tint signals interactivity instead. The global rules live in `DeckbuilderApp.CSS` and apply to all screens:

- Resting: `background: $surface`, `border: none`, `height: 1`
- Focused: `background: $panel`, `border: none`

Target `SelectCurrent` (not `Select`) to style the visible trigger of a dropdown.

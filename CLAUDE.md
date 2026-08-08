# Commander Deckbuilder — development notes

## Code style

- **DRY**: if the same logic appears more than once, extract it — a helper function, a method on the relevant class, or a shared constant. Do not repeat detection strings, pop patterns, or branching logic across commander/partner paths or other symmetric cases. Ask yourself where the logic *belongs* before writing it, not after.

- **Base-class gravity**: when writing or modifying a child class, check whether the new or changed code could live in the base class instead. If two sibling classes share the same method body, event handler, or attribute initialisation, it belongs in the base. Apply this check proactively — don't wait to be asked.

---

## Partner / background commander logic

The `p` keybinding and any "Partner" label in the top bar must only be shown and functional when the primary commander actually supports a second commander. Everything needed is in the card's `keywords` list and `oracle_text` — no external lookup required.

### Detection — all known cases

| Case | Detection | Valid partners |
|---|---|---|
| **Generic Partner** | `"partner" in keywords` AND no `"partner with"` in keywords AND no `Partner—` in oracle_text | Any other generic-Partner card (same three conditions) |
| **Partner with (specific)** | `"partner with" in keywords` | Exactly one card; extract name with `re.search(r"Partner with ([^(\n]+)", oracle_text)` |
| **Partner—X (pool variant)** | `re.search(r"Partner—([^(]+)", oracle_text)` matches | Any card whose oracle_text contains the **same** `Partner—X` string |
| **Doctor's companion (Doctor side)** | `"time lord doctor" in type_line` | Any card with `"doctor's companion" in keywords` |
| **Doctor's companion (Companion side)** | `"doctor's companion" in keywords` | Any card with `"time lord doctor" in type_line` |
| **Choose a Background** | `"choose a background" in keywords` | Any card with `"background" in type_line` |

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

### Helper function (`partner.py`)

`partner_mode(card: Card) -> dict | None` returns one of:

```python
None                                          # no second commander allowed
{"type": "partner"}                           # generic partner pool
{"type": "partner_with", "name": str}         # exactly one named card
{"type": "partner_variant", "mechanic": str}  # Partner—X pool (extract X from oracle)
{"type": "doctors_companion"}                 # Doctor ↔ companion pairing
{"type": "background"}                        # Background enchantments
```

Used in both the top bar rendering (show/hide the label) and the `action_search_partner` handler so the logic is not duplicated.

### Color-choosing commanders

Some commanders/partners require a color choice before the game begins. Detection: `"is your commander, choose a color before the game begins" in card.oracle_text.lower()`. Currently matches: Faceless One, Clara Oswald, The Prismatic Piper.

When such a card is set as commander or partner, `SearchScreen._maybe_prompt_color_choice` pushes `ColorChoiceModal` (in `widgets.py`). The chosen color is stored as `CardEntry.color_identity_override: Optional[list[str]]`. `CardEntry.color_identity` (property) returns the override if set, otherwise `card.color_identity`. All code that needs the effective color identity (e.g. `_implied_node` in `search.py`) uses `entry.color_identity`, never `entry.card.color_identity` directly.

The override is shown in the deck tree as `[U]` etc. after the card name, saved/loaded in the JSON as `"color_identity_override"` on the commander/partner object, and defaults to `null` (no override) when absent.

### Detection priority (in order — stop at first match)

1. `"partner with" in keywords` → `partner_with`
2. `"doctor's companion" in keywords` → `doctors_companion`
3. `"time lord doctor" in type_line` → `doctors_companion`
4. `"choose a background" in keywords` → `background`
5. `re.search(r"Partner—([^(]+)", face.oracle_text)` for each face → `partner_variant` (extract mechanic name)
6. `"partner" in keywords` → `partner`

---

## Data model

### CardFace

`CardFace` (in `db.py`) holds all per-face data: `name`, `mana_cost`, `oracle_text`, `type_line`, `power`, `toughness`, `loyalty`. Every `Card` has at least one face; multi-face cards (split, transform, modal_dfc, adventure, etc.) have one `CardFace` per Scryfall face.

`type_line` is stored **lowercase** (normalized at parse time). `oracle_text` keeps its original case — it is displayed to the user and searched case-insensitively at query time.

### Card

`Card` stores only card-level fields: `oracle_id`, `name`, `cmc`, `colors`, `color_identity`, `keywords`, `rarity`, `layout`, `printings`, `faces`. Per-face data lives exclusively in `card.faces` — there are no `oracle_text`, `type_line`, `power`, `toughness`, `loyalty`, or `mana_cost` fields on `Card` itself.

`keywords` are stored **lowercase** (normalized at parse time). Pass lowercase strings when checking membership.

`Card.has_type(text) -> bool` returns True if `text` appears in any face's `type_line`. Since `type_line` is lowercase, always pass a lowercase argument. `Card.has_oracle(text) -> bool` returns True if `text` appears in any face's `oracle_text`. Use these instead of accessing face fields directly when checking a simple substring across all faces.

`Card.allows_multiple() -> bool` returns True for basic lands and cards whose oracle text contains `"a deck can have any number of cards named"`. Uses `has_type` and `has_oracle` internally. Do not duplicate this check elsewhere.

`_CARD_KEEP_FIELDS` and `_CARD_FACE_KEEP_FIELDS` in `scryfall.py` are whitelists applied to `default_cards` during download (via `_strip_card`). Only fields actually used by `_parse_card` / `_extract_printings` are kept; everything else (artist, flavor text, image URIs, legalities, purchase URIs, etc.) is discarded at sync time to keep the on-disk file small. `_CARD_FACE_KEEP_FIELDS` includes `name`, `mana_cost`, `oracle_text`, `type_line`, `power`, `toughness`, `loyalty`. If a new field is needed, add it to the appropriate whitelist and re-sync with `python scryfall.py --force`.

`_SPLIT_LAYOUTS` in `db.py` lists layouts where face data lives inside `card_faces` rather than at the top level. Currently: `transform`, `modal_dfc`, `flip`, `split`, `adventure`, `battle`, `prepare`. For these layouts `_parse_card` builds one `CardFace` per Scryfall face. For all other layouts it builds a single `CardFace` from the top-level fields. When Scryfall introduces a new multi-face layout and cards show empty oracle text, add it here.

`Card.display_label(currency, printing_idx) -> rich.text.Text` returns a formatted label: `[mana cost] Name [EUR: 1.23]`. For multi-face cards where multiple faces carry their own mana cost it renders `[1R] Fire // [1U] Ice [EUR: 0.50]`; for transform/flip cards (only front face has a cost) it renders `[U] Delver of Secrets // Insectile Aberration`. Returns a `Text` object (not a string) so brackets are always literal, never parsed as Rich markup.

Search filters `o:`, `t:`, `power`, `toughness` loop over `card.faces` and match if **any** face satisfies the condition. This means `power>=10` matches a DFC whose back face is a 13/13.

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

---

## Oracle tag hierarchy

Tags in `oracle_tags.json` form a parent–child hierarchy via `parent_ids`. During `load_db()` each card's tag set is expanded to include all ancestor labels (e.g. "mana rock" → "mana producer" → "ramp"). This means `otag:ramp` correctly matches mana rocks, mana dorks, land ramp spells, etc. without hardcoding child tag names.

The expansion is done once at load time via a memoised recursive `_all_labels(tag_id)` helper defined inside `load_db()`. The stored `db.tags[oracle_id]` list already contains all ancestor labels. Leaf-only tags are stored separately in `db.leaf_tags[oracle_id]`. See also the [Oracle tag leaf vs. ancestor tags](#oracle-tag-leaf-vs-ancestor-tags) section below.

`db.tags_norm[oracle_id]` is a pre-built `frozenset[str]` of the same expanded labels with spaces and hyphens stripped and lowercased (via `_norm_tag`). Used by `otag:` evaluation for O(1) normalized lookup — no per-query normalization cost.

---

## Search syntax

| Filter | Meaning |
|---|---|
| bare word | name substring |
| `t:type` | type line word-boundary match (`t:rat` matches Rat but not Pirate) |
| `o:"text"` | oracle text substring (quotes allow spaces; `\"` escapes a literal quote) |
| `o:/regex/` | oracle text regex (Python syntax, case-insensitive; `\/` escapes a literal `/`) |
| `id:wubrg` | color identity is a **subset** of the given colors; `id:c` means colorless (Scryfall-consistent — `C` is filtered out of WUBRG, leaving an empty set that only colorless cards satisfy) |
| `id=ur` | color identity is **exactly** the given colors; `id=c` means exactly colorless |
| `c:rg` | card colors include **at least** red and green |
| `otag:ramp` | oracle tag match (matches ancestors — see above); spaces and hyphens are ignored, so `otag:manadork` and `otag:mana-dork` both match `mana dork` |
| `kw:partner` | keyword match; stored lowercase so the check is case-insensitive in practice |
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
| any face of `type_line` contains `"land"` | **Lands** |
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

`CardDB.tags[oracle_id]` holds all ancestor-expanded tag labels (used for search). `CardDB.leaf_tags[oracle_id]` holds only the directly-assigned tags. Both are populated at load time in `load_db()`.

`db.get_tags(oracle_id)` returns the expanded list. `db.get_leaf_tags(oracle_id)` returns the leaf list.

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
| `o` | Cycle sort order within groups (Name → MV → Price → Name …) |
| `G` | Cycle grouping mode (Named → Type → MV → Named) |
| `S` | Focus deck filter input (filters tree in place; stays active until manually cleared) |
| `ctrl+e` | Export deck (opens exporter picker) |
| `ctrl+n` | New deck — resets to initial state (five permanent groups, no cards) |
| `ctrl+s` | Save deck (prompts for name on first save, then saves in place) |
| `ctrl+o` | Open saved deck (shows list sorted by most-recently-modified) |
| `+` | Increment copy count for the focused card (only if `card.allows_multiple()`) |
| `-` | Decrement copy count for the focused card |
| `x` | Open Color Scout (explore card counts by color identity) |
| `q` | Quit |

---

## QueryInput (`widgets.py`)

`QueryInput(Input)` is a reusable input widget used everywhere a query string is entered (search screen and deck filter). It handles three concerns in one place:

- **Quote auto-pairing** via `_on_key`: if `"` is typed and the cursor is already on a `"`, jump over it; otherwise insert `""` and position the cursor between them. Uses `event.prevent_default()` (not `event.stop()`) — `prevent_default()` sets `_no_default_action` which stops Textual's MRO dispatch loop, while `event.stop()` only prevents widget-tree bubbling.
- **Debounce** via `watch_value`: starts/restarts a timer on every value change; posts `QueryInput.Debounced(input, value, from_submit)` when it fires, and fires immediately on Enter. `from_submit` is `False` for timer-fired events and `True` for Enter. Timer delay is configurable (`delay` constructor param; search screen uses 1.0s, deck filter uses 0.4s).
- **Validation tinting**: `watch_value` calls `validate_query` and adds/removes the `query-error` CSS class. The CSS rules for `query-error` must live in **app-level or screen-level CSS** — `DEFAULT_CSS` has lower priority than app CSS and will be silently overridden.

**Textual CSS gotchas for `QueryInput`:**
- App-level `Input { ... }` rules do **not** automatically apply to `QueryInput` — add explicit `QueryInput { ... }` rules alongside them.
- `DEFAULT_CSS` on a widget has **lower** priority than app/screen CSS. Error-state rules (`QueryInput.query-error`) must be in `DeckbuilderApp.CSS` and `SearchScreen.CSS`, not in `DEFAULT_CSS`.
- `on_input_changed` defined on a widget is **not** called for events the widget itself posts (Textual dispatches to parents, not the sender). Use `watch_value` instead for reactive logic that needs to fire on the widget itself.
- `select_on_focus=False` must be set on the input to prevent `inp.focus()` from selecting all text after autocomplete.

Consumers handle `on_query_input_debounced(event: QueryInput.Debounced)` to react to the settled value. `event.input` gives back the widget (useful for filtering by id); `event.from_submit` distinguishes Enter-triggered fires from timer-triggered ones (used by `SearchScreen` to decide whether to close the suggestion dropdown).

---

## Filter token autocomplete (`widgets.py`, `search.py`)

When the user types a supported filter prefix (`otag:`, `t:`, `kw:`) in the search input or deck filter, a suggestion dropdown appears below showing matching values. Selecting a value completes the token in-place.

### Implementation

- The search input is a `QueryInput` (see above). `select_on_focus=False` must be set to prevent `inp.focus()` from selecting all text after autocomplete.

- **`_filter_token_context(value, pos, prefixes) -> tuple[int, int, str, str] | None`** — scans left from cursor to find the current token, checks for any of the given prefixes (with optional leading `-`), handles quoted and unquoted forms. Returns `(token_start, token_end, partial, matched_prefix)` or `None` if not in a matching token or the token is already complete (closing `"` present).

- **`FilterSuggestions`** — manages a dropdown for a `QueryInput` + `ListView` pair. Takes `candidates: dict[str, list[str]]` mapping each prefix to its completion list. Core methods: `update(value, pos)` detects the active prefix and filters candidates; `apply(value, callback)` replaces the token, appends a trailing space if the character after the replaced range isn't already one (prevents the dropdown from immediately reopening), and optionally calls back with the new query; `navigate(direction)` handles Tab/Shift+Tab cycling; `current_value()` returns the highlighted entry. Convenience handler methods — call these from screen `on_*` methods to share logic without duplication: `handle_input_changed(event)`, `handle_debounced(event, callback)`, `handle_key(event, callback)` (returns True if consumed), `handle_list_selected(event, callback)` (returns True if consumed). Used by `SearchScreen` (`#srch-suggest`), `ColorScoutScreen` (`#cs-suggest`), and `DeckbuilderApp` (`#deck-suggest`).

- **`build_filter_candidates(db) -> dict[str, list[str]]`** — builds all three candidate lists in one pass. **Call once per session** (in `DeckbuilderApp.on_mount`); store the result in `self._filter_candidates` and pass it to every `SearchScreen` via the `filter_candidates=` constructor argument. `SearchScreen.on_mount` no longer recomputes it. Candidate sources:
  - `otag:` → all ancestor-expanded tag labels from `db.tags`
  - `t:` → `extract_type_words()` applied to every card's type line
  - `kw:` → all unique keyword strings from `card.keywords`

- **`extract_type_words(type_line) -> set[str]`** — splits a type line into individual completion tokens. Replaces `—` with a space, strips `//` (split-card separator), and preserves entries in `_MULTIWORD_TYPES` as single tokens before splitting the remainder. Add new multi-word types to `_MULTIWORD_TYPES` in `widgets.py` (currently: `["time lord"]`). Tokens are already lowercase since `type_line` is stored lowercase.

- Values with spaces are wrapped in quotes on completion: `otag:"card draw"`, `kw:"partner with"`. Values without spaces are unquoted.

- `SearchScreen.on_query_input_debounced` uses `event.from_submit` to decide whether to close the dropdown: timer-fired debounce (`from_submit=False`) leaves it open; Enter (`from_submit=True`) closes it.

- Dropdown (`#srch-suggest` / `#deck-suggest`) is positioned below the input. Navigation while suggestions are visible (focus stays in the input):
  - `enter` — apply the currently highlighted suggestion
  - `tab` — cycle highlight forward (wraps around)
  - `shift+tab` — cycle highlight backward (wraps around)
  - `escape` — close the dropdown
  Both `tab` and `shift+tab` call both `event.prevent_default()` and `event.stop()`. `event.stop()` alone is sufficient in `SearchScreen` (screen-level handler stops the event before it reaches the App, so the App's `focus_next` binding never fires), but in `DeckbuilderApp` the handler is already at the App level — `event.prevent_default()` is required there to cancel the built-in Tab focus-cycling action.

---

## Card list screens (`search.py`)

### `CardListScreen` base class

`CardListScreen` is the shared base for `SearchScreen` and `SimilarCardsScreen`. It provides:

- A two-panel layout: scrollable `ListView` (`#card-list`) on the left, `CardDetail` on the right.
- All shared event handlers: `on_input_changed` (autocomplete), `on_query_input_debounced` (search), `on_list_view_selected` (detail sync), `on_key` (autocomplete nav).
- All card-manipulation actions: `action_toggle_card`, `action_increment_card`, `action_add_to_maybeboard`.
- Hook methods subclasses override: `_compose_header()` (yields the title bar / search bar), `_run_search(query)` (executes the search and populates `self._results`), `_card_extra_prefix(card)` (optional Rich Text prefix shown before the card label).
- `self._suggestions: Optional[FilterSuggestions]` — set by subclasses in `__init__`; all handlers guard with `if self._suggestions`.

When writing a new card-list screen, subclass `CardListScreen`, set `self._suggestions` in `__init__`, override `_compose_header` and `_run_search`, and inherit everything else. Use `CSS = CardListScreen.CSS + """..."""` — Textual only loads `CSS` from the most-derived class, so subclass CSS must concatenate the base explicitly.

### `SearchScreen`

Typed `Screen[str]`; dismisses with the current query string. `DeckbuilderApp` stores this in `_last_search_query` and passes it back as `initial_query` the next time the group search screen is opened, so the query is remembered for the session. Commander and partner searches always open with an empty query.

`_sync_detail_to_cursor()` is called via `call_after_refresh` at the end of every `_rebuild_list()`. This ensures the `CardDetail` panel always reflects the actual cursor position after a rebuild — Textual's `NodeHighlighted` event does not re-fire when the cursor index is unchanged but a different card is now at that position (e.g. after a card moves to another group).

`on_input_changed` handles autocomplete suggestions (immediate, every keystroke). `on_query_input_debounced` handles the actual search (debounced via `QueryInput`).

---

## Find Similar (`similar.py`)

`f` on a card in the main deck tree opens `SimilarCardsScreen` — a ranked list of the most similar cards in the database, scored by shared leaf oracle tags (tag-vector dot product).

### Scoring

`find_similar(card, db, color_identity, top_n, candidate_ids)`:
- Builds a `target_tags` frozenset from the source card's **leaf** tags (directly assigned, not ancestors), excluding noise tags via `_is_noise_tag`.
- Scores every candidate as `len(target_tags & candidate_leaf_tags)`. Cards with score 0 are excluded.
- Returns up to `_TOP_N = 50` results sorted by descending score. Pass `top_n=None` to get all matches.
- If `color_identity` is given, only cards whose `color_identity ⊆ identity` are scored (same semantics as `id:` filter).
- If `candidate_ids` is given, only cards in that set are scored (used for filter-before-score mode).

### Tag blacklist

`_TAG_BLACKLIST` and `_is_noise_tag(tag)` exclude tags that describe card name or formatting rather than gameplay:

- Static blacklist: `alliteration`, `namesake spell`, `single english word name`, `unique type line`
- Dynamic: any tag containing `"errata"`, or starting with `"cycle-"`

Extend `_TAG_BLACKLIST` or the dynamic conditions in `_is_noise_tag` as new noise patterns emerge.

### Filter bar

`SimilarCardsScreen` inherits the full `CardListScreen` search bar and autocomplete. The query is applied as a pre-filter (controlled by `_FILTER_BEFORE_SCORE: bool = True`):

- `True` (default): filter candidate pool first, then score within it — top 50 are the best matches *among* cards passing the query.
- `False`: score all cards first, then filter — top 50 are the best overall matches, minus cards failing the query.

Flip `_FILTER_BEFORE_SCORE` at the top of `similar.py` to compare both strategies quickly. `_TOP_N` is the single source of truth for the cap.

---

## Deck filter (main window)

`S` focuses a `QueryInput` (`#deck-search`) above the tree. While active it filters the tree to only show matching cards — including commander/partner. Empty groups are hidden when a filter is active. The filter stays in place until manually cleared; pressing `tab` returns focus to the tree without clearing it.

`DeckbuilderApp._deck_filter: str` stores the current filter value. `_rebuild_tree()` calls `self._db.query(parse_query(self._deck_filter))` to get matching oracle_ids, then filters all entries (including commander/partner) against that set. `on_query_input_debounced` updates `_deck_filter` and rebuilds.

Invalid filter syntax turns the input red (`query-error` class) — same behaviour as the search screen. The filter uses the full search syntax (mv, otag, eur, etc.).

---

## Color Scout (`color_scout.py`)

Opened with `x` from the main window. A pre-deckbuilding exploration tool: enter a search query and see how many matching cards are playable in each of the 32 color identities (all subsets of WUBRG, plus colorless), sorted by count descending.

**Playable-within semantics**: a card is counted for an identity if `card.color_identity ⊆ identity`. A mono-green card counts for Golgari, Simic, Five-Color, etc. This answers "if I build this identity, how many cards support my theme?"

**Identity coverage**: all 32 combinations are always shown (including zero-count ones). Named identities:
- Mono: White / Blue / Black / Red / Green
- Two-color guilds: Azorius (WU), Dimir (UB), Rakdos (BR), Gruul (RG), Selesnya (WG), Orzhov (WB), Izzet (UR), Golgari (BG), Boros (WR), Simic (UG)
- Three-color: Esper (WUB), Jeskai (WUR), Bant (WUG), Mardu (WBR), Abzan (WBG), Naya (WRG), Grixis (UBR), Sultai (UBG), Temur (URG), Jund (BRG)
- Four-color: Non-Green (WUBR), Non-Red (WUBG), Non-Black (WURG), Non-Blue (WBRG), Non-White (UBRG)
- Five-Color (WUBRG), Colorless (C)

**Navigating to search**: pressing Enter on a row pushes `SearchScreen` in MODE_GROUP with `id:{identity} {query}` as the initial query. From there the user can browse the actual cards. Pressing Escape on `ColorScoutScreen` returns to the main window.

**Autocomplete and input behavior**: identical to `SearchScreen` — same `QueryInput` delay (1.0 s), same `FilterSuggestions` dropdown, same Tab/Shift+Tab/Enter/Escape handling.

`_ALL_IDENTITIES` and `_identity_str()` live in `color_scout.py`. `_identity_str` returns letters in WUBRG order, or `"C"` for the empty set.

---

## CardDetail printing select (`widgets.py`)

The printing `Select` in `CardDetail` encodes both the oracle_id and the printing index in the option value using a `_PrintingKey(oracle_id, idx)` NamedTuple. This means `on_select_changed` is entirely self-contained — it reads `event.value.oracle_id` and `event.value.idx` directly rather than relying on any widget-level mutable state.

This matters because Textual fires `Select.Changed` asynchronously: by the time the handler runs, the user may have already navigated to a different card, making any `_current_oracle_id` field stale. Encoding the identity in the value avoids that race entirely.

`isinstance(event.value, _PrintingKey)` is the guard — blank/reset events from `set_options` produce `Select.BLANK`, which is not a `_PrintingKey` and is silently ignored.

---

## Grouping (`grouping.py`)

Cards in the main deck tree can be grouped three ways, cycled with `G`:

- **Named** (`NamedGrouper`) — user-defined groups in deck order, Uncategorized at the bottom. Group nodes carry a `Group` data object so the `d` key works normally.
- **Type** (`TypeGrouper`) — one group per primary card type in the order: Creature, Instant, Sorcery, Artifact, Enchantment, Planeswalker, Battle, Land, Other. Type is determined by `card.has_type()` against each label in that priority order (first match wins). Maybeboard appended last.
- **MV** (`MVGrouper`) — one group per exact mana value present in the deck (no cap — a deck with a 12-mana card gets an MV 12 bucket); pure lands separated into a Land group. Maybeboard appended last.

All groups — including the Commander / Partner section — use `group_label()` in `_rebuild_tree`, which appends a price subtotal and card count when pricing data is available (e.g. `Ramp  (8)  €12.40`).

Synthetic group nodes (Type and MV modes) have `data=None`, so `d` on a group node is a no-op in those modes. `d`, `e`, `m` on card leaf nodes work identically in all modes.

`DeckbuilderApp._groupers()` returns `[NamedGrouper(), TypeGrouper(), MVGrouper()]`. Add new grouping strategies by appending to `_groupers()`.

`label` is an abstract property on both `CardGrouper` and `CardSorter` — concrete subclasses must implement it as a `@property`.

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

## Export system

`ctrl+e` opens `ExportModal` (in `widgets.py`) — a list picker that dismisses with the chosen `DeckExporter`. The app calls `exporter.export(deck)` and shows a notification with the exporter name on success, or an error notification on failure.

### Adding a new exporter

1. Create a new file (e.g. `moxfield.py`) with a class that extends `DeckExporter` from `exporter.py`.
2. Implement the two abstract members:
   ```python
   @property
   def name(self) -> str: return "My Exporter"
   def export(self, deck: Deck) -> None: ...
   ```
3. Add an instance to `_EXPORTERS` in `app.py`.

### Existing exporters

**`ArchidektExporter`** (`archidekt.py`) — builds an Archidekt sandbox URL and opens it in the default browser.

URL format: `https://archidekt.com/sandbox?deck=[{...}, ...]`

Each card entry is `{"c": category, "f": finish, "q": count, "u": scryfall_id}`:
- `c`: `"c"` for commander/partner, `"m"` for main deck, `"s"` for maybeboard (maps to sideboard in the sandbox, excluded from price)
- `f`: `0` for nonfoil, `1` for foil/etched (Archidekt treats any non-zero as foil)
- `u`: `Printing.scryfall_id` — the printing-specific Scryfall card ID (stored on `Printing` as `scryfall_id`, populated from `raw["id"]` at load time). Cards with no printing or missing `scryfall_id` are silently skipped.

**`ClipboardExporter`** (`clipboard.py`) — formats a text decklist and copies it via `pyperclip` (cross-platform; uses `xclip`/`xsel`/`wl-clipboard` on Linux, `pbcopy` on macOS, win32 on Windows).

Decklist format:
```
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
...

Maybeboard
1 Some Card
```

### `Printing.scryfall_id`

Added to the `Printing` dataclass in `db.py`. All `Printing` objects created from the same Scryfall card entry share the same `scryfall_id` (one entry expands into multiple `Printing` objects, one per finish). The `f` field in the Archidekt URL distinguishes finishes.

---

## CSS conventions

Input fields and Select dropdowns use no border — a background tint signals interactivity instead. The global rules live in `DeckbuilderApp.CSS` and apply to all screens:

- Resting: `background: $surface`, `border: none`, `height: 1`
- Focused: `background: $panel`, `border: none`

Target `SelectCurrent` (not `Select`) to style the visible trigger of a dropdown.

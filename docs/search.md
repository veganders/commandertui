# Search

## Search syntax

| Filter | Meaning |
|---|---|
| bare word | name substring |
| `t:type` | type line word-boundary match (`t:rat` matches Rat but not Pirate) |
| `o:"text"` | oracle text substring (quotes allow spaces; `\"` escapes a literal quote) |
| `o:/regex/` | oracle text regex (Python syntax, case-insensitive; `\/` escapes a literal `/`) |
| `[q1, q2, q3]{N}` | at least N of the comma-separated sub-queries must match; `{N}` defaults to 1 (OR) if omitted. Filters outside the brackets are AND-ed normally. Multiple groups allowed in one query. |
| `id:wubrg` | color identity is a **subset** of the given colors; `id:c` means colorless (Scryfall-consistent — `C` is filtered out of WUBRG, leaving an empty set that only colorless cards satisfy) |
| `id=ur` | color identity is **exactly** the given colors; `id=c` means exactly colorless |
| `c:rg` | card colors include **at least** red and green |
| `otag:ramp` | oracle tag match (matches ancestors); spaces and hyphens are ignored, so `otag:manadork` and `otag:mana-dork` both match `mana dork` |
| `kw:partner` | keyword match; stored lowercase so the check is case-insensitive in practice |
| `r:rare` | exact rarity |
| `mv>=3` | mana value comparison (`=` `<` `>` `<=` `>=`) |
| `power>=3` / `toughness<=5` | power/toughness comparison; `:` means `=`. Non-numeric values (e.g. `*`) count as 0, consistent with Scryfall. |
| `eur<=1` / `usd>=5` / `tix=0` | price comparison against cheapest printing |
| `-t:creature` | negate any filter |
| `AND` / `OR` / `( )` | explicit boolean; AND has higher precedence than OR |

Implied `id:` filter is applied automatically in group-search mode based on the commander + partner color identity.

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

## Filter token autocomplete (`widgets.py`, `search.py`)

When the user types a supported filter prefix (`otag:`, `t:`, `kw:`) in the search input or deck filter, a suggestion dropdown appears below showing matching values. Selecting a value completes the token in-place.

- **`_filter_token_context(value, pos, prefixes) -> tuple[int, int, str, str] | None`** — scans left from cursor to find the current token, checks for any of the given prefixes (with optional leading `-`), handles quoted and unquoted forms. Returns `(token_start, token_end, partial, matched_prefix)` or `None` if not in a matching token or the token is already complete (closing `"` present).

- **`FilterSuggestions`** — manages a dropdown for a `QueryInput` + `ListView` pair. Takes `candidates: dict[str, list[str]]` mapping each prefix to its completion list. Core methods: `update(value, pos)` detects the active prefix and filters candidates; `apply(value, callback)` replaces the token, appends a trailing space if the character after the replaced range isn't already one (prevents the dropdown from immediately reopening), and optionally calls back with the new query; `navigate(direction)` handles Tab/Shift+Tab cycling; `current_value()` returns the highlighted entry. Convenience handler methods — call these from screen `on_*` methods to share logic without duplication: `handle_input_changed(event)`, `handle_debounced(event, callback)`, `handle_key(event, callback)` (returns True if consumed), `handle_list_selected(event, callback)` (returns True if consumed). Used by `SearchScreen` (`#srch-suggest`), `ColorScoutScreen` (`#cs-suggest`), and `DeckbuilderApp` (`#deck-suggest`).

- **`build_filter_candidates(db) -> dict[str, list[str]]`** — builds all three candidate lists in one pass. **Call once per session** (in `DeckbuilderApp.on_mount`); store the result in `self._filter_candidates` and pass it to every `SearchScreen` via the `filter_candidates=` constructor argument. Candidate sources: `otag:` → ancestor-expanded tag labels; `t:` → `extract_type_words()` applied to every card's type line; `kw:` → all unique keyword strings.

- **`extract_type_words(type_line) -> set[str]`** — splits a type line into individual completion tokens. Replaces `—` with a space, strips `//` (split-card separator), and preserves entries in `_MULTIWORD_TYPES` as single tokens before splitting the remainder. Add new multi-word types to `_MULTIWORD_TYPES` in `widgets.py` (currently: `["time lord"]`). Tokens are already lowercase since `type_line` is stored lowercase.

- Values with spaces are wrapped in quotes on completion: `otag:"card draw"`, `kw:"partner with"`. Values without spaces are unquoted.

- Dropdown navigation (focus stays in input): `enter` applies highlighted suggestion; `tab` / `shift+tab` cycle highlight (wrap); `escape` closes. Both `tab` and `shift+tab` call both `event.prevent_default()` and `event.stop()`. `event.stop()` alone is sufficient in `SearchScreen` (screen-level handler stops the event before it reaches the App), but in `DeckbuilderApp` `event.prevent_default()` is also required to cancel the built-in Tab focus-cycling action.

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

## Find Similar (`similar.py`)

`f` on a card in the main deck tree opens `SimilarCardsScreen` — a ranked list of the most similar cards in the database, scored by shared leaf oracle tags (tag-vector dot product).

`find_similar(card, db, color_identity, top_n, candidate_ids)`:
- Builds a `target_tags` frozenset from the source card's **leaf** tags (directly assigned, not ancestors), excluding noise tags via `is_noise_tag` from `tag_filter.py`.
- Scores every candidate as `len(target_tags & candidate_leaf_tags)`. Cards with score 0 are excluded.
- Returns up to `_TOP_N = 50` results sorted by descending score. Pass `top_n=None` to get all matches.
- If `color_identity` is given, only cards whose `color_identity ⊆ identity` are scored (same semantics as `id:` filter).
- If `candidate_ids` is given, only cards in that set are scored (used for filter-before-score mode).

**Tag blacklist** lives in `tag_filter.py` (shared with `grouping.py`). `is_noise_tag(tag, extra)` excludes tags that describe card name or formatting rather than gameplay. Noise sources: `TAG_BLACKLIST` (individual tags; currently `"face-commander"` and `"unique type line"`); `BLACKLISTED_PARENT_TAGS` (entire subtrees by parent label; currently `"card names"`); dynamic (any tag containing `"errata"`, or starting with `"cycle-"`). `get_noise_tags(db)` computes the full set once and caches it in `_noise_cache`.

**Filter bar**: `SimilarCardsScreen` inherits the full `CardListScreen` search bar. The query is applied as a pre-filter (controlled by `_FILTER_BEFORE_SCORE: bool = True`): `True` filters candidate pool first, then scores within it; `False` scores all cards first, then filters. `_TOP_N` is the single source of truth for the cap.

## Deck filter (main window)

`S` focuses a `QueryInput` (`#deck-search`) above the tree. While active it filters the tree to only show matching cards — including commander/partner. Empty groups are hidden when a filter is active. The filter stays in place until manually cleared; pressing `tab` returns focus to the tree without clearing it.

`DeckbuilderApp._deck_filter: str` stores the current filter value. `_rebuild_tree()` calls `self._db.query(parse_query(self._deck_filter))` to get matching oracle_ids, then filters all entries (including commander/partner) against that set. `on_query_input_debounced` updates `_deck_filter` and rebuilds.

Invalid filter syntax turns the input red (`query-error` class). The filter uses the full search syntax (mv, otag, eur, etc.).

## Color Scout (`color_scout.py`)

Opened with `x` from the main window. Enter a search query and see how many matching cards are playable in each of the 32 color identities (all subsets of WUBRG, plus colorless), sorted by count descending.

**Playable-within semantics**: a card is counted for an identity if `card.color_identity ⊆ identity`. This answers "if I build this identity, how many cards support my theme?"

**Identity coverage**: all 32 combinations are always shown (including zero-count ones). Named identities: mono (W/U/B/R/G); guilds (Azorius WU, Dimir UB, Rakdos BR, Gruul RG, Selesnya WG, Orzhov WB, Izzet UR, Golgari BG, Boros WR, Simic UG); shards/wedges; four-color; Five-Color (WUBRG); Colorless (C).

**Navigating to search**: pressing Enter on a row pushes `SearchScreen` in MODE_GROUP with `id:{identity} {query}` as the initial query. `_ALL_IDENTITIES` and `_identity_str()` live in `color_scout.py`. `_identity_str` returns letters in WUBRG order, or `"C"` for the empty set.

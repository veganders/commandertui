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

### Group

```python
@dataclass
class CardEntry:
    card: Card
    count: int = 1

@dataclass
class Group:
    name: str
    cards: list[CardEntry]   # one entry per oracle_id, count tracks copies
    permanent: bool = False  # if True, d-key clears cards but never removes the group
```

Groups expose helpers: `add(card)`, `remove_one(oracle_id)`, `remove_all(oracle_id)`, `count_of(oracle_id) -> int`, `total_count() -> int`. Use these — do not manipulate `group.cards` directly.

### Deck

`Deck.all_entries() -> list[tuple[Card, int]]` returns commander + partner (count 1 each) + all group cards, deduped by oracle_id. `card_count()`, `mana_curve()`, and `total_cost()` all use this so quantities are respected everywhere.

Mana curve excludes cards where every face is a land (`all("Land" in face for face in type_line.split(" // "))`). MDFCs with a non-land face (e.g. `"Sorcery // Land"`) are included.

---

## Oracle tag hierarchy

Tags in `oracle_tags.json` form a parent–child hierarchy via `parent_ids`. During `load_db()` each card's tag set is expanded to include all ancestor labels (e.g. "mana rock" → "mana producer" → "ramp"). This means `otag:ramp` correctly matches mana rocks, mana dorks, land ramp spells, etc. without hardcoding child tag names.

The expansion is done once at load time via a memoised recursive `_all_labels(tag_id)` helper defined inside `load_db()`. The stored `db.tags[oracle_id]` list already contains all ancestor labels.

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

## TODO

### Card group membership editor

Pressing `e` on a card leaf in the main tree should open a modal/screen listing all groups. Each group is shown with an indicator of whether the card is currently a member, and the user can toggle membership the same way cards are toggled in the search screen (space to add/remove). This allows manually correcting auto-routing and adding a card to groups it wouldn't naturally land in (e.g. putting a modal MDFC into both Lands and Interaction).

Implementation notes:
- Reuse the toggle-in/toggle-out pattern from `SearchScreen` — show a `[+]` or count prefix, toggle on space.
- `+` / `-` should adjust count for `allows_multiple()` cards, same as in search.
- The screen receives the `Card` and the `Deck` and operates directly on `deck.groups`.
- On dismiss, call `_rebuild_tree()` so the tree reflects membership changes.

---

## Key bindings (main window)

| Key | Action |
|---|---|
| `c` | Search / set commander |
| `p` | Search / set partner (hidden when commander has no partner mode) |
| `s` | Open card search for the current group |
| `g` | Create a new group (prompts for name via `GroupNameModal`) |
| `d` | On a card leaf: remove all copies from its group. On a group: delete group and its cards (permanent groups: clear cards only). |
| `+` | Increment copy count for the focused card (only if `card.allows_multiple()`) |
| `-` | Decrement copy count for the focused card |
| `q` | Quit |

---

## CSS conventions

Input fields and Select dropdowns use no border — a background tint signals interactivity instead. The global rules live in `DeckbuilderApp.CSS` and apply to all screens:

- Resting: `background: $surface`, `border: none`, `height: 1`
- Focused: `background: $panel`, `border: none`

Target `SelectCurrent` (not `Select`) to style the visible trigger of a dropdown.

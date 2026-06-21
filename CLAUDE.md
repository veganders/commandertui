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

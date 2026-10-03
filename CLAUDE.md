# Commander Deckbuilder — development notes

## Code style

- **DRY**: if the same logic appears more than once, extract it — a helper function, a method on the relevant class, or a shared constant. Do not repeat detection strings, pop patterns, or branching logic across commander/partner paths or other symmetric cases. Ask yourself where the logic *belongs* before writing it, not after.

- **Base-class gravity**: when writing or modifying a child class, check whether the new or changed code could live in the base class instead. If two sibling classes share the same method body, event handler, or attribute initialisation, it belongs in the base. Apply this check proactively — don't wait to be asked.

- **No magic numbers**: every numeric literal that encodes a policy or threshold must be assigned a named constant at module level. The only exception is values so universally understood they need no name (0, 1, -1, and basic geometric constants like 2 for halving). A width, a count cap, a timeout, a score threshold — all get names.

- **Tests go in `tests/`**: any test written to verify behaviour must be added as a pytest test in `tests/` rather than run ad-hoc on the command line and discarded. Run the suite with `python -m pytest tests/` (venv active). Pure-logic tests (parsing, validation, model helpers) load no data and run in ~0.1 s. Tests that need real card data request the `db` session-scoped fixture from `tests/conftest.py` — the database is loaded once per `pytest` invocation and shared across all tests that use it.

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

### Color-choosing commanders

Detection: `"is your commander, choose a color before the game begins" in card.oracle_text.lower()`. Currently matches: Faceless One, Clara Oswald, The Prismatic Piper.

When such a card is set as commander or partner, `SearchScreen._maybe_prompt_color_choice` pushes `ColorChoiceModal` (in `widgets.py`). The chosen color is stored as `CardEntry.color_identity_override: Optional[list[str]]`. `CardEntry.color_identity` (property) returns the override if set, otherwise `card.color_identity`. All code that needs the effective color identity uses `entry.color_identity`, never `entry.card.color_identity` directly.

### Detection priority (in order — stop at first match)

1. `"partner with" in keywords` → `partner_with`
2. `"doctor's companion" in keywords` → `doctors_companion`
3. `"time lord doctor" in type_line` → `doctors_companion`
4. `"choose a background" in keywords` → `background`
5. `re.search(r"Partner—([^(]+)", face.oracle_text)` for each face → `partner_variant` (extract mechanic name)
6. `"partner" in keywords` → `partner`

---

## Textual / app.py gotchas

- `push_screen` is on `App` — use `self.app.push_screen` from inside a `Screen`.
- `query_one`, `notify`, and `@work` workers belong to `Screen`/`Widget` — use `self.*` (not `self.app.*`) from `MainScreen`.
- `render()` is a reserved Textual method name — never name an abstract or helper method `render()`.
- `@work(exit_on_error=False)` for async workers that should show error notifications rather than crashing the app.

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
| `G` | Cycle grouping mode (Named → Type → MV → Tag → Named) |
| `S` | Focus deck filter input (filters tree in place; stays active until manually cleared) |
| `f` | Open Find Similar screen for the focused card |
| `ctrl+e` | Export deck (opens exporter picker) |
| `ctrl+n` | New deck — resets to initial state (five permanent groups, no cards) |
| `ctrl+s` | Save deck (prompts for name on first save, then saves in place) |
| `ctrl+r` | Save as — always prompts for a name (pre-filled if already saved); writes to a new file |
| `ctrl+o` | Open saved deck (shows list sorted by most-recently-modified) |
| `ctrl+u` | Import deck from Archidekt (prompts for URL or deck ID) |
| `+` | Increment copy count for the focused card (only if `card.allows_multiple()`) |
| `-` | Decrement copy count for the focused card |
| `x` | Open Color Scout (explore card counts by color identity) |
| `?` | Open Settings (card pool filters, currency) |
| `q` | Quit |

---

@docs/data-model.md
@docs/search.md
@docs/ui.md
@docs/io.md

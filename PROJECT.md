# Commander Deckbuilder

## Mission

A terminal UI tool for building Magic: The Gathering Commander decks. The goal is a fast, keyboard-driven experience that works offline (after an initial data sync) and lets you browse, search, and organise cards into named groups while tracking cost and mana curve.

## What exists

### Data layer

**`scryfall.py`** — downloads three Scryfall bulk data files into `data/`:
- `default_cards.json` — one entry per printing, filtered to commander-legal cards only at download time
- `rulings.json` — official rulings, linked by oracle_id
- `oracle_tags.json` — community tags (e.g. "mana rock", "draw"), linked by oracle_id

Re-downloads only if the file is missing or older than 30 days. `--force` flag skips the age check. Run with `python scryfall.py`.

**`db.py`** — loads the three files into an in-memory `CardDB`:
- Cards deduplicated by `oracle_id`; all printings accumulated with per-source prices (`usd`, `eur`, `tix`)
- `CardDB.search(name, colors, type_line, tag, oracle_text, rarity, cmc)` — simple filter method used for direct lookups
- `CardDB.query(node)` — evaluates a boolean AST against all cards (used by the search screen)
- `CardDB.get_rulings(oracle_id)` / `get_tags(oracle_id)` — detail lookups
- `parse_query(query_string)` — parses a Scryfall-like query string into a boolean AST (`Atom`, `And`, `Or` nodes)

**Query syntax** (used in the search screen):

| Token | Meaning |
|---|---|
| `sol ring` | Name substring (bare words are implicit AND) |
| `t:creature` | Type line substring |
| `o:"draw a card"` | Oracle text substring; quotes allow multi-word phrases |
| `ci:wubrg` | Color identity subset |
| `tag:ramp` | Oracle tag substring |
| `r:rare` | Exact rarity |
| `cmc:3` / `cmc>=2` | Mana value comparison (`=` `<` `>` `<=` `>=`) |
| `AND` / `OR` | Explicit boolean; AND is also implicit between adjacent terms |
| `( ... )` | Grouping; AND has higher precedence than OR |

Example: `(t:creature or t:planeswalker) o:"draw a card" cmc<=4`

**`settings.py`** — thin JSON-backed settings at `~/.config/deckbuilder/settings.json`. Currently stores `currency` (`usd` / `eur` / `tix`). Easy to extend: add a field to the `Settings` dataclass and its name to `_KNOWN`.

### TUI (`app.py`)

Built with [Textual](https://textual.textualize.io/). Run with `python app.py`.

**Top bar**
- Commander (and partner, when set)
- Card count out of 100
- Mana curve as a compact bar-and-number display (`0▁0  1█4  2▄2 …`)
- Currency selector (Select widget) — persisted in settings
- Total deck cost in the selected currency; shows fraction if some cards have no price data

**Main view — left panel**
- `Tree` widget showing groups with cards nested inside
- Groups can be anything: "Ramp", "Draw", "Goblins", etc.
- Arrow keys navigate; cards can belong to multiple groups

**Main view — right panel**
- Card detail: name, mana cost, type line, oracle text, P/T or loyalty
- Tags from oracle_tags
- Rulings
- Printing selector (Select widget): lists every known printing with set name, collector number, finish, and price in the active currency. Defaults to the cheapest printing for the active currency. Selecting a different printing updates the deck cost.

**Key bindings (main view)**

| Key | Action |
|---|---|
| `s` | Open search screen for the currently selected group |
| `c` | Open commander search |
| `p` | Open partner search (auto-switches to background mode if commander has "Choose a Background") |
| `q` | Quit |

**Search screen** (`SearchScreen` in `app.py`)

Full-screen overlay with the same left/right split:
- Top: query input bar with placeholder showing supported syntax
- Left: scrollable list of matching cards; green `[+]` / `[N]` prefix for cards in the deck
- Right: card detail panel (same as main view, printing selector included)

Behaviour:
- Typing is debounced — search fires 1 second after the last keystroke
- Press `↓` from the input to move focus to the result list
- **Group mode**: color identity of the commander is implied (cards outside it are hidden unless overridden with an explicit `ci:` token)
- **Commander / partner / background mode**: post-filters results to valid candidates only

| Key | Action |
|---|---|
| `space` | Toggle card in/out of the target group (or set commander/partner slot) |
| `+` | Add another copy (basic lands and "any number of" cards only) |
| `-` | Remove one copy |
| `Escape` | Close and return to main view |

### Data model (in-memory, not yet persisted)

- `Deck`: commander, optional partner, list of `Group`s, `selected_printings` dict (oracle_id → printing index)
- `Group`: name + list of `Card`s
- `Deck.total_cost(currency)` uses selected or cheapest printing per card

## Known issues / deferred work

- **`default_cards.json` is still large** (~100k entries) because `default_cards` includes one entry per printing, not one per card. The filter removes non-commander-legal cards but many reprints remain. Plan: fold all printings of the same oracle_id into a single record at sync time. Deferred.

## What to build next

1. **Deck persistence** — save/load the deck (groups, selected printings, commander) to a JSON file so work survives between runs.
2. **Group management** — create, rename, and delete groups from within the TUI.
3. **Compact data format** — fold all printings of the same card into one record at sync time to shrink `default_cards.json` and speed up startup.
4. **More settings** — e.g. preferred language fallback, default price source for "cheapest" calculation.

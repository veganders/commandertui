# Commander Deckbuilder

## Mission

A terminal UI tool for building Magic: The Gathering Commander decks. The goal is a fast, keyboard-driven experience that works offline (after an initial data sync) and lets you browse, search, and organise cards into named groups while tracking cost and mana curve.

## What exists

### Data layer

**`scryfall.py`** — downloads three Scryfall bulk data files into `data/`:
- `default_cards.json` — one printing per card (best/default printing), filtered to commander-legal cards only at download time
- `rulings.json` — official rulings, linked by oracle_id
- `oracle_tags.json` — community tags (e.g. "mana rock", "draw"), linked by oracle_id

Re-downloads only if the file is missing or older than 30 days. `--force` flag skips the age check. Run with `python scryfall.py`.

**`db.py`** — loads the three files into an in-memory `CardDB`:
- Cards deduplicated by `oracle_id`; all printings accumulated with per-source prices (`usd`, `eur`, `tix`)
- `CardDB.search(name, colors, type_line, tag)` — substring/subset filters, all optional
- `CardDB.get_rulings(oracle_id)` / `get_tags(oracle_id)` — detail lookups

**`settings.py`** — thin JSON-backed settings at `~/.config/deckbuilder/settings.json`. Currently stores `currency` (`usd` / `eur` / `tix`). Easy to extend: add a field to the `Settings` dataclass and add its name to `_KNOWN`.

### TUI (`app.py`)

Built with [Textual](https://textual.textualize.io/). Run with `python app.py`.

**Top bar**
- Commander (and partner, when set)
- Card count out of 100
- Mana curve as a compact bar-and-number display (`0▁0  1█4  2▄2 …`)
- Currency selector (Select widget) — persisted in settings
- Total deck cost in the selected currency; shows fraction if some cards have no price data

**Bottom — left panel**
- `Tree` widget showing groups with cards nested inside
- Groups can be anything: "Ramp", "Draw", "Goblins", etc.
- Arrow keys navigate; cards can belong to multiple groups

**Bottom — right panel**
- Card detail: name, mana cost, type line, oracle text, P/T or loyalty
- Tags from oracle_tags
- Rulings
- Printing selector (Select widget): lists every known printing with set name, collector number, finish, and price in the active currency. Defaults to the cheapest printing for the active currency. Selecting a different printing updates the deck cost.

### Data model (in-memory, not yet persisted)

- `Deck`: commander, optional partner, list of `Group`s, `selected_printings` dict (oracle_id → printing index)
- `Group`: name + list of `Card`s
- `Deck.total_cost(currency)` uses selected or cheapest printing per card

## Known issues / deferred work

- **`default_cards.json` is still large** (~100k entries) because `default_cards` includes one entry per printing, not one per card. The filter removes non-commander-legal cards but the file still contains many reprints. Plan: build a custom compact format at sync time that folds all printings of the same oracle_id into a single record, storing only the fields needed for the TUI. Deferred.

## What to build next

1. **Deck persistence** — save/load the deck (groups, selected printings, commander) to a JSON file so work survives between runs.
2. **Card search UI** — a search screen (modal or split panel) to find cards and add them to a group. Should support the existing `db.search` filters (name, color identity, type, tag) plus a free-text oracle text search.
3. **Group management** — create, rename, and delete groups from within the TUI.
4. **Commander selection** — search for and set the commander (and partner/background) from within the TUI, with color identity automatically derived.
5. **Compact data format** — fold all printings of the same card into one record at sync time to shrink `default_cards.json` and speed up startup.
6. **More settings** — e.g. preferred language fallback, default price source for "cheapest" calculation.

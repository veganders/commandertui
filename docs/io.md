# Save, load, import, and export

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

## Deck import (generic, `importer.py` + `archidekt_import.py`)

`ctrl+u` opens `ImportUrlModal` (in `widgets.py`) — paste any deck URL or bare ID.

**Routing**: `_IMPORTERS` in `app.py` is a list of `DeckImporter` instances (parallel to `_EXPORTERS`). The worker `_run_import` calls `can_handle(url)` on each importer in order and delegates to the first match. If none match, it shows `"I don't know how to import cards from {domain}"`, extracting the domain via `urllib.parse.urlparse(url).netloc` (falls back to the raw input for non-URL strings).

**Adding a new import backend**: subclass `DeckImporter` from `importer.py`, implement `can_handle(url)` and `async import_deck(url, db)`, then append an instance to `_IMPORTERS` in `app.py`.

**`DeckImporter` ABC** (`importer.py`):
```python
class DeckImporter(ABC):
    def can_handle(self, url: str) -> bool: ...
    async def import_deck(self, url: str, db: CardDB) -> tuple[Deck, list[str]]: ...
```

**`ArchidektImporter`** (`archidekt_import.py`): `can_handle` returns True when `"archidekt.com"` appears in the URL. `import_deck` delegates to `import_from_url`.

Archidekt API endpoint (no auth needed for public decks): `GET https://archidekt.com/api/decks/{id}/`

Key Archidekt parsing details:
- `_extract_deck_id(url)` extracts the numeric ID from a full URL or bare integer.
- Commander detection: category with `isPremier: true` in the top-level `categories` array.
- Maybeboard detection: categories with `includedInDeck: false`.
- Card name at `entry["card"]["oracleCard"]["name"]`. Printing: `entry["card"]["edition"]["editioncode"]`, `entry["card"]["collectorNumber"]`, `entry["modifier"]` (`"Normal"` / `"Foil"` / `"Etched"`).
- Category names sometimes include a count suffix like `"Creatures (28 Cards)"` — stripped by `_clean_category_name` before use as a group name.
- Two name-lookup dicts: full card name first, then front face name (for DFCs Archidekt stores by front name only).
- `_parse_archidekt_deck(data, db)` is pure sync — testable without network. `import_from_url(url, db)` is the async entry point.

In `app.py`: `action_import_deck` opens the modal via `self.app.push_screen`; the `@work(exit_on_error=False)` worker `_run_import` does the routing, fetch, and deck mutation, calling `self.notify` / `self.query_one(TopBar)` (all `self.*` since those are on `MainScreen`, not `App`).

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
- `c`: `"c"` for commander/partner, `"m"` for main deck, `"s"` for maybeboard
- `f`: `0` for nonfoil, `1` for foil/etched
- `u`: `Printing.scryfall_id` — the printing-specific Scryfall card ID (stored on `Printing` as `scryfall_id`, populated from `raw["id"]` at load time). Cards with no printing or missing `scryfall_id` are silently skipped.

**`ClipboardExporter`** (`clipboard.py`) — formats a text decklist and copies it via `pyperclip`.

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

Added to the `Printing` dataclass in `db.py`. All `Printing` objects created from the same Scryfall card entry share the same `scryfall_id` (one entry expands into multiple `Printing` objects, one per finish).

# UI components

## CardDetail printing select (`widgets.py`)

The printing `Select` in `CardDetail` encodes both the oracle_id and the printing index in the option value using a `_PrintingKey(oracle_id, idx)` NamedTuple. This means `on_select_changed` is entirely self-contained — it reads `event.value.oracle_id` and `event.value.idx` directly rather than relying on any widget-level mutable state.

This matters because Textual fires `Select.Changed` asynchronously: by the time the handler runs, the user may have already navigated to a different card, making any `_current_oracle_id` field stale. Encoding the identity in the value avoids that race entirely.

`isinstance(event.value, _PrintingKey)` is the guard — blank/reset events from `set_options` produce `Select.BLANK`, which is not a `_PrintingKey` and is silently ignored.

## Group detail panel

When a group node is selected in the deck tree, `app.py` checks `isinstance(data, GroupInfo)` and calls `card_detail.show_group_info(data)`.

`GroupInfo` (in `grouping.py`) is an ABC with `@abstractmethod def to_text(self) -> Text`. Subclasses implement `to_text()` — **not** `render()`, which is a reserved Textual method name.

`TagGroupInfo(GroupInfo)` renders the tag's description and full ancestor chain. `to_text()` walks upward through `db.tag_parents` recursively (with deduplication), then formats each tag in the chain with its label (bold), description, and a separator line. `_TAG_SEPARATOR_WIDTH` is the module-level constant controlling separator width.

To add a detail view for a new grouper: subclass `GroupInfo`, implement `to_text()`, and yield your `GroupInfo` instance as the third element from `CardGrouper.groups()`. No changes to `app.py` dispatch logic are needed.

## Grouping (`grouping.py`)

Cards in the main deck tree can be grouped four ways, cycled with `G`:

- **Named** (`NamedGrouper`) — user-defined groups sorted **alphabetically by name**, Maybeboard always last, Uncategorized at the bottom. Group nodes carry a `Group` data object so the `d` key works normally.
- **Type** (`TypeGrouper`) — one group per primary card type in the order: Creature, Instant, Sorcery, Artifact, Enchantment, Planeswalker, Battle, Land, Other. Type is determined by `card.has_type()` against each label in that priority order (first match wins). Maybeboard appended last.
- **MV** (`MVGrouper`) — one group per exact mana value present in the deck (no cap — a deck with a 12-mana card gets an MV 12 bucket); pure lands separated into a Land group. Maybeboard appended last.
- **Tag** (`TagGrouper(db)`) — one group per leaf oracle tag, sorted alphabetically. Cards with multiple leaf tags appear in all matching groups. Cards with no leaf tags go to an 'Untagged' group. Maybeboard appended last. Requires `db` in its constructor.

All groups — including the Commander / Partner section — use `group_label()` in `_rebuild_tree`, which appends a price subtotal and card count when pricing data is available (e.g. `Ramp  (8)  €12.40`).

Synthetic group nodes (Type, MV, and Tag modes) have `data=None`, so `d` on a group node is a no-op in those modes. `d`, `e`, `m` on card leaf nodes work identically in all modes.

`CardGrouper.groups()` return type: `list[tuple[str, list[CardEntry], object]]` — the third element is the `GroupInfo` instance (or `None` for synthetic nodes).

`DeckbuilderApp._groupers()` returns `[NamedGrouper(), TypeGrouper(), MVGrouper(), TagGrouper(self._db)]`. Add new grouping strategies by appending to `_groupers()`.

`label` is an abstract property on both `CardGrouper` and `CardSorter` — concrete subclasses must implement it as a `@property`.

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

## Maybeboard

A card is in the maybeboard when it belongs to the `"Maybeboard"` group (constant `MAYBEBOARD` in `models.py`). The Maybeboard group is a permanent group added to every new deck and injected into older loaded decks via `_ensure_permanent_groups`.

`CardEntry.is_maybe() -> bool` returns `MAYBEBOARD in self.groups`. This is the single check used everywhere:
- `Deck.all_entries()` excludes `is_maybe()` entries → `card_count`, `mana_curve`, `total_cost` automatically exclude maybeboard cards.
- `_rebuild_tree()` filters `is_maybe()` entries from all non-Maybeboard group nodes (and from Uncategorized).
- Search screen shows `[M]` prefix instead of `[+]` for maybeboard cards; `[M]` takes priority over count in the display logic.

`m` in the main tree toggles maybeboard on the focused card. `m` in the search screen adds a card to the maybeboard if not already there, or removes it if it is (toggle). In the search screen, `[M]` is checked before `count` so maybeboard cards never incorrectly show `[+]`.

Maybeboard cards are stored in the JSON save format like any other card (as a group membership), so save/load requires no special handling.

## CSS conventions

Input fields and Select dropdowns use no border — a background tint signals interactivity instead. The global rules live in `DeckbuilderApp.CSS` and apply to all screens:

- Resting: `background: $surface`, `border: none`, `height: 1`
- Focused: `background: $panel`, `border: none`

Target `SelectCurrent` (not `Select`) to style the visible trigger of a dropdown.

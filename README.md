# CommanderTUI

A keyboard-driven terminal UI for building Magic: The Gathering Commander decks.

Search cards, organise them into named groups, track mana curve and deck cost, and export to Archidekt or your clipboard — all without leaving the terminal.

Card data is sourced from [Scryfall](https://scryfall.com).

![screenshot placeholder]

## Requirements

- Python 3.10+
- On Linux, clipboard export requires `xclip`, `xsel`, or `wl-clipboard`

## Installation

```bash
git clone https://github.com/veganders/commandertui.git
cd commandertui
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Fetch card data

Before first run, download card data from Scryfall:

```bash
python scryfall.py
```

Data is cached locally and reused until it is 30 days old. To force a re-download:

```bash
python scryfall.py --force
```

## Run

```bash
python app.py
```

## Key bindings

| Key | Action |
|---|---|
| `c` | Set commander |
| `p` | Set partner / background |
| `s` | Search cards |
| `x` | Color Scout — explore card counts by color identity |
| `g` | New group |
| `d` | Remove card / group |
| `e` | Edit card groups and count |
| `m` | Toggle maybeboard |
| `o` | Cycle sort order |
| `S` | Filter deck |
| `ctrl+s` | Save deck |
| `ctrl+o` | Open deck |
| `ctrl+e` | Export deck |
| `ctrl+n` | New deck |
| `q` | Quit |

## Search syntax

| Filter | Meaning |
|---|---|
| `sol ring` | Name substring |
| `t:creature` | Type line |
| `o:"draw a card"` | Oracle text |
| `id:wubrg` | Color identity is a subset of the given colors |
| `id=ur` | Color identity is exactly the given colors |
| `otag:ramp` | Oracle tag |
| `kw:partner` | Keyword |
| `mv>=3` | Mana value |
| `eur<=1` | Price |
| `-t:land` | Negate any filter |
| `AND` / `OR` / `()` | Boolean logic |

## License

MIT — see [LICENSE](LICENSE).

Card data © [Scryfall](https://scryfall.com). Oracle text and card images © Wizards of the Coast.

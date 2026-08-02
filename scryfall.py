"""Scryfall bulk data management."""

import gzip
import json
import time
from pathlib import Path

import httpx

BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
DATA_DIR = Path(__file__).parent / "data"
MAX_AGE_DAYS = 30

WANTED_TYPES = {"default_cards", "rulings", "oracle_tags"}


def _age_days(path: Path) -> float:
    mtime = path.stat().st_mtime
    return (time.time() - mtime) / 86400


def _needs_update(path: Path) -> bool:
    return not path.exists() or _age_days(path) > MAX_AGE_DAYS


def _is_commander_legal(card: dict) -> bool:
    return card.get("legalities", {}).get("commander") == "legal"


# Whitelist of top-level fields to keep for default_cards.
# legalities is intentionally excluded — only used for filtering, not stored.
_CARD_KEEP_FIELDS = frozenset({
    "oracle_id", "name", "type_line", "oracle_text", "mana_cost", "cmc",
    "colors", "color_identity", "keywords", "rarity", "layout",
    "power", "toughness", "loyalty",
    "card_faces", "finishes", "prices", "set", "set_name",
    "collector_number", "id",
})
_CARD_FACE_KEEP_FIELDS = frozenset({"name", "mana_cost", "oracle_text", "type_line", "power", "toughness", "loyalty"})


def _strip_card(card: dict) -> dict:
    stripped = {k: v for k, v in card.items() if k in _CARD_KEEP_FIELDS}
    if "card_faces" in stripped:
        stripped["card_faces"] = [
            {k: v for k, v in face.items() if k in _CARD_FACE_KEEP_FIELDS}
            for face in stripped["card_faces"]
        ]
    return stripped


# Post-processing filters applied after download, keyed by bulk type.
_POSTPROCESS: dict[str, object] = {
    "default_cards": _is_commander_legal,
}

# Field strippers applied after filtering, keyed by bulk type.
_STRIP: dict[str, object] = {
    "default_cards": _strip_card,
}


def _download(url: str, dest: Path, keep: object = None, strip: object = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    print(f"  Downloading {dest.name}...")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        if keep is None:
            # No filtering — write gzipped JSONL directly to disk
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r  {pct:3d}% ({done // 1_000_000} MB)", end="", flush=True)
            print()
        else:
            # Filter required — decompress, filter, recompress as JSONL
            raw_chunks = []
            for chunk in r.iter_bytes(chunk_size=65536):
                raw_chunks.append(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {pct:3d}% ({done // 1_000_000} MB)", end="", flush=True)
            print()
            print(f"  Filtering {dest.name}...", end="", flush=True)
            raw = b"".join(raw_chunks)
            kept = 0
            total_count = 0
            with gzip.open(tmp, "wt", encoding="utf-8") as out:
                with gzip.open(__import__("io").BytesIO(raw)) as gz:
                    for line in gz:
                        line = line.strip()
                        if not line:
                            continue
                        total_count += 1
                        item = json.loads(line)
                        if keep(item):
                            out.write(json.dumps(strip(item) if strip else item) + "\n")
                            kept += 1
            print(f" kept {kept}/{total_count}")

    tmp.rename(dest)


def sync_bulk_data(force: bool = False) -> None:
    """Download missing or stale Scryfall bulk data files."""
    print("Fetching bulk data index...")
    r = httpx.get(BULK_DATA_URL, timeout=30)
    r.raise_for_status()
    index = r.json()

    entries = {item["type"]: item for item in index["data"]}

    for bulk_type in WANTED_TYPES:
        entry = entries.get(bulk_type)
        if entry is None:
            print(f"  Warning: {bulk_type!r} not found in bulk data index")
            continue

        dest = DATA_DIR / f"{bulk_type}.jsonl.gz"
        if not force and not _needs_update(dest):
            age = _age_days(dest)
            print(f"  {bulk_type}: up to date ({age:.0f} days old)")
            continue

        updated_at = entry.get("updated_at", "unknown")
        print(f"  {bulk_type}: downloading (last updated {updated_at})")
        _download(entry["jsonl_download_uri"], dest, keep=_POSTPROCESS.get(bulk_type), strip=_STRIP.get(bulk_type))

    print("Done.")


def load_bulk(bulk_type: str) -> list[dict]:
    """Load a bulk data file, raising if it hasn't been synced yet."""
    path = DATA_DIR / f"{bulk_type}.jsonl.gz"
    if not path.exists():
        raise FileNotFoundError(
            f"{bulk_type} bulk data not found. Run sync_bulk_data() first."
        )
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    sync_bulk_data(force=force)

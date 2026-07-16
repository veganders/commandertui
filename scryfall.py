"""Scryfall bulk data management."""

import json
import time
from datetime import datetime, timezone
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


# Post-processing filters applied after download, keyed by bulk type.
_POSTPROCESS: dict[str, object] = {
    "default_cards": _is_commander_legal,
}


def _download(url: str, dest: Path, keep: object = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    print(f"  Downloading {dest.name}...")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {pct:3d}% ({done // 1_000_000} MB)", end="", flush=True)
    print()

    if keep is not None:
        print(f"  Filtering {dest.name}...", end="", flush=True)
        with tmp.open() as f:
            data = json.load(f)
        filtered = [item for item in data if keep(item)]
        with tmp.open("w") as f:
            json.dump(filtered, f)
        print(f" kept {len(filtered)}/{len(data)}")

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

        dest = DATA_DIR / f"{bulk_type}.json"
        if not force and not _needs_update(dest):
            age = _age_days(dest)
            print(f"  {bulk_type}: up to date ({age:.0f} days old)")
            continue

        updated_at = entry.get("updated_at", "unknown")
        print(f"  {bulk_type}: downloading (last updated {updated_at})")
        _download(entry["download_uri"], dest, keep=_POSTPROCESS.get(bulk_type))

    print("Done.")


def load_bulk(bulk_type: str) -> list[dict]:
    """Load a bulk data file, raising if it hasn't been synced yet."""
    path = DATA_DIR / f"{bulk_type}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{bulk_type} bulk data not found. Run sync_bulk_data() first."
        )
    with path.open() as f:
        return json.load(f)


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    sync_bulk_data(force=force)

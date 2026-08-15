"""Noise-tag filtering shared by Find Similar and the Tag grouping mode.

Both features work with leaf oracle tags and need to exclude the same set of
tags that describe card naming/formatting rather than gameplay.  This module
owns the blacklist, the one-time cache derivation, and the two public helpers:

    noise = get_noise_tags(db)          # call once; result is cached
    keep  = not is_noise_tag(tag, noise)
"""

from __future__ import annotations

from typing import Optional

from db import CardDB

# Individual tags excluded from leaf-tag operations (one-offs not worth a whole
# parent category).
TAG_BLACKLIST: frozenset[str] = frozenset([
    "face-commander",    # format metadata, not gameplay
    "unique type line",  # 6.0% — has a type line no other card shares
])

# Entire tag subtrees to exclude.  Every tag whose ancestors include any of
# these labels is treated as noise.  Add new parent labels here to blacklist
# whole categories at once without touching individual tag names.
BLACKLISTED_PARENT_TAGS: frozenset[str] = frozenset([
    "card names",  # all children describe naming/wordplay, not gameplay
])

_noise_cache: Optional[frozenset[str]] = None


def get_noise_tags(db: CardDB) -> frozenset[str]:
    """Return the cached set of tag labels that descend from any blacklisted
    parent category.

    Computed once on the first call (db is loaded once per session and never
    mutated afterwards).
    """
    global _noise_cache
    if _noise_cache is None:
        _noise_cache = frozenset(
            label
            for label, ancestors in db.tag_ancestors.items()
            if ancestors & BLACKLISTED_PARENT_TAGS
        )
    return _noise_cache


def is_noise_tag(tag: str, extra: frozenset[str] = frozenset()) -> bool:
    """Return True for tags that should be excluded from leaf-tag operations.

    `extra` is the frozenset returned by get_noise_tags() — pass it in to
    avoid recomputing per-tag.  Dynamic rules: any tag containing "errata",
    or starting with "cycle-".
    """
    t = tag.lower()
    return tag in TAG_BLACKLIST or tag in extra or "errata" in t or t.startswith("cycle-")

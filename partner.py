"""Partner / second-commander detection and filtering."""

from __future__ import annotations

import re
from typing import Callable, Optional

from db import Card


def partner_mode(card: Card) -> Optional[dict]:
    """Return a dict describing what second commander this card supports, or None.

    Detection priority per CLAUDE.md:
      partner_with      → "Partner with" keyword (one specific named card)
      doctors_companion → "Doctor's companion" keyword or "Time Lord Doctor" type
      background        → "Choose a background" keyword (lowercase b — Scryfall convention)
      partner_variant   → "Partner—X" in oracle text (Friends forever, Character select, …)
      partner           → generic "Partner" keyword
    """
    kws = card.keywords
    oracle = card.oracle_text

    if "Partner with" in kws:
        m = re.search(r"Partner with ([^(\n]+)", oracle)
        return {"type": "partner_with", "name": m.group(1).strip() if m else None}

    if "Doctor's companion" in kws:
        return {"type": "doctors_companion", "role": "companion"}

    if "Time Lord Doctor" in card.type_line:
        return {"type": "doctors_companion", "role": "doctor"}

    if "Choose a background" in kws:
        return {"type": "background"}

    m = re.search(r"Partner—([^(]+?)\s*\(", oracle)
    if m:
        return {"type": "partner_variant", "mechanic": m.group(1).strip()}

    if "Partner" in kws:
        return {"type": "partner"}

    return None


def partner_filter(info: dict) -> Callable[[Card], bool]:
    """Return a Card predicate matching valid partners for the given partner_mode dict."""
    t = info["type"]
    if t == "partner":
        return lambda c: (
            "Partner" in c.keywords
            and "Partner with" not in c.keywords
            and not re.search(r"Partner—", c.oracle_text)
        )
    if t == "partner_with":
        name = info.get("name") or ""
        return lambda c, _n=name: c.name == _n
    if t == "partner_variant":
        tag = "Partner—" + info["mechanic"]
        return lambda c, _t=tag: _t in c.oracle_text
    if t == "doctors_companion":
        if info.get("role") == "doctor":
            return lambda c: "Doctor's companion" in c.keywords
        return lambda c: "Time Lord Doctor" in c.type_line
    if t == "background":
        return lambda c: "Background" in c.type_line
    return lambda c: True

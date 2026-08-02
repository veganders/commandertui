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

    if "Partner with" in kws:
        for face in card.faces:
            m = re.search(r"Partner with ([^(\n]+)", face.oracle_text)
            if m:
                return {"type": "partner_with", "name": m.group(1).strip()}
        return {"type": "partner_with", "name": None}

    if "Doctor's companion" in kws:
        return {"type": "doctors_companion", "role": "companion"}

    if card.has_type("Time Lord Doctor"):
        return {"type": "doctors_companion", "role": "doctor"}

    if "Choose a background" in kws:
        return {"type": "background"}

    for face in card.faces:
        m = re.search(r"Partner—([^(]+?)\s*\(", face.oracle_text)
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
            and not any(re.search(r"Partner—", f.oracle_text) for f in c.faces)
        )
    if t == "partner_with":
        name = info.get("name") or ""
        return lambda c, _n=name: c.name == _n
    if t == "partner_variant":
        tag = "Partner—" + info["mechanic"]
        return lambda c, _t=tag: c.has_oracle(_t)
    if t == "doctors_companion":
        if info.get("role") == "doctor":
            return lambda c: "Doctor's companion" in c.keywords
        return lambda c: c.has_type("Time Lord Doctor")
    if t == "background":
        return lambda c: c.has_type("Background")
    return lambda c: True

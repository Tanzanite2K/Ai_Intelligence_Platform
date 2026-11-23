"""Opaque, stable, URL-safe IDs for organisations.

Organisations are the distinct ``canonical_name`` values of ``entity_mappings`` (only 62 of them also have a
``canonical_entities`` row). Names can contain any character, so the API ID is the base64url-encoded name —
reversible, stable across restarts and needing no new table.
"""

from __future__ import annotations

import base64
import binascii


def encode_entity_id(name: str) -> str:
    return "e_" + base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")


def decode_entity_id(entity_id: str) -> str | None:
    if not entity_id.startswith("e_") or len(entity_id) > 600:
        return None
    raw = entity_id[2:]
    try:
        name = base64.b64decode((raw + "=" * (-len(raw) % 4)).encode(), altchars=b"-_", validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return name or None

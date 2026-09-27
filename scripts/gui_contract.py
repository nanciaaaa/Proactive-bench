#!/usr/bin/env python3
"""Shared GUI capture contract helpers (capabilities, timestamps)."""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = json.loads((ROOT / 'contracts/gui-capabilities.json').read_text())


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError('timestamp requires timezone')
    return parsed

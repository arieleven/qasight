"""NDJSON event emitter used when ``--ci`` is passed to ``qasight run``.

Each event is a single JSON object on its own line, written to stdout and
flushed immediately so CI log tailers see lines in real time.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any


def emit_ndjson(payload: Mapping[str, Any]) -> None:
    """Write one NDJSON event line to stdout."""
    sys.stdout.write(json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True))
    sys.stdout.write("\n")
    sys.stdout.flush()

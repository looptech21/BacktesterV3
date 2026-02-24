"""Deterministic experiment ID computation.

Identical configs always produce the same experiment ID (SHA-256 hash).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backtester.config import RunConfig


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))


def experiment_id(config: RunConfig) -> str:
    config_dump = config.model_dump(mode="json")
    # Runner and output config are infrastructure — do not affect experiment identity.
    config_dump.pop("runner", None)
    config_dump.pop("output", None)
    config_dump.pop("run_name", None)
    normalized = canonical_json(config_dump)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def code_version() -> str:
    return "backtester-v3.0.0"

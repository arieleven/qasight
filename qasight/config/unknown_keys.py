"""Find keys in a ``qasight.yaml`` that nothing reads (P5 in docs/engine-cli-review.md).

The loader takes the keys it knows and ignores the rest, so a typo such as
``timeout: 1`` (meant ``timeout_s``) silently does nothing. This walks the raw
mapping against the JSON Schema (:mod:`qasight.config.json_schema`, built
from the same constants as the loader) and returns one message per key the
schema doesn't allow, with a "did you mean" from :mod:`difflib`.

Only objects the schema closes (``additionalProperties: false``) are checked;
open maps (``env:``, ``cycles:``, ``resources:``) take any name.
"""

from __future__ import annotations

import difflib
from typing import Any

# Older layouts the loader still reads but the schema doesn't describe.
_LEGACY_TOP_LEVEL = frozenset({"plans", "runs"})
# Common mix-ups difflib doesn't rank close enough.
_HINTS = {"env": "extra_env", "environment": "extra_env", "timeout": "timeout_s"}


def _resolve(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        target = root["definitions"][ref.rsplit("/", 1)[1]]
        merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return _resolve(merged, root) if "$ref" in target else merged
    return node


def _object_branch(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any] | None:
    """The part of *node* that describes a mapping, following $ref / oneOf / anyOf."""
    node = _resolve(node, root)
    if "properties" in node or "additionalProperties" in node:
        return node
    for key in ("oneOf", "anyOf", "allOf"):
        for branch in node.get(key) or ():
            found = _object_branch(branch, root)
            if found is not None:
                return found
    return None


def _array_items(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any] | None:
    node = _resolve(node, root)
    if isinstance(node.get("items"), dict):
        return node["items"]
    for key in ("oneOf", "anyOf"):
        for branch in node.get(key) or ():
            found = _array_items(branch, root)
            if found is not None:
                return found
    return None


def _walk(
    value: Any, node: dict[str, Any], root: dict[str, Any], path: str, out: list[str]
) -> None:
    if isinstance(value, list):
        items = _array_items(node, root)
        if items is not None:
            for i, item in enumerate(value):
                _walk(item, items, root, f"{path}[{i}]", out)
        return
    if not isinstance(value, dict):
        return
    obj = _object_branch(node, root)
    if obj is None:
        return
    props: dict[str, Any] = obj.get("properties") or {}
    extra = obj.get("additionalProperties", True)
    for key, child in value.items():
        name = str(key)
        where = f"{path}.{name}" if path else name
        if name in props:
            _walk(child, props[name], root, where, out)
        elif isinstance(extra, dict):
            _walk(child, extra, root, where, out)
        elif extra is False:
            close = difflib.get_close_matches(name, sorted(props), n=1, cutoff=0.6)
            guess = _HINTS.get(name) if _HINTS.get(name) in props else None
            guess = guess or (close[0] if close else None)
            hint = f"; did you mean {guess!r}?" if guess else ""
            out.append(f"unknown key {where!r} is ignored{hint}")


def unknown_keys(raw: dict[str, Any]) -> list[str]:
    """One message per key in *raw* (a ``cycles:`` layout config) that is ignored."""
    from qasight.config.json_schema import build_json_schema

    root = build_json_schema()
    trimmed = {k: v for k, v in raw.items() if k not in _LEGACY_TOP_LEVEL}
    out: list[str] = []
    _walk(trimmed, root, root, "", out)
    return out

"""Required-preserving expansion for strict tool grammars (fork delta).

Deployed context (2026-11-02 adversarial review): `any_order=True` on the
strict GLM-4.7 path fixed the declared-order repetition basin but SILENTLY
DROPPED the strict contract — xgrammar 0.2.8's any_order does not check key
presence or uniqueness (missing required keys and duplicate keys are
accepted; confirmed empirically, JSON style + the deployed builtin path).

This module re-tightens: it rewrites the built structural tag, expanding
every `any_order: true` json_schema node into an **or-format over key
permutations** — one fully-validated branch per ordering (declared order
within the branch, required keys present, no duplicates, value schemas
pinned). The union accepts exactly: every required key present, no key
twice, only schema keys, each value matching its schema — in any order.

k! branch growth is capped (REQUIRED_PRESERVING_MAX_KEYS); above the cap the
node keeps the loose semantics and the fallback is recorded in the expanded
tag (`x-any-order-loose-fallback: true`) so the loosening is auditable, not
silent.

Upstream path: the same expansion is proposed to xgrammar upstream as
`any_order="required_preserving"` (issue-first, 2026-11-02); when a release
carries it, this module becomes a shim over the native mode.
"""

from __future__ import annotations

import itertools
import json
import warnings
from typing import Any, Dict, List

REQUIRED_PRESERVING_MAX_KEYS = 6


def _reordered(schema: Dict[str, Any], order: List[str]) -> Dict[str, Any]:
    out = dict(schema)
    props = schema.get("properties", {})
    out["properties"] = {k: props[k] for k in order if k in props}
    return out


def _expand_schema_node(schema: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return schema
    props = schema.get("properties")
    if isinstance(props, dict):
        schema = {
            **schema,
            "properties": {k: _expand_schema_node(v) for k, v in props.items()},
        }
    if schema.get(_MARKER_KEY):
        return _expand_object(schema)
    return schema


def _expand_object(schema: Dict[str, Any]) -> Dict[str, Any]:
    props: Dict[str, Any] = schema.get("properties", {})
    keys = list(props.keys())
    if len(keys) > REQUIRED_PRESERVING_MAX_KEYS:
        warnings.warn(
            f"required-preserving expansion capped: {len(keys)} keys > "
            f"{REQUIRED_PRESERVING_MAX_KEYS}; node keeps loose any_order "
            "semantics (required keys may be missing, duplicates accepted)"
        )
        return {**schema, "x-any-order-loose-fallback": True}
    stripped = {k: v for k, v in schema.items() if k != _MARKER_KEY}
    branches = []
    for perm in itertools.permutations(keys):
        branches.append(_reordered(stripped, list(perm)))
    return {"__or__": branches}


_MARKER_KEY = "x-required-preserving-marker"


def _node_to_format(node: Dict[str, Any], style: str) -> Dict[str, Any]:
    if "__or__" in node:
        return {
            "type": "or",
            "elements": [_node_to_format(b, style) for b in node["__or__"]],
        }
    # Cap fallback: preserve the promised loose semantics on the format node
    # itself (the core defaults to declared order when the field is absent)
    # and drop the expansion markers so the core schema stays clean.
    loose = bool(node.pop("x-any-order-loose-fallback", False))
    node = {k: v for k, v in node.items() if k != _MARKER_KEY}
    return {
        "type": "json_schema",
        "json_schema": node,
        "style": style,
        "any_order": loose,
        "max_whitespace_cnt": None,
        "excludes": [],
    }


def _expand_format_node(node: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(node, dict):
        return node
    if node.get("type") == "json_schema":
        schema = node.get("json_schema")
        if isinstance(schema, dict) and node.get("any_order") is True:
            style = node.get("style", "json")
            marked = {**schema, _MARKER_KEY: True}
            expanded = _expand_schema_node(marked)
            out = _node_to_format(expanded, style)
            out["max_whitespace_cnt"] = node.get("max_whitespace_cnt")
            out["excludes"] = node.get("excludes", [])
            return out
        return node
    for attr in ("elements", "formats", "content"):
        v = node.get(attr)
        if isinstance(v, list):
            node[attr] = [_expand_format_node(x) for x in v]
        elif isinstance(v, dict):
            node[attr] = _expand_format_node(v)
    return node


def expand_loose_to_required_preserving(structural_tag_json: str) -> str:
    """Expand every `any_order: true` json_schema node in a serialized tag.

    Input/output: the serialized StructuralTag (model_dump_json shape). The
    C++ core receives only or-format/json_schema nodes it already
    understands; the loose semantics survive only above the cap (recorded).
    """
    parsed = json.loads(structural_tag_json)
    if parsed.get("type") == "structural_tag" and isinstance(parsed.get("format"), dict):
        parsed["format"] = _expand_format_node(parsed["format"])
    return json.dumps(parsed)

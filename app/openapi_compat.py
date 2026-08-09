"""Downgrade FastAPI's generated document to genuine OpenAPI 3.0.0.

Setting `app.openapi_version = "3.0.0"` changes only the version string. Pydantic v2
still emits JSON Schema 2020-12 shapes, so the document claims 3.0 while containing
constructs that only exist in 3.1:

    {"anyOf": [{"type": "string"}, {"type": "null"}]}   ->  {"type": "string", "nullable": true}
    {"exclusiveMinimum": 0}                             ->  {"minimum": 0, "exclusiveMinimum": true}
    {"const": "x"}                                      ->  {"enum": ["x"]}

indigo.ai's importer validates against 3.0 and rejects the mismatch outright with
"There is an error with inserted Schema". This module rewrites the tree so the
document is what it says it is.

Nullable `$ref` needs care: in 3.0 any sibling of `$ref` is ignored, so `nullable`
has to be lifted out into an `allOf` wrapper rather than sat next to it.
"""

from __future__ import annotations

from typing import Any

NULL_SCHEMA = {"type": "null"}

# Valid in JSON Schema 2020-12, meaningless or illegal in OpenAPI 3.0.
UNSUPPORTED_KEYS = ("$schema", "prefixItems", "unevaluatedProperties", "contentMediaType")


def _merge_nullable(members: list[Any], siblings: dict[str, Any]) -> dict[str, Any]:
    """Fold `anyOf: [X, null]` into a nullable X, preserving sibling keywords."""
    if len(members) == 1:
        single = members[0]
        if "$ref" in single:
            # A sibling of $ref is ignored in 3.0, so wrap it.
            return {"allOf": [single], "nullable": True, **siblings}
        return {**single, **siblings, "nullable": True}
    return {"anyOf": members, "nullable": True, **siblings}


# Response fields collapsed to a bare type plus their description. These are
# recovery payloads the agent reads at runtime, not things it has to construct, so
# a full schema buys nothing and costs several inlined copies of ProductSummary.
# Their semantics survive in the description, which is what the model actually reads.
COLLAPSED_RESPONSE_FIELDS = {"suggestions": "object", "alternatives": "array"}

# Keys that identify a node as a JSON Schema rather than, say, `info` or a response
# object. Used to keep schema-only edits from touching the rest of the document.
SCHEMA_MARKERS = ("type", "properties", "items", "enum", "$ref", "allOf", "anyOf", "oneOf")


def simplify_for_tool_runtimes(node: Any) -> Any:
    """Strip constructs that OpenAPI allows but LLM function-calling runtimes reject.

    indigo.ai's importer accepts the document, then hands it to the configured model
    as function declarations. Gemini in particular takes only a narrow subset of
    JSON Schema: no `allOf`, no `additionalProperties`, no `exclusiveMinimum`, no
    `title`. An unsupported keyword fails the whole tool set, and the agent errors
    before it can make a request.

    The importer also inlines every `$ref`, so a schema reused five times is expanded
    five times. Collapsing the deep, repeated response schemas is what keeps the
    document to a sane size - and their semantics live in the field descriptions,
    which survive.
    """
    if isinstance(node, list):
        return [simplify_for_tool_runtimes(item) for item in node]
    if not isinstance(node, dict):
        return node

    node = {key: simplify_for_tool_runtimes(value) for key, value in node.items()}

    # NOTE: the `allOf` wrappers around nullable $refs are deliberately left alone.
    # Flattening them merges siblings onto a `$ref`, which OpenAPI 3.0 forbids, and
    # indigo.ai's importer rejects the document outright ("There is an error with
    # inserted Schema"). Importer conformance wins over model-runtime preference:
    # a spec that will not import is worth nothing, and a model that dislikes `allOf`
    # can be swapped for one that tolerates it.
    # Only inside schema objects. `info.title` is REQUIRED by OpenAPI, and stripping
    # it globally produced an invalid document that the importer rejected with the
    # same opaque "error with inserted Schema" as every other cause.
    if any(marker in node for marker in SCHEMA_MARKERS):
        node.pop("title", None)          # adds nothing a description does not say
        node.pop("additionalProperties", None)

    # `exclusiveMinimum: true` is 3.0-valid but unsupported; plain `minimum` is not.
    if node.pop("exclusiveMinimum", None) is not None and "minimum" not in node:
        node["minimum"] = 0
    node.pop("exclusiveMaximum", None)

    properties = node.get("properties")
    if isinstance(properties, dict):
        for field, kind in COLLAPSED_RESPONSE_FIELDS.items():
            child = properties.get(field)
            if not isinstance(child, dict):
                continue
            # The child may be an inline object, an array, a bare $ref, or - after the
            # 3.0 downgrade wraps a nullable reference - an allOf. Match all four.
            if not any(key in child for key in ("properties", "items", "$ref", "allOf")):
                continue
            collapsed: dict[str, Any] = {"type": kind}
            if kind == "array":
                collapsed["items"] = {"type": "object"}
            for key in ("description", "nullable"):
                if key in child:
                    collapsed[key] = child[key]
            properties[field] = collapsed
    return node


def remove_validation_error_responses(spec: dict[str, Any]) -> dict[str, Any]:
    """Delete the auto-generated 422 responses, which this service never returns.

    FastAPI declares a 422 on any operation with validated parameters. We install a
    RequestValidationError handler that answers 200 with a recoverable
    `invalid_parameter` body instead, so leaving the 422 in the document would
    describe behaviour that cannot happen - and the spec is read by a model deciding
    how to handle our responses.
    """
    for path in spec.get("paths", {}).values():
        for operation in path.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)

    schemas = spec.get("components", {}).get("schemas", {})
    for orphan in ("HTTPValidationError", "ValidationError"):
        schemas.pop(orphan, None)
    return spec


def downgrade_to_openapi_30(node: Any) -> Any:
    """Recursively rewrite a generated document into valid OpenAPI 3.0.0."""
    if isinstance(node, list):
        return [downgrade_to_openapi_30(item) for item in node]
    if not isinstance(node, dict):
        return node

    node = {key: downgrade_to_openapi_30(value) for key, value in node.items()}

    for combiner in ("anyOf", "oneOf"):
        members = node.get(combiner)
        if not isinstance(members, list):
            continue
        without_null = [member for member in members if member != NULL_SCHEMA]
        if len(without_null) == len(members):
            continue
        siblings = {k: v for k, v in node.items() if k != combiner}
        node = (
            _merge_nullable(without_null, siblings)
            if without_null
            else {**siblings, "nullable": True}
        )

    # 3.1 states the bound as a number; 3.0 states it as a boolean beside minimum/maximum.
    for bound, limit in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        value = node.get(bound)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            node[limit] = value
            node[bound] = True

    if "const" in node:
        node["enum"] = [node.pop("const")]

    # In OpenAPI 3.0 a `$ref` must stand alone - any sibling key is ignored by spec,
    # and strict validators reject the document. Pydantic emits `$ref` alongside a
    # `description` for model-typed fields, so wrap any such node in `allOf`. This is
    # the exact construct that produced "There is an error with inserted Schema".
    if "$ref" in node and len(node) > 1:
        reference = node.pop("$ref")
        node = {"allOf": [{"$ref": reference}], **node}

    for key in UNSUPPORTED_KEYS:
        node.pop(key, None)

    # 3.0 allows only a single `example`; an `examples` array is 3.1 syntax inside a schema.
    if isinstance(node.get("examples"), list):
        examples = node.pop("examples")
        if examples and "example" not in node:
            node["example"] = examples[0]

    return node

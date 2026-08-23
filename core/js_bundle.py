"""Convert the viewer's ES modules into classic scripts for the offline export.

The standalone HTML export injected ``three.module.js`` and ``OrbitControls.js``
verbatim into ``<script type="text/javascript">`` blocks. ``three.module.js`` ends
with a top-level ``export { ... }`` and ``OrbitControls.js`` both begins with an
``import`` and ends with an ``export``. A classic script rejects all three forms,
so the browser raised SyntaxError before any code ran and every exported "offline
3D report" opened as a blank page.

These helpers rewrite the module syntax so the same code runs in a classic script.
"""
from __future__ import annotations

import re

_EXPORT_BLOCK = re.compile(r"^export\s*\{(?P<names>[^}]*)\}\s*;?\s*$", re.M)
_IMPORT_BLOCK = re.compile(
    r"^import\s*\{[^}]*\}\s*from\s*['\"][^'\"]+['\"]\s*;?\s*$", re.M | re.S
)
_IMPORT_STAR = re.compile(
    r"^import\s+\*\s+as\s+\w+\s+from\s*['\"][^'\"]+['\"]\s*;?\s*$", re.M
)
_EXPORT_DECL = re.compile(r"^export\s+(class|function|const|let|var)\s", re.M)


class JsBundleError(RuntimeError):
    """Raised when a viewer module cannot be rewritten for the offline export."""


def strip_imports(source: str) -> str:
    """Remove every top-level import statement from a module."""
    body = _IMPORT_BLOCK.sub("", source)
    return _IMPORT_STAR.sub("", body)


def module_to_namespace(source: str, namespace: str) -> str:
    """Rewrite a library module into an IIFE assigned to one global namespace.

    The module's trailing ``export { a, b as c }`` list becomes the object the
    IIFE returns, so every exported binding stays reachable as ``namespace.a``.
    """
    last = None
    for last in _EXPORT_BLOCK.finditer(source):
        pass
    if last is None:
        raise JsBundleError(
            f"no top-level export block found while bundling '{namespace}'"
        )

    pairs = []
    for entry in last.group("names").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if " as " in entry:
            local, exported = (part.strip() for part in entry.split(" as ", 1))
        else:
            local = exported = entry
        pairs.append(f"    {exported}: {local}")

    if not pairs:
        raise JsBundleError(f"empty export list while bundling '{namespace}'")

    body = strip_imports(source[: last.start()] + source[last.end():])
    returned = ",\n".join(pairs)
    return (
        f"var {namespace} = (function () {{\n"
        f"{body}\n"
        f"  return {{\n{returned}\n  }};\n"
        f"}})();\n"
    )


def module_to_classic(source: str, preamble: str = "") -> str:
    """Strip module syntax from a first-party module, leaving its bindings global.

    ``export class Foo`` becomes ``class Foo``, which at classic-script top level
    is reachable from the scripts that follow.
    """
    body = strip_imports(source)
    body = _EXPORT_DECL.sub(r"\1 ", body)
    body = _EXPORT_BLOCK.sub("", body)
    return preamble + body

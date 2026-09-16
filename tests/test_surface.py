"""What this package imports from Postulo, read out of its source rather than promised.

`postulo.plugins.api` is the whole contract: the names there keep working across a minor
release, and everything else in `postulo` is that month's internals and may be renamed in a
patch. A plugin that reaches past it works until it does not, and then fails at somebody's
instance rather than here.

So this walks the package's own source and fails on any other `postulo` import. It reads
the files instead of importing them, which is the point: an import that only happens inside
a function, or on the unlucky branch of a `try`, is exactly the one no test exercises.

The plugin's *tests* are deliberately not held to this. They drive the registry, build
connections and borrow Postulo's settings, which is reaching into the application on
purpose and from outside the shipped package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "postulo_apprise"

SURFACE = "postulo.plugins.api"


def postulo_imports(source: str) -> set[str]:
    """Every module in the `postulo` namespace this file imports, however it imports it."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if _is_postulo(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _is_postulo(node.module):
            found.add(node.module)
    return found


def _is_postulo(name: str | None) -> bool:
    return bool(name) and (name == "postulo" or name.startswith("postulo."))


@pytest.mark.parametrize("path", sorted(PACKAGE.rglob("*.py")), ids=lambda p: p.name)
def test_the_package_imports_nothing_of_postulos_but_the_surface(path: Path):
    reached = postulo_imports(path.read_text(encoding="utf-8")) - {SURFACE}
    assert reached == set(), (
        f"{path.name} imports {', '.join(sorted(reached))}. Only {SURFACE} is a promise; "
        "ask for the name there if it is missing."
    )


def test_the_surface_really_carries_what_this_plugin_asks_of_it():
    """The other half: the names above exist, so the test above cannot pass by being wrong."""
    api = pytest.importorskip(SURFACE)

    for name in ("FieldSpec", "TestResult", "check_destination", "DestinationRefused"):
        assert getattr(api, name, None) is not None, f"{SURFACE} has no {name}"

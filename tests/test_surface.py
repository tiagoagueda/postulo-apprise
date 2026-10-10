"""What this package imports from Postulo, read out of its source rather than promised.

`postulo.plugins.api` is the whole contract: the names there keep working across a minor
release, and everything else in `postulo` is that month's internals and may be renamed in a
patch. A plugin that reaches past it works until it does not, and then fails at somebody's
instance rather than here.

**The walk is the core's now, not this repository's** (postulo/postulo#229). Five official
plugins each kept their own copy of it, and no two agreed about relative imports or about
what counted as reaching past — which is the drift a published check exists to stop. It
still reads the files instead of importing them, which is the point: an import that only
happens inside a function, or on the unlucky branch of a `try`, is exactly the one no test
exercises.

The plugin's *tests* are deliberately not held to this. They drive the registry, build
connections and borrow Postulo's settings, which is reaching into the application on
purpose and from outside the shipped package.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from postulo.plugins.testing import SURFACE, assert_imports_only_the_surface, imports_of

PACKAGE = "postulo_apprise"
SOURCE = Path(__file__).resolve().parents[1] / "src" / PACKAGE


def test_there_is_something_to_check():
    """A sanity check on the check: an empty package would make everything below pass."""
    assert len(list(SOURCE.rglob("*.py"))) >= 2


def test_the_package_imports_nothing_of_postulos_but_the_surface():
    assert_imports_only_the_surface(SOURCE, package=PACKAGE)


def test_the_surface_is_what_this_plugin_actually_uses():
    """The other half: the surface is imported, rather than merely not reached past."""
    found = imports_of(SOURCE, package=PACKAGE)
    assert found, "the package imports nothing from Postulo"
    # By name since postulo/postulo 12504e44f: `postulo.plugins.api.FieldSpec`, not the module.
    assert all(name == SURFACE or name.startswith(SURFACE + ".") for name in found), found


@pytest.mark.parametrize(
    "name", ["DestinationRefused", "FieldSpec", "TestResult", "check_destination"]
)
def test_the_surface_really_carries_what_this_plugin_asks_of_it(name: str):
    """Not merely importable: on `__all__`, which is what the promise is made about."""
    from postulo.plugins import api

    assert name in api.__all__, f"{SURFACE} does not promise {name}"

"""Every service Apprise supports, offered to Postulo as a sub-plugin of this plugin (#20).

Apprise knows its services: ``Apprise().details()`` lists each with its name, the schemes
that address it, where it is set up and, with ``show_disabled``, whether the machine has what
it needs. That list is read once, when the plugin loads, and handed to Postulo as
:class:`~postulo.plugins.api.SubPlugin`: the administrator's plugins page lists each service
under this plugin and switches it alone, which is the allow-list of services an instance
offers. Nothing here is translated, because nothing here is ours to say: a service's name and
its addresses are Apprise's, and what is said about one that cannot work is a sentence of
this plugin's own.

The identifier of a service is its first scheme as Apprise lists them, secure ones first
(``tgram``, ``ntfys``): it is what a policy row holds, so it must not change when Apprise
reorders its list. It is only ever a lookup key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

import apprise
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy
from postulo.plugins.api import SubPlugin

#: Services that act on the machine Postulo runs on, not on a service of the person's: the
#: system log, the desktop's notification daemons, a local mail client, a USB light (#12).
#: The notifier refuses their lines whatever is switched, so they are listed as unavailable.
LOCAL_SCHEMES = frozenset(
    {
        "syslog",
        "dbus",
        "kde",
        "qt",
        "glib",
        "gio",
        "gnome",
        "macosx",
        "windows",
        "evolution",
        "evolutions",
        "blink1",
    }
)

#: The Lucide icon every service is drawn with: Apprise has no icon of its own for one.
ICON = "bell"

_ID = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True)
class Service:
    """One service Apprise supports, as this plugin needs to know it."""

    id: str
    name: str
    schemes: tuple[str, ...]
    url: str
    setup_url: str
    packages_missing: tuple[str, ...]
    enabled: bool = True

    @property
    def local(self) -> bool:
        return bool(set(self.schemes) & LOCAL_SCHEMES)


def _schemes(entry: dict) -> tuple[str, ...]:
    found: list[str] = []
    for key in ("secure_protocols", "protocols"):
        value = entry.get(key)
        if isinstance(value, str):
            value = [value]
        for scheme in value or ():
            scheme = str(scheme).strip().lower()
            if scheme and scheme not in found:
                found.append(scheme)
    return tuple(found)


def _identifier(scheme: str, taken: set[str]) -> str:
    base = _ID.sub("-", scheme.lower()).strip("-_")[:30] or "service"
    candidate, number = base, 2
    while candidate in taken:
        suffix = f"-{number}"
        candidate, number = base[: 30 - len(suffix)] + suffix, number + 1
    return candidate


@cache
def services() -> tuple[Service, ...]:
    """Every service Apprise supports, in its own order, read once per process."""
    try:
        details = apprise.Apprise().details(show_requirements=True, show_disabled=True)
    except TypeError:  # an Apprise older than the arguments: only what works is listed
        details = apprise.Apprise().details()
    found: list[Service] = []
    taken: set[str] = set()
    for entry in details.get("schemas", ()):
        schemes = _schemes(entry)
        if not schemes:
            continue
        identifier = _identifier(schemes[0], taken)
        taken.add(identifier)
        enabled = bool(entry.get("enabled", True))
        requirements = entry.get("requirements") or {}
        missing = (
            ()
            if enabled
            else tuple(str(name) for name in requirements.get("packages_required") or ())
        )
        found.append(
            Service(
                id=identifier,
                name=str(entry.get("service_name") or schemes[0]),
                schemes=schemes,
                url=str(entry.get("service_url") or ""),
                setup_url=str(entry.get("setup_url") or ""),
                packages_missing=missing,
                enabled=enabled,
            )
        )
    return tuple(found)


@cache
def by_scheme() -> dict[str, Service]:
    """The service each scheme addresses; the first listed keeps a scheme two claim."""
    mapping: dict[str, Service] = {}
    for service in services():
        for scheme in service.schemes:
            mapping.setdefault(scheme, service)
    return mapping


def service_for(scheme: str) -> Service | None:
    return by_scheme().get(scheme.strip().lower())


def _description(service: Service) -> str:
    schemes = ", ".join(f"{scheme}://" for scheme in service.schemes)
    parts = [schemes, service.setup_url or service.url]
    return " · ".join(part for part in parts if part)


def _unavailable(service: Service):
    if service.local:
        return gettext_lazy(
            "Acts on the machine Postulo runs on, not on a service of yours, so it cannot be used."
        )
    if service.packages_missing:
        return format_lazy(
            gettext_lazy("Needs {packages}, which is not installed on this machine."),
            packages=", ".join(service.packages_missing),
        )
    if not service.enabled:
        return gettext_lazy("Not available on this machine.")
    return ""


def sub_plugins() -> list[SubPlugin]:
    """One `SubPlugin` per service, on unless it cannot work on this machine."""
    return [
        SubPlugin(
            id=service.id,
            label=service.name,
            description=_description(service),
            default_on=True,
            unavailable=_unavailable(service),
            icon=ICON,
        )
        for service in services()
    ]

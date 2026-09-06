"""The notifier: one connection holding Apprise URLs, one call per notification.

What is particular about Apprise is that the address *is* the credential. A Telegram URL
carries the bot token, a Discord URL the webhook secret, a mail URL the password. So the
one field this plugin asks for is a secret as a whole: Postulo stores it encrypted, never
shows it back, and the connections list is given a description with the secret parts
masked (Apprise knows which parts those are).

Two things are checked at the form rather than at delivery time. Each line must be a URL
Apprise understands — a typo in a scheme is caught when it is typed, not when a reminder
falls due at three in the morning. And a URL that names a server of the person's own — an
ntfy, a Gotify, a Matrix homeserver on the LAN — is subject to the instance's destination
policy, exactly as a request through ``postulo.plugins.http`` would be: private addresses
are refused unless the operator allowed them. The same check runs again before every
send, because the policy is the operator's and may change.
"""

from __future__ import annotations

import ipaddress
import re

import apprise
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from postulo.plugins import http
from postulo.plugins.base import FieldSpec, TestResult

from . import __version__

#: A host that is a name to resolve, as opposed to a token Apprise happens to keep in the
#: host slot (Telegram's bot token, a Discord webhook id, a Pushover key). Only real
#: destinations are checked against the destination policy; a token is not an address.
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.I)


class ConfigurationError(ValueError):
    """The stored URLs cannot be used. The message is for the person."""


def split_urls(text: str) -> list[str]:
    """The URLs in the field: one per line, blank lines and ``#`` comments ignored."""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def build(urls: list[str]) -> tuple[apprise.Apprise, list[tuple[int, str]]]:
    """An Apprise object holding every URL it accepted, and the (line, scheme) it refused.

    Apprise's own parser is the validator: a URL it will not add is one it cannot deliver
    to. Only the scheme of a refused line is reported, never the line itself, which may
    hold a credential.
    """
    client = apprise.Apprise()
    rejected: list[tuple[int, str]] = []
    for number, url in enumerate(urls, start=1):
        if not client.add(url):
            rejected.append((number, url.split("://", 1)[0] if "://" in url else url[:20]))
    return client, rejected


def destination_of(server) -> str | None:
    """The address a service will be reached at, if the URL names one; else ``None``.

    Apprise keeps whatever came before the first slash in ``host``. For a self-hosted
    service that is a hostname or an IP address; for a hosted one it is usually a token.
    """
    host = str(getattr(server, "host", "") or "").strip("[]")
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if host.lower() != "localhost" and not _HOSTNAME.match(host):
            return None
    return f"https://{host}"


def describe(server) -> str:
    """The service's name and its address with every secret part masked, for a list."""
    masked = server.url(privacy=True).split("?", 1)[0].rstrip("/")
    return f"{server.service_name} {masked}"


class AppriseNotifier:
    name = "apprise"
    version = __version__
    kind = "notifier"
    label = gettext_lazy("Apprise")

    def config_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec(
                "urls",
                str(_("Apprise URLs")),
                type="textarea",
                secret=True,
                help=str(
                    _(
                        "One per line, in Apprise's form: tgram://bot-token/chat-id, "
                        "ntfy://topic, discord://webhook-id/webhook-token, "
                        "mailto://user:password@example.org, and any of the others Apprise "
                        "lists. The credentials live inside the URL, so the whole field is "
                        "stored encrypted and never shown again; the connections list names "
                        "each service with the secret parts masked."
                    )
                ),
            ),
        ]

    # ------------------------------------------------------------- checking

    def validate(self, config: dict) -> dict[str, list[str]]:
        """Behind the form: every line must be an Apprise URL, and reachable under policy."""
        problems = self._problems(config)
        return {"urls": problems} if problems else {}

    def summary(self, config: dict) -> str:
        """One line for the connections list, secrets masked."""
        client, _rejected = build(split_urls(config.get("urls", "")))
        return " · ".join(describe(server) for server in client)

    def _problems(self, config: dict) -> list[str]:
        urls = split_urls(config.get("urls", ""))
        if not urls:
            return [str(_("At least one Apprise URL is needed."))]
        client, rejected = build(urls)
        problems = [
            str(_("Line %(line)d is not an Apprise URL this version understands (%(scheme)s)."))
            % {"line": number, "scheme": scheme}
            for number, scheme in rejected
        ]
        for server in client:
            destination = destination_of(server)
            if destination is None:
                continue
            try:
                http.check_destination(destination)
            except http.DestinationRefused as error:
                problems.append(
                    str(_("%(service)s at %(host)s: %(error)s"))
                    % {"service": server.service_name, "host": server.host, "error": error}
                )
        return problems

    def _client(self, config: dict) -> apprise.Apprise:
        problems = self._problems(config)
        if problems:
            raise ConfigurationError(" ".join(problems))
        client, _rejected = build(split_urls(config.get("urls", "")))
        return client

    # -------------------------------------------------------------- sending

    def test(self, config: dict) -> TestResult:
        """Behind the *Test* button: one real message to every URL."""
        try:
            client = self._client(config)
        except ConfigurationError as error:
            return TestResult(False, str(error))
        sent = client.notify(
            title=str(_("Postulo can reach you here")),
            body=str(
                _(
                    "This is the test message from your Apprise connection. Reminders and "
                    "captures will arrive the same way."
                )
            ),
        )
        if sent:
            count = len(client)
            return TestResult(
                True,
                str(
                    _("Delivered to %(count)d service.")
                    if count == 1
                    else _("Delivered to %(count)d services.")
                )
                % {"count": count},
            )
        return TestResult(False, str(_("At least one service refused the message.")))

    def send(self, notification, config: dict, user) -> None:
        """Carry one notification. Raise on failure: Postulo records it on the connection."""
        client = self._client(config)
        parts = []
        if notification.body:
            parts.append(notification.body)
        if notification.url:
            parts.append(notification.url)
        body = "\n\n".join(parts) or notification.title
        if not client.notify(title=notification.title, body=body):
            raise RuntimeError(str(_("At least one service refused the message.")))

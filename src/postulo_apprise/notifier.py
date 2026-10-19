"""The notifier: one connection holding Apprise URLs, one call per notification.

What is particular about Apprise is that the address *is* the credential. A Telegram URL
carries the bot token, a Discord URL the webhook secret, a mail URL the password. So the
one field this plugin asks for is a secret as a whole: Postulo stores it encrypted, never
shows it back, and the connections list is given a description that repeats none of it:
the service's name and, for a service on a server of the person's own, that server's host.
Apprise masks what it models as a password or a token, but not a path or a topic, and for
a webhook or a public ntfy channel the path or the topic is the whole credential (#11).

Three things are checked at the form rather than at delivery time. Each line must be a URL
Apprise understands: a typo in a scheme is caught when it is typed, not when a reminder
falls due at three in the morning. A URL that names a server of the person's own (an
ntfy, a Gotify, a Matrix homeserver on the LAN, a webhook at a container next door) is
subject to the instance's destination policy, exactly as a request through ``client()``
would be: private addresses are refused unless the operator allowed them. And a line that
would act on the machine Postulo runs on, or on a file there, is refused (#12, #10). The
same checks run again before every send, because the policy is the operator's and may
change.

**What is dialled is decided by the service, not by the spelling of the line.** Apprise
keeps whatever came before the first slash in ``host`` whether that is an address or a
credential, and for some services the address that is opened is named by an argument
elsewhere in the line (``smtp=`` for mail, ``xmpp=`` for XMPP, a key file for Firebase).
:func:`dialled` is the one table that says, per service, which addresses a server object
will open; the destination policy is asked about those and about nothing else. A service
Apprise reads from its own URL template is taken to dial its ``host``, and a service this
cannot read is treated the same way: a refusal a person can read and an operator can
lift, rather than a hole nobody sees.

**Apprise never follows a redirect for this plugin**, and no line may ask it to: a public
host that answers ``307`` with a private ``Location`` would otherwise lead the server to an
address the policy never saw (#15). A socket timeout is capped, and a send has one overall
deadline, so one connection cannot hold the shared scheduler (#17).

**One thing this cannot do is close the gap between the check and the send.** Apprise
opens its own connections and resolves the name again when it does, so a name that
answered publicly here is free to answer with a private address a moment later. Closing it
means carrying the custom-webhook schemes (``json``, ``xml``, ``form``) over ``client()``,
where approving and connecting are one act; that is a larger change and is not this one.
"""

from __future__ import annotations

import ipaddress
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import apprise
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from postulo.plugins.api import DestinationRefused, FieldSpec, TestResult, check_destination

from . import __version__

#: The longest, in seconds, a connection or a read may wait. The core's own client waits
#: ten for the same reason, and a line's ``rto=`` / ``cto=`` cannot raise it (#17).
TIMEOUT_CEILING = 10.0

#: The longest, in seconds, one send (every URL of the connection, in turn) may take. A read
#: timeout bounds each read, not the request, so an endpoint answering a byte at a time
#: would outlast it; this is the bound on the whole.
SEND_DEADLINE = 45.0

#: How many lines one connection may hold. Each is a connection that can wait its timeout.
MAX_LINES = 20

#: Services that act on the machine Postulo runs on, not on a remote party: the system log,
#: the desktop's notification daemons, a local mail client, a USB light. A connection is
#: one person's setting; these write to something that belongs to the operator (#12).
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

#: Services whose ``host`` slot holds something other than the address that is opened: the
#: sender's domain of a mail API, an agent id. They send to their own API (#16).
HOST_IS_NOT_DIALLED = frozenset({"mailgun", "sparkpost", "smtp2go", "sendpulse", "wechat"})

#: Arguments that name a file on the machine rather than a remote address. A key file given
#: as an address is checked as one; given as a path it is refused.
FILE_ARGUMENTS = ("keyfile", "subfile")

#: Attributes that replace ``host`` as the address a server opens, when the line sets them:
#: mail's ``smtp=`` and XMPP's ``xmpp=``.
DIALLED_INSTEAD_OF_HOST = ("smtp_host", "xmpp_host")


class ConfigurationError(ValueError):
    """The stored URLs cannot be used. The message is for the person."""


@dataclass(frozen=True)
class Refusal:
    """A line this plugin will not hand to Apprise, and the sentence that says why."""

    line: int
    message: str


def split_urls(text: str) -> list[str]:
    """The URLs in the field: one per line, blank lines and ``#`` comments ignored."""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def new_client() -> apprise.Apprise:
    """An Apprise that says what the words are and follows no redirect.

    ``body_format=TEXT``: the title and the body are words, some of them a captured page's,
    and a service that sends HTML (Telegram, mail and a dozen API mailers) must receive
    them escaped rather than as markup (#21). ``http_redirects=False``: see the module
    docstring (#15).
    """
    asset = apprise.AppriseAsset(body_format=apprise.NotifyFormat.TEXT, http_redirects=False)
    return apprise.Apprise(asset=asset)


def _scheme_of(url: str) -> str:
    return url.split("://", 1)[0].strip().lower() if "://" in url else ""


def _protocols(server) -> set[str]:
    names: set[str] = set()
    for attribute in ("protocol", "secure_protocol"):
        value = getattr(server, attribute, None)
        if isinstance(value, str):
            names.add(value.lower())
        elif isinstance(value, (list, tuple, set, frozenset)):
            names.update(str(item).lower() for item in value)
    return names


def build(urls: list[str]) -> tuple[apprise.Apprise, list[tuple[int, str]]]:
    """An Apprise object holding every URL it accepted, and the (line, scheme) it refused.

    Apprise's own parser is the validator: a URL it will not add is one it cannot deliver
    to. Only the scheme of a refused line is reported, never the line itself, which may
    hold a credential. The two timeouts of every server are brought down to the ceiling.
    """
    client = new_client()
    rejected: list[tuple[int, str]] = []
    for number, url in enumerate(urls, start=1):
        if not client.add(url):
            rejected.append((number, url.split("://", 1)[0] if "://" in url else url[:20]))
    for server in client:
        cap_timeouts(server)
    return client, rejected


def cap_timeouts(server) -> None:
    """A line's ``rto=`` and ``cto=`` may lower a wait but not raise it past the ceiling."""
    for attribute in ("socket_connect_timeout", "socket_read_timeout"):
        value = getattr(server, attribute, None)
        if value is None or value > TIMEOUT_CEILING:
            setattr(server, attribute, TIMEOUT_CEILING)


def servers_by_line(urls: list[str]) -> Iterator[tuple[int, object | None]]:
    """Each line's number with the servers Apprise made of it; ``None`` for a line it refused.

    A single line may hold several URLs (Apprise splits on commas and spaces), so the
    refusals below are made per server, with the number of the line it came from.
    """
    for number, url in enumerate(urls, start=1):
        one = new_client()
        if not one.add(url):
            yield number, None
            continue
        for server in one:
            cap_timeouts(server)
            yield number, server


def names_a_server(server) -> bool:
    """Whether this scheme's host slot holds an address, rather than a token.

    Apprise's own URL template is the authority, and it is the only one that scales: a
    service whose URL names a machine of yours declares a ``host`` token (ntfy, Gotify,
    Matrix, Mattermost, ``mailto``, and the custom webhooks ``json``, ``xml`` and ``form``)
    while a hosted one declares what it really keeps there, ``bot_token`` for Telegram,
    ``webhook_id`` for Discord, ``user_key`` for Pushover. Over two hundred schemes are
    sorted by that one question, and a service Apprise adds tomorrow is sorted with them
    instead of waiting for a list here to be updated.

    **A scheme this cannot read is a destination.** If the template is missing, as in a
    plugin that does not use one or an Apprise that has renamed it, the answer is yes, and
    the host is checked. That way round a token in the host slot of an unusual service is
    refused with a sentence naming it, which somebody reads and reports; the other way
    round an unchecked address is a hole nothing announces (#1).

    The template cannot tell the services that declare a ``host`` and keep something else
    there: ntfy without a server (the host slot holds the topic) and the API mailers named
    in :data:`HOST_IS_NOT_DIALLED`. :func:`dialled` takes those out (#16).
    """
    template = getattr(type(server), "template_tokens", None)
    if not isinstance(template, dict) or not template:
        return True
    return "host" in template


def _authority(host: str, port) -> str:
    host = host.strip().strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        authority = host
    else:
        authority = f"[{host}]" if address.version == 6 else host
    return f"{authority}:{port}" if port else authority


def dialled(server) -> list[str]:
    """The ``host`` or ``host:port`` that a server will open, for each address it opens.

    Empty for a service that opens none the policy can judge: a hosted one (Telegram,
    Discord), cloud ntfy (``ntfy.sh``), an API mailer. Mail dials ``smtp_host`` rather than
    the domain of the sender; XMPP dials ``xmpp_host``; a key file given as an address is
    an address of its own. A key file given as a path is not here: it is refused, see
    :func:`refusals`.
    """
    addresses: list[str] = []
    port = getattr(server, "port", None)
    mode = str(getattr(getattr(server, "mode", ""), "value", getattr(server, "mode", "")))
    cloud_ntfy = bool(_protocols(server) & {"ntfy", "ntfys"}) and mode == "cloud"
    host = str(getattr(server, "host", "") or "")
    for attribute in DIALLED_INSTEAD_OF_HOST:
        instead = getattr(server, attribute, None)
        if isinstance(instead, str) and instead.strip():
            host = instead
            break
    if (
        host.strip()
        and names_a_server(server)
        and not cloud_ntfy
        and not (_protocols(server) & HOST_IS_NOT_DIALLED)
    ):
        addresses.append(_authority(host, port))
    for attribute in FILE_ARGUMENTS:
        for attachment in _attachments(getattr(server, attribute, None)):
            if getattr(attachment, "schema", "") in ("http", "https") and attachment.host:
                addresses.append(
                    _authority(str(attachment.host), getattr(attachment, "port", None))
                )
    return addresses


def _attachments(value) -> list:
    """What a key-file argument holds, as the attachment objects Apprise made of it."""
    if value is None or isinstance(value, str):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def destination_of(server) -> str | None:
    """The first address a service will be reached at, if the URL names one; else ``None``.

    Every host of a scheme that names a server is a destination, whatever it looks like:
    a dotted name, a single label such as a Docker service or a LAN machine, an address
    literal, or something that resolves nowhere at all: the policy is asked about all
    four, and answers for the last by refusing it.
    """
    addresses = dialled(server)
    return f"https://{addresses[0]}" if addresses else None


def refusals(urls: list[str]) -> list[Refusal]:
    """The lines this plugin will not use whatever the destination policy says."""
    out: list[Refusal] = []
    if len(urls) > MAX_LINES:
        out.append(
            Refusal(
                0,
                str(
                    ngettext(
                        "A connection holds at most %(count)d Apprise URL.",
                        "A connection holds at most %(count)d Apprise URLs.",
                        MAX_LINES,
                    )
                )
                % {"count": MAX_LINES},
            )
        )
    for number, url in enumerate(urls, start=1):
        scheme = _scheme_of(url)
        if scheme in LOCAL_SCHEMES:
            out.append(Refusal(number, _local(number, scheme)))
    return out


def _local(number: int, scheme: str) -> str:
    return str(
        _(
            "Line %(line)d (%(scheme)s) acts on the machine Postulo runs on, not on a service "
            "of yours, so it cannot be used here."
        )
    ) % {"line": number, "scheme": scheme}


def server_refusal(number: int, server) -> str | None:
    """Why this server may not be used, apart from where it connects; else ``None``."""
    protocols = _protocols(server)
    local = protocols & LOCAL_SCHEMES
    if local:
        return _local(number, sorted(local)[0])
    if getattr(server, "redirects", False):
        return str(
            _(
                "Line %(line)d asks for redirects to be followed; Postulo never follows one, "
                "so give the final address."
            )
        ) % {"line": number}
    for attribute in FILE_ARGUMENTS:
        value = getattr(server, attribute, None)
        if not value:
            continue
        remote = [
            item for item in _attachments(value) if getattr(item, "schema", "") in ("http", "https")
        ]
        if isinstance(value, str) or len(remote) != len(_attachments(value)):
            return str(
                _(
                    "Line %(line)d names a file on the machine Postulo runs on (%(argument)s=). "
                    "Give an https address instead."
                )
            ) % {"line": number, "argument": attribute}
    return None


def describe(server) -> str:
    """The service's name and, if it is on a server of the person's own, that server.

    Never the path, the user part, the topic or the query: Apprise masks what it models as
    a password or a token, but a webhook's path and a public ntfy topic are credentials it
    does not model (#11). Host and port are not.
    """
    addresses = dialled(server)
    return f"{server.service_name} {addresses[0]}" if addresses else str(server.service_name)


def _notify(client: apprise.Apprise, **kwargs) -> bool:
    """``client.notify`` with one overall deadline, so a slow endpoint cannot hold the caller.

    The work runs in a thread that is abandoned, not killed, when the deadline passes: its
    sockets are capped (:data:`TIMEOUT_CEILING`) and it ends by itself.
    """
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["sent"] = client.notify(body_format=apprise.NotifyFormat.TEXT, **kwargs)
        except Exception as error:  # noqa: BLE001  # handed back to the caller's thread
            outcome["error"] = error

    worker = threading.Thread(target=run, name="postulo-apprise-send", daemon=True)
    worker.start()
    worker.join(SEND_DEADLINE)
    if worker.is_alive():
        raise TimeoutError(
            str(_("The services did not answer within %(seconds)d seconds."))
            % {"seconds": SEND_DEADLINE}
        )
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return bool(outcome.get("sent"))


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
                        "stored encrypted and never shown again; the connections list shows "
                        "only the name of each service and, for one on a server of your own, "
                        "that server."
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
        """One line for the connections list: services and servers, nothing secret."""
        client, _rejected = build(split_urls(config.get("urls", "")))
        return " · ".join(describe(server) for server in client)

    def _problems(self, config: dict) -> list[str]:
        urls = split_urls(config.get("urls", ""))
        if not urls:
            return [str(_("At least one Apprise URL is needed."))]
        refused = refusals(urls)
        problems = [refusal.message for refusal in refused]
        refused_lines = {refusal.line for refusal in refused}
        for number, server in servers_by_line(urls):
            if number in refused_lines:
                continue
            if server is None:
                scheme = _scheme_of(urls[number - 1]) or urls[number - 1][:20]
                problems.append(
                    str(
                        _(
                            "Line %(line)d is not an Apprise URL this version understands "
                            "(%(scheme)s)."
                        )
                    )
                    % {"line": number, "scheme": scheme}
                )
                continue
            reason = server_refusal(number, server)
            if reason:
                problems.append(reason)
                continue
            for address in dialled(server):
                try:
                    check_destination(f"https://{address}")
                except DestinationRefused as error:
                    problems.append(
                        str(_("%(service)s at %(host)s: %(error)s"))
                        % {"service": server.service_name, "host": address, "error": error}
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
            sent = _notify(
                client,
                title=str(_("Postulo can reach you here")),
                body=str(
                    _(
                        "This is the test message from your Apprise connection. Reminders and "
                        "captures will arrive the same way."
                    )
                ),
            )
        except (ConfigurationError, TimeoutError) as error:
            return TestResult(False, str(error))
        if sent:
            count = len(client)
            return TestResult(
                True,
                str(
                    ngettext(
                        "Delivered to %(count)d service.",
                        "Delivered to %(count)d services.",
                        count,
                    )
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
        if not _notify(client, title=notification.title, body=body):
            raise RuntimeError(str(_("At least one service refused the message.")))

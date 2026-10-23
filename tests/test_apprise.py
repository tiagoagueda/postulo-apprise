"""The plugin, exercised inside a real Postulo: through its registry and its forms."""

import ipaddress
import threading

import apprise
import pytest
from django.urls import reverse
from django.utils import translation
from postulo.notifications.base import Notification
from postulo.plugins import public_addresses, registry
from postulo.plugins.models import Connection

from postulo_apprise import notifier as notifier_module
from postulo_apprise.notifier import (
    LOCAL_SCHEMES,
    MAX_LINES,
    TIMEOUT_CEILING,
    AppriseNotifier,
    ConfigurationError,
    build,
    describe,
    destination_of,
    names_a_server,
    server_refusal,
    split_urls,
)

TELEGRAM = "tgram://123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/987654"
NTFY = "ntfy://ntfy.example.org/postulo"
GOTIFY_PRIVATE = "gotify://10.0.0.5/AbCdEfGhIjKlMnO"
#: A webhook at a container next door: no dot anywhere, and a real address all the same.
WEBHOOK_SERVICE = "json://redis:6379/"
FORM_SERVICE = "form://paperless:8000/"


@pytest.fixture
def sent(monkeypatch):
    """Capture what Apprise would deliver, instead of delivering it."""
    calls = []

    def notify(self, body="", title="", **kwargs):
        calls.append({"title": title, "body": body, "urls": [s.url() for s in self], **kwargs})
        return True

    monkeypatch.setattr("apprise.Apprise.notify", notify)
    return calls


@pytest.fixture
def no_dns(monkeypatch):
    """Record which destinations are checked without resolving any name."""
    checked = []
    monkeypatch.setattr("postulo_apprise.notifier.check_destination", checked.append)
    return checked


@pytest.fixture
def compose_network(monkeypatch):
    """Answer for a service name the way a Docker network does, and for nothing else.

    A single-label name is exactly what a resolver on the test machine may or may not have
    an opinion about, so the resolver is the thing to replace: the policy, not the name
    server, is what these tests are about.
    """

    def addresses(host: str):
        return [ipaddress.ip_address("172.18.0.7")] if host in {"redis", "paperless"} else []

    monkeypatch.setattr(public_addresses, "_addresses_for", addresses)


@pytest.fixture
def public_only(settings, monkeypatch):
    """The default policy, and a resolver that knows only the public names these tests use."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    known = {
        "example.org": "93.184.216.34",
        "ntfy.sh": "93.184.216.35",
        "mg.example.org": "93.184.216.36",
        "hooks.example.org": "93.184.216.37",
        "keys.example.org": "93.184.216.38",
        "ntfy.example.org": "93.184.216.39",
    }

    def addresses(host: str):
        return [ipaddress.ip_address(known[host])] if host in known else []

    monkeypatch.setattr(public_addresses, "_addresses_for", addresses)


# --------------------------------------------------------------- the pieces


def test_the_field_takes_one_url_per_line_and_ignores_notes():
    assert split_urls(f"  {TELEGRAM}\n\n# my phone\n{NTFY}\n") == [TELEGRAM, NTFY]


def test_apprise_itself_is_the_parser():
    client, rejected = build([TELEGRAM, "nope://x", "not a url at all"])
    assert len(client) == 1
    assert rejected == [(2, "nope"), (3, "not a url at all")]


def test_only_real_addresses_are_destinations():
    client, _ = build([TELEGRAM, NTFY, GOTIFY_PRIVATE, "discord://1234/abcd"])
    telegram, ntfy, gotify, discord = list(client)
    assert destination_of(telegram) is None, "a bot token is not an address"
    assert destination_of(discord) is None
    assert destination_of(ntfy) == "https://ntfy.example.org"
    assert destination_of(gotify) == "https://10.0.0.5"


def test_a_host_without_a_dot_is_still_a_host():
    """The bug this plugin had: a name had to be dotted to be worth checking (#1)."""
    client, _ = build([WEBHOOK_SERVICE, FORM_SERVICE, "ntfy://internal/postulo"])
    webhook, form, ntfy = list(client)
    assert destination_of(webhook) == "https://redis:6379"
    assert destination_of(form) == "https://paperless:8000"
    assert destination_of(ntfy) == "https://internal"


def test_the_scheme_says_whether_the_host_is_an_address():
    """Apprise's own URL template sorts the two, over two hundred schemes at a time."""
    servers = {}
    for url in [
        WEBHOOK_SERVICE,
        FORM_SERVICE,
        "xml://webhook.example/",
        NTFY,
        GOTIFY_PRIVATE,
        "matrixs://user:pass@matrix.example.org",
        "mailto://me:pw@example.org",
        TELEGRAM,
        "discord://1234/abcd",
        "slack://TokenA/TokenB/TokenC/",
        "pover://user@token",
    ]:
        client, rejected = build([url])
        assert not rejected, url
        servers[url.split(":", 1)[0]] = next(iter(client))

    for scheme in ("json", "form", "xml", "ntfy", "gotify", "matrixs", "mailto"):
        assert names_a_server(servers[scheme]), f"{scheme} names a machine somebody owns"
    for scheme in ("tgram", "discord", "slack", "pover"):
        assert not names_a_server(servers[scheme]), f"{scheme} keeps a credential there"


def test_a_scheme_that_cannot_be_read_is_treated_as_an_address():
    """Erring towards a refusal somebody can read, never towards an unchecked address."""

    class Unreadable:
        host = "redis"
        port = None
        service_name = "Unreadable"

    assert names_a_server(Unreadable())
    assert destination_of(Unreadable()) == "https://redis"


def test_a_description_names_the_service_and_repeats_nothing_secret():
    """A path and a topic are credentials Apprise does not mask, so none is shown (#11)."""
    lines = [
        TELEGRAM,
        GOTIFY_PRIVATE,
        "ntfy://ntfy.example.org/my-private-topic",
        "ntfy://my-private-topic",
        "json://hooks.example.org/services/T000/B000/secretpath",
        "mailto://me:pw@example.org",
    ]
    client, rejected = build(lines)
    assert not rejected
    described = [describe(server) for server in client]
    assert described == [
        "Telegram",
        "Gotify 10.0.0.5",
        "ntfy ntfy.example.org",
        "ntfy",
        "JSON hooks.example.org",
        "E-Mail example.org:25",
    ]
    everything = " ".join(described)
    for secret in ("ABC-DEF1234", "987654", "AbCdEfGhIjKlMnO", "my-private-topic", "secretpath"):
        assert secret not in everything
    assert "pw" not in everything.split()


# ---------------------------------------------------------------- checking


def test_validation_refuses_nothing_and_nonsense(no_dns):
    notifier = AppriseNotifier()
    assert notifier.validate({"urls": ""}) == {"urls": ["At least one Apprise URL is needed."]}
    problems = notifier.validate({"urls": f"{TELEGRAM}\nnope://x"})["urls"]
    assert problems == ["Line 2 is not an Apprise URL this version understands (nope)."]
    assert notifier.validate({"urls": TELEGRAM}) == {}


def test_validation_checks_self_hosted_services_against_the_destination_policy(no_dns):
    AppriseNotifier().validate({"urls": f"{TELEGRAM}\n{NTFY}\n{GOTIFY_PRIVATE}"})
    assert no_dns == ["https://ntfy.example.org", "https://10.0.0.5"]


@pytest.mark.django_db
def test_a_private_server_needs_the_operators_say_so(settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    problems = AppriseNotifier().validate({"urls": GOTIFY_PRIVATE})["urls"]
    assert len(problems) == 1 and problems[0].startswith("Gotify at 10.0.0.5:")
    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in problems[0]

    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    assert AppriseNotifier().validate({"urls": GOTIFY_PRIVATE}) == {}


@pytest.mark.django_db
def test_a_webhook_at_a_container_next_door_is_held_to_the_same_policy(settings, compose_network):
    """`json://redis:6379/` went out unchecked because `redis` has no dot in it (#1)."""
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    problems = AppriseNotifier().validate({"urls": f"{WEBHOOK_SERVICE}\n{FORM_SERVICE}"})["urls"]
    assert len(problems) == 2
    assert problems[0].startswith("JSON at redis:") and "private or local" in problems[0]
    assert problems[1].startswith("Form at paperless:")

    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    assert AppriseNotifier().validate({"urls": WEBHOOK_SERVICE}) == {}


@pytest.mark.django_db
def test_a_name_that_resolves_nowhere_is_refused_rather_than_skipped(settings, compose_network):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    problems = AppriseNotifier().validate({"urls": "json://nowhere/"})["urls"]
    assert len(problems) == 1 and problems[0].startswith("JSON at nowhere:")


# ------------------------------------------ what is dialled (#10, #16)


@pytest.mark.django_db
def test_a_mail_line_is_checked_where_it_connects(public_only):
    """`smtp=` names the machine opened; the domain is only the sender's (#10)."""
    notifier = AppriseNotifier()
    plain = "mailto://me:pw@example.org:2525"
    assert notifier.validate({"urls": plain}) == {}
    problems = notifier.validate({"urls": f"{plain}?smtp=127.0.0.1"})["urls"]
    assert len(problems) == 1 and problems[0].startswith("E-Mail at 127.0.0.1:2525:")


@pytest.mark.django_db
def test_a_key_file_is_an_address_that_is_checked_or_a_path_that_is_refused(public_only):
    notifier = AppriseNotifier()
    fcm = "fcm://project/token"
    assert notifier.validate({"urls": f"{fcm}?keyfile=https://keys.example.org/key.json"}) == {}
    private = notifier.validate({"urls": f"{fcm}?keyfile=http://127.0.0.1:9000/key.json"})["urls"]
    assert len(private) == 1
    assert private[0].startswith("Firebase Cloud Messaging at 127.0.0.1:9000")
    for path in ("/etc/passwd", "file:///etc/passwd"):
        problems = notifier.validate({"urls": f"{fcm}?keyfile={path}"})["urls"]
        assert len(problems) == 1 and "(keyfile=)" in problems[0], path


@pytest.mark.django_db
def test_a_refused_line_never_reaches_apprise(public_only, sent):
    notifier = AppriseNotifier()
    for line in (
        "mailto://me:pw@example.org:2525?smtp=127.0.0.1",
        "fcm://project/token?keyfile=http://127.0.0.1:9000/key.json",
    ):
        result = notifier.test({"urls": line})
        assert not result.ok and "127.0.0.1" in result.message
        with pytest.raises(ConfigurationError, match="127.0.0.1"):
            notifier.send(Notification(event="reminder_due", title="x"), {"urls": line}, user=None)
    assert sent == []


@pytest.mark.django_db
def test_the_examples_in_the_help_text_are_accepted_on_a_default_instance(public_only):
    """`ntfy://topic` was refused because the topic was looked up as a host name (#16)."""
    notifier = AppriseNotifier()
    examples = [
        TELEGRAM,
        "ntfy://my-topic",
        "discord://1234/abcd",
        "mailto://user:password@example.org",
        "mailgun://user@mg.example.org/apikey/to@example.org",
        # The domain of a mail API's sender need not resolve.
        "mailgun://user@no-address-record.example.net/apikey/to@example.org",
    ]
    for line in examples:
        assert notifier.validate({"urls": line}) == {}, line
    # A server of the person's own is still held to the policy.
    problems = notifier.validate({"urls": "ntfy://internal/postulo"})["urls"]
    assert len(problems) == 1 and problems[0].startswith("ntfy at internal:")


def test_what_a_service_dials_is_decided_per_server_not_per_class():
    client, _ = build(
        [
            "ntfy://my-topic",
            "ntfy://ntfy.sh/my-topic",
            "mailgun://user@mg.example.org/apikey/to@example.org",
            "mailto://me:pw@example.org:2525?smtp=10.1.2.3",
        ]
    )
    cloud, private, mailgun, mail = list(client)
    assert destination_of(cloud) is None
    assert destination_of(private) == "https://ntfy.sh"
    assert destination_of(mailgun) is None
    assert destination_of(mail) == "https://10.1.2.3:2525"


# ------------------------------------------------ the server itself (#12)


@pytest.mark.django_db
@pytest.mark.parametrize("scheme", sorted(LOCAL_SCHEMES))
def test_a_scheme_that_acts_on_the_server_is_refused(scheme, sent):
    """Judged by the scheme as typed, so it holds on a platform where Apprise loads it too."""
    notifier = AppriseNotifier()
    problems = notifier.validate({"urls": f"{TELEGRAM}\n{scheme.upper()}://"})["urls"]
    assert len(problems) == 1
    assert problems[0].startswith("Line 2 (")
    assert "acts on the machine Postulo runs on" in problems[0]
    with pytest.raises(ConfigurationError, match="Line 2"):
        notifier.send(
            Notification(event="reminder_due", title="x"),
            {"urls": f"{TELEGRAM}\n{scheme}://"},
            user=None,
        )
    assert not notifier.test({"urls": f"{scheme}://"}).ok
    assert sent == []


def test_a_local_service_is_refused_whatever_the_alias():
    class Aliased:
        protocol = ("qt",)
        secure_protocol = None
        redirects = False

    assert server_refusal(3, Aliased()).startswith("Line 3 (qt)")


# ------------------------------------------------------ redirects (#15)


def test_no_server_follows_a_redirect():
    client, rejected = build(
        ["json://hooks.example.org/x", "ntfy://ntfy.example.org/t", "gotify://g.example.org/tok"]
    )
    assert not rejected
    assert [server.redirects for server in client] == [False, False, False]


@pytest.mark.django_db
def test_a_line_that_asks_for_redirects_is_refused(public_only):
    problems = AppriseNotifier().validate({"urls": "json://hooks.example.org/x?redirect=yes"})
    assert len(problems["urls"]) == 1
    assert problems["urls"][0].startswith("Line 1 asks for redirects")
    assert AppriseNotifier().validate({"urls": "json://hooks.example.org/x?redirect=no"}) == {}


# ------------------------------------------------------- timeouts (#17)


def test_a_line_cannot_raise_the_socket_timeouts_past_the_ceiling():
    client, _ = build(
        [
            "json://hooks.example.org/x?rto=86400&cto=86400",
            "json://hooks.example.org/y?rto=2&cto=3",
            "json://hooks.example.org/z",
        ]
    )
    long, short, plain = list(client)
    assert (long.socket_connect_timeout, long.socket_read_timeout) == (TIMEOUT_CEILING,) * 2
    assert (short.socket_connect_timeout, short.socket_read_timeout) == (3.0, 2.0)
    assert (plain.socket_connect_timeout, plain.socket_read_timeout) == (4.0, 4.0)


def test_a_send_that_never_returns_is_given_up_on(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(notifier_module, "SEND_DEADLINE", 0.2)
    monkeypatch.setattr("apprise.Apprise.notify", lambda self, **kw: release.wait(30) or True)
    notifier = AppriseNotifier()
    try:
        with pytest.raises(TimeoutError, match="did not answer"):
            notifier.send(
                Notification(event="reminder_due", title="x"), {"urls": TELEGRAM}, user=None
            )
        result = notifier.test({"urls": TELEGRAM})
        assert not result.ok and "did not answer" in result.message
    finally:
        release.set()


def test_a_connection_holds_a_bounded_number_of_lines():
    many = "\n".join(f"json://hooks.example.org/{n}" for n in range(MAX_LINES + 1))
    problems = AppriseNotifier().validate({"urls": many})["urls"]
    assert problems[0] == f"A connection holds at most {MAX_LINES} Apprise URLs."


# ------------------------------------------------ what the words are (#21)


def test_the_words_are_text_so_an_html_service_escapes_them(monkeypatch):
    """A captured page's title must not arrive in Telegram or in mail as markup."""
    from apprise.plugins.telegram import NotifyTelegram

    bodies = []

    def send(self, body, title="", **kwargs):
        bodies.append((title, body))
        return True

    monkeypatch.setattr(NotifyTelegram, "send", send)
    hostile = '<a href="https://evil.example/">Postulo</a> & <img src=x>'
    AppriseNotifier().send(
        Notification(event="capture_received", title=f"Captured: {hostile}", body=hostile),
        {"urls": TELEGRAM},
        user=None,
    )
    assert bodies, "Telegram was reached"
    text = " ".join(title + body for title, body in bodies)
    assert "<a href" not in text and "<img" not in text
    assert "&lt;a href" in text and "&amp;" in text


def test_every_send_declares_its_body_as_text(sent, no_dns):
    notifier = AppriseNotifier()
    notifier.send(Notification(event="reminder_due", title="x"), {"urls": TELEGRAM}, user=None)
    notifier.test({"urls": TELEGRAM})
    assert [call["body_format"] for call in sent] == [apprise.NotifyFormat.TEXT] * 2
    client, _ = build([TELEGRAM])
    assert client.asset.body_format == apprise.NotifyFormat.TEXT


# ----------------------------------------------------------------- sending


def test_the_test_button_sends_one_message_to_every_url(sent, no_dns):
    result = AppriseNotifier().test({"urls": f"{TELEGRAM}\n{NTFY}"})
    assert result.ok and result.message == "Delivered to 2 services."
    assert len(sent) == 1 and sent[0]["title"] == "Postulo can reach you here"
    assert len(sent[0]["urls"]) == 2


def test_the_test_button_says_service_for_one_line(sent, no_dns):
    result = AppriseNotifier().test({"urls": TELEGRAM})
    assert result.ok and result.message == "Delivered to 1 service."


def test_the_test_button_explains_a_bad_configuration_instead_of_sending(sent):
    result = AppriseNotifier().test({"urls": "nope://x"})
    assert not result.ok and "Line 1" in result.message
    assert sent == []


def test_a_notification_travels_as_title_and_body_with_its_link(sent):
    AppriseNotifier().send(
        Notification(
            event="reminder_due",
            title="Chase Black Mesa",
            body="You meant to follow up today.",
            url="https://postulo.example/applications/1/",
        ),
        {"urls": TELEGRAM},
        user=None,
    )
    assert sent[0]["title"] == "Chase Black Mesa"
    assert (
        sent[0]["body"]
        == "You meant to follow up today.\n\nhttps://postulo.example/applications/1/"
    )


def test_a_refusal_by_the_service_raises_so_postulo_records_it(monkeypatch):
    monkeypatch.setattr("apprise.Apprise.notify", lambda self, **kwargs: False)
    with pytest.raises(RuntimeError, match="refused"):
        AppriseNotifier().send(
            Notification(event="reminder_due", title="x"), {"urls": TELEGRAM}, user=None
        )


# --------------------------------------------------------------- in Postulo


@pytest.mark.django_db
def test_postulo_finds_the_notifier_through_its_entry_point():
    registry.plugins("notifier", refresh=True)
    notifier = registry.find_plugin("notifier", "apprise")
    assert notifier is not None and notifier.kind == "notifier"


@pytest.mark.django_db
def test_the_connection_form_keeps_the_urls_secret_and_the_list_shows_none_of_them(
    client, sent, no_dns
):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="ana", email="ana@example.org", password="pw" * 8
    )  # noqa: S106
    client.force_login(user)
    registry.plugins("notifier", refresh=True)

    url = reverse("connections:create", args=["notifier", "apprise"])
    html = client.get(url).content.decode()
    assert "<textarea" in html and 'name="plugin_urls"' in html

    response = client.post(
        url, {"label": "My phone", "enabled": "on", "plugin_urls": f"{TELEGRAM}\nnope://x"}
    )
    assert response.status_code == 200, "a bad line stops the form"
    assert "Line 2 is not an Apprise URL" in response.content.decode()

    response = client.post(
        url, {"label": "My phone", "enabled": "on", "plugin_urls": f"{TELEGRAM}\n{NTFY}"}
    )
    assert response.status_code == 302
    connection = Connection.objects.get(owner=user)
    assert connection.secrets == {"urls": f"{TELEGRAM}\n{NTFY}"}
    assert connection.config.get("urls") is None, "the URLs are a secret, not configuration"

    html = client.get(reverse("connections:list")).content.decode()
    assert "Telegram" in html and "ntfy ntfy.example.org" in html
    assert "ABC-DEF1234" not in html, "the bot token never reaches a page"
    assert "987654" not in html, "nor the chat id"
    assert "example.org/postulo" not in html and "ntfy://" not in html, "nor the topic"

    html = client.get(reverse("connections:edit", args=[connection.pk])).content.decode()
    assert "ABC-DEF1234" not in html and "leave blank to keep" in html

    client.post(reverse("connections:test", args=[connection.pk]))
    connection.refresh_from_db()
    assert connection.last_ok_at is not None and len(sent) == 1


@pytest.mark.django_db
def test_the_plugin_speaks_french_and_portuguese_by_itself():
    registry.plugins("notifier", refresh=True)
    notifier = registry.find_plugin("notifier", "apprise")
    with translation.override("fr-FR"):
        assert notifier.config_fields()[0].label == "URL Apprise"
        assert notifier.validate({"urls": ""}) == {"urls": ["Il faut au moins une URL Apprise."]}
    with translation.override("pt-PT"):
        assert notifier.validate({"urls": ""}) == {"urls": ["É preciso pelo menos um URL Apprise."]}
    with translation.override("en-GB"):
        assert notifier.config_fields()[0].label == "Apprise URLs"


@pytest.mark.django_db
def test_it_is_named_for_what_it_does_and_keeps_its_identifier():
    """The name people read changes (#19); the identifier rows key on does not."""
    registry.plugins("notifier", refresh=True)
    notifier = registry.find_plugin("notifier", "apprise")
    assert notifier.name == "apprise"
    with translation.override("en-GB"):
        assert str(notifier.label) == "Third-party notifications via Apprise"
        assert "Apprise" in str(notifier.description)

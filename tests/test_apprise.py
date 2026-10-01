"""The plugin, exercised inside a real Postulo: through its registry and its forms."""

import ipaddress

import pytest
from django.urls import reverse
from django.utils import translation
from postulo.notifications.base import Notification
from postulo.plugins import public_addresses, registry
from postulo.plugins.models import Connection

from postulo_apprise.notifier import (
    AppriseNotifier,
    build,
    describe,
    destination_of,
    names_a_server,
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
        calls.append({"title": title, "body": body, "urls": [s.url() for s in self]})
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

    assert names_a_server(Unreadable())
    assert destination_of(Unreadable()) == "https://redis"


def test_a_description_names_the_service_and_masks_the_secret():
    client, _ = build([TELEGRAM, GOTIFY_PRIVATE])
    telegram, gotify = (describe(server) for server in client)
    assert telegram.startswith("Telegram tgram://")
    assert "ABC-DEF1234" not in telegram and "987654" in telegram
    assert gotify.startswith("Gotify gotify://10.0.0.5/")
    assert "AbCdEfGhIjKlMnO" not in gotify


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


# ----------------------------------------------------------------- sending


def test_the_test_button_sends_one_message_to_every_url(sent, no_dns):
    result = AppriseNotifier().test({"urls": f"{TELEGRAM}\n{NTFY}"})
    assert result.ok and result.message == "Delivered to 2 services."
    assert len(sent) == 1 and sent[0]["title"] == "Postulo can reach you here"
    assert len(sent[0]["urls"]) == 2


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
def test_the_connection_form_keeps_the_urls_secret_and_the_list_masks_them(client, sent, no_dns):
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
    assert "Telegram tgram://" in html and "ntfy ntfy://ntfy.example.org/postulo" in html
    assert "ABC-DEF1234" not in html, "the bot token never reaches a page"

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

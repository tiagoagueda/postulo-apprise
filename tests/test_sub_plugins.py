"""Every service Apprise supports, listed as a sub-plugin and switched one by one (#20).

These run inside a real Postulo, through its registry and its policy, so they need a core
that has `SubPlugin` (postulo/postulo, branch w5-apprise-surface or later).
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from postulo.notifications.base import Notification
from postulo.plugins import registry
from postulo.plugins.api import SubPlugin, enabled_sub_plugins, sub_plugin_enabled
from postulo.plugins.models import PluginPolicy

from postulo_apprise import notifier as notifier_module
from postulo_apprise import services
from postulo_apprise.notifier import AppriseNotifier, ConfigurationError, switched_off

pytestmark = pytest.mark.django_db

TELEGRAM = "tgram://123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/987654"
NTFY = "ntfy://ntfy.example.org/postulo"


@pytest.fixture
def person():
    return get_user_model().objects.create_user(
        email="person@example.org",
        password="a-fairly-long-password-42",  # noqa: S106
        username="person",
    )


def test_apprise_lists_well_over_a_hundred_services():
    listed = services.services()
    assert len(listed) > 100
    assert len({service.id for service in listed}) == len(listed), "an id names one service"
    assert {"tgram", "discord", "ntfys", "mailtos"} <= {service.id for service in listed}


def test_every_service_is_a_valid_sub_plugin():
    declared = AppriseNotifier().sub_plugins()
    assert declared and all(isinstance(sub, SubPlugin) for sub in declared)
    assert all(not sub.problems() for sub in declared)
    assert all(sub.label and sub.description for sub in declared)


def test_the_registry_loads_them_under_the_plugin():
    names = {entry.name for entry in registry.sub_plugins("apprise")}
    assert len(names) == len(services.services())
    assert "apprise:tgram" in names


def test_a_service_that_acts_on_this_machine_is_listed_as_unavailable_with_the_reason():
    local = next(s for s in AppriseNotifier().sub_plugins() if s.id == "qt")
    assert local.unavailable
    assert "machine" in str(local.unavailable)


def test_a_service_missing_its_library_is_unavailable_and_says_which(monkeypatch):
    missing = services.Service("x", "X", ("x",), "", "", ("somelib",))
    assert "somelib" in str(services._unavailable(missing))
    assert services._unavailable(services.Service("y", "Y", ("y",), "", "", ())) == ""
    off = services.Service("z", "Z", ("z",), "", "", (), enabled=False)
    assert str(services._unavailable(off)) == "Not available on this machine."


def test_the_services_are_on_until_an_administrator_says_otherwise(person):
    assert sub_plugin_enabled("apprise", "tgram", person)
    on = {sub.id for sub in enabled_sub_plugins("apprise", person)}
    assert "tgram" in on and "qt" not in on


def test_a_switched_off_service_is_refused_at_the_send(person, monkeypatch):
    PluginPolicy.objects.create(
        plugin="apprise:tgram", person=None, state=PluginPolicy.State.FORCED_OFF
    )
    assert switched_off([TELEGRAM, NTFY], person) == ["Telegram"]
    assert switched_off([NTFY], person) == []

    monkeypatch.setattr(
        "apprise.Apprise.notify", lambda self, **kwargs: pytest.fail("nothing may be sent")
    )
    with pytest.raises(ConfigurationError) as refused:
        AppriseNotifier().send(
            Notification(event="reminder_due", title="x"), {"urls": TELEGRAM}, person
        )
    assert "Telegram" in str(refused.value)


def test_a_service_still_on_is_sent(person, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "apprise.Apprise.notify", lambda self, **kwargs: sent.append(kwargs) or True
    )
    PluginPolicy.objects.create(
        plugin="apprise:discord", person=None, state=PluginPolicy.State.FORCED_OFF
    )
    AppriseNotifier().send(
        Notification(event="reminder_due", title="x"), {"urls": TELEGRAM}, person
    )
    assert sent


def test_the_plugin_still_imports_only_the_surface():
    from pathlib import Path

    from postulo.plugins.testing import assert_imports_only_the_surface

    assert_imports_only_the_surface(
        Path(notifier_module.__file__).parent, package="postulo_apprise"
    )

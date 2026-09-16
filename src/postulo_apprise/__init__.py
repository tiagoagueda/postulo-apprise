"""postulo-apprise: notifications from Postulo through Apprise.

Apprise delivers one message to well over a hundred services through one interface, each
addressed by a URL — ``tgram://bot-token/chat-id``, ``ntfy://topic``,
``discord://webhook-id/webhook-token``, ``matrix://…``, ``gotify://…``, ``mailto://…``.
This package registers a **notifier** with Postulo, so that a person adds a connection
holding one or more of those URLs, chooses which events reach it, and is told on the
phone, in the chat, or wherever they already look.

It is the first plugin built outside Postulo's core, and it is deliberately small: the
notifier interface does the work of drawing the form, storing the URLs encrypted, and
choosing events; this package turns a notification into an Apprise call and nothing more.

Every string a person reads is translated by this package itself, from ``locale/`` next to
this file; Postulo never carries a plugin's words.
"""

from importlib import metadata

#: One version, and it is the installed distribution's. Written out by hand it was a label
#: rather than a fact -- `0.1.0` here while `pyproject.toml` said `0.3.0`, so every
#: connection recorded a version the package had not had for two releases (#1).
__version__ = metadata.version("postulo-apprise")

# postulo-apprise

**Notifications from [Postulo](https://source.tiagoagueda.com/postulo/postulo) through
[Apprise](https://github.com/caronc/apprise): Telegram, ntfy, Discord, Matrix, Gotify,
Pushover, Signal, email and well over a hundred other services, each named by one URL.**

Postulo tells you when a reminder falls due, when a posting arrives through the capture
API, and when an application has gone quiet. Out of the box it says so by email. This
plugin lets it say so wherever you already look: add a connection, paste the URL of the
service, tick the events you want, and that is the whole set-up.

It is the first plugin built outside Postulo's core, and deliberately small. Postulo's
notifier interface draws the form, stores the URLs encrypted, offers the *Test* button and
the switch per event; this package turns a notification into an Apprise call.

## Install

Into the environment of a running Postulo:

```sh
uv pip install git+https://source.tiagoagueda.com/postulo/postulo-apprise.git
```

Restart Postulo. In a container, bake it into the image instead — the two ways are on
[Installing Postulo](https://source.tiagoagueda.com/postulo/postulo/wiki/Installing-Postulo):

```sh
docker compose -f docker/compose.yml build \
  --build-arg POSTULO_EXTRA_PACKAGES="git+https://source.tiagoagueda.com/postulo/postulo-apprise.git"
```

## Use

Under **Settings → Connections → Add a connection**, choose *Apprise*. The one field
takes Apprise URLs, one per line:

```text
tgram://123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/987654
ntfy://ntfy.example.org/job-hunt
mailto://me:app-password@example.org
```

Every service Apprise speaks, and the form of its URL, is in
[Apprise's own list](https://github.com/caronc/apprise/wiki). Press *Test*: one message
goes to every line. Tick the events you want under the URLs.

Some things worth knowing:

- **The URLs are secrets.** A Telegram URL carries the bot token; a mail URL carries the
  password. The whole field is stored encrypted, is never shown back, and the connections
  list names each service with the secret parts masked (`tgram://1...1/987654`). To
  change the list, type it again.
- **A typo is caught at the form.** Each line must be a URL Apprise understands; the
  form says which line is not, and nothing is saved until every line is.
- **Your own servers are subject to the instance's destination policy.** An ntfy, Gotify
  or Matrix server, or a `json://`, `xml://` or `form://` webhook, on a private address is
  refused unless the operator set `POSTULO_CONNECTIONS_ALLOW_PRIVATE=true`, exactly as for
  every other connection. That includes a name with no dot in it — `json://redis:6379/`,
  `form://paperless:8000/`, a Docker service or a machine on the LAN — which is a real
  destination like any other and was let through unchecked before 0.3.0. Which URLs name a
  server of yours is read from Apprise's own description of each service; hosted ones
  (Telegram, Discord, Pushover…) keep a token where the host would be, name no address of
  yours, and are not affected.
- **Several connections or several lines?** Either. One connection with three URLs sends
  every chosen event to all three; two connections let you send reminders to the phone
  and captures to a chat.
- **Email stays built in.** Apprise has `mailto://`, and Postulo's own Email notifier is
  not going anywhere: the core works with no plugin installed.

## Translations

French and Portuguese ship with the package, in `src/postulo_apprise/locale/`. Postulo
never translates a plugin's strings; a catalogue for every language Postulo offers is in place; fill one and run
`uv run postulo-messages compile`. `uv run postulo-messages extract` refreshes them from
the source, and `uv run pytest -m release` holds the twenty-four European Union
catalogues complete before a release.

A language code here is a BCP 47 tag in its canonical form, as everywhere in Postulo: the
language in lower case, a script with a capital, a region in capitals (`fr-FR`, `pt-BR`,
`sr-Cyrl`). The directories under `locale/` are the one exception: `fr_FR` and `sr_Cyrl`
are gettext's names for a catalogue, not codes, and `postulo-messages` derives them from
the tags, so nobody types one.

## Develop

```sh
uv sync        # brings in Postulo itself from its repository, for the tests
uv run pytest
uv run ruff check .
```

The tests run the plugin inside a real Postulo, through its registry and its connection
form, with Apprise's delivery replaced by a recorder. No message leaves the machine.

## Licence

AGPL-3.0-or-later, like Postulo. Apprise is BSD-2-Clause.

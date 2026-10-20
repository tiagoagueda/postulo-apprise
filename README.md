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
  password. The whole field is stored encrypted and is never shown back. The connections
  list shows only the name of each service and, for one on a server of your own, that
  server's host and port (`Telegram · ntfy ntfy.example.org`): never a path, a topic, a
  user name or a token, because a webhook's path and an ntfy topic are credentials Apprise
  does not know how to mask. To change the list, type it again.
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
  yours, and are not affected. What is checked is the address a service really opens: a
  `mailto://` line's `smtp=` server rather than the sender's domain, a Firebase `keyfile=`
  given as an address. `ntfy://my-topic`, which uses ntfy.sh, and the API mailers (Mailgun,
  SparkPost, SMTP2GO, SendPulse) have no server of yours and are not looked up.
- **Redirects are never followed.** A public address that answers with a redirect to a
  private one would otherwise lead the server past the destination policy, so a line that
  sets `redirect=yes` is refused and a server that redirects `http` to `https` needs the
  `https` form of the scheme (`jsons://`, `ntfys://`).
- **Lines that act on the server itself are refused**: `syslog://`, the desktop
  notifiers (`dbus`, `kde`, `qt`, `glib`, `gnome`, `macosx`, `windows`), `evolution://`
  and `blink1://`, and a key file named by a path on the server (`keyfile=`, `subfile=`).
- **Waits are bounded.** A line's `rto=` and `cto=` can lower Apprise's socket timeouts but
  not raise them past ten seconds, one send is given up after 45 seconds, and one
  connection holds at most twenty URLs, so a slow endpoint cannot hold up other people's
  reminders.
- **Words are text.** A title or a body is sent as plain text and escaped for the services
  that send HTML (Telegram, email), so a captured page's title cannot arrive as markup.
- **Several connections or several lines?** Either. One connection with three URLs sends
  every chosen event to all three; two connections let you send reminders to the phone
  and captures to a chat.
- **Email stays built in.** Apprise has `mailto://`, and Postulo's own Email notifier is
  not going anywhere: the core works with no plugin installed.

## Translations

French and Portuguese ship with the package, in `src/postulo_apprise/locale/`. Postulo never translates a plugin's strings, and nobody translates this one in a commit:
every translation is made in Weblate, at
[translate.tiagoagueda.com](https://translate.tiagoagueda.com/projects/postulo/), which drafts
new strings by machine and sends what speakers save back as a pull request from its
`weblate` branch. A commit runs `uv run postulo-messages extract`, which leaves an empty slot
for a new string in every catalogue, and `uv run postulo-messages guard` in CI refuses one
that writes a translation (postulo/postulo#706). The `.mo` files are not committed:
`uv run postulo-messages compile` writes them, before the tests and before `uv build`.
`uv run pytest -m release` holds the twenty-four European Union catalogues complete before
a release.

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

Copyright (C) 2026 Postulo contributors. [AGPL-3.0-or-later](LICENSE), like Postulo. Apprise is
BSD-2-Clause.

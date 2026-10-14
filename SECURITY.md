# Security policy

## Supported versions

Repcut is pre-1.0. Only the latest commit on `main` receives fixes.

## Reporting a vulnerability

**Do not open a public issue with details of a vulnerability.**

Report it privately through GitHub: the repository's **Security** tab →
**Report a vulnerability**. If that option is unavailable, open an issue that
says only that you have a security report and asks for a private channel —
no details, no proof of concept.

Please include what an attacker can do, the steps to reproduce, and the commit
you tested against. Never include real footage, API keys or personal data in a
report.

You can expect an acknowledgement within a week. Fixes land on a branch and
merge through the normal gated process; the advisory is published once the
fix is on `main`.

## If you find a leaked secret

If you see a credential anywhere in this repository or its history, report it
the same way. It is treated as permanently compromised and rotated at the
provider; rewriting history is not considered a fix.

## Threat model in brief

Repcut is a single-user, local-first application with **no accounts and no
authentication**, by design. That is only safe because the boundary is drawn
elsewhere:

| Trusted | Hostile |
|---|---|
| The person at the keyboard | Every other web page open in their browser |
| Processes they started | Any other device on the network |
| Files they chose to upload | The *contents* of those files |
| The local disk | Anything read back from the database or `ffprobe` |

The engine therefore binds to loopback, enforces an exact `Host` allow-list
(DNS-rebinding defence), uses explicit CORS origins without credentials,
checks `Origin` on every WebSocket before accepting it, bounds every request
field and upload, builds every filesystem path through one store that refuses
anything outside `$DATA_DIR`, and only invokes FFmpeg through a builder that
validates its inputs. The UI ships a strict CSP with `frame-ancestors 'none'`.

In scope for reports: anything that lets a web page, another device, or a
crafted media file read footage, write outside `$DATA_DIR`, run commands,
make the engine send data off the machine, or exhaust the disk.

The full rules are in [`.claude/rules/security.md`](.claude/rules/security.md)
and [`.claude/rules/secrets.md`](.claude/rules/secrets.md).

## Privacy

Footage never leaves the machine. The only outbound data is one sampled frame
per detected scene (EXIF/GPS stripped) plus scene metadata, sent to the Gemini
API for scene understanding, cached so it is never sent twice, and disclosed
in the UI when it happens.

# amirhossein-danesh mail

A lightweight, **single-image, single-container self-hosted mail platform** combining [Stalwart](https://github.com/stalwartlabs/stalwart) with the modern [Bulwark Lite](https://github.com/bulwarkmail/webmail) webmail UI.

**Status: integration starter / not production-verified.** Static repository tests pass, but the image build, runtime, webmail activation and SMTP end-to-end tests must run before production use.

## One build, one process

- Mail engine: **Stalwart v0.16.26** — SMTP, IMAP, JMAP, identity, storage, administration.
- Modern web UI: **Bulwark Lite 1.14.2** — prebuilt static assets served *by Stalwart itself*.
- One OCI image, one final running container, no Node.js process, extra PostgreSQL or Redis.
- One-time registration of the bundled Bulwark zip as a Stalwart web Application after initial setup.
- Persistent Stalwart data/config volumes, DNS/mail ports kept private by default.
- Human-facing name: **amirhossein-danesh mail**; repository: **WebMail-Service**.

The build consumes pinned upstream release artifacts, not an unaudited source-code mashup. The Bulwark release ZIP is verified against a pinned SHA-256 during image build. The Stalwart image is pinned to a tag (production must additionally pin the OCI digest). The exact upstream source commits are recorded in `upstreams.lock.json`.

## Quick start (safe local bootstrap)

Requires Docker Engine with Compose and adequate memory/disk:

```bash
cp .env.example .env
docker compose build --pull
docker compose up -d
docker compose logs --tail=100 mail
```

By default only `127.0.0.1:8080` on the host is published. On a remote VPS use an SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 USER@VPS_IP
```

Open `http://127.0.0.1:8080/admin` to complete Stalwart setup. Read initial credentials from Stalwart logs and change them; never store passwords in Git.

### Enable modern webmail

In Stalwart Admin, navigate to **Settings → Web Applications**, create an Application:

| Field | Value |
|---|---|
| Description | Bulwark Lite |
| Resource URL | `file:///opt/webmail-service/bulwark-lite-stalwart.zip` |
| URL prefix | `/webmail` (map-style URL prefix field) |

Use **Actions → Update applications** (or restart Stalwart) to import the application. Check logs for errors. Then browse `http://127.0.0.1:8080/webmail/` locally, or after configuring HTTPS visit **https://mail.amirhossein-danesh.ir/webmail/**. The management UI is under **/admin**; admin access should be restricted separately at the reverse proxy.

After setup, add domain `amirhossein-danesh.ir` and create user `me@amirhossein-danesh.ir`.

### Public SMTP/IMAP is opt-in

Do NOT expose real mail ports until DNS, mail-provider permissions, PTR, DKIM, SPF, DMARC, TLS and anti-open-relay tests are complete:

```bash
docker compose -f compose.yaml -f compose.public.yaml up -d
```

The above override additionally publishes TCP 25, 587, and 993. Keep 80/443 on the host's existing HTTPS reverse proxy; do not route raw SMTP through an HTTP proxy.

See [deployment (Persian)](docs/deployment-fa.md), [architecture](docs/architecture.md), [security checklist](docs/security.md), and [third-party notices](NOTICE.md).

The existing VPS also hosts `mail.unilrn.ir` and `mail.kappaut.ir` on the same
mail engine. See [three-domain deployment and remaining delivery gates](docs/three-domains-fa.md).
For the current public SMTP/IMAP configuration, certificate renewal and provider
TCP 25/PTR blockers, see [Internet mail deployment](docs/internet-mail-fa.md).

## Audit and verification

```bash
./scripts/fetch-upstreams.sh   # clones exact tagged source and verifies Git SHAs
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
./scripts/check.sh             # needs Docker: checks, build, asset verification
```

Build CI checks a single image, asset presence, Compose validity and local HTTP health. Actual mail and webmail end-to-end production checks remain separate deployment gates. Back up **both** persistent volumes and verify restores.

The integration glue is offered under MIT. Stalwart and Bulwark retain their upstream **AGPL** terms; read [NOTICE.md](NOTICE.md) before redistribution or branded modification.

**Not in scope yet:** custom mail UI rewriting, tenant isolation, bulk transactional sending, multi-organization SaaS and automatic DNS changes. The goal is a small trustworthy personal instance first, extensible to additional domains later.

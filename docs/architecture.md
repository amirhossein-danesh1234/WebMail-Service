# Architecture

```text
mail.amirhossein-danesh.ir
         |
existing HTTPS reverse proxy on VPS (host :443)
         |
127.0.0.1:8080 -> [ ONE CONTAINER: Stalwart v0.16.26 ]
                              |              |
                         SMTP/IMAP/JMAP  /admin and /webmail
                              |              |
                         persistent      Bulwark Lite static
                         two volumes     embedded ZIP registered
                                         as a Web Application
```

## Decisions

**Single OCI image and runtime process** rather than a Node.js backend and a mail server coordinated through Supervisor. Bulwark Lite is a static bundle; Stalwart >= 0.16 serves its Web Applications directly. A one-time admin action registers the local archive.

**Artifact composition, not source-tree merging:** the Docker build downloads the pinned Bulwark release and checks its SHA256, then copies it into the pinned Stalwart image. Exact upstream source commits can be audited with the fetch script. This keeps build time and runtime dependencies low.

**Storage:** volumes `stalwart_config` and `stalwart_data` must survive container replacement. No extra PostgreSQL or Redis is required for the intended initial small instance.

**Trust boundaries:** admin shares the HTTP origin with the Webmail/JMAP API; restrict `/admin` at the reverse proxy and enforce separate strong credentials. Static JavaScript might access browser-held tokens. Review HTML sanitization, XSS, CSP, token storage, and API permissions before exposing publicly.

**Future growth:** JMAP allows a redesigned Webmail UI without replacing SMTP/IMAP. Support for several domains does not imply isolated organizational multi-tenancy. Bulk sending for Baazargaan requires separate deliverability, abuse control, reputation, and sender policy analysis.

## Known limitations

- Registration of Bulwark as an Application is currently a documented **manual bootstrap step**.
- No deployment or e2e SMTP validation is claimed in the source scaffold.
- Production needs immutable base image digest and vulnerability review.

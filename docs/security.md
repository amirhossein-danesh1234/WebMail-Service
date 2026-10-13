# Security audit checklist

This integration has **not** undergone a production security audit.

## Supply chain
- [x] Pinned Stalwart tag (`v0.16.26`).
- [x] Pinned Bulwark tag (`1.14.2`) plus build-time SHA-256 verification.
- [x] Recorded immutable source Git commit hashes.
- [ ] Pin Stalwart OCI digest and verify signatures/attestation.
- [ ] Generate SBOM; run Grype/Trivy and inspect critical advisories.
- [ ] Reassess licensing and obligations before redistributing modified bundles.

## Web/security boundaries
- [x] Host-only HTTP port by default; external mail ports opt-in.
- [x] `no-new-privileges`; drop extra capabilities except `NET_BIND_SERVICE`.
- [x] Persistent data/config volumes separated from image.
- [ ] Verify effective non-root Stalwart runtime UID with `docker inspect`.
- [ ] Strong credentials, MFA where available, restricted admin route.
- [ ] Set HTTPS/HSTS, no-sniff and carefully audited CSP.
- [ ] Review HTML email sanitization, attachment handling and session token storage.
- [ ] Test that unauthenticated SMTP users cannot relay mail.
- [ ] Mail rate limiting, abuse controls, log rotation and anomaly monitoring.

## Deliverability and operations
- [ ] SPF, DKIM, DMARC, MX, PTR, TLS/STARTTLS validated.
- [ ] Confirm provider outbound TCP 25 availability and IP reputation.
- [ ] Encrypt backups; test both volumes restore on a clean VPS.
- [ ] Conduct mail sender/recipient E2E tests and reverse proxy tests.
- [ ] Confirm SaaS tenant separation requirements before other organizations use the service.

**A single container does not make the admin UI safe to expose publicly.**

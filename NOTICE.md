# Third-party notices and source availability

The **amirhossein-danesh mail** repository is an integration/distribution project, NOT a rewrite or full source-tree fork of the two upstream applications. It builds a single image using pinned third-party release artifacts.

- **Stalwart Mail Server:** https://github.com/stalwartlabs/stalwart, tag `v0.16.26`, commit `18f4e9505027d790fd7e9fdad7f922be6719101d`. Upstream AGPL-3.0 / enterprise licensing applies; this integration uses the AGPL option. Source: https://github.com/stalwartlabs/stalwart/tree/v0.16.26
- **Bulwark Lite:** https://github.com/bulwarkmail/webmail, tag `1.14.2`, commit `5f2d26f2e1902b3010415f478615765299c6fd31`. Upstream AGPL-3.0-only applies. Source: https://github.com/bulwarkmail/webmail/tree/1.14.2
- Bulwark build artifact: `bulwark-lite-stalwart.zip` from that release. SHA-256: `3846b16d051168d8d7b22f2eed22c4a391ef382edf9c0fd0d3020040cf6eb6bf`.

The original glue/configuration files in this repository are MIT-licensed. That **does not** relicense upstream code or grant an exception to AGPL obligations for combined binaries, modifications, or distribution. Keep copyright notices, source offers and upstream license texts intact. Review the complete license text at https://www.gnu.org/licenses/agpl-3.0.txt.

To audit sources, use `./scripts/fetch-upstreams.sh`, which clones and verifies the exact upstream commits recorded in `upstreams.lock.json`. Images should be published with build provenance and dependency/SBOM review.

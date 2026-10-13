#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
docker compose config -q
docker compose build --pull
docker run --rm --entrypoint /bin/sh webmail-service:0.1.0 \
  -c 'test -s /opt/webmail-service/bulwark-lite-stalwart.zip && echo "Bundled webmail present"'
echo "Static tests and Docker build finished. Activation and SMTP end-to-end tests still required."

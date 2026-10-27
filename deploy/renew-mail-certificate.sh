#!/bin/sh
set -eu
# Certbot runs this for every lineage; other certificates remain independent.
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/webmail-mx ]; then
    /usr/bin/python3 /opt/webmail-service/scripts/configure-mail-tls.py
fi

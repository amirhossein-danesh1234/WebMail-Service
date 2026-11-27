#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/baazargaan-mx ]; then
    /usr/bin/python3 /opt/webmail-service/scripts/configure-baazargaan-mail.py --certificate-only
fi

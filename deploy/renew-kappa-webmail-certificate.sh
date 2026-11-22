#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/kappa-webmail-admin ]; then
    /usr/sbin/nginx -t
    /usr/bin/systemctl reload nginx
fi

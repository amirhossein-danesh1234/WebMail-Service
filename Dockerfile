# syntax=docker/dockerfile:1.7
# amirhossein-danesh mail: one image, one Stalwart runtime process.
FROM alpine:3.22 AS bulwark-artifact
ARG BULWARK_VERSION=1.14.2
ARG BULWARK_SHA256=3846b16d051168d8d7b22f2eed22c4a391ef382edf9c0fd0d3020040cf6eb6bf
RUN apk add --no-cache ca-certificates curl \
    && mkdir -p /out \
    && curl --proto '=https' --tlsv1.2 -fL --retry 3 \
      "https://github.com/bulwarkmail/webmail/releases/download/${BULWARK_VERSION}/bulwark-lite-stalwart.zip" \
       -o /out/bulwark-lite-stalwart.zip \
    && echo "${BULWARK_SHA256}  /out/bulwark-lite-stalwart.zip" | sha256sum -c -

FROM stalwartlabs/stalwart:v0.16.26@sha256:254ce5d7f3dbe4bb628ce2c7912e664ecb1c3466db79907f4a44d9a00117776b
LABEL org.opencontainers.image.title="amirhossein-danesh mail" \
      org.opencontainers.image.description="Single-container Stalwart + Bulwark Lite" \
      org.opencontainers.image.source="https://github.com/amirhossein-danesh1234/WebMail-Service" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
# Preserve upstream ENTRYPOINT, USER and healthcheck. Do not chmod the new directory to 0644.
COPY --from=bulwark-artifact --chown=2000:2000 \
    /out/bulwark-lite-stalwart.zip /opt/webmail-service/bulwark-lite-stalwart.zip

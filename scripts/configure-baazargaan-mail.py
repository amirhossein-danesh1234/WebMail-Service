#!/usr/bin/env python3
"""Provision Baazargaan on the shared Stalwart host; no global settings changed."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import urllib.request

DOMAIN = "baazargaan.com"
HOST = "mx." + DOMAIN
SELECTOR = "bz-rsa-20261127"
CERT = Path("/etc/letsencrypt/live/baazargaan-mx")
ROOT = Path("/root")


def call(method, args):
    request = urllib.request.Request(
        "http://127.0.0.1:8080/jmap/",
        data=json.dumps({"using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
                         "methodCalls": [[method, args, "a"]]}).encode(),
        headers={"Authorization": authorization, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        name, result, _ = json.load(response)["methodResponses"][0]
    for key in ("notCreated", "notUpdated"):
        if result.get(key):
            details = [(item.get("type"), item.get("properties")) for item in result[key].values()]
            raise RuntimeError(f"{method}: {details}")
    if name != method:
        raise RuntimeError(f"{method}: {result.get('type')}")
    return result


def configure_certificate():
    cert = {
        "certificate": {"@type": "Text", "value": (CERT / "fullchain.pem").read_text()},
        "privateKey": {"@type": "Text", "secret": (CERT / "privkey.pem").read_text()},
    }
    certificates = call("x:Certificate/get", {"properties": ["id", "subjectAlternativeNames"]})["list"]
    existing = next((item for item in certificates if HOST in item.get("subjectAlternativeNames", {})), None)
    change = {"update": {existing["id"]: cert}} if existing else {"create": {"baazargaan": cert}}
    call("x:Certificate/set", change)
    call("x:Action/set", {"create": {"reload": {"@type": "ReloadTlsCertificates"}}})
    print("Baazargaan TLS certificate loaded.")


def configure_mailbox():
    key = (ROOT / "baazargaan-mail-dkim" / f"{SELECTOR}.pem").read_text()
    path = ROOT / "webmail-baazargaan.com-credentials.json"
    domains = call("x:Domain/get", {"properties": ["id", "name", "isEnabled"]})["list"]
    domain = next((item for item in domains if item["name"] == DOMAIN), None)
    if domain is None:
        domain = call("x:Domain/set", {"create": {"baazargaan": {
            "name": DOMAIN, "isEnabled": True, "allowRelaying": False,
            "certificateManagement": {"@type": "Manual"},
            "dnsManagement": {"@type": "Manual"},
            "dkimManagement": {"@type": "Manual"},
        }}})["created"]["baazargaan"]
    domain_id = domain["id"]
    signatures = call("x:DkimSignature/get", {"properties": ["domainId", "selector"]})["list"]
    if not any(s.get("domainId") == domain_id and s.get("selector") == SELECTOR for s in signatures):
        call("x:DkimSignature/set", {"create": {"baazargaan": {
            "@type": "Dkim1RsaSha256", "domainId": domain_id, "selector": SELECTOR,
            "privateKey": {"@type": "Text", "secret": key}, "stage": "active",
        }}})
    accounts = call("x:Account/get", {"properties": ["id", "name", "domainId"]})["list"]
    if not any(a.get("name") == "info" and a.get("domainId") == domain_id for a in accounts):
        if not path.exists():
            with path.open("x", encoding="utf-8") as f:
                json.dump({"username": "info@" + DOMAIN, "secret": secrets.token_urlsafe(32)}, f)
        user = json.loads(path.read_text())
        call("x:Account/set", {"create": {"info": {
            "@type": "User", "name": "info", "domainId": domain_id,
            "description": "Baazargaan information mailbox", "roles": {"@type": "User"},
            "credentials": {"0": {"@type": "Password", "secret": user["secret"]}},
        }}})
    call("x:Action/set", {"create": {"reload": {"@type": "ReloadSettings"}}})
    print("Baazargaan domain, DKIM and info mailbox configured.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate-only", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    admin = json.loads((ROOT / "webmail-admin-credentials.json").read_text())
    authorization = "Basic " + base64.b64encode(
        (admin["username"] + ":" + admin["secret"]).encode()
    ).decode()
    configure_certificate()
    if not args.certificate_only:
        configure_mailbox()

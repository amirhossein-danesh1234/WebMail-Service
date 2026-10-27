#!/usr/bin/env python3
"""Add the three hosted mail domains using the existing private admin credential.

Run on the VPS after backing up both Stalwart volumes. Secrets stay in /root.
Existing domains, accounts and passwords are preserved. No SMTP ports are opened.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import urllib.request

DOMAINS = ("amirhossein-danesh.ir", "unilrn.ir", "kappaut.ir")
BASE = "http://127.0.0.1:8080"


def private_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle)
    os.chmod(path, 0o600)


def main():
    os.umask(0o077)
    credential = json.loads(Path("/root/webmail-admin-credentials.json").read_text())
    authorization = "Basic " + base64.b64encode(
        (credential["username"] + ":" + credential["secret"]).encode()
    ).decode()

    def call(method, arguments):
        payload = {"using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
                   "methodCalls": [[method, arguments, "a"]]}
        request = urllib.request.Request(
            BASE + "/jmap/", data=json.dumps(payload).encode(),
            headers={"Authorization": authorization, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=45) as response:
            name, result, _ = json.load(response)["methodResponses"][0]
        if name != method or any(result.get(key) for key in
                                  ("notCreated", "notUpdated", "notDestroyed")):
            raise RuntimeError("Management operation failed: " + method)
        return result

    domains = {item["name"]: item for item in call("x:Domain/get", {})["list"]}
    for name in DOMAINS:
        if name not in domains:
            call("x:Domain/set", {"create": {"domain": {
                "name": name, "isEnabled": True, "allowRelaying": False,
                "certificateManagement": {"@type": "Manual"},
                "dnsManagement": {"@type": "Manual"},
                "dkimManagement": {"@type": "Automatic"}}}})
    domains = {item["name"]: item for item in call("x:Domain/get", {})["list"]}
    # Public SPA client: only these exact HTTPS callbacks are accepted.
    callbacks = {"https://mail." + domain + suffix: True for domain in DOMAINS
                 for suffix in ("/webmail/", "/webmail/oauth/callback")}
    clients = call("x:OAuthClient/get", {})["list"]
    client = next((c for c in clients if c["clientId"] == "bulwark-webmail"), None)
    if client:
        callbacks = {**client.get("redirectUris", {}), **callbacks}
        call("x:OAuthClient/set", {"update": {client["id"]: {"redirectUris": callbacks}}})
    else:
        call("x:OAuthClient/set", {"create": {"webmail": {
            "clientId": "bulwark-webmail", "description": "Hosted Bulwark webmail",
            "redirectUris": callbacks}}})
    # Request public account fields only; never export existing credentials.
    accounts = call("x:Account/get", {"properties": ["id", "name", "domainId"]})["list"]
    for name in ("unilrn.ir", "kappaut.ir"):
        domain_id = domains[name]["id"]
        if any(a.get("name") == "me" and a.get("domainId") == domain_id for a in accounts):
            print(name + ": existing mailbox preserved")
            continue
        path = Path("/root/webmail-" + name + "-credentials.json")
        if path.exists():
            user = json.loads(path.read_text())
        else:
            user = {"username": "me@" + name, "secret": secrets.token_urlsafe(32)}
            private_json(path, user)
        call("x:Account/set", {"create": {"mailbox": {
            "@type": "User", "name": "me", "domainId": domain_id,
            "description": "Site owner mailbox", "roles": {"@type": "User"},
            "credentials": [{"@type": "Password", "secret": user["secret"]}]}}})
        print(name + ": owner mailbox created")
    for name, domain in domains.items():
        if name in DOMAINS:
            path = Path("/root/webmail-" + name + "-dns-zone.txt")
            path.write_text(domain["dnsZoneFile"], encoding="utf-8")
            os.chmod(path, 0o600)
    print("Three mail domains and restricted webmail OAuth callbacks configured.")


if __name__ == "__main__":
    main()

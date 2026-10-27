#!/usr/bin/env python3
"""Import the host Certbot certificate and configure secure mail listeners.

Run as root on the VPS. Used by Certbot's deploy hook on renewal as well.
Certificate private material remains on the VPS and is never logged.
"""
import base64
import json
import os
from pathlib import Path
import urllib.request

HOST = "mx.amirhossein-danesh.ir"
PUBLIC_URL = "https://mail.amirhossein-danesh.ir"
CERTIFICATE = Path("/etc/letsencrypt/live/webmail-mx")


def main():
    os.umask(0o077)
    credential = json.loads(Path("/root/webmail-admin-credentials.json").read_text())
    authorization = "Basic " + base64.b64encode(
        (credential["username"] + ":" + credential["secret"]).encode()).decode()

    def call(method, args):
        data = {"using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
                "methodCalls": [[method, args, "a"]]}
        request = urllib.request.Request(
            "http://127.0.0.1:8080/jmap/", data=json.dumps(data).encode(),
            headers={"Authorization": authorization, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=45) as response:
            name, result, _ = json.load(response)["methodResponses"][0]
        if name != method or any(result.get(k) for k in ("notCreated", "notUpdated")):
            raise RuntimeError("Mail configuration failed: " + method)
        return result

    certificate = {
        "certificate": {"@type": "Text", "value": (CERTIFICATE / "fullchain.pem").read_text()},
        "privateKey": {"@type": "Text", "secret": (CERTIFICATE / "privkey.pem").read_text()}}
    existing = call("x:Certificate/get", {
        "properties": ["id", "subjectAlternativeNames"]})["list"]
    previous = next((c for c in existing if HOST in c.get("subjectAlternativeNames", {})), None)
    if previous:
        certificate_id = previous["id"]
        call("x:Certificate/set", {"update": {certificate_id: certificate}})
    else:
        certificate_id = call("x:Certificate/set", {
            "create": {"mail": certificate}})["created"]["mail"]["id"]
    settings = call("x:SystemSettings/get", {"ids": ["singleton"]})["list"][0]
    services = settings["services"]
    for protocol in ("imap", "smtp", "pop3", "managesieve"):
        services[protocol] = {"hostname": HOST, "cleartext": protocol == "smtp"}
    for protocol in ("jmap", "caldav", "carddav", "webdav"):
        services[protocol] = {"hostname": "mail.amirhossein-danesh.ir", "cleartext": False}
    call("x:SystemSettings/set", {"update": {"singleton": {
        "defaultHostname": HOST, "defaultCertificateId": certificate_id,
        "mailExchangers": {"0": {"hostname": HOST, "priority": 10}}, "services": services}}})
    listeners = call("x:NetworkListener/get", {})["list"]
    submission = next((v for v in listeners if v["name"] == "submission"), None)
    value = {"bind": {"[::]:587": True}, "protocol": "smtp", "useTls": True,
             "tlsImplicit": False}
    if submission:
        call("x:NetworkListener/set", {"update": {submission["id"]: value}})
    else:
        call("x:NetworkListener/set", {"create": {"submission": {"name": "submission", **value}}})
    # Require authentication on submission ports and prevent sender spoofing.
    call("x:MtaStageAuth/set", {"update": {"singleton": {
        "require": {"else": "local_port != 25", "match": {}},
        "mustMatchSender": {"else": "true", "match": {}}}}})
    call("x:MtaStageRcpt/set", {"update": {"singleton": {
        "allowRelaying": {"else": "!is_empty(authenticated_as)", "match": {}}}}})
    call("x:Action/set", {"create": {"reload": {"@type": "ReloadSettings"}}})
    call("x:Action/set", {"create": {"reload": {"@type": "ReloadTlsCertificates"}}})
    print("Valid mail certificate imported; hostname, listeners and relay policy configured.")


if __name__ == "__main__":
    main()

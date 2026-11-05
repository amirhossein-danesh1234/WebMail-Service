#!/usr/bin/env python3
"""Plan/apply scoped SMTP hardening to Stalwart 0.16; never changes DNS or mailboxes."""
import argparse
import base64
import json
import os
from pathlib import Path
import urllib.request

HOSTNAME = "mx.amirhossein-danesh.ir"
OBJECTS = ("MtaConnectionStrategy", "MtaOutboundStrategy", "MtaInboundThrottle")


def build_plan(snapshot):
    """Leave routes, queues, existing throttles and accounts unchanged."""
    connection = next((x for x in snapshot["MtaConnectionStrategy"] if x.get("name") == "default"), None)
    if not connection:
        raise ValueError("default_connection_strategy_missing")
    plan = []
    if connection.get("ehloHostname") != HOSTNAME:
        plan.append(("MtaConnectionStrategy", {"update": {connection["id"]: {"ehloHostname": HOSTNAME}}}))
    strategy = next((x for x in snapshot["MtaOutboundStrategy"] if x.get("id") == "singleton"), None)
    if not strategy:
        raise ValueError("outbound_strategy_missing")
    tls = {"match": {}, "else": "'default'"}
    if strategy.get("tls") != tls:
        plan.append(("MtaOutboundStrategy", {"update": {"singleton": {"tls": tls}}}))
    for description, count, period in (
        ("Authenticated sender: 60 MAIL FROM per minute", 60, 60000),
        ("Authenticated sender: 1000 MAIL FROM per day", 1000, 86400000),
    ):
        value = {"enable": True, "description": description,
                 "key": {"authenticatedAs": True},
                 "match": {"match": {}, "else": "!is_empty(authenticated_as)"},
                 "rate": {"count": count, "period": period}}
        existing = next((x for x in snapshot["MtaInboundThrottle"] if x.get("description") == description), None)
        if existing:
            if any(existing.get(k) != v for k, v in value.items()):
                plan.append(("MtaInboundThrottle", {"update": {existing["id"]: value}}))
        else:
            plan.append(("MtaInboundThrottle", {"create": {"sender-limit": value}}))
    return plan


def api_call(name, args):
    credentials = json.loads(Path("/root/webmail-admin-credentials.json").read_text())
    authorization = "Basic " + base64.b64encode(
        (credentials["username"] + ":" + credentials["secret"]).encode()).decode()
    payload = {"using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
               "methodCalls": [[name, args, "a"]]}
    request = urllib.request.Request("http://127.0.0.1:8080/jmap/",
                                    data=json.dumps(payload).encode(),
                                    headers={"Authorization": authorization, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        method, result, _ = json.load(response)["methodResponses"][0]
    if method != name or any(result.get(k) for k in ("notCreated", "notUpdated", "notDestroyed")):
        raise RuntimeError("management_operation_failed:" + name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Default is read-only planning")
    parser.add_argument("--backup-dir", type=Path, help="Existing private cold-backup directory required for --apply")
    args = parser.parse_args()
    snapshot = {name: api_call("x:" + name + "/get", {"ids": ["singleton"]} if name == "MtaOutboundStrategy" else {})["list"] for name in OBJECTS}
    # This change must not accidentally start accepting unauthenticated relay or sender spoofing.
    auth = api_call("x:MtaStageAuth/get", {"ids": ["singleton"]})["list"][0]
    rcpt = api_call("x:MtaStageRcpt/get", {"ids": ["singleton"]})["list"][0]
    tls = api_call("x:MtaTlsStrategy/get", {})["list"]
    default_tls = next(x for x in tls if x.get("name") == "default")
    if (default_tls.get("allowInvalidCerts") is not False
            or auth.get("require", {}).get("else") != "local_port != 25"
            or auth.get("mustMatchSender", {}).get("else") != "true"
            or rcpt.get("allowRelaying", {}).get("else") != "!is_empty(authenticated_as)"):
        raise RuntimeError("security_baseline_requires_review")
    plan = build_plan(snapshot)
    if args.apply:
        backup = args.backup_dir
        if (backup is None or not backup.is_absolute() or not backup.is_dir()
                or not (backup / "stalwart-cold-backup.tar.gz").is_file()
                or backup.stat().st_mode & 0o077):
            raise RuntimeError("private_cold_backup_required")
        os.umask(0o077)
        # Exclusive file creation preserves the original rollback snapshot on reruns.
        target = backup / "direct-mail-settings-before.json"
        if not target.exists():
            with target.open("x", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2)
        for name, change in plan:
            api_call("x:" + name + "/set", change)
        if plan:
            api_call("x:Action/set", {"create": {"reload": {"@type": "ReloadSettings"}}})
        after = {name: api_call("x:" + name + "/get", {"ids": ["singleton"]} if name == "MtaOutboundStrategy" else {})["list"] for name in OBJECTS}
        if build_plan(after):
            raise RuntimeError("configuration_verification_failed")
    print(json.dumps({"mode": "applied" if args.apply else "plan", "operations": len(plan),
                      "objects": [name for name, _ in plan], "dns_changed": False,
                      "mailboxes_changed": False, "queues_deleted": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Direct mail configuration failed; sensitive upstream output suppressed.")

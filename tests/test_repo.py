"""Offline repository invariants. Docker and SMTP E2E tests run separately."""
from pathlib import Path
import json
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "/opt/webmail-service/bulwark-lite-stalwart.zip"
DIGEST = "3846b16d051168d8d7b22f2eed22c4a391ef382edf9c0fd0d3020040cf6eb6bf"


class RepositorySafetyTests(unittest.TestCase):
    def test_dockerfile_pins_upstream_versions_and_hash(self):
        text = (ROOT / "Dockerfile").read_text()
        self.assertIn("stalwartlabs/stalwart:v0.16.26", text)
        self.assertIn("ARG BULWARK_VERSION=1.14.2", text)
        self.assertIn(DIGEST, text)
        self.assertIn("sha256sum -c -", text)
        self.assertIn(BUNDLE, text)
        self.assertNotRegex(text, r"(?m)^\s*(CMD|ENTRYPOINT)\s")

    def test_compose_runs_one_service_and_uses_persistent_volumes(self):
        compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
        self.assertEqual(set(compose["services"]), {"mail"})
        mail = compose["services"]["mail"]
        self.assertEqual(mail["build"]["context"], ".")
        self.assertEqual(len(mail["volumes"]), 2)
        self.assertEqual(mail["ports"], ["127.0.0.1:8080:8080"])
        self.assertEqual(mail["security_opt"], ["no-new-privileges:true"])

    def test_public_ports_must_be_explicit_opt_in(self):
        compose = yaml.safe_load((ROOT / "compose.public.yaml").read_text())
        self.assertSetEqual(set(compose["services"]["mail"]["ports"]), {
            "25:25", "587:587", "993:993"
        })

    def test_docs_register_local_verified_bundle(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("file://" + BUNDLE, readme)
        self.assertIn("Update applications", readme)
        self.assertIn("not production-verified", readme)
        notices = (ROOT / "NOTICE.md").read_text()
        self.assertIn(DIGEST, notices)
        self.assertIn("AGPL", notices)

    def test_upstream_lock_matches_image_and_fetch_script(self):
        versions = json.loads((ROOT / "upstreams.lock.json").read_text())
        script = (ROOT / "scripts/fetch-upstreams.sh").read_text()
        docker = (ROOT / "Dockerfile").read_text()
        self.assertIn(versions["stalwart"]["runtime_image"], docker)
        self.assertIn(versions["stalwart"]["commit"], script)
        self.assertIn(versions["bulwark_lite"]["commit"], script)
        self.assertIn(versions["bulwark_lite"]["release_sha256"], docker)

    def test_repo_does_not_contain_secrets_or_bundled_third_party_code(self):
        for path in ROOT.rglob("*"):
            if not path.is_file() or "/.git/" in str(path):
                continue
            if path.suffix in {".zip", ".key", ".pem", ".p12"} or path.name == ".env":
                self.fail(f"Secret or vendored artifact is included: {path}")


if __name__ == "__main__":
    unittest.main()

import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("direct_mail", Path(__file__).parents[1] / "scripts/configure-direct-mail.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DirectMailPlanTests(unittest.TestCase):
    def fixture(self):
        return {"MtaConnectionStrategy": [{"id": "connection", "name": "default", "ehloHostname": None}],
                "MtaOutboundStrategy": [{"id": "singleton", "route": {"else": "'mx'"}, "tls": {"else": "'invalid-tls'"}}],
                "MtaInboundThrottle": [{"id": "original", "description": "original", "rate": {"count": 25, "period": 3600000}}]}

    def test_scoped_plan_preserves_inputs_and_routes(self):
        state = self.fixture()
        original = copy.deepcopy(state)
        plan = module.build_plan(state)
        self.assertEqual(state, original)
        self.assertEqual(len(plan), 4)
        self.assertEqual(plan[0][1]["update"]["connection"], {"ehloHostname": module.HOSTNAME})
        self.assertEqual(plan[1][1]["update"]["singleton"], {"tls": {"match": {}, "else": "'default'"}})
        for _, operation in plan:
            self.assertNotIn("destroy", operation)

    def test_repeated_plan_is_empty_after_apply(self):
        state = self.fixture()
        for kind, operation in module.build_plan(state):
            for key, value in operation.get("update", {}).items():
                next(x for x in state[kind] if x["id"] == key).update(value)
            for key, value in operation.get("create", {}).items():
                state[kind].append({"id": key + str(len(state[kind])), **value})
        self.assertEqual(module.build_plan(state), [])
        self.assertEqual(state["MtaInboundThrottle"][0]["id"], "original")

    def test_limits_are_per_authenticated_sender_only(self):
        rules = [op["create"]["sender-limit"] for kind, op in module.build_plan(self.fixture()) if "create" in op]
        self.assertEqual([r["rate"]["count"] for r in rules], [60, 1000])
        for rule in rules:
            self.assertEqual(rule["key"], {"authenticatedAs": True})
            self.assertEqual(rule["match"]["else"], "!is_empty(authenticated_as)")

    def test_missing_connection_fails_before_changes(self):
        state = self.fixture()
        state["MtaConnectionStrategy"] = []
        with self.assertRaisesRegex(ValueError, "default_connection"):
            module.build_plan(state)

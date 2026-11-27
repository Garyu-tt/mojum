"""Offline regression tests for PFD dispatcher."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pfd_accept", Path(__file__).resolve().parents[1] / "tools" / "pfd_accept.py")
pfd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pfd)

REPO = "Garyu-tt/mojum"
CHECKS = {"Plugin CI / contract": 123, "Plugin CI / scope": 123}

def pr():
    return dict(number=1, headRefOid="a"*40, headRefName="dev/demo/test",
                baseRefName="plugin/demo", isDraft=False, isCrossRepository=False,
                headRepository={"nameWithOwner": REPO}, reviewDecision="APPROVED",
                mergeStateStatus="CLEAN", statusCheckRollup=[
                    dict(workflowName="Plugin CI", name=n, status="COMPLETED", conclusion="SUCCESS")
                    for n in ("contract", "scope")])

def protection():
    return dict(required_status_checks={"strict":True, "contexts":["contract","scope"],
                 "checks":[{"context":n,"app_id":123} for n in ("contract","scope")]},
                required_pull_request_reviews={"required_approving_review_count":1,
                "dismiss_stale_reviews":True,"require_last_push_approval":True},
                enforce_admins={"enabled":True})

class AcceptanceTest(unittest.TestCase):
    def test_candidate(self):
        self.assertEqual(pfd.check_pr(pr(),REPO,"demo",CHECKS),"candidate")

    def test_blockers(self):
        for key,value in [("isDraft",True),("isCrossRepository",True),
                          ("baseRefName","main"),("headRefOid","a"),
                          ("headRefName","dev/other/x"),("reviewDecision","REVIEW_REQUIRED"),
                          ("mergeStateStatus","BEHIND"),("headRepository",{"nameWithOwner":"other/repo"})]:
            with self.subTest(key=key):
                self.assertNotEqual(pfd.check_pr(dict(pr(),**{key:value}),REPO,"demo",CHECKS),"candidate")

    def test_missing_failed_duplicated_checks(self):
        for kind in ("missing","failure","skipped","duplicate"):
            v=pr()
            if kind=="missing": v["statusCheckRollup"].pop()
            elif kind=="duplicate": v["statusCheckRollup"].append(v["statusCheckRollup"][0])
            else: v["statusCheckRollup"][0]["conclusion"]=kind.upper()
            self.assertNotEqual(pfd.check_pr(v,REPO,"demo",CHECKS),"candidate")

    def test_protection(self):
        self.assertTrue(pfd.check_protection(protection(),CHECKS))
        for key in ("required_status_checks","required_pull_request_reviews","enforce_admins"):
            v=protection()
            v.pop(key)
            self.assertFalse(pfd.check_protection(v,CHECKS))
        v=protection()
        v["required_status_checks"]["checks"][0]["app_id"]=999
        self.assertFalse(pfd.check_protection(v,CHECKS))
        v=protection()
        v["required_status_checks"]["strict"]=False
        self.assertFalse(pfd.check_protection(v,CHECKS))

    def test_read_only_and_apply_disabled(self):
        with patch.object(pfd,"gh",return_value=protection()), patch.object(pfd.subprocess,"run") as run:
            self.assertEqual(pfd.inspect(REPO,"demo",CHECKS,pr(),False),"candidate-only-not-authorized")
            run.assert_not_called()
            self.assertEqual(pfd.inspect(REPO,"demo",CHECKS,pr(),True),"apply-disabled")
            run.assert_not_called()

    def test_apply_rejected_before_config_or_process(self):
        with patch.object(pfd.sys, "argv", ["pfd_accept.py", "--apply"]), \
             patch.object(pfd.Path, "read_text") as read, \
             patch.object(pfd.subprocess, "check_output") as output, \
             patch.object(pfd.subprocess, "run") as run, \
             patch.object(pfd.subprocess, "Popen") as popen, \
             patch.object(pfd.sys, "stderr"):
            with self.assertRaises(SystemExit) as exc:
                pfd.main()
            self.assertEqual(exc.exception.code, 2)
            for operation in (read, output, run, popen):
                operation.assert_not_called()

    def test_apply_inspect_short_circuits_before_validation(self):
        with patch.object(pfd, "check_pr") as check, \
             patch.object(pfd, "gh") as gh:
            self.assertEqual(pfd.inspect(None, None, None, None, True), "apply-disabled")
            check.assert_not_called()
            gh.assert_not_called()

    def test_cli_candidate_only_uses_read_commands(self):
        config = {"repository": REPO, "plugins": {"demo": {"checks": CHECKS}}}
        responses = [pfd.json.dumps([{"number": 1}]), pfd.json.dumps(pr()),
                     pfd.json.dumps(protection())]
        with patch.object(pfd.sys, "argv", ["pfd_accept.py"]), \
             patch.object(pfd.Path, "read_text", return_value=pfd.json.dumps(config)), \
             patch.object(pfd.subprocess, "check_output", side_effect=responses) as output, \
             patch.object(pfd.subprocess, "run") as run, \
             patch.object(pfd.subprocess, "Popen") as popen, \
             patch("builtins.print") as printed:
            self.assertEqual(pfd.main(), 0)
            self.assertEqual([call.args[0] for call in output.call_args_list], [
                ["gh", "pr", "list", "-R", REPO, "--state", "open",
                 "--base", "plugin/demo", "--limit", "100", "--json", "number"],
                ["gh", "pr", "view", "1", "-R", REPO, "--json",
                 "number,headRefOid,headRefName,baseRefName,isDraft,isCrossRepository,"
                 "headRepository,reviewDecision,mergeStateStatus,statusCheckRollup"],
                ["gh", "api", "repos/Garyu-tt/mojum/branches/plugin%2Fdemo/protection"],
            ])
            self.assertEqual([call.kwargs for call in output.call_args_list],
                             [{"text": True, "timeout": 60}] * 3)
            self.assertEqual(pfd.json.loads(printed.call_args.args[0])["result"],
                             "candidate-only-not-authorized")
            run.assert_not_called()
            popen.assert_not_called()

    def test_no_merge_without_protection(self):
        with patch.object(pfd,"gh",return_value={}), patch.object(pfd.subprocess,"run") as run:
            self.assertEqual(pfd.inspect(REPO,"demo",CHECKS,pr(),False),"branch-protection-not-verified")
            run.assert_not_called()

    def test_config(self):
        self.assertEqual(pfd.validate({"repository":REPO,"plugins":{}})["plugins"],{})
        for checks in ({},{"Plugin CI / *":123},{"Plugin CI / scope":-1}):
            with self.assertRaises(ValueError):
                pfd.validate({"repository":REPO,"plugins":{"demo":{"checks":checks}}})

if __name__=="__main__":
    unittest.main()

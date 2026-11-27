import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("audit",Path(__file__).resolve().parents[1]/"tools/pfd_migration_audit.py")
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def fixture():
    repo="owner/repo"
    head={"ref":"dev/demo/r1","sha":"a"*40,"repo":{"full_name":repo}}
    base={"ref":"plugin/demo","sha":"b"*40,"repo":{"full_name":repo}}
    pr={"number":1,"state":"open","draft":False,"head":head,"base":base,"mergeable":True,"changed_files":1,"user":{"login":"author"}}
    lane={"pr":1,"repo":repo,"base":base["ref"],"head":head["ref"],"candidate_sha":head["sha"],"base_sha":base["sha"],"allowed_files":["plugin.py"],"checks":["contract"]}
    files=[{"filename":"plugin.py"}]
    runs=[{"name":"contract","app":{"id":15368},"status":"completed","conclusion":"success"}]
    reviews=[{"state":"APPROVED","commit_id":head["sha"],"user":{"login":"reviewer"}}]
    return pr,lane,files,runs,reviews

class MigrationAuditTests(unittest.TestCase):
    def test_never_ready_even_with_nominal_checks(self):
        result=audit.evaluate(*fixture(),True)
        self.assertEqual(result["decision"],"HOLD")
        self.assertIn("trusted-ci-provenance-not-verified",result["blockers"])
    def test_drift_scope_and_protection(self):
        pr,lane,files,runs,reviews=fixture()
        pr["head"]["sha"]="c"*40
        files[0]["filename"]="other.py"
        result=audit.evaluate(pr,lane,files,runs,reviews,False)
        for key in ("pin-changed-reapproval-required","scope-unverified","head-review-missing","target-protection-not-observed"):
            self.assertIn(key,result["blockers"])
    def test_duplicates_and_wrong_app(self):
        pr,lane,files,runs,reviews=fixture()
        for bad in ([runs[0],runs[0]],[dict(runs[0],app={"id":1})],[]):
            self.assertIn("check-unverified:contract",audit.evaluate(pr,lane,files,bad,reviews,True)["blockers"])
    def test_paginated_shape(self):
        self.assertEqual(audit.flatten([[1],[2]]),[1,2])
        with self.assertRaises(ValueError): audit.flatten({"items":[]})

    def test_reject_unexpected_api_target(self):
        with patch.object(audit,"get_page") as fetch:
            for repo,suffix in (("x/..","pulls/1"),("x/y","../secrets"),("x/y","pulls/1?redirect=https://evil.invalid"),("x/y","https://evil.invalid")):
                with self.assertRaises(ValueError):
                    audit.api(repo,suffix)
            fetch.assert_not_called()
    def test_pagination_does_not_follow_foreign_link(self):
        with patch.object(audit,"get_page",side_effect=[([1],'<https://evil.invalid>; rel="next"'),([2],"")]) as fetch:
            self.assertEqual(audit.api("x/y","pulls/1/files?per_page=100",True),[[1],[2]])
            self.assertEqual(fetch.call_args_list[1].args[0],"https://api.github.com/repos/x/y/pulls/1/files?per_page=100&page=2")
    def test_redirect_rejected(self):
        with self.assertRaises(ValueError):
            audit.NoRedirect().redirect_request(None,None,302,"",{},"https://evil.invalid")
    def test_pagination_budget(self):
        with patch.object(audit,"MAX_PAGES",2), patch.object(audit,"get_page",return_value=([],'rel="next"')):
            with self.assertRaisesRegex(ValueError,"pagination budget"):
                audit.api("x/y","branches?protected=true&per_page=100",True)

    def test_observed_run_correlation_is_not_trust(self):
        pr,lane,files,runs,reviews=fixture()
        sha="d"*40
        pr["merge_commit_sha"]=sha
        lane["observed_run"]={"id":9,"workflow":".github/workflows/test.yml","merge_sha":sha,"test_step":"Tests"}
        run={"id":9,"path":".github/workflows/test.yml","event":"pull_request","head_sha":"a"*40,
             "status":"completed","conclusion":"success","pull_requests":[{"number":1,
             "head":{"sha":"a"*40},"base":{"sha":"b"*40}}]}
        jobs=[{"name":"contract","run_id":9,"head_sha":"a"*40,"status":"completed",
               "conclusion":"success","steps":[{"name":"Tests","conclusion":"success"}]}]
        merge={"sha":sha,"parents":[{"sha":"b"*40},{"sha":"a"*40}]}
        good=audit.observe_run(pr,lane,run,jobs,merge)
        self.assertTrue(good["metadata_consistent"])
        self.assertFalse(good["trust_verified"])
        for bad in (dict(run,event="push"),dict(run,head_sha="e"*40),dict(run,path="other.yml")):
            self.assertFalse(audit.observe_run(pr,lane,bad,jobs,merge)["metadata_consistent"])
        self.assertFalse(audit.observe_run(pr,lane,run,jobs+jobs,merge)["metadata_consistent"])
        self.assertFalse(audit.observe_run(pr,lane,run,jobs,dict(merge,parents=[]))["metadata_consistent"])
        self.assertFalse(audit.observe_run(pr,lane,run,[dict(jobs[0],steps=[])],merge)["metadata_consistent"])

        lane["observed_run"]["job_steps"]={"contract":["Tests","Unit regressions"]}
        self.assertFalse(audit.observe_run(pr,lane,run,jobs,merge)["metadata_consistent"])
        jobs[0]["steps"].append({"name":"Unit regressions","conclusion":"success"})
        self.assertTrue(audit.observe_run(pr,lane,run,jobs,merge)["metadata_consistent"])
        jobs[0]["steps"][1]["conclusion"]="skipped"
        self.assertFalse(audit.observe_run(pr,lane,run,jobs,merge)["metadata_consistent"])

import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("audit",Path(__file__).resolve().parents[1]/"tools/pfd_migration_audit.py")
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def fixture():
    repo="owner/repo"
    head={"ref":"dev/demo/r1","sha":"a"*40,"repo":{"full_name":repo}}
    base={"ref":"plugin/demo","sha":"b"*40,"repo":{"full_name":repo}}
    pr={"number":1,"state":"open","draft":False,"head":head,"base":base,"mergeable":True,"changed_files":1,"user":{"login":"author"}}
    lane={"repo":repo,"base":base["ref"],"head":head["ref"],"candidate_sha":head["sha"],"base_sha":base["sha"],"allowed_files":["plugin.py"],"checks":["contract"]}
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

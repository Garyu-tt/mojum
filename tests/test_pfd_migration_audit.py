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
    runs=[{"id":1,"name":"contract","app":{"id":15368},"status":"completed","conclusion":"success"}]
    reviews=[{"state":"APPROVED","commit_id":head["sha"],"user":{"login":"reviewer"}}]
    return pr,lane,files,runs,reviews

class MigrationAuditTests(unittest.TestCase):
    def test_never_ready_even_with_nominal_checks(self):
        result=audit.evaluate(*fixture(),True)
        self.assertEqual(result["decision"],"HOLD")
        self.assertIn("trusted-ci-provenance-not-verified",result["blockers"])
    def test_draft_is_explicit_hold_not_identity_mismatch(self):
        pr,lane,files,runs,reviews=fixture()
        pr["draft"]=True
        result=audit.evaluate(pr,lane,files,runs,reviews,True)
        self.assertEqual(result["decision"],"HOLD")
        self.assertIn("draft-not-ready",result["blockers"])
        self.assertNotIn("unexpected-pr-identity",result["blockers"])
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
    def test_deep_json_response_refused_without_aborting_later_lane(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        from unittest.mock import MagicMock
        cfg={"repository":"owner/repo","lanes":[{"pr":1},{"pr":2}]}
        stream=io.StringIO()
        opener=MagicMock()
        response=opener.open.return_value.__enter__.return_value
        # CPython 3.13's C decoder may exceed the Python recursion limit.
        depth=max(20000,sys.getrecursionlimit()+100)
        response.read.side_effect=[b"["*depth+b"0"+b"]"*depth,b'{"pr":2,"decision":"HOLD"}']
        response.headers={}
        def inspect(repo,lane):
            return audit.get_page("https://api.github.com/repos/owner/repo/pulls/"+str(lane["pr"]))[0]
        with patch.object(sys,"argv",["audit"]), patch.object(Path,"read_text",return_value=json.dumps(cfg)), patch.object(audit,"build_opener",return_value=opener), patch.object(audit,"audit",side_effect=inspect), redirect_stdout(stream):
            self.assertEqual(audit.main(),1)
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([(row["pr"],row["decision"]) for row in rows],[(1,"HOLD"),(2,"HOLD")])
        self.assertIn("JSON nesting",rows[0]["error"])
        self.assertEqual(response.read.call_count,2)

    def test_http_protocol_errors_do_not_abort_later_lane(self):
        import io
        import json
        import sys
        from http.client import HTTPResponse
        from contextlib import redirect_stdout

        class Socket:
            def __init__(self, raw):
                self.raw = raw
            def makefile(self, *args, **kwargs):
                return io.BytesIO(self.raw)

        class Opener:
            def __init__(self, raw):
                self.responses = iter(raw)
            def open(self, *args, **kwargs):
                response = HTTPResponse(Socket(next(self.responses)))
                response.begin()
                return response

        cfg = {"repository":"owner/repo","lanes":[{"pr":1},{"pr":2}]}
        valid = b'{"pr":2,"decision":"HOLD"}'
        good = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(valid)).encode() + b"\r\n\r\n" + valid
        failures = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\n{}",
                    b"private-invalid-status\r\n")
        def inspect(repo, lane):
            return audit.get_page("https://api.github.com/repos/owner/repo/pulls/"+str(lane["pr"]))[0]
        for bad in failures:
            with self.subTest(response=bad):
                stream = io.StringIO()
                opener = Opener([bad, good])
                with patch.object(sys,"argv",["audit"]), patch.object(Path,"read_text",return_value=json.dumps(cfg)), patch.object(audit,"build_opener",return_value=opener), patch.object(audit,"audit",side_effect=inspect) as inspect_lane, redirect_stdout(stream):
                    self.assertEqual(audit.main(),1)
                self.assertEqual(inspect_lane.call_count,2)
                rows = [json.loads(line) for line in stream.getvalue().splitlines()]
                self.assertEqual(rows,[{"pr":1,"decision":"HOLD","error":"GitHub HTTP protocol error"},{"pr":2,"decision":"HOLD"}])

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
               "conclusion":"success","steps":[{"name":"Tests","status":"completed","conclusion":"success"}]}]
        merge={"sha":sha,"parents":[{"sha":"b"*40},{"sha":"a"*40}]}
        good=audit.observe_run(pr,lane,run,jobs,merge)
        self.assertTrue(good["metadata_consistent"])
        self.assertFalse(good["trust_verified"])
        for status in ("queued", "in_progress", "pending", None, "unknown"):
            with self.subTest(step_status=status):
                step = dict(jobs[0]["steps"][0])
                if status is None:
                    step.pop("status")
                else:
                    step["status"] = status
                report = audit.observe_run(pr,lane,run,[dict(jobs[0],steps=[step])],merge)
                self.assertFalse(report["metadata_consistent"])
                self.assertIn("job-metadata-mismatch:contract",report["reasons"])
                self.assertFalse(report["trust_verified"])
        for bad in (dict(run,event="push"),dict(run,head_sha="e"*40),dict(run,path="other.yml")):
            self.assertFalse(audit.observe_run(pr,lane,bad,jobs,merge)["metadata_consistent"])
        self.assertFalse(audit.observe_run(pr,lane,run,jobs+jobs,merge)["metadata_consistent"])
        self.assertFalse(audit.observe_run(pr,lane,run,jobs,dict(merge,parents=[]))["metadata_consistent"])
        self.assertFalse(audit.observe_run(pr,lane,run,[dict(jobs[0],steps=[])],merge)["metadata_consistent"])

        lane["observed_run"]["job_steps"]={"contract":["Tests","Unit regressions"]}
        self.assertFalse(audit.observe_run(pr,lane,run,jobs,merge)["metadata_consistent"])
        jobs[0]["steps"].append({"name":"Unit regressions","status":"completed","conclusion":"success"})
        self.assertTrue(audit.observe_run(pr,lane,run,jobs,merge)["metadata_consistent"])
        jobs[0]["steps"][1]["conclusion"]="skipped"
        self.assertFalse(audit.observe_run(pr,lane,run,jobs,merge)["metadata_consistent"])

        selected=dict(lane["observed_run"],checks=["contract"])
        jobs[0]["steps"][1]["conclusion"]="success"
        self.assertTrue(audit.observe_run(pr,lane,run,jobs,merge,observed=selected)["metadata_consistent"])

    def test_required_step_policy_requires_native_nonempty_names(self):
        pr,lane,files,runs,reviews=fixture()
        sha="d"*40
        pr["merge_commit_sha"]=sha
        expected={"id":9,"workflow":"test.yml","merge_sha":sha,"test_step":"T"}
        run={"id":9,"path":"test.yml","event":"pull_request","head_sha":"a"*40,
             "status":"completed","conclusion":"success","pull_requests":[{"number":1,
             "head":{"sha":"a"*40},"base":{"sha":"b"*40}}]}
        jobs=[{"name":"contract","run_id":9,"head_sha":"a"*40,"status":"completed",
               "conclusion":"success","steps":[{"name":"T","status":"completed","conclusion":"success"}]}]
        merge={"sha":sha,"parents":[{"sha":"b"*40},{"sha":"a"*40}]}
        for policy in (expected,dict(expected,job_steps={"contract":["T"]})):
            self.assertTrue(audit.observe_run(pr,lane,run,jobs,merge,observed=policy)["metadata_consistent"])
        malformed=[dict(expected,job_steps={"contract":v}) for v in
                   ("T",{"T":True},None,[],[""],[" "],[1],[True],[["T"]],["T","T"])]
        malformed.extend(dict(expected,job_steps=v) for v in (None,[],True))
        malformed.extend(dict(expected,test_step=v) for v in (None,1,""," "))
        for policy in malformed:
            with self.subTest(policy=policy):
                report=audit.observe_run(pr,lane,run,jobs,merge,observed=policy)
                self.assertFalse(report["metadata_consistent"])
                self.assertIn("invalid-job-step-policy:contract",report["reasons"])
                self.assertFalse(report["trust_verified"])

    def test_missing_reviewer_identity(self):
        for user in (None, {}, {"login": ""}, {"login": 7}):
            pr,lane,files,runs,reviews=fixture()
            reviews[0]["user"]=user
            result=audit.evaluate(pr,lane,files,runs,reviews,True)
            self.assertIn("head-review-missing",result["blockers"])
            self.assertEqual(result["decision"],"HOLD")

    def test_wrong_pr_number(self):
        pr,lane,files,runs,reviews=fixture()
        pr["number"]=2
        self.assertIn("unexpected-pr-identity",audit.evaluate(pr,lane,files,runs,reviews,True)["blockers"])

    def test_duplicate_file_rows(self):
        pr,lane,files,runs,reviews=fixture()
        pr["changed_files"]=2
        self.assertIn("scope-unverified",audit.evaluate(pr,lane,files+files,runs,reviews,True)["blockers"])

    def test_duplicate_step_name_with_one_success_is_not_consistent(self):
        pr,lane,files,runs,reviews=fixture()
        sha="d"*40
        pr["merge_commit_sha"]=sha
        lane["observed_run"]={"id":9,"workflow":"test.yml","merge_sha":sha,"test_step":"Tests"}
        run={"id":9,"path":"test.yml","event":"pull_request","head_sha":"a"*40,
             "status":"completed","conclusion":"success","pull_requests":[{"number":1,
             "head":{"sha":"a"*40},"base":{"sha":"b"*40}}]}
        jobs=[{"name":"contract","run_id":9,"head_sha":"a"*40,"status":"completed",
               "conclusion":"success","steps":[{"name":"Tests","status":"completed","conclusion":"success"},
               {"name":"Tests","status":"completed","conclusion":"skipped"}]}]
        merge={"sha":sha,"parents":[{"sha":"b"*40},{"sha":"a"*40}]}
        result=audit.observe_run(pr,lane,run,jobs,merge)
        self.assertFalse(result["metadata_consistent"])
        self.assertIn("job-metadata-mismatch:contract",result["reasons"])
        self.assertFalse(result["trust_verified"])

    def test_malformed_api_response_does_not_abort_remaining_lanes(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        cfg={"repository":"owner/repo","lanes":[{"pr":1},{"pr":2}]}
        stream=io.StringIO()
        with patch.object(sys,"argv",["audit"]), patch.object(Path,"read_text",return_value=json.dumps(cfg)), patch.object(audit,"audit",side_effect=[AttributeError("malformed API object"),{"pr":2,"decision":"HOLD"}]) as inspect, redirect_stdout(stream):
            self.assertEqual(audit.main(),1)
        self.assertEqual(inspect.call_count,2)
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([(row["pr"],row["decision"]) for row in rows],[(1,"HOLD"),(2,"HOLD")])
        self.assertIn("error",rows[0])

    def test_final_pr_read_refuses_identity_drift(self):
        from copy import deepcopy
        cases = [(None,None), (("updated_at",),"new"),
                 (("head","sha"),"e"*40), (("base","sha"),"e"*40),
                 (("merge_commit_sha",),"e"*40), (("base","ref"),"other"),
                 (("head","repo","full_name"),"other/repo"),
                 (("state",),"closed"), (("draft",),True),
                 (("changed_files",),2), (("mergeable",),False),
                 (("number",),2), (("user","login"),"other")]
        for path,value in cases:
            with self.subTest(path=path):
                pr,lane,files,runs,reviews=fixture()
                pr["merge_commit_sha"]="d"*40
                latest=deepcopy(pr)
                if path:
                    node=latest
                    for part in path[:-1]:
                        node=node[part]
                    node[path[-1]]=value
                responses=[pr,[files],[reviews],[{"total_count":len(runs),"check_runs":runs}],
                           [[{"name":lane["base"]}]],latest]
                with patch.object(audit,"api",side_effect=responses) as fetch:
                    if path not in (None,("updated_at",)):
                        with self.assertRaisesRegex(ValueError,"PR identity changed"):
                            audit.audit(lane["repo"],lane)
                    else:
                        result=audit.audit(lane["repo"],lane)
                        self.assertEqual(result["decision"],"HOLD")
                    self.assertEqual(fetch.call_count,6)
                    self.assertEqual(fetch.call_args_list[-1].args,(lane["repo"],"pulls/1"))

    def test_malformed_policy_lane_does_not_abort_remaining_lanes(self):
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        for malformed in ({}, None, [], {"pr":True}, {"pr":0}, {"pr":"1"}, {"pr":-1}):
            with self.subTest(lane=malformed):
                cfg={"repository":"owner/repo","lanes":[malformed,{"pr":2}]}
                stream=io.StringIO()
                with patch.object(sys,"argv",["audit"]), patch.object(Path,"read_text",return_value=json.dumps(cfg)), patch.object(audit,"audit",return_value={"pr":2,"decision":"HOLD"}) as inspect, redirect_stdout(stream):
                    self.assertEqual(audit.main(),1)
                inspect.assert_called_once_with("owner/repo",{"pr":2})
                rows=[json.loads(line) for line in stream.getvalue().splitlines()]
                self.assertEqual([(row["pr"],row["decision"]) for row in rows],[(None,"HOLD"),(2,"HOLD")])
                self.assertIn("error",rows[0])

    def test_final_read_follows_observed_run_evidence(self):
        from copy import deepcopy
        for drift in (False, True):
            with self.subTest(drift=drift):
                pr,lane,files,runs,reviews=fixture()
                pr["merge_commit_sha"]="d"*40
                lane["observed_run"]={"id":9,"merge_sha":"d"*40}
                latest=deepcopy(pr)
                if drift:
                    latest["merge_commit_sha"]="e"*40
                responses=[pr,[files],[reviews],[{"total_count":len(runs),"check_runs":runs}],
                           [[{"name":lane["base"]}]],{},[{"total_count":0,"jobs":[]}],{},latest]
                with patch.object(audit,"api",side_effect=responses) as fetch, patch.object(audit,"observe_run",return_value={"metadata_consistent":True}) as observe:
                    if drift:
                        with self.assertRaisesRegex(ValueError,"PR identity changed"):
                            audit.audit(lane["repo"],lane)
                    else:
                        result=audit.audit(lane["repo"],lane)
                        self.assertEqual(result["decision"],"HOLD")
                        self.assertEqual(result["observed_runs"],[{"metadata_consistent":True}])
                    observe.assert_called_once()
                    self.assertEqual(fetch.call_count,9)
                    self.assertEqual(fetch.call_args_list[-2].args,(lane["repo"],"git/commits/"+"d"*40))
                    self.assertEqual(fetch.call_args_list[-1].args,(lane["repo"],"pulls/1"))

    def test_counted_pages_require_complete_unique_native_ids(self):
        for key in ("jobs", "check_runs"):
            def page(n, ids):
                return {"total_count": n, key: [{"id": i} for i in ids]}
            for pages in ([page(0, [])], [page(2, [1]), page(2, [2])]):
                self.assertEqual(audit.counted_rows(pages, key),
                                 [row for p in pages for row in p[key]])
            bad = ([page(2, [1])], [page(2, [1]), page(3, [2])],
                   [page(2, [1]), page(2, [1])], [page(1, [True])],
                   [page(1, [0])], [{key: [{"id": 1}]}],
                   [page(True, [1])], [], [page(0, [1])],
                   [{"total_count": 0, key: {}}])
            for pages in bad:
                with self.subTest(key=key, pages=pages):
                    with self.assertRaises(ValueError):
                        audit.counted_rows(pages, key)

    def test_audit_refuses_truncated_check_or_job_pages(self):
        for key in ("check_runs", "jobs"):
            with self.subTest(key=key):
                pr,lane,files,runs,reviews=fixture()
                check_pages=[{"total_count":len(runs),"check_runs":runs}]
                branches=[[{"name":lane["base"]}]]
                if key == "check_runs":
                    check_pages[0]["total_count"] += 1
                    responses=[pr,[files],[reviews],check_pages]
                else:
                    lane["observed_run"]={"id":9,"merge_sha":"d"*40}
                    responses=[pr,[files],[reviews],check_pages,branches,{},
                               [{"total_count":1,"jobs":[]}]]
                with patch.object(audit,"api",side_effect=responses):
                    with self.assertRaisesRegex(ValueError,"incomplete pagination: "+key):
                        audit.audit(lane["repo"],lane)

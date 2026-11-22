"""Hermetic native Cezar CI glyph tests: no writes, SHA-bound and bounded."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("cezar_public_ci_checks",
                                              TOOLS / "cezar_public_ci_checks.py")
import sys
sys.path.insert(0, str(TOOLS))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def query(numbers):
    aliases = "\n".join(
        f"    p{i}: pullRequest(number: {n}) {{ commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ state }} }} }} }} }}"
        for i, n in enumerate(numbers))
    return c.PRE + aliases + c.SUF


def args(numbers):
    return ["api", "graphql", "-f", "query=" + query(numbers),
            "-f", "owner=Garyu-tt", "-f", "name=mojum"]


def pr(n, sha):
    return {"number": n, "html_url": f"https://github.com/Garyu-tt/mojum/pull/{n}",
            "state": "open", "head": {"sha": sha}}


def checks(*entries):
    return {"total_count": len(entries), "check_runs": [
        {"status": status, "conclusion": conclusion}
        for status, conclusion in entries]}


class FakeResponse:
    status = 200
    def __init__(self, url, obj): self.url, self.obj = url, obj
    def geturl(self): return self.url
    def read(self, size): return json.dumps(self.obj).encode()
    def __enter__(self): return self
    def __exit__(self, *_): return False


class FakeClient:
    def __init__(self, heads, runs):
        self.heads, self.runs = heads, runs
        self.calls = []
    def open(self, request, timeout=5):
        self.calls.append(request.full_url)
        url = request.full_url
        if url.startswith(c.BASE + "/pulls?"):
            result = self.heads
        elif "/check-runs?" in url:
            sha = url.split("/commits/")[1].split("/check-runs")[0]
            result = self.runs[sha]
        else:
            raise AssertionError("unapproved GitHub URL: " + url)
        return FakeResponse(url, result)


class PublicCIChecksTests(unittest.TestCase):
    def test_canonical_native_query(self):
        self.assertEqual(c.parse_checks(args([612, 685])), [612, 685])

    def test_malicious_and_unsupported_queries_refused(self):
        invalid = [
            ["api", "graphql", "-f", "query=mutation {deleteRepository}", "-f", "owner=Garyu-tt", "-f", "name=mojum"],
            args([612])[:-1] + ["name=another"],
            args([612,612]),
            args([612]) + ["-f", "extra=value"],
            ["api", "graphql", "-f", "query=" + query([612]).replace("statusCheckRollup { state }", "repository {id}"), "-f", "owner=Garyu-tt", "-f", "name=mojum"],
        ]
        for test in invalid:
            with self.subTest(test=test[:3]), self.assertRaises(c.PublicTabDenied):
                c.parse_checks(test)

    def test_green_red_pending_and_unknown_real_checks(self):
        a,b,d,e = ("a"*40,"b"*40,"d"*40,"e"*40)
        client=FakeClient([pr(612,a),pr(613,b),pr(614,d),pr(615,e)],{
            a:checks(("completed","success"),("completed","neutral")),
            b:checks(("completed","success"),("completed","failure")),
            d:checks(("completed","success"),("in_progress",None)),
            e:checks(),
        })
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/"private";directory.mkdir(mode=0o700)
            output=json.loads(c.render_checks([612,613,614,615],client=client,cache_dir=directory,now=10000))
            repo=output["data"]["repository"]
            states=[repo["p"+str(i)]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]
                    for i in range(4)]
            self.assertEqual(states,[{"state":"SUCCESS"},{"state":"FAILURE"},{"state":"PENDING"},None])
            self.assertEqual(len(client.calls),5)
            self.assertEqual((directory/c.FILE).stat().st_mode&0o777,0o600)

    def test_cache_no_repeat_network_and_sha_change_invalidates_green(self):
        a,b="a"*40,"b"*40
        client=FakeClient([pr(612,a)],{a:checks(("completed","success")),b:checks(("completed","failure"))})
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/"private";folder.mkdir(mode=0o700)
            first=json.loads(c.render_checks([612],client=client,cache_dir=folder,now=10000))
            again=json.loads(c.render_checks([612],client=client,cache_dir=folder,now=10001))
            self.assertEqual(first,again)
            self.assertEqual(len(client.calls),2)
            client.heads=[pr(612,b)]
            updated=json.loads(c.render_checks([612],client=client,cache_dir=folder,now=10601))
            s=updated["data"]["repository"]["p0"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"]
            self.assertEqual(s,{"state":"FAILURE"})
            self.assertEqual(len(client.calls),4)

    def test_per_call_and_hourly_budget_enforced(self):
        heads=[pr(i+100,format(i+1,"040x")) for i in range(15)]
        runs={x["head"]["sha"]:checks(("completed","success")) for x in heads}
        client=FakeClient(heads,runs)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/"private";folder.mkdir(mode=0o700)
            one=json.loads(c.render_checks([x["number"] for x in heads[:12]],client=client,cache_dir=folder,now=10000))
            states=[one["data"]["repository"][f"p{i}"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"] for i in range(12)]
            self.assertEqual(states[:8],[{"state":"SUCCESS"}]*8)
            self.assertEqual(states[8:],[None]*4)
            self.assertEqual(len(client.calls),9)
            two=json.loads(c.render_checks([x["number"] for x in heads[8:]],client=client,cache_dir=folder,now=10001))
            self.assertEqual(len(client.calls),16)
            self.assertEqual(two["data"]["repository"]["p0"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"],{"state":"SUCCESS"})

    def test_failed_or_incomplete_check_results_cannot_be_green(self):
        for payload in (
            {"total_count":101,"check_runs":checks(("completed","success"))["check_runs"]},
            {"total_count":2,"check_runs":checks(("completed","success"))["check_runs"]},
            checks(("completed","skipped")),
            checks(("completed","neutral")),
            checks(("completed","unknown")),
        ):
            client=FakeClient([pr(612,"a"*40)],{"a"*40:payload})
            self.assertIsNone(c._check_state("a"*40,client))

    def test_state_file_parent_must_be_private(self):
        client=FakeClient([pr(612,"a"*40)],{"a"*40:checks(("completed","success"))})
        with tempfile.TemporaryDirectory() as tmp:
            bad=Path(tmp)/"public";bad.mkdir(mode=0o755);bad.chmod(0o755)
            with self.assertRaises(c.PublicTabDenied):
                c.render_checks([612],client=client,cache_dir=bad,now=10000)


if __name__ == "__main__":
    unittest.main()

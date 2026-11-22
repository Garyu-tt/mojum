"""Hermetic tests for native Cezar GitHub tab backed by public REST."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "cezar_public_github_tab.py"
spec = importlib.util.spec_from_file_location("cezar_public_github_tab", MODULE)
tab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tab)

Q = ("query ($owner: String!, $name: String!, $issuesLimit: Int!, $prsLimit: Int!, "
     "$issuesCursor: String, $prsCursor: String) { repository(owner: $owner, name: $name) "
     "{ issues(first: $issuesLimit, after: $issuesCursor, states: OPEN, "
     "orderBy: {field: CREATED_AT, direction: DESC}) {totalCount pageInfo} "
     "pullRequests(first: $prsLimit, after: $prsCursor, states: OPEN, "
     "orderBy: {field: CREATED_AT, direction: DESC}) {isDraft totalCount pageInfo} } }")


def args(q=Q, *, owner="Garyu-tt", name="mojum", iss="50", prs="50",
         issues_cursor=None, prs_cursor=None):
    out = ["api", "graphql", "-f", "query=" + q, "-f", "owner=" + owner,
           "-f", "name=" + name, "-F", "issuesLimit=" + iss,
           "-F", "prsLimit=" + prs]
    if issues_cursor is not None:
        out += ["-f", "issuesCursor=" + issues_cursor]
    if prs_cursor is not None:
        out += ["-f", "prsCursor=" + prs_cursor]
    return out


def record(number, kind, *, draft=False):
    return {
        "number": number,
        "title": f"PR #{number}" if kind == "pr" else f"Issue #{number}",
        "html_url": f"https://github.com/Garyu-tt/mojum/{'pull' if kind == 'pr' else 'issues'}/{number}",
        "created_at": "2026-11-22T19:01:00Z",
        "user": {"login": "example-bot"},
        "labels": [{"name": "qa", "color": "123456"}],
        "comments": 2,
        **({"pull_request": {}, "draft": draft} if kind == "pr" else {}),
    }


class FakeResponse:
    status = 200
    def __init__(self, url, obj):
        self.url, self.obj = url, obj
    def geturl(self):
        return self.url
    def read(self, size):
        return json.dumps(self.obj).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, records):
        self.records = records
        self.calls = []
    def open(self, req, timeout=13):
        from urllib.parse import parse_qs, urlsplit
        self.calls.append(req.full_url)
        page = int(parse_qs(urlsplit(req.full_url).query)["page"][0])
        return FakeResponse(req.full_url, self.records[(page-1)*100:page*100])


class PublicGithubTabTests(unittest.TestCase):
    def test_exact_bounded_query_and_cursor(self):
        p = tab.parse_graphql(args(prs_cursor="p:50", issues_cursor="i:2"))
        self.assertEqual((p["prsLimit"], p["issuesLimit"],p["prsCursor"],p["issuesCursor"]),
                         (50,50,50,2))

    def test_cross_repo_and_graphql_mutations_refused(self):
        for bad in (args(owner="other"), args(name="other"),
                    args(q="mutation { deleteRepository }"),
                    args(iss="101"), args(prs="-1"),
                    args(prs_cursor="p:9999"),
                    args() + ["-f", "owner=evil"],
                    ["api","graphql","-f","query=other"]):
            with self.subTest(bad=bad[:3]), self.assertRaises(tab.PublicTabDenied):
                tab.parse_graphql(bad)

    def test_open_prs_show_real_title_author_draft_label_and_comments(self):
        opener = FakeOpener([record(612,"pr",draft=True),record(478,"issue")])
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory) / "private"
            d.mkdir(mode=0o700)
            params = tab.parse_graphql(args())
            rendered = json.loads(tab.render_graphql(params,opener=opener,cache_dir=d))
            root = rendered["data"]["repository"]
            self.assertEqual((root["issues"]["totalCount"],root["pullRequests"]["totalCount"]), (1,1))
            item = root["pullRequests"]["nodes"][0]
            self.assertEqual(item["number"],612)
            self.assertEqual(item["title"],"PR #612")
            self.assertEqual(item["author"]["login"],"example-bot")
            self.assertEqual(item["comments"],{"totalCount":2})
            self.assertEqual(item["labels"]["nodes"],[{"name":"qa","color":"123456"}])
            self.assertTrue(item["isDraft"])
            self.assertNotIn("additions",item)
            self.assertEqual(len(opener.calls),1)
            self.assertEqual((d/tab.CACHE_NAME).stat().st_mode&0o777, 0o600)

    def test_snapshot_cache_prevents_rate_limit_spam(self):
        opener = FakeOpener([record(612,"pr")])
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory) / "private"
            d.mkdir(mode=0o700)
            p = tab.parse_graphql(args())
            one=tab.render_graphql(p,opener=opener,cache_dir=d)
            two=tab.render_graphql(p,opener=opener,cache_dir=d)
            self.assertEqual(one,two)
            self.assertEqual(len(opener.calls),1)

    def test_native_graphql_pagination_keeps_both_lists(self):
        records = ([record(i+1000,"pr",draft=True) for i in range(55)]
                   + [record(i+2000,"issue") for i in range(25)])
        opener=FakeOpener(records)
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory)/"private"
            d.mkdir(mode=0o700)
            a=json.loads(tab.render_graphql(tab.parse_graphql(args(iss="10",prs="20")),
                                            opener=opener,cache_dir=d))["data"]["repository"]
            self.assertEqual(len(a["issues"]["nodes"]),10)
            self.assertEqual(len(a["pullRequests"]["nodes"]),20)
            self.assertEqual(a["prsTotal"] if "prsTotal" in a else a["pullRequests"]["totalCount"],55)
            self.assertEqual(a["issues"]["pageInfo"]["endCursor"],"i:10")
            self.assertEqual(a["pullRequests"]["pageInfo"]["endCursor"],"p:20")
            b=json.loads(tab.render_graphql(tab.parse_graphql(args(
                iss="20",prs="35",issues_cursor="i:10",prs_cursor="p:20")),
                opener=opener,cache_dir=d))["data"]["repository"]
            self.assertEqual(len(b["issues"]["nodes"]),15)
            self.assertEqual(len(b["pullRequests"]["nodes"]),35)
            self.assertFalse(b["pullRequests"]["pageInfo"]["hasNextPage"])
            self.assertEqual(len(opener.calls),1)

    def test_large_open_set_uses_second_real_rest_page(self):
        opener=FakeOpener([record(i+1000,"pr") for i in range(115)])
        snapshot=tab.collect(opener=opener)
        self.assertEqual(len(snapshot["prs"]),115)
        self.assertEqual(len(opener.calls),2)

    def test_wrong_resource_or_missing_draft_rejected(self):
        bad=record(612,"pr")
        bad["html_url"]="https://other.example/pull/612"
        with self.assertRaises(tab.PublicTabDenied):
            tab.collect(opener=FakeOpener([bad]))
        bad=record(612,"pr")
        del bad["draft"]
        with self.assertRaises(tab.PublicTabDenied):
            tab.collect(opener=FakeOpener([bad]))

    def test_private_cache_directory_is_mandatory(self):
        opener=FakeOpener([record(612,"pr")])
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory)/"public"
            d.mkdir(mode=0o755)
            d.chmod(0o755)
            with self.assertRaises(tab.PublicTabDenied):
                tab.snapshot(opener=opener,cache_dir=d)
            private=Path(directory)/"private"
            private.mkdir(mode=0o700)
            alias=Path(directory)/"alias"
            alias.symlink_to(private,target_is_directory=True)
            with self.assertRaises(tab.PublicTabDenied):
                tab.snapshot(opener=opener,cache_dir=alias)


if __name__ == "__main__":
    unittest.main()

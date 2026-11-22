"""Hermetic security and no-replay tests for the Cezar Telegram replacement."""
import importlib.util
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

MODULE=Path(__file__).resolve().parent / "bot.py"
spec=importlib.util.spec_from_file_location("cezar_telegram_bot",MODULE)
bot=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


def fake_cezar(path):
    if path.startswith("/api/v1/github/checks?prs="):
        return {"available":True,"checks":{path.split("=")[-1]:"passing"}}
    if path.startswith("/api/v1/github"):
        return {"available":True,"repo":"Garyu-tt/mojum","prsTotal":2,"issuesTotal":5,"issues":[],
                "prs":[{"number":700,"title":"Create CPU pool","isDraft":True,
                        "author":{"login":"agent"},"comments":0},
                       {"number":699,"title":"Fix Cezar viewer","isDraft":False,
                        "author":{"login":"agent"},"comments":1}]}
    if path=="/api/v1/health":
        return {"version":"0.20.0","repoRoot":"/var/lib/cezar/projects/mojum"}
    if path=="/api/v1/runs":
        return [{"id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","status":"cancelled","prNumber":612},
                {"id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","status":"running"}]
    raise AssertionError("unexpected endpoint "+path)


class CezarTelegramTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base=Path(temp.name)
        self.base.chmod(0o700)
        self.state_path=self.base/"state.json"
        self.patch_state=patch.object(bot,"STATE_DIR",self.base)
        self.patch_file=patch.object(bot,"STATE_FILE",self.state_path)
        self.patch_cezar=patch.object(bot,"cezar_get",side_effect=fake_cezar)
        for p in (self.patch_state,self.patch_file,self.patch_cezar):p.start();self.addCleanup(p.stop)
        self.state=bot._state()

    def test_status_is_from_actual_cezar_api_not_old_alt_events(self):
        value=bot.status_text()
        self.assertIn("0.20.0",value)
        self.assertIn("PR открыто: 2",value)
        self.assertIn("running=1",value)
        self.assertIn("cancelled=1",value)
        self.assertNotIn("лимит API GitHub",value)

    def test_pr_and_ci_do_not_invent_results(self):
        self.assertIn("#700",bot.prs_text())
        self.assertIn("[Draft]",bot.pr_text(700))
        self.assertIn("успешно",bot.ci_text(700))
        self.assertIn("не найден",bot.pr_text(900))

    def test_unauthorized_person_and_group_are_ignored(self):
        messages=[]
        with patch.object(bot,"send",side_effect=lambda *a,**kw:messages.append(a)):
            bot.handle_update("secret",42,{"message":{"chat":{"id":9,"type":"private"},
                "from":{"id":9},"text":"/status"}},self.state)
            bot.handle_update("secret",42,{"message":{"chat":{"id":42,"type":"group"},
                "from":{"id":42},"text":"/status"}},self.state)
            bot.handle_update("secret",42,{"callback_query":{"from":{"id":9},
                "message":{"chat":{"id":42}},"data":"prs"}},self.state)
        self.assertEqual(messages,[])

    def test_owner_command_routes_to_cezar_and_not_arbitrary_gh(self):
        sent=[]
        with patch.object(bot,"send",side_effect=lambda _token,_admin,msg,**kw:sent.append(msg)):
            bot.handle_update("secret",42,{"message":{"chat":{"id":42,"type":"private"},
                "from":{"id":42},"text":"/status"}},self.state)
        self.assertEqual(len(sent),1)
        self.assertIn("Cezar",sent[0])

    def test_owner_can_toggle_only_notifications(self):
        a,_=bot.command("/mute",self.state)
        self.assertFalse(self.state["alerts"])
        a,_=bot.command("/unmute",self.state)
        self.assertTrue(self.state["alerts"])
        a,_=bot.command("/alerts",self.state)
        self.assertFalse(self.state["alerts"])

    def test_safe_command_names_deny_model_dispatch_and_merge(self):
        for command in ("/merge 612","/model run","/execute rm -rf /","/settoken secret"):
            result,_=bot.command(command,self.state)
            self.assertIn("Неизвестная команда",result)

    def test_private_offset_uses_negative_start_and_does_not_replay_old(self):
        calls=[]
        def tg(token,method,payload,**k):
            calls.append((method,payload))
            return [{"update_id":12345}]
        with patch.object(bot,"telegram",side_effect=tg):
            bot._telegram_offset(self.state,"redacted")
        self.assertEqual(self.state["offset"],12346)
        self.assertEqual(calls[0][1]["offset"],-1)
        self.assertEqual(bot._state()["offset"],12346)

    def test_first_snapshot_notifies_nobody(self):
        outputs=[]
        with patch.object(bot,"send",side_effect=lambda *a,**kw:outputs.append(a)):
            bot.seed_and_watch("secret",42,self.state)
        self.assertEqual(outputs,[])
        self.assertTrue(self.state["primed"])
        self.assertEqual(self.state["highest_pr"],700)
        self.assertEqual(len(self.state["run_states"]),2)

    def test_new_pr_and_run_changes_generate_one_real_digest(self):
        sent=[]
        def new_cezar(path):
            x=fake_cezar(path)
            if path.startswith("/api/v1/github"):
                x["prs"][0]={"number":701,"title":"New PR","isDraft":True,
                             "createdAt":"2026-11-23T00:00:00Z"}
                x["prsTotal"]=3
            if path=="/api/v1/runs":
                x[1]["status"]="completed"
            return x
        with patch.object(bot,"send",side_effect=lambda _token,_admin,msg,**kw:sent.append(msg)):
            bot.seed_and_watch("secret",42,self.state)
            self.state["started_at"]="2026-11-22T00:00:00Z"
            with patch.object(bot,"cezar_get",side_effect=new_cezar):
                bot.seed_and_watch("secret",42,self.state)
                self.assertEqual(len(sent),2)
                self.assertIn("#701",sent[0])
                self.assertIn("running → completed",sent[1])
                bot.seed_and_watch("secret",42,self.state)
                self.assertEqual(len(sent),2)

    def test_preexisting_pr_hidden_by_cache_never_notified_as_new(self):
        sent=[]
        bot.seed_and_watch("secret",42,self.state)
        self.state["started_at"]="2026-11-22T20:43:00Z"
        self.state["highest_pr"]=685
        with patch.object(bot,"send",side_effect=lambda *a,**kw:sent.append(a)):
            bot.seed_and_watch("secret",42,self.state)
        self.assertEqual(sent,[])
        self.assertEqual(self.state["highest_pr"],700)

    def test_owner_muted_suppresses_notices_but_state_progresses(self):
        self.state["alerts"]=False
        bot.seed_and_watch("secret",42,self.state)
        self.state["highest_pr"]=1
        with patch.object(bot,"send",side_effect=AssertionError("muted must not send")):
            bot.seed_and_watch("secret",42,self.state)
        self.assertEqual(self.state["highest_pr"],700)

    def test_corrupt_world_readable_state_rejected(self):
        self.state_path.write_text('{"schema":1}',encoding="utf-8")
        self.state_path.chmod(0o644)
        with self.assertRaises(bot.BotFailure):
            bot._state()

    def test_unicode_title_control_characters_are_neutralized(self):
        self.assertEqual(bot.stable_text("foo\nbar\x00z"),"foo bar z")
        self.assertEqual(bot.stable_text(None),"")


if __name__=="__main__":
    unittest.main()

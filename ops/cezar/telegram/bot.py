#!/usr/bin/env python3
"""ClaudeFabric replacement: owner-only Telegram front end to Cezar.

Cezar is the ONLY source for PR, CI and run state. This bot never runs
models, searches GitHub independently, creates PRs, or merges anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:14331"
API = "https://api.telegram.org/bot"
STATE_DIR = Path("/var/lib/cezar-telegram")
STATE_FILE = STATE_DIR / "state.json"
LOCK = STATE_DIR / "singleton.lock"
STATE_MAX_BYTES = 256 * 1024
POLL_SECONDS = 70
REPO = "Garyu-tt/mojum"
SHORT = 84
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",re.I)
PR_NUM = re.compile(r"[1-9][0-9]{0,6}\Z")
SAFE_COMMANDS = frozenset({"start","help","status","prs","pr","ci","runs","run","fleet","agents","alerts","mute","unmute","refresh"})

class BotFailure(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise BotFailure("HTTP redirect refused")


def stable_text(value, maximum=SHORT):
    if not isinstance(value, str):
        return ""
    return "".join(ch if ch.isprintable() else " " for ch in value)[:maximum].strip()


def cezar_get(path):
    if not path.startswith("/api/v1/"):
        raise BotFailure("Cezar endpoint denied")
    req = urllib.request.Request(BASE + path, headers={
        "Accept": "application/json", "User-Agent": "claudefabric-cezar-ui/1"
    })
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=19) as r:
            if r.status != 200 or r.geturl() != BASE + path:
                raise BotFailure("unexpected Cezar response")
            raw = r.read(900_001)
        if len(raw) > 900_000:
            raise BotFailure("Cezar response too long")
        return json.loads(raw)
    except (OSError,ValueError,UnicodeError) as e:
        raise BotFailure("Cezar is unreachable") from e


def telegram(token, method, args, *, timeout=32):
    if method not in {"getMe","getUpdates","sendMessage","answerCallbackQuery","setMyCommands"}:
        raise BotFailure("Telegram method not allowed")
    request = urllib.request.Request(
        API + token + "/" + method, data=json.dumps(args,ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout) as r:
            if r.status != 200:
                raise BotFailure("Telegram API unavailable")
            response = json.load(r)
        if not isinstance(response,dict) or response.get("ok") is not True:
            raise BotFailure("Telegram operation rejected")
        return response.get("result")
    except (OSError,ValueError,UnicodeError) as e:
        raise BotFailure("Telegram transport unavailable") from e


def _state():
    default = {"schema":1,"offset":None,"primed":False,"alerts":True,
               "highest_pr":0,"run_states":{},"announced":False}
    # A pathname check followed by read_text() is a symlink/replace race.
    # Pin one descriptor, validate its inode and cap the actual bytes read.
    try:
        fd = os.open(STATE_FILE, os.O_RDONLY | os.O_NOFOLLOW | getattr(os,"O_CLOEXEC",0))
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise BotFailure("cannot safely open private state") from exc
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077 or st.st_nlink != 1
                or st.st_size > STATE_MAX_BYTES):
            raise BotFailure("invalid private state file")
        with os.fdopen(fd,"rb",closefd=False) as source:
            raw = source.read(STATE_MAX_BYTES + 1)
        if len(raw) > STATE_MAX_BYTES:
            raise BotFailure("private state exceeds byte budget")
        obj = json.loads(raw)
    finally:
        os.close(fd)
    if type(obj) is not dict or obj.get("schema") != 1:
        raise BotFailure("unsupported local state")
    return obj


def save(state):
    raw = (json.dumps(state,ensure_ascii=False,separators=(",",":"))+"\n").encode()
    if len(raw) > STATE_MAX_BYTES:
        raise BotFailure("private state exceeds byte budget")
    temp = STATE_DIR / (".state." + str(os.getpid()))
    descriptor = os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(descriptor,"wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,STATE_FILE)
        # Offset and notification dedup survive a sudden power loss only if
        # the directory entry itself is committed after the atomic rename.
        directory_fd = os.open(STATE_DIR,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    finally:
        try:temp.unlink()
        except FileNotFoundError:pass


def keyboard():
    return {"inline_keyboard":[
        [{"text":"📊 Cezar","callback_data":"status"},{"text":"🔀 PR","callback_data":"prs"}],
        [{"text":"🧪 CI #612","callback_data":"ci 612"},{"text":"⚙️ Запуски","callback_data":"runs"}],
        [{"text":"👥 Флот · расписания и слоты","callback_data":"fleet"}],
        [{"text":"🔕/🔔 Уведомления","callback_data":"alerts"}],
    ]}


def send(token,admin,message,*,buttons=False):
    return telegram(token,"sendMessage",{
        "chat_id":admin,"text":message[:3500],"disable_web_page_preview":True,
        **({"reply_markup":keyboard()} if buttons else {}),
    })


def _github(*,refresh=False):
    d=cezar_get("/api/v1/github?limit=50&refresh="+("1" if refresh else "0"))
    if not isinstance(d,dict) or d.get("available") is not True or d.get("repo")!=REPO:
        raise BotFailure("Cezar GitHub view unavailable")
    if not isinstance(d.get("prs"),list) or not isinstance(d.get("issues"),list):
        raise BotFailure("Cezar GitHub data malformed")
    return d


def _runs():
    data=cezar_get("/api/v1/runs")
    if not isinstance(data,list):
        raise BotFailure("Cezar runs response malformed")
    return data


def _fleet():
    data=cezar_get("/api/v1/fleet")
    if not isinstance(data,dict) or data.get("schema")!="cezar.fleet.visibility.v1":
        raise BotFailure("Cezar fleet source unavailable")
    return data


def fleet_text():
    data=_fleet()
    c=data.get("cezar") or {}
    s=data.get("schedules") or {}
    m=data.get("models") or {}
    g=data.get("github") or {}
    lines=["👥 Флот Mojum, наблюдение из Cezar",
           "Задачи Cezar сейчас: "+str(c.get("active","неизвестно")),
           "Расписаний Mojum: "+str(s.get("enabled","неизвестно"))+
               " (разработчиков "+str(s.get("developers_enabled","?"))+
               ", QA "+str(s.get("qa_enabled","?"))+")",
           "Модели: "+str(m.get("inference_busy_known","?"))+
               "/"+str(m.get("capacity_slots_known","?"))+" слотов занято",
           "GitHub PR открыто: "+str(g.get("open_prs","?")),
           "Внешние выполняются сейчас: неизвестно (heartbeat не подключён)."]
    if not s.get("snapshot_fresh"):
        lines.append("⚠️ Снимок расписаний устарел. Последние значения не являются текущими.")
    from datetime import datetime
    agents=s.get("agents")
    if isinstance(agents,list):
        lines.append("")
        for agent in agents[:12]:
            if not isinstance(agent,dict): continue
            stamp=agent.get("last_run")
            when=stamp[:16].replace("T"," ")+" UTC" if isinstance(stamp,str) else "неизвестно"
            state={"completed":"завершён","in_progress":"выполнялся",
                   "failed":"ошибка","no_update":"нет результата"}.get(
                       agent.get("last_status"),"статус неизвестен")
            lines.append("• "+stable_text(agent.get("name","?"),52)+": "+when+" ("+state+")")
    lines.append("Расписания, занятые слоты и PR не суммируются в «активных агентов».")
    return "\n".join(lines)


def status_text():
    health=cezar_get("/api/v1/health")
    git=_github()
    runs=_runs()
    statuses={}
    for run in runs:
        if isinstance(run,dict):
            kind=str(run.get("status","unknown"))
            statuses[kind]=statuses.get(kind,0)+1
    active=sum(statuses.get(s,0) for s in ("running","queued","waiting","starting"))
    try:
        fleet=_fleet()
        schedule=fleet.get("schedules") or {}
        models=fleet.get("models") or {}
        fleet_line=(f"Расписаний Mojum: {schedule.get('enabled','?')} "
                    f"(разработчиков {schedule.get('developers_enabled','?')}, "
                    f"QA {schedule.get('qa_enabled','?')}); "
                    f"моделей занято {models.get('inference_busy_known','?')}/"
                    f"{models.get('capacity_slots_known','?')}\n"
                    "Внешние активны сейчас: неизвестно, нет heartbeat\n")
        if not schedule.get("snapshot_fresh"):
            fleet_line+="⚠️ Снимок расписаний устарел\n"
    except BotFailure:
        fleet_line="Флот: нет достоверной телеметрии\n"
    return ("🤖 Cezar | Mojum\n"
            f"Сервер: {stable_text(str(health.get('version','unknown')),24)}\n"
            f"Репозиторий: {REPO}\n"
            f"PR открыто: {git.get('prsTotal','?')} | Issues: {git.get('issuesTotal','?')}\n"
            f"Запусков Cezar: {len(runs)} | активных ВНУТРИ Cezar: {active}\n"
            f"{fleet_line}"
            f"Состояния: {', '.join(f'{k}={v}' for k,v in sorted(statuses.items())) or 'нет'}\n"
            "Источник: только API Cezar. Без независимых ALT-циклов и авто-merge.")


def prs_text():
    d=_github()
    rows=d["prs"][:12]
    if not rows:
        return "В Cezar нет открытых PR."
    lines=[f"🔀 Mojum: {d.get('prsTotal','?')} открытых PR (последние {len(rows)}):"]
    for pr in rows:
        if not isinstance(pr,dict):continue
        num=pr.get("number")
        if type(num) is not int:continue
        title=stable_text(pr.get("title",""),62)
        draft=" [Draft]" if pr.get("isDraft") else ""
        lines.append(f"#{num}{draft} {title}")
    lines.append("\nПроверить CI: /ci 612. Конкретный PR: /pr 612")
    return "\n".join(lines)


def pr_text(number):
    rows=_github()["prs"]
    pr=next((x for x in rows if isinstance(x,dict) and x.get("number")==number),None)
    if pr is None:
        return f"PR #{number} не найден среди открытых PR Cezar. Закрытые PR здесь не показываются."
    author=pr.get("author") or {}
    login=stable_text(author.get("login","?") if isinstance(author,dict) else "?",55)
    return (f"🔀 PR #{number}{' [Draft]' if pr.get('isDraft') else ''}\n"
            f"{stable_text(pr.get('title',''),400)}\n"
            f"Автор: {login}\n"
            f"Комментарии: {pr.get('comments','?')}\n"
            f"https://github.com/{REPO}/pull/{number}\n"
            f"Проверки: /ci {number}")


def ci_text(number):
    data=cezar_get("/api/v1/github/checks?prs="+str(number))
    if not isinstance(data,dict) or data.get("available") is not True:
        raise BotFailure("Cezar CI view unavailable")
    state=(data.get("checks") or {}).get(str(number))
    label={"passing":"✅ успешно","pending":"⏳ выполняются",
           "failing":"❌ ошибка"}.get(state,"❔ нет достоверного результата")
    return (f"🧪 PR #{number}: {label}\n"
            "Статус из Cezar может быть кеширован. Для приёмки обязателен CI текущего SHA.")


def runs_text():
    runs=_runs()
    if not runs:
        return "В Cezar пока нет запусков."
    lines=["⚙️ Последние запуски Cezar:"]
    for run in runs[:12]:
        if isinstance(run,dict):
            key=str(run.get("id",""))[:8]
            status=stable_text(str(run.get("status","unknown")),22)
            pr=run.get("prNumber")
            lines.append(f"{key} • {status}"+(f" • PR #{pr}" if type(pr) is int else ""))
    lines.append("\nВнешние PR не считаются запусками Cezar.")
    return "\n".join(lines)


def run_text(run_id):
    for item in _runs():
        if isinstance(item,dict) and item.get("id")==run_id:
            return (f"⚙️ Cezar {run_id}\n"
                    f"Статус: {stable_text(str(item.get('status','?')),35)}\n"
                    f"PR: {item.get('prNumber','нет')}\n"
                    "История запуска доступна в панели Cezar.")
    return "Такой запуск Cezar не найден."


def help_text():
    return ("🤖 ClaudeFabric теперь клиент Cezar для Mojum.\n"
            "/status — состояние Cezar и очереди\n"
            "/prs — открытые PR GitHub\n"
            "/pr 612 — карточка PR\n"
            "/ci 612 — проверки PR\n"
            "/runs — запуски Cezar\n"
            "/fleet — разработчики, QA, модели и источники\n"
            "/run UUID — конкретный запуск\n"
            "/refresh — запросить обновление списка PR\n"
            "/alerts — вкл/выкл уведомления\n"
            "/help — эта справка\n\n"
            "Бот НЕ запускает отдельные модели, НЕ создаёт PR и НЕ делает merge. "
            "Управление разработкой и приёмкой остаётся в Cezar.")


def command(text,state):
    words=text.strip().split(maxsplit=1)
    if not words:
        return help_text(),False
    op=words[0].lstrip("/").split("@",1)[0].lower()
    arg=words[1].strip() if len(words)>1 else ""
    if op not in SAFE_COMMANDS:
        return "Неизвестная команда. /help",False
    if op in ("start","help"):
        return help_text(),True
    if op=="status":
        return status_text(),True
    if op=="prs":
        return prs_text(),False
    if op=="runs":
        return runs_text(),False
    if op in ("fleet","agents"):
        return fleet_text(),False
    if op in ("pr","ci"):
        if not PR_NUM.fullmatch(arg):
            return "Укажи номер PR, например /ci 612",False
        return (pr_text(int(arg)) if op=="pr" else ci_text(int(arg))),False
    if op=="run":
        if not UUID.fullmatch(arg):
            return "Укажи полный UUID запуска Cezar.",False
        return run_text(arg.lower()),False
    if op=="refresh":
        data=_github(refresh=True)
        return f"🔄 Cezar обновил список: {data.get('prsTotal','?')} PR, {data.get('issuesTotal','?')} Issues.",False
    if op in ("alerts","mute","unmute"):
        state["alerts"]=(not bool(state.get("alerts",True))) if op=="alerts" else op=="unmute"
        return ("🔔 Уведомления Cezar включены." if state["alerts"] else
                "🔕 Уведомления выключены. Команды продолжают работать."),True
    return help_text(),False


def _telegram_offset(state,token):
    if state.get("offset") is not None:
        return
    # Old ClaudeFabric was a separate poller. Do not replay its old commands.
    latest=telegram(token,"getUpdates",{"offset":-1,"timeout":0,
                                         "allowed_updates":["message","callback_query"]},timeout=12)
    if not isinstance(latest,list):
        raise BotFailure("unexpected Telegram startup")
    state["offset"]=max((u.get("update_id",0) for u in latest if isinstance(u,dict)),default=0)+1
    save(state)


def handle_update(token,admin,update,state):
    m=update.get("message")
    callback=update.get("callback_query")
    if callback and isinstance(callback,dict):
        person=callback.get("from") or {}
        message=callback.get("message") or {}
        chat=message.get("chat") or {}
        if person.get("id")!=admin or chat.get("id")!=admin:
            return
        datum=callback.get("data")
        text=datum if isinstance(datum,str) else "help"
        callback_id=callback.get("id")
        if isinstance(callback_id,str):
            try:telegram(token,"answerCallbackQuery",{"callback_query_id":callback_id},timeout=10)
            except BotFailure:pass
    elif m and isinstance(m,dict):
        chat=m.get("chat") or {}
        sender=m.get("from") or {}
        if chat.get("type")!="private" or chat.get("id")!=admin or sender.get("id")!=admin:
            return
        text=m.get("text")
        if not isinstance(text,str) or len(text)>500:
            return
    else:
        return
    try:
        message,buttons=command(text,state)
    except BotFailure as exc:
        message="⚠️ Нет достоверных данных Cezar. Подключение временно недоступно."
        buttons=False
    send(token,admin,message,buttons=buttons)
    save(state)


def created_after_bootstrap(pr, cutoff):
    # Cezar's public GitHub snapshot may be cached when the bot starts.
    # A higher PR number alone is not evidence that the PR is new.
    if not isinstance(cutoff, str) or not isinstance(pr.get("createdAt"), str):
        return False
    try:
        start = datetime.fromisoformat(cutoff.replace("Z","+00:00"))
        created = datetime.fromisoformat(pr["createdAt"].replace("Z","+00:00"))
        return start.tzinfo is not None and created.tzinfo is not None and created > start
    except ValueError:
        return False


def seed_and_watch(token,admin,state):
    if not isinstance(state.get("started_at"),str):
        state["started_at"]=datetime.now(timezone.utc).isoformat()
        save(state)
    try:
        git=_github()
        runs=_runs()
    except BotFailure:
        return
    values={str(x["id"]):str(x.get("status","unknown")) for x in runs
            if isinstance(x,dict) and isinstance(x.get("id"),str)}
    high=max((x.get("number",0) for x in git["prs"]
              if isinstance(x,dict) and type(x.get("number")) is int),default=0)
    if not state.get("primed"):
        state["highest_pr"]=high
        state["run_states"]=values
        state["primed"]=True
        save(state)
        return
    old_high=int(state.get("highest_pr",0))
    new_prs=sorted([x for x in git["prs"] if isinstance(x,dict)
                    and type(x.get("number")) is int
                    and x["number"]>old_high
                    and created_after_bootstrap(x,state["started_at"])],key=lambda x:x["number"])
    old_runs=state.get("run_states") or {}
    changed=[]
    for r in runs:
        if not isinstance(r,dict) or not isinstance(r.get("id"),str):
            continue
        rid=str(r["id"])
        status=str(r.get("status","unknown"))
        was=old_runs.get(rid)
        if was is None and status in ("running","queued","waiting"):
            changed.append(f"▶️ {rid[:8]}: {status}")
        elif was is not None and status!=was:
            changed.append(f"⚙️ {rid[:8]}: {was} → {status}")
    texts=[]
    if new_prs:
        lines=[f"🔀 Cezar: новых PR {len(new_prs)}."]
        for pr in new_prs[:6]:
            num=pr["number"]
            lines.append(f"#{num}{' [Draft]' if pr.get('isDraft') else ''}: {stable_text(pr.get('title',''),65)}")
        if len(new_prs)>6:lines.append(f"… и ещё {len(new_prs)-6}.")
        texts.append("\n".join(lines))
    if changed:
        texts.append("⚙️ Cezar: изменения запусков\n"+"\n".join(changed[:8]))
    if state.get("alerts",True):
        # Store only after delivery; failed transport is retried, not invented.
        for message in texts:
            send(token,admin,message)
    state["highest_pr"]=max(old_high,high)
    state["run_states"]=dict(list(values.items())[:500])
    save(state)


def main():
    token=os.getenv("TG_BOT_TOKEN","").strip()
    try: admin=int(os.getenv("TG_ADMIN_ID","0"))
    except ValueError:admin=0
    if not token or admin<=0:
        raise BotFailure("missing Telegram owner config")
    st=STATE_DIR.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
        raise BotFailure("bot state directory is not private")
    descriptor=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise BotFailure("another Telegram poller is active") from None
    state=_state()
    telegram(token,"getMe",{},timeout=14)
    _telegram_offset(state,token)
    seed_and_watch(token,admin,state)
    if not state.get("announced"):
        send(token,admin,"✅ ClaudeFabric переведён на Cezar.\nСтарые ALT GitHub циклы и уведомления отключены.\n"
             "Теперь PR, CI и статусы задач берутся из живой панели Cezar. /status /prs /ci 612 /runs",buttons=True)
        state["announced"]=True
        save(state)
    last_scan=time.monotonic()
    failures=0
    while True:
        try:
            updates=telegram(token,"getUpdates",{
                "offset":int(state["offset"]),"timeout":20,"limit":30,
                "allowed_updates":["message","callback_query"]},timeout=29)
            if not isinstance(updates,list):
                raise BotFailure("unexpected Telegram updates")
            for update in updates:
                if not isinstance(update,dict) or type(update.get("update_id")) is not int:
                    continue
                uid=update["update_id"]
                if uid<int(state["offset"]):
                    continue
                # Save offset before any response, so a crash cannot replay commands.
                state["offset"]=uid+1
                save(state)
                handle_update(token,admin,update,state)
            if time.monotonic()-last_scan>=POLL_SECONDS:
                seed_and_watch(token,admin,state)
                last_scan=time.monotonic()
            failures=0
        except (BotFailure,OSError,ValueError,TypeError) as exc:
            failures+=1
            print("cezar_telegram_transient_failure",type(exc).__name__,flush=True)
            time.sleep(min(60,2**min(failures,5)))


if __name__=="__main__":
    try:main()
    except (BotFailure,OSError,ValueError) as exc:
        print("cezar_telegram_startup_failed",type(exc).__name__,flush=True)
        raise SystemExit(2)

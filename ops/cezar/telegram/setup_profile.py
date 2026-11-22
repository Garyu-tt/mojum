#!/usr/bin/env python3
"""One-time safe Telegram Bot API profile cutover; never print credentials."""
import json
from pathlib import Path
import urllib.request

values={}
for line in Path("/etc/cezar-telegram/telegram.env").read_text().splitlines():
    key,eq,value=line.partition("=")
    if eq and key in ("TG_BOT_TOKEN","TG_ADMIN_ID"):
        values[key]=value.strip().strip('"').strip("'")
token=values["TG_BOT_TOKEN"]
owner=int(values["TG_ADMIN_ID"])
def call(method,payload):
    req=urllib.request.Request("https://api.telegram.org/bot"+token+"/"+method,
        data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as response:
        value=json.load(response)
    if not isinstance(value,dict) or value.get("ok") is not True:
        raise RuntimeError("Telegram refused profile update")
    return value.get("result")
me=call("getMe",{})
assert isinstance(me,dict) and me.get("is_bot") is True
commands=[
    {"command":"start","description":"Открыть пульт Cezar"},
    {"command":"status","description":"Состояние Cezar и очереди"},
    {"command":"prs","description":"Открытые PR Mojum"},
    {"command":"pr","description":"Карточка PR, например /pr 612"},
    {"command":"ci","description":"CI конкретного PR, например /ci 612"},
    {"command":"runs","description":"Запуски агентов в Cezar"},
    {"command":"run","description":"Запуск по UUID"},
    {"command":"refresh","description":"Обновить список PR"},
    {"command":"alerts","description":"Вкл/выкл уведомления"},
    {"command":"help","description":"Команды нового бота"},
]
scope={"type":"chat","chat_id":owner}
assert call("setMyName",{"name":"Cezar | Mojum"}) is True
assert call("setMyShortDescription",{
    "short_description":"Контроль Mojum через Cezar: PR, CI, запуски и уведомления."
}) is True
assert call("setMyDescription",{
    "description":"Пульт Cezar для Mojum. Показывает настоящие PR, GitHub CI и состояния задач. "
                  "Старые ALT GitHub циклы удалены. Бот не создаёт PR и не выполняет merge."
}) is True
assert call("setMyCommands",{"commands":commands,"scope":scope}) is True
assert call("deleteMyCommands",{"scope":{"type":"default"}}) is True
actual=call("getMyCommands",{"scope":scope})
assert [x["command"] for x in actual]==[x["command"] for x in commands]
print("CEZAR_BOT_PROFILE_READY username_verified=yes owner_command_count="+str(len(actual)))

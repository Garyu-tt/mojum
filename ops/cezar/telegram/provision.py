#!/usr/bin/env python3
"""One-time local owner-only Telegram credential migration; prints no secrets."""
from pathlib import Path
import os
import re
import stat
import subprocess

SOURCE=Path("/etc/claude-factory/factory.env")
DEST_DIR=Path("/etc/cezar-telegram")
DEST=DEST_DIR/"telegram.env"
req=("TG_BOT_TOKEN","TG_ADMIN_ID")
raw=SOURCE.read_text("utf-8")
line_map={}
for line in raw.splitlines():
    name,separator,value=line.partition("=")
    if separator and name in req:
        if name in line_map:
            raise SystemExit("duplicate Telegram key")
        line_map[name]=line
if tuple(sorted(line_map))!=tuple(sorted(req)):
    raise SystemExit("Telegram owner/token config missing")
token=line_map["TG_BOT_TOKEN"].partition("=")[2].strip().strip('\\\"\\\'')
admin=line_map["TG_ADMIN_ID"].partition("=")[2].strip().strip('\\\"\\\'')
if not re.fullmatch(r"[0-9]{6,16}:[A-Za-z0-9_-]{25,128}",token):
    raise SystemExit("invalid Telegram token config")
if not admin.isascii() or not admin.isdecimal() or int(admin)<=0:
    raise SystemExit("invalid owner ID")
DEST_DIR.mkdir(mode=0o700,parents=True,exist_ok=True)
os.chmod(DEST_DIR,0o700)
fd=os.open(DEST,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
    with os.fdopen(fd,"w",encoding="utf-8") as file:
        file.write("\n".join(line_map[x] for x in req)+"\n")
        file.flush()
        os.fsync(file.fileno())
finally:
    os.chmod(DEST,0o600)
print("CEZAR_TELEGRAM_CREDS_READY owner_only=yes provider_keys_excluded=yes")

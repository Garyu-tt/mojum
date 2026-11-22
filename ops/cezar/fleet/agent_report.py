#!/usr/bin/env python3
"""Bounded owner-connector event receipt for Cezar. Does not claim proven liveness."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

DIR=Path("/var/lib/cezar/home/fleet/agent-reports")
AGENTS={"qa","peer1","peer2","peer3","peer4"}
PHASES={"observed","started","progress","completed","blocked","failed"}
ACTIONS={"build","qa","ship","observe","sync","other"}
SCHEMA="cezar.agent-report.v1"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--agent",required=True,choices=sorted(AGENTS))
    ap.add_argument("--phase",required=True,choices=sorted(PHASES))
    ap.add_argument("--action",required=True,choices=sorted(ACTIONS))
    ap.add_argument("--pr",type=int)
    ap.add_argument("--note",required=True)
    ap.add_argument("--source",choices=("owner-observation","self-report"),default="self-report")
    args=ap.parse_args()
    if args.pr is not None and not 1<=args.pr<=10_000_000:
        ap.error("invalid PR")
    if len(args.note)>240 or len(args.note)<3 or len(args.note.encode("utf-8"))>800 or any(not c.isprintable() for c in args.note):
        ap.error("invalid note")
    if not DIR.exists():
        DIR.mkdir(mode=0o700)
        if os.geteuid()==0:
            import pwd
            uid=pwd.getpwnam("mirbi").pw_uid
            gid=pwd.getpwnam("mirbi").pw_gid
            os.chown(DIR,uid,gid)
    st=os.lstat(DIR)
    if not stat.S_ISDIR(st.st_mode) or st.st_mode&0o077 or stat.S_ISLNK(st.st_mode):
        raise RuntimeError("unsafe report directory")
    data={
        "schema":SCHEMA,"agent":args.agent,"phase":args.phase,
        "action":args.action,"pr":args.pr,"note":args.note,
        "source":args.source,"reported_at":datetime.now(timezone.utc).isoformat(),
    }
    path=DIR/(args.agent+".json")
    blob=(json.dumps(data,ensure_ascii=False,sort_keys=True)+"\n").encode("utf-8")
    if path.exists():
        old=os.lstat(path)
        if not stat.S_ISREG(old.st_mode) or old.st_nlink!=1 or old.st_mode&0o077:
            raise RuntimeError("unsafe report file")
    temp=DIR/("." + args.agent + "-" + str(os.getpid()) + ".tmp")
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(blob);f.flush();os.fsync(f.fileno())
        if os.geteuid()==0:
            os.chown(temp,st.st_uid,st.st_gid)
        os.replace(temp,path)
        dfd=os.open(DIR,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps({"ok":True,"agent":args.agent,"phase":args.phase,"recorded_at":data["reported_at"]}))
if __name__=="__main__":main()

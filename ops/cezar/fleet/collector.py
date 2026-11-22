#!/usr/bin/env python3
"""Cezar fleet visibility: separate scheduled agents, live runs and model slots.

This is a READ-only observer, not an agent launcher. No claimed external
concurrency without an explicit live provider heartbeat. Public UI snapshot
contains no prompts, bot tokens, credentials, paths or private identifiers.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

SCHEDULES = Path("/var/lib/cezar/home/fleet/automations.json")
WEB = Path("/var/lib/cezar/runtime/node_modules/@open-mercato/cezar/web/dist/assets/cezar-fleet-r01.json")
PRIVATE = Path("/var/lib/cezar/home/fleet/current.json")
CEZAR = "http://127.0.0.1:4329/api/v1"
MODEL_PORTS = (48081,48082,48083,48084,48085)
MAX_BYTES = 500_000
FRESHNESS_SECONDS = 7200
ACTIVE = frozenset(("running","queued","starting","waiting"))
NOW = lambda: datetime.now(timezone.utc)


class FleetError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise FleetError("HTTP redirect forbidden")


def _get(url):
    if not (url.startswith(CEZAR + "/") or
            url in {f"http://127.0.0.1:{n}/slots" for n in MODEL_PORTS}):
        raise FleetError("unapproved observation endpoint")
    req=Request(url,headers={"Accept":"application/json","User-Agent":"cezar-fleet-r01/1"})
    client=build_opener(ProxyHandler({}),NoRedirect())
    with client.open(req, timeout=6) as response:
        if response.status!=200 or response.geturl()!=url:
            raise FleetError("unexpected telemetry HTTP response")
        data=response.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise FleetError("oversized telemetry")
    return json.loads(data)


def _parse_time(value):
    if not isinstance(value,str) or len(value)>55:
        return None
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def _schedule_list(now):
    statinfo=os.lstat(SCHEDULES)
    if (not stat.S_ISREG(statinfo.st_mode) or statinfo.st_uid!=os.geteuid()
            or statinfo.st_nlink!=1 or statinfo.st_mode & 0o077):
        raise FleetError("schedule snapshot must be private owner-only regular file")
    raw=SCHEDULES.read_bytes()
    if len(raw)>32768:raise FleetError("schedule snapshot size")
    source=json.loads(raw)
    if source.get("schema")!="cezar.external-automations.v1":
        raise FleetError("unknown schedule snapshot")
    timestamp=_parse_time(source.get("observed_at"))
    records=source.get("agents")
    if not isinstance(records,list) or len(records)>24:
        raise FleetError("invalid schedule entries")
    items=[]
    for record in records:
        if not isinstance(record,dict):
            raise FleetError("invalid schedule")
        title=record.get("title")
        if not isinstance(title,str) or len(title)>100:
            raise FleetError("bad schedule name")
        if title.startswith("Кубик D3 mojum — peer "):
            role="developer"
        elif title=="Mojum — независимая приёмка":
            role="qa"
        else:
            raise FleetError("non-Mojum schedule")
        if type(record.get("enabled")) is not bool:
            raise FleetError("invalid enabled state")
        last=_parse_time(record.get("last_run"))
        nxt=_parse_time(record.get("next_run"))
        reported_status=record.get("last_status")
        if reported_status not in (None,"completed","in_progress","failed","no_update"):
            raise FleetError("unexpected automation run state")
        finished=_parse_time(record.get("last_completed"))
        items.append({
            "name":title,"role":role,"enabled":record["enabled"],
            "last_run":last.isoformat() if last else None,
            "next_run":nxt.isoformat() if nxt else None,
            "last_status":reported_status,
            "last_completed":finished.isoformat() if finished else None,
            "last_90m":bool(last and timedelta(0)<=now-last<=timedelta(minutes=90)),
        })
    recent=timestamp is not None and timedelta(0)<=now-timestamp<=timedelta(seconds=FRESHNESS_SECONDS)
    return {
        "source":"ChatGPT Automations",
        "observed_at":timestamp.isoformat() if timestamp else None,
        "snapshot_fresh":recent,
        "enabled":sum(i["enabled"] for i in items),
        "developers_enabled":sum(i["enabled"] and i["role"]=="developer" for i in items),
        "qa_enabled":sum(i["enabled"] and i["role"]=="qa" for i in items),
        "ran_last_90m":sum(i["last_90m"] for i in items),
        "running":None,  # Scheduling is NOT a live execution heartbeat
        "agents":items,
    }


def _models():
    def one(port):
        try:
            slots=_get(f"http://127.0.0.1:{port}/slots")
            if not isinstance(slots,list) or not 1<=len(slots)<=64:
                raise FleetError("unexpected slot data")
            busy=0
            for s in slots:
                if not isinstance(s,dict) or type(s.get("is_processing")) is not bool:
                    raise FleetError("malformed model slot")
                busy+=int(s["is_processing"])
            return {"port":port,"slots":len(slots),"busy":busy,"online":True}
        except (OSError,ValueError,TypeError,TimeoutError):
            return {"port":port,"slots":None,"busy":None,"online":False}
    with ThreadPoolExecutor(max_workers=len(MODEL_PORTS)) as pool:
        endpoints=list(pool.map(one,MODEL_PORTS))
    known=[p for p in endpoints if p["online"]]
    return {
        "online_endpoints":len(known),"total_endpoints":len(endpoints),
        "capacity_slots_known":sum(p["slots"] for p in known),
        "inference_busy_known":sum(p["busy"] for p in known),
        "capacity_complete":len(known)==len(endpoints),
        "endpoints":endpoints,
    }


def _cezar():
    runs=_get(CEZAR+"/runs")
    if not isinstance(runs,list) or len(runs)>1000:
        raise FleetError("unexpected Cezar task list")
    counts={}
    for record in runs:
        if not isinstance(record,dict) or not isinstance(record.get("status"),str):
            raise FleetError("invalid Cezar run")
        status=record["status"]
        counts[status]=counts.get(status,0)+1
    return {"total":len(runs),
            "active":sum(n for s,n in counts.items() if s in ACTIVE),
            "statuses":counts}


def _github(now):
    # Native Cezar GitHub adapter, not an independent GitHub client.
    data=_get(CEZAR+"/github?limit=50")
    if not isinstance(data,dict) or data.get("available") is not True or data.get("repo")!="Garyu-tt/mojum":
        raise FleetError("Cezar GitHub tab unavailable")
    prs=data.get("prs")
    if not isinstance(prs,list):
        raise FleetError("invalid PR listing")
    recent=0
    for item in prs:
        if not isinstance(item,dict):
            continue
        created=_parse_time(item.get("createdAt"))
        if created and timedelta(0)<=now-created<=timedelta(minutes=90):
            recent+=1
    return {
        "repo":"Garyu-tt/mojum","open_prs":data.get("prsTotal"),
        "open_issues":data.get("issuesTotal"),
        "new_prs_last_90m_visible":recent,
        "visibility":"open PR artifacts; NOT live agent executions",
    }


def collect(now=None):
    now=now or NOW()
    observations={"schema":"cezar.fleet.visibility.v1","generated_at":now.isoformat()}
    sections=[
        ("cezar",lambda:_cezar()),
        ("schedules",lambda:_schedule_list(now)),
        ("models",_models),
        ("github",lambda:_github(now)),
    ]
    # A temporary failure in one source is explicit, never silently zero.
    for name,run in sections:
        try:
            observations[name]=run()
        except (OSError,ValueError,TypeError,TimeoutError,json.JSONDecodeError) as exc:
            observations[name]={"available":False,"reason":type(exc).__name__}
    observations["external_running_confirmed"]=None
    observations["external_running_explanation"]="No live heartbeat from ChatGPT/external executors; not equal to zero."
    observations["truth_contract"]={
        "cezar_active":"only Cezar-owned runs",
        "schedule_enabled":"configured automations, not running jobs",
        "inference_busy":"live llama-server slot occupancy, not worker identity",
        "open_prs":"GitHub artifacts, not active workers",
    }
    return observations


def _owner_dir(path):
    statinfo=os.lstat(path)
    if not stat.S_ISDIR(statinfo.st_mode) or statinfo.st_uid!=os.geteuid() or statinfo.st_mode & 0o077:
        raise FleetError("destination directory must be private")


def save(data,path):
    if path not in (WEB,PRIVATE):
        raise FleetError("unsupported output path")
    _owner_dir(path.parent)
    blob=(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8")
    if len(blob)>MAX_BYTES:
        raise FleetError("fleet snapshot too large")
    if path.exists():
        p=os.lstat(path)
        if not stat.S_ISREG(p.st_mode) or p.st_uid!=os.geteuid() or p.st_nlink!=1 or p.st_mode & 0o077:
            raise FleetError("unsafe existing telemetry target")
    temp=path.parent/("."+path.name+".tmp."+str(os.getpid()))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(blob);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        try:temp.unlink()
        except FileNotFoundError:pass


if __name__=="__main__":
    info=collect()
    save(info,PRIVATE)
    save(info,WEB)
    print(json.dumps({
        "cezar_active":info["cezar"].get("active"),
        "configured_agents":info["schedules"].get("enabled"),
        "schedule_snapshot_fresh":info["schedules"].get("snapshot_fresh"),
        "model_slots":info["models"].get("capacity_slots_known"),
        "model_busy":info["models"].get("inference_busy_known"),
        "open_prs":info["github"].get("open_prs"),
        "external_live_confirmed":info["external_running_confirmed"],
    },sort_keys=True))

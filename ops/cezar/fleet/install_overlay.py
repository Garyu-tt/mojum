#!/usr/bin/env python3
"""Reinstall read-only Cezar Fleet widget after an upstream cockpit rebuild.

Fails closed on symlinks, ambiguous index.html, existing unrelated backups
and nonprivate deployed files. Does not restart Cezar or touch native JS
bundles; backup is preserved for rollback.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import stat

DIST = Path("/var/lib/cezar/runtime/node_modules/@open-mercato/cezar/web/dist")
BACKUP = Path("/var/lib/cezar/home/fleet/index-original-r01.html")
SCRIPT = Path(__file__).with_name("overlay.js")
JS_NAME = "cezar-fleet-r01.js"
MARKER = '<script defer src="/assets/cezar-fleet-r01.js"></script>'
MAX_INDEX = 300_000
MAX_SCRIPT = 120_000


class DeployRefused(ValueError):
    pass


def private_dir(path):
    info=os.lstat(path)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
            or info.st_mode & 0o077):
        raise DeployRefused("target directory not owner-private")


def regular_file(path):
    info=os.lstat(path)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
            or info.st_nlink!=1 or info.st_mode & 0o077):
        raise DeployRefused("target is not a private regular file")
    return info


def _write_once(dest,data):
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())


def _replace(dest,data):
    temp=dest.parent/("."+dest.name+".fleet-tmp."+str(os.getpid()))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(temp,dest)
    finally:
        try:temp.unlink()
        except FileNotFoundError:pass


def install(dist=DIST,script=SCRIPT,backup=BACKUP):
    private_dir(dist)
    private_dir(dist/"assets")
    private_dir(backup.parent)
    index=dist/"index.html"
    if regular_file(index).st_size>MAX_INDEX:
        raise DeployRefused("index size exceeded")
    js=script.read_bytes()
    if not 1<=len(js)<=MAX_SCRIPT:
        raise DeployRefused("invalid fleet script")
    if (not script.is_file() or script.is_symlink()
            or not js.startswith(b"/* Cezar Fleet visibility.")):
        raise DeployRefused("wrong fleet module")
    target=dist/"assets"/JS_NAME
    if target.exists() or target.is_symlink():
        regular_file(target)
    data=index.read_text("utf-8")
    if data.count("</body>")!=1:
        raise DeployRefused("unrecognized cockpit HTML shell")
    installed=MARKER in data
    if data.count(MARKER)>1:
        raise DeployRefused("duplicate fleet script marker")
    if backup.exists() or backup.is_symlink():
        regular_file(backup)
        if not installed:
            raise DeployRefused("stale backup differs from rebuilt cockpit")
    elif not installed:
        _write_once(backup,data.encode("utf-8"))
    else:
        raise DeployRefused("injected UI without a known-good backup")
    _replace(target,js)
    if not installed:
        updated=data.replace("</body>",
                             f'    <!-- owner-private, read-only Cezar fleet -->\n'
                             f'    {MARKER}\n  </body>')
        _replace(index,updated.encode("utf-8"))
    return {"installed":True,"was_present":installed,
            "read_only":True,"backup_preserved":True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist",type=Path,default=DIST)
    parser.add_argument("--script",type=Path,default=SCRIPT)
    parser.add_argument("--backup",type=Path,default=BACKUP)
    args=parser.parse_args(argv)
    try:
        result=install(args.dist,args.script,args.backup)
    except (OSError,UnicodeError,DeployRefused) as exc:
        parser.exit(2,"fleet overlay install refused: "+type(exc).__name__+"\n")
    print("Cezar fleet overlay installed, backup preserved, was_present="+str(result["was_present"]).lower())


if __name__=="__main__":
    main()

#!/usr/bin/env python3
"""Cezar-to-Telegram: narrowly allowlisted local read-only HTTP gateway.

No credentials, no forwarding of arbitrary URL/method/headers, no write routes.
Exposed only through a pinned SSH loopback reverse tunnel to mk-neurocms.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import re
import urllib.parse
import urllib.request

UPSTREAM = "http://127.0.0.1:4329"
FLEET_FILE=Path("/var/lib/cezar/home/fleet/current.json")
BOUND = ("127.0.0.1", 4331)
MAX_BYTES = 900_000
CHECK = re.compile(r"^[1-9][0-9]{0,6}(?:,[1-9][0-9]{0,6}){0,7}$")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ValueError("upstream redirect denied")


NO_REDIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def route(path):
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    pairs = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    if parsed.path in ("/api/v1/health", "/api/v1/runs", "/api/v1/fleet") and not pairs:
        return parsed.path
    if parsed.path == "/api/v1/github":
        if set(pairs) - {"limit", "refresh"} or any(len(v) != 1 for v in pairs.values()):
            return None
        limit = pairs.get("limit", ["15"])[0]
        if not limit.isascii() or not limit.isdecimal() or not 1 <= int(limit) <= 50:
            return None
        refresh = pairs.get("refresh", ["0"])[0]
        if refresh not in ("0", "1"):
            return None
        return "/api/v1/github?" + urllib.parse.urlencode({"limit":limit,"refresh":refresh})
    if parsed.path == "/api/v1/github/checks":
        if set(pairs) != {"prs"} or len(pairs["prs"]) != 1 or not CHECK.fullmatch(pairs["prs"][0]):
            return None
        return "/api/v1/github/checks?prs=" + pairs["prs"][0]
    return None


class ReadOnlyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # No bot tokens or chat content ever appear in gateway logs.
        pass

    def do_GET(self):
        path = route(self.path)
        if not path:
            self.send_error(404, "route not available")
            return
        try:
            if path=="/api/v1/fleet":
                info=os.lstat(FLEET_FILE)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                        or info.st_nlink!=1 or info.st_mode & 0o077 or info.st_size>MAX_BYTES):
                    raise ValueError("unsafe local fleet telemetry")
                body=FLEET_FILE.read_bytes()
                obj=json.loads(body)
                if not isinstance(obj,dict) or obj.get("schema")!="cezar.fleet.visibility.v1":
                    raise ValueError("untrusted fleet telemetry schema")
            else:
                request = urllib.request.Request(UPSTREAM + path, headers={
                    "Accept":"application/json", "User-Agent":"cezar-bot-readonly-proxy/1.0"
                })
                with NO_REDIRECT.open(request,timeout=16) as response:
                    if response.status != 200 or response.geturl() != UPSTREAM + path:
                        raise ValueError("unexpected upstream")
                    body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES or not isinstance(json.loads(body), (list,dict)):
                    raise ValueError("invalid upstream response")
            self.send_response(200)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError,ValueError,UnicodeError,TimeoutError) as exc:
            self.send_error(503,"upstream unavailable")

    def do_POST(self):
        self.send_error(405,"read-only gateway")

    def do_PUT(self):
        self.send_error(405,"read-only gateway")

    def do_DELETE(self):
        self.send_error(405,"read-only gateway")

    def do_PATCH(self):
        self.send_error(405,"read-only gateway")


if __name__ == "__main__":
    ThreadingHTTPServer(BOUND, ReadOnlyHandler).serve_forever(poll_interval=0.5)

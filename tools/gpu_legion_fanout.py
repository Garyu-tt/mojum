#!/usr/bin/env python3
"""CPU-first admission-controlled model fanout for Spartan agent clones.

Mojum: up to 300 *logical* agent tasks with independent replica requests.
This does not launch GPU models or create systemd units. It derives actual
concurrency from already-live loopback llama.cpp /slots endpoints. Duplicate
agent requests are routed to separate pre-existing physical model replicas.

Default is plan-only. Executing requires --execute and --output (0600).
Only 127.0.0.1:18180/18181 are allowed; never contacts arbitrary services,
follows redirects, uses proxies, runs generated code or exposes credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

PORTS = (18180, 18181)
MODEL = "GPT20B"
MAX_LOGICAL_AGENTS = 300
MAX_TASKS = 300
MAX_REQUESTS = 600
MAX_RUNNING = 16
MAX_CONTENT_BYTES = 65536
MAX_PROMPT_CHARS = 8192
MAX_TASK_FILE_BYTES = 16 * 1024 * 1024


class FanoutError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise FanoutError("loopback redirect rejected")


def _client():
    return build_opener(ProxyHandler({}), _NoRedirect())


def _request_json(port: int, path: str, *, body=None, timeout=5, opener=None):
    if type(port) is not int or port not in PORTS:
        raise FanoutError("endpoint not in approved 127.0.0.1 allowlist")
    if path not in ("/health", "/slots", "/v1/chat/completions"):
        raise FanoutError("endpoint path not allowed")
    if body is not None and path != "/v1/chat/completions":
        raise FanoutError("writes only permitted to completion API")
    if not 1 <= timeout <= 75:
        raise FanoutError("timeout outside 1..75 seconds")
    binary = None if body is None else json.dumps(body, allow_nan=False).encode()
    if binary is not None and len(binary) > 32768:
        raise FanoutError("request body exceeds 32 KiB")
    url = f"http://127.0.0.1:{port}{path}"
    req = Request(url, data=binary, method="GET" if body is None else "POST",
                  headers={"Accept": "application/json",
                           "Content-Type": "application/json"})
    try:
        with (opener or _client()).open(req, timeout=timeout) as response:
            if response.status != 200:
                raise FanoutError(f"endpoint HTTP status {response.status}")
            if not response.headers.get("Content-Type","").lower().startswith(
                    "application/json"):
                raise FanoutError("non-JSON response")
            data = response.read(MAX_CONTENT_BYTES + 1)
            if len(data) > MAX_CONTENT_BYTES:
                raise FanoutError("oversize inference response")
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise FanoutError(f"loopback endpoint unavailable: {type(exc).__name__}") from exc
    try:
        return json.loads(data, parse_constant=lambda x: (_ for _ in ()).throw(
            FanoutError("nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise FanoutError("invalid JSON response") from exc


@dataclass(frozen=True)
class Backend:
    port: int
    total_slots: int
    free_slots: int
    per_slot_context: int


@dataclass(frozen=True)
class Work:
    task_id: str
    agent_id: int
    clone: int
    target_port: int
    system: str
    prompt: str
    max_tokens: int


def discover_backend(port, *, request=_request_json):
    health = request(port, "/health")
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise FanoutError(f"backend {port}: not ready")
    slots = request(port, "/slots")
    if not isinstance(slots, list) or not 1 <= len(slots) <= MAX_RUNNING:
        raise FanoutError(f"backend {port}: bad slot response")
    seen = set()
    contexts = []
    for slot in slots:
        if not isinstance(slot, dict):
            raise FanoutError("invalid slot object")
        sid, ctx, busy = slot.get("id"), slot.get("n_ctx"), slot.get("is_processing")
        if type(sid) is not int or sid in seen or type(ctx) is not int or ctx < 256:
            raise FanoutError("malformed slot id/context")
        if type(busy) is not bool:
            raise FanoutError("missing authoritative slot busy flag")
        contexts.append(ctx)
        seen.add(sid)
    free = sum(not slot["is_processing"] for slot in slots)
    return Backend(port, len(slots), free, min(contexts))


def _unique_json_object(pairs):
    """Reject conflicting or repeated task fields before task admission."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise FanoutError("duplicate tasks JSON field")
        result[key] = value
    return result


def load_tasks_file(path):
    """Read a bounded regular JSON file, never a blocking FIFO or device.

    This check precedes json.loads, which otherwise loads unbounded attacker-
    supplied bytes into memory. Verify the opened inode instead of relying
    on a racy pathname stat. Do not follow final symlinks where supported.
    """
    fd = -1
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FanoutError("tasks input must be a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(MAX_TASK_FILE_BYTES + 1)
    except OSError as exc:
        raise FanoutError("cannot read tasks file: " + type(exc).__name__) from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > MAX_TASK_FILE_BYTES:
        raise FanoutError("tasks file byte limit exceeded")
    try:
        return json.loads(raw.decode("utf-8"),
                          object_pairs_hook=_unique_json_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              FanoutError("nonfinite task JSON")))
    except FanoutError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FanoutError("invalid tasks JSON") from exc


def validate_tasks(data):
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_TASKS:
        raise FanoutError("expected list of 1..300 logical agent tasks")
    seen = set()
    total = 0
    valid = []
    for item in data:
        if not isinstance(item, dict):
            raise FanoutError("task must be a JSON object")
        task_id = item.get("id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", task_id):
            raise FanoutError("invalid task id")
        if task_id in seen:
            raise FanoutError("duplicate task identity")
        seen.add(task_id)
        agent = item.get("agent_id")
        clone_count = item.get("clones", 1)
        prompt = item.get("prompt")
        system = item.get("system", "You are a sandboxed analytical agent. Do not claim external actions.")
        max_tokens = item.get("max_tokens", 128)
        if type(agent) is not int or not 1 <= agent <= MAX_LOGICAL_AGENTS:
            raise FanoutError("agent_id must be an integer in 1..300")
        if type(clone_count) is not int or clone_count not in (1,2):
            raise FanoutError("clones must be 1 or 2")
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= MAX_PROMPT_CHARS:
            raise FanoutError("prompt too long or empty")
        if not isinstance(system, str) or len(system)>4096:
            raise FanoutError("system prompt too large")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 256:
            raise FanoutError("max_tokens outside 1..256")
        total += clone_count
        if total > MAX_REQUESTS:
            raise FanoutError("expanded workload exceeds 600 clones")
        valid.append((task_id, agent, clone_count, system, prompt, max_tokens))
    return valid


def plan(validated, backends, *, limit=MAX_REQUESTS):
    healthy = [b for b in backends if b.free_slots > 0]
    if not healthy:
        raise FanoutError("no free live model slots")
    if type(limit) is not int or not 1 <= limit <= MAX_REQUESTS:
        raise FanoutError("invalid request limit")
    weights = Counter()
    work = []
    for task_id, agent, clones, system, prompt, max_tokens in validated:
        if clones==2 and len(healthy)<2:
            raise FanoutError("two clones require two distinct healthy physical replicas")
        for clone in range(clones):
            if len(work) >= limit:
                raise FanoutError("job count exceeds explicitly permitted budget")
            if clones == 2:
                target = healthy[clone]
            else:
                target = min(healthy, key=lambda b:(weights[b.port]/b.free_slots,b.port))
            weights[target.port] += 1
            work.append(Work(task_id,agent,clone,target.port,system,prompt,max_tokens))
    return work


def infer(job: Work, *, request=_request_json):
    sys_prompt = (
        f"Logical agent identity: {job.agent_id}; independent clone: {job.clone}. "
        "This is a text-only inference request. Do not claim to have run code.\n"
        + job.system)
    body = {"model": MODEL,
            "messages": [{"role":"system","content":sys_prompt},
                         {"role":"user","content":job.prompt}],
            "max_tokens":job.max_tokens,"temperature":0,"stream":False}
    begin=time.monotonic()
    data=request(job.target_port, "/v1/chat/completions", body=body, timeout=75)
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        raise FanoutError("model returned no choices")
    message=data["choices"][0].get("message",{})
    text=message.get("content")
    if not isinstance(text,str):
        raise FanoutError("model response text missing")
    usage=data.get("usage",{})
    used=usage.get("completion_tokens")
    if type(used) is not int or not 0 <= used <= job.max_tokens:
        raise FanoutError("invalid token usage")
    return {"task_id":job.task_id,"agent_id":job.agent_id,
            "clone":job.clone,"port":job.target_port,
            "completion_tokens":used,"latency_s":round(time.monotonic()-begin,3),
            "text":text}


def execute(work, backends, *, caller=infer):
    groups = {b.port: [] for b in backends if b.free_slots > 0}
    for w in work:
        groups[w.target_port].append(w)
    output = []
    def safe_call(job):
        try:
            return caller(job)
        except Exception as exc:
            # A failed model request may already have spent tokens. Persist
            # its identity without leaking prompt, response or error details.
            # Never automatically retry an uncertain request.
            return {"task_id": job.task_id, "agent_id": job.agent_id,
                    "clone": job.clone, "port": job.target_port,
                    "failed": True, "error_type": type(exc).__name__}

    def run_group(port, jobs):
        capacity = next(b.free_slots for b in backends if b.port==port)
        with ThreadPoolExecutor(max_workers=capacity) as executor:
            futures = [executor.submit(safe_call,j) for j in jobs]
            return [future.result() for future in futures]
    with ThreadPoolExecutor(max_workers=len(groups)) as executor:
        futures = [executor.submit(run_group,p,jobs) for p,jobs in groups.items() if jobs]
        for future in as_completed(futures):
            output.extend(future.result())
    return sorted(output,key=lambda x:(x["task_id"],x["clone"]))


def _require_owner_controlled_output_directory(path):
    """Refuse replaceable output path ancestors before model calls.

    O_EXCL and mode 0600 protect the opened inode, but a different local
    account can still unlink it during inference if the parent is writable.
    Same-UID races are outside this POSIX owner/mode preflight.
    """
    if os.name != "posix":
        return
    if ".." in path.parts:
        raise FanoutError("output directory parent traversal refused")
    parent = Path(os.path.abspath(path)).parent
    for depth, directory in enumerate((parent, *parent.parents)):
        try:
            info = directory.lstat()
        except OSError as exc:
            raise FanoutError("cannot inspect output directory chain") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise FanoutError("output directory chain contains a symlink or non-directory")
        if depth == 0:
            unsafe = (info.st_uid != os.geteuid() or bool(info.st_mode & 0o022))
        else:
            unsafe = (info.st_uid not in (0, os.geteuid())
                      or (bool(info.st_mode & 0o022)
                          and not bool(info.st_mode & stat.S_ISVTX)))
        if unsafe:
            raise FanoutError("output directory chain is not owner-controlled")


@contextmanager
def reserve_private_output(path):
    """Atomically claim the 0600 output before any paid/model inference.

    A pre-execution Path.exists() check is racy. O_EXCL is the actual
    reservation; a failing inference run cannot leave an empty artifact.
    """
    path=Path(path)
    if not path.parent.is_dir():
        raise FanoutError("output directory does not exist")
    _require_owner_controlled_output_directory(path)
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
    if hasattr(os,"O_NOFOLLOW"):flags|=os.O_NOFOLLOW
    fd=os.open(path,flags,0o600)
    committed=False
    try:
        with os.fdopen(fd,"wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        committed=True
    finally:
        if not committed:
            path.unlink(missing_ok=True)


def exclusive_json_output(path, data):
    encoded=(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    with reserve_private_output(path) as stream:
        stream.write(encoded)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status",action="store_true",help="read-only loopback /health + /slots")
    ap.add_argument("--tasks",type=Path,help="JSON tasks file, 1..300 logical agent IDs")
    ap.add_argument("--execute",action="store_true",help="send bounded inference; otherwise only plan")
    ap.add_argument("--output",type=Path,help="new private 0600 JSON output; never overwritten")
    ap.add_argument("--max-jobs",type=int,default=32,
                    help="safety budget for expanded requests (default 32, max 600)")
    args=ap.parse_args(argv)
    if not args.status and args.tasks is None:
        ap.error("--status or --tasks required")
    if args.execute and (args.tasks is None or args.output is None):
        ap.error("--execute requires --tasks and --output")
    backends=[]
    for port in PORTS:
        try: backends.append(discover_backend(port))
        except FanoutError as exc:
            print(json.dumps({"backend":port,"status":"not_ready","reason":str(exc)}))
    if args.status and args.tasks is None:
        print(json.dumps({"active_physical_model_backends":len(backends),
             "observed_slot_capacity":sum(b.total_slots for b in backends),
             "currently_free_slots":sum(b.free_slots for b in backends),
             "logical_agent_capacity":MAX_LOGICAL_AGENTS,
             "backend_details":[b.__dict__ for b in backends],
             "no_models_started":True,"no_inference_performed":True},sort_keys=True))
        return 0
    tasks=load_tasks_file(args.tasks)
    work=plan(validate_tasks(tasks),backends,limit=args.max_jobs)
    if not args.execute:
        print(json.dumps({"planned_requests":len(work),
             "physical_models":len(backends),
             "actual_concurrent_slot_budget":sum(b.free_slots for b in backends),
             "per_model_assignments":dict(Counter(w.target_port for w in work)),
             "logical_workers_are_not_distinct_model_processes":True,
             "model_calls_performed":0},sort_keys=True))
        return 0
    # Refuse an occupied/unwritable artifact path BEFORE launching model calls.
    with reserve_private_output(args.output) as stream:
        results=execute(work,backends)
        failures=sum(result.get("failed") is True for result in results)
        report={"schema":"mojum.gpu-legion-fanout.v1",
                "physical_models":len(backends),
                "observed_total_slots":sum(b.total_slots for b in backends),
                "inference_requests_completed":len(results)-failures,
                "inference_requests_failed":failures,
                "partial_batch":failures != 0,
                "results":results}
        stream.write((json.dumps(report,ensure_ascii=False,
                                 sort_keys=True,indent=2)+"\n").encode())
    print(json.dumps({"completed":len(results)-failures,
                      "failed":failures,"saved_to":str(args.output),
                      "content_printed":False},sort_keys=True))
    return 2 if failures else 0


if __name__=="__main__":
    raise SystemExit(main())

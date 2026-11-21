"""Private single-process Unix-socket gateway to synthetic Aquarium state writes.

Linux/POSIX-local transport, not a network server, cryptographic identity,
distributed consensus, or safe boundary against processes with raw SQLite
file access. One process owns the GuardedAquarium for the service lifetime.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import sys

from .guarded_aquarium import GuardedAquarium
from .local_authority import AuthorityError
from .write_intent import IntentGuardedAquarium

MAX_RPC_BYTES = 64_000
_SOCKET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z", re.ASCII)
_HEADER = struct.Struct("!I")
_PEER = struct.Struct("3i")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON number")
    return result


def _json(raw):
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                            parse_float=_finite_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                ValueError("nonfinite JSON constant")))
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid UTF-8 JSON") from exc
    if type(result) is not dict:
        raise ValueError("RPC message must be JSON object")
    return result


def _read_exact(stream, count):
    chunks = []
    while count > 0:
        part = stream.recv(count)
        if not part:
            raise ValueError("truncated RPC message")
        chunks.append(part)
        count -= len(part)
    return b"".join(chunks)


def _receive(stream):
    (length,) = _HEADER.unpack(_read_exact(stream, _HEADER.size))
    if not 1 <= length <= MAX_RPC_BYTES:
        raise ValueError("invalid or oversized RPC message")
    return _json(_read_exact(stream, length))


def _send(stream, obj):
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
                     ensure_ascii=True).encode("ascii")
    if not 1 <= len(raw) <= MAX_RPC_BYTES:
        raise ValueError("RPC response too large")
    stream.sendall(_HEADER.pack(len(raw)) + raw)


def _private_directory(path, label, *, client_gid=None):
    """Require owner-only 0700, or strictly owned 0710 group-traverse socket dir."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError(label + " must be an absolute trusted directory")
    try:
        st = path.stat()
    except OSError as exc:
        raise ValueError(label + " is missing") from exc
    if (not stat.S_ISDIR(st.st_mode) or os.name != "posix"
            or st.st_uid != os.getuid()):
        raise ValueError(label + " requires a service-owned directory")
    if client_gid is None:
        if st.st_mode & 0o077:
            raise ValueError(label + " must be owned and private (0700)")
    elif (st.st_gid != client_gid or st.st_mode & 0o777 != 0o710):
        raise ValueError(label + " requires service-owned 0710 directory with client group")
    return path


def _private_sqlite_file(path, label):
    """Cross-UID mode: client group must have no access to DB or parent."""
    path = Path(path)
    _private_directory(path.parent, label + " parent")
    if path.is_symlink():
        raise ValueError(label + " cannot be symlinked")
    try:
        info = path.stat()
    except OSError as exc:
        raise ValueError(label + " unavailable") from exc
    if (not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not info.st_mode & stat.S_IRUSR
            or not info.st_mode & stat.S_IWUSR):
        raise ValueError(label + " must be private 0600 service-owned SQLite")
    return path



def _archive_name(value):
    if (type(value) is not str or _SOCKET_NAME.fullmatch(value) is None
            or value.startswith(".") or value in (".", "..")):
        raise ValueError("archive filename must be safe single-component name")
    return value


class WriterService:
    """Serial Unix AF_UNIX endpoint for a single GuardedAquarium instance.

    By default only same-UID peers are admitted. Separate-UID mode accepts
    exactly one configured client UID through a 0710 group-traverse parent
    and 0660 socket; Noosphere/registry data stay private to service UID.
    UID is not proof of a particular program or remote identity.
    """

    def __init__(self, socket_path, db_path, registry_path, seed, graph, *,
                 writer, epoch, generation, archive_dir,
                 client_uid=None, client_gid=None, durable_intents=False):
        if not hasattr(socket, "SO_PEERCRED") or not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("Linux AF_UNIX/SO_PEERCRED required")
        self._socket_path = Path(socket_path)
        if not self._socket_path.is_absolute() or not self._socket_path.name:
            raise ValueError("socket path must be absolute")
        self._owner_uid = os.getuid()
        if type(durable_intents) is not bool:
            raise ValueError("durable_intents must be explicit boolean")
        self._write_protocol = (
            "durable_intent_v1" if durable_intents else "legacy_local_guard_v1"
        )
        if (client_uid is None) != (client_gid is None):
            raise ValueError("client_uid and client_gid must be set together")
        self._client_gid = None
        self._allowed_client_uid = self._owner_uid
        if client_uid is not None:
            if (type(client_uid) is not int or not 0 < client_uid < 2**31
                    or type(client_gid) is not int or not 0 < client_gid < 2**31
                    or self._owner_uid == 0 or client_uid == self._owner_uid):
                raise ValueError("cross-UID mode needs separate non-root client/service UIDs and GID")
            if client_gid not in (os.getgid(), *os.getgroups()):
                raise ValueError("service must belong to the configured client socket group")
            self._client_gid = client_gid
            self._allowed_client_uid = client_uid
            _private_sqlite_file(db_path, "Aquarium journal")
            _private_sqlite_file(registry_path, "authority registry")
        _private_directory(self._socket_path.parent, "socket parent",
                           client_gid=self._client_gid)
        self._archive_dir = _private_directory(archive_dir, "archive directory")
        if os.path.lexists(self._socket_path):
            raise ValueError("socket name already exists; never unlink another listener")
        self._socket = None
        self._guard = None
        self._socket_identity = None
        self._running = False
        try:
            guard_class = IntentGuardedAquarium if durable_intents else GuardedAquarium
            # IntentGuardedAquarium refuses missing schema, any pending intent,
            # FROZEN ownership or journal-anchor mismatch BEFORE binding IPC.
            # The schema is intentionally never installed by this daemon.
            self._guard = guard_class(
                db_path, registry_path, seed, graph,
                writer=writer, epoch=epoch, generation=generation,
            )
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.bind(str(self._socket_path))
            self._socket_identity = self._path_identity()
            if self._client_gid is not None:
                os.chown(self._socket_path, -1, self._client_gid)
                self._socket_path.chmod(0o660)
            else:
                self._socket_path.chmod(0o600)
            self._socket.listen(16)
            self._socket.settimeout(0.2)
            self._running = True
        except BaseException:
            self.close()
            raise

    def _path_identity(self):
        try:
            info = self._socket_path.lstat()
        except OSError as exc:
            raise AuthorityError("writer socket missing or inaccessible") from exc
        if not stat.S_ISSOCK(info.st_mode):
            raise AuthorityError("writer socket is not a Unix socket")
        return (info.st_dev, info.st_ino)

    def _assert_isolation(self):
        """Recheck permission boundary before accepting each RPC."""
        if self._client_gid is None:
            return
        _private_directory(self._socket_path.parent, "socket parent",
                           client_gid=self._client_gid)
        _private_directory(self._archive_dir, "archive directory")
        _private_sqlite_file(self._guard._db_path, "Aquarium journal")
        _private_sqlite_file(self._guard._registry_path, "authority registry")
        st = self._socket_path.lstat()
        if (not stat.S_ISSOCK(st.st_mode) or st.st_uid != self._owner_uid
                or st.st_gid != self._client_gid
                or st.st_mode & 0o777 != 0o660):
            raise AuthorityError("writer socket permission boundary changed")

    def _status(self):
        checkpoint = self._guard.checkpoint()
        return {
            "status": "LOCAL_COOPERATIVE_WRITER_ONLY_NOT_NETWORK_FENCING",
            "write_protocol": self._write_protocol,
            "organism_id": checkpoint["agent_id"],
            "tick": checkpoint["tick"],
            "journal_head": checkpoint["journal_head"],
            "epoch": self._guard.epoch,
            "generation": self._guard.generation,
        }

    def _dispatch(self, command):
        op = command.get("op")
        if type(op) is not str:
            raise ValueError("RPC op must be text")
        if op == "status":
            if set(command) != {"op"}:
                raise ValueError("unexpected status fields")
            return self._status()

        if op in ("step", "acquire", "export"):
            required = {
                "step": {"op", "expected_generation", "dx", "dy"},
                "acquire": {"op", "expected_generation", "event"},
                "export": {"op", "expected_generation", "filename"},
            }[op]
            if set(command) != required:
                raise ValueError("incorrect fields for operation")
            generation = command["expected_generation"]
            if type(generation) is not int or generation != self._guard.generation:
                raise AuthorityError("stale RPC writer generation")
            if op == "step":
                # Reject incorrect JSON scalar types before invoking a mutator.
                if (type(command["dx"]) not in (int, float)
                        or type(command["dy"]) not in (int, float)):
                    raise ValueError("movement must contain numeric dx/dy")
                self._guard.step(dx=command["dx"], dy=command["dy"])
                return self._status()
            if op == "acquire":
                if type(command["event"]) is not dict:
                    raise ValueError("acquisition must be an event object")
                self._guard.acquire(command["event"])
                return self._status()

            name = _archive_name(command["filename"])
            manifest = self._guard.export_cold(self._archive_dir / name)
            return {**self._status(), "archive": name,
                    "manifest_sha256": manifest["manifest_sha256"]}
        raise ValueError("unsupported local writer operation")

    def serve_once(self):
        if not self._running:
            raise RuntimeError("writer service is stopped")
        if self._path_identity() != self._socket_identity:
            raise AuthorityError("writer socket replaced; fail closed")
        self._assert_isolation()
        try:
            conn, _ = self._socket.accept()
        except socket.timeout:
            return False
        with conn:
            conn.settimeout(2.0)
            try:
                uid = _PEER.unpack(
                    conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                    _PEER.size)
                )[1]
                if uid != self._allowed_client_uid:
                    raise PermissionError("local peer UID not enrolled")
                cmd = _receive(conn)
                answer = {"ok": True, "result": self._dispatch(cmd)}
            except (OSError, ValueError, PermissionError, OverflowError,
                    RecursionError, TimeoutError) as exc:
                # Never include private input, SQL internals or event payload
                # in an RPC error; safe for local metadata logs.
                answer = {"ok": False, "error": type(exc).__name__}
            try:
                _send(conn, answer)
            except (OSError, ValueError, TimeoutError):
                pass
        return True

    def serve_forever(self):
        try:
            while self._running:
                self.serve_once()
        finally:
            self.close()

    def close(self):
        self._running = False
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._guard is not None:
            self._guard.close()
            self._guard = None
        if self._socket_identity is not None:
            try:
                if self._path_identity() == self._socket_identity:
                    self._socket_path.unlink()
            except OSError:
                pass
            self._socket_identity = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def request(socket_path, message, *, timeout=2.0):
    """Send one bounded local JSON RPC request; no TCP or HTTP fallback."""
    if type(message) is not dict:
        raise ValueError("RPC request must be JSON object")
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError("invalid local request timeout")
    raw = json.dumps(message, sort_keys=True, separators=(",", ":"), allow_nan=False,
                     ensure_ascii=True).encode("ascii")
    if not 1 <= len(raw) <= MAX_RPC_BYTES:
        raise ValueError("request too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(os.fspath(socket_path))
        connection.sendall(_HEADER.pack(len(raw)) + raw)
        return _receive(connection)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private single-host Aquarium writer")
    modes = parser.add_subparsers(dest="mode", required=True)
    serve = modes.add_parser("serve")
    serve.add_argument("--socket", required=True)
    serve.add_argument("--db", required=True)
    serve.add_argument("--registry", required=True)
    serve.add_argument("--archive-dir", required=True)
    serve.add_argument("--writer", required=True)
    serve.add_argument("--epoch", required=True, type=int)
    serve.add_argument("--generation", required=True, type=int)
    serve.add_argument(
        "--durable-intents", action="store_true",
        help="opt-in prepared journal event hash in existing authority registry; "
             "requires separately installed write-intents schema; no auto-repair",
    )
    serve.add_argument("--client-uid", type=int,
                       help="one authorized separate client UID (not service UID)")
    serve.add_argument("--client-gid", type=int,
                       help="socket access group; requires --client-uid")
    root = Path(__file__).resolve().parents[2]
    serve.add_argument("--genome", type=Path, default=root / "examples/genesis_seed.json")
    serve.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    client = modes.add_parser("request")
    client.add_argument("--socket", required=True)
    args = parser.parse_args(argv)
    if args.mode == "request":
        try:
            raw = sys.stdin.buffer.read(MAX_RPC_BYTES + 1)
            if len(raw) > MAX_RPC_BYTES:
                raise ValueError("stdin request too large")
            response = request(args.socket, _json(raw))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(response, sort_keys=True))
        return 0 if response.get("ok") is True else 3

    try:
        seed = json.loads(args.genome.read_text("utf-8"))
        graph = json.loads(args.graph.read_text("utf-8"))
        with WriterService(
            args.socket, args.db, args.registry, seed, graph,
            writer=args.writer, epoch=args.epoch,
            generation=args.generation, archive_dir=args.archive_dir,
            client_uid=args.client_uid, client_gid=args.client_gid,
            durable_intents=args.durable_intents,
        ) as server:
            def shutdown(_signum, _frame):
                server._running = False

            old_term = signal.signal(signal.SIGTERM, shutdown)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                return 0
            finally:
                signal.signal(signal.SIGTERM, old_term)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

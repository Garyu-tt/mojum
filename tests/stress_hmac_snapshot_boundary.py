"""Actual 10k+ event SQLite journal: signed HMAC cache vs full CPU replay.

Heavy, intentional one-time CI job. Only disposable synthetic fixture;
no user files, private real organism data, GPU or external services.
No absolute performance assertions across GitHub runners.
"""
from pathlib import Path
import json
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mojum.aquarium import Aquarium
from mojum.verified_snapshot import create_key_file, load_snapshot


SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
FRAMES = 10_002
TAIL = 3


def main():
    with tempfile.TemporaryDirectory(prefix="mojum-hmac-stress-") as tmp:
        root = Path(tmp)
        db = root / "private.synthetic.sqlite3"
        cache = root / "synthetic.hmac.snapshot"
        key = root / "separate.hmac.key"
        create_key_file(key)

        def open_aquarium(use_cache):
            opts = dict(neural_mode="disabled", exploration_mode="scripted")
            if use_cache:
                opts.update(replay_snapshot_file=cache,
                            replay_snapshot_key_file=key)
            return Aquarium(db, SEED, GRAPH, **opts)

        started = time.monotonic()
        with open_aquarium(True) as q:
            for _ in range(FRAMES):
                q.step(dx=0.0, dy=0.0)
            before = q.checkpoint()
            saved = q.save_replay_snapshot()
        initial_build = time.monotonic() - started
        assert before["tick"] == FRAMES
        assert load_snapshot(cache, key)["tick"] == FRAMES
        assert len(saved["state_sha256"]) == 64

        # This intentionally creates a *stale* but HMAC-valid snapshot.
        # The journal grows; the cache must replay just three fresh events.
        with open_aquarium(False) as live:
            assert live.checkpoint() == before
            for _ in range(TAIL):
                live.step(dx=0.0, dy=0.0)
            final = live.checkpoint()

        began = time.monotonic()
        with open_aquarium(True) as cached:
            assert cached.tick == FRAMES + TAIL
            assert cached.checkpoint() == final
            assert cached._committed_event_count == FRAMES + TAIL + 1
            cached.save_replay_snapshot()
        cached_seconds = time.monotonic() - began

        began = time.monotonic()
        with open_aquarium(False) as replayed:
            assert replayed.tick == FRAMES + TAIL
            assert replayed.checkpoint() == final
        full_seconds = time.monotonic() - began

        assert load_snapshot(cache, key)["tick"] == FRAMES + TAIL
        size = cache.stat().st_size
        if os.name == "posix":
            assert cache.stat().st_mode & 0o077 == 0
            assert key.stat().st_mode & 0o077 == 0
        print(json.dumps({
            "status": "PASS_ACTUAL_10000_EVENT_HMAC_SNAPSHOT",
            "source": "synthetic_10002_frames_plus_3_tail_no_external_data",
            "frames_before_snapshot": FRAMES,
            "committed_tail_events": TAIL,
            "final_events": FRAMES + TAIL + 1,
            "signed_sidecar_bytes": size,
            "hmac_verified": True,
            "exact_checkpoint_equal_to_full_replay": True,
            "full_journal_chain_verified_in_both_modes": True,
            "seconds": {
                "initial_build_and_snapshot": round(initial_build, 3),
                "signed_cold_reopen_and_snapshot": round(cached_seconds, 3),
                "full_cold_reopen_without_snapshot": round(full_seconds, 3),
            },
            "scope": "no_Android_battery_or_fast_signature_proof",
        }, sort_keys=True))


if __name__ == "__main__":
    main()

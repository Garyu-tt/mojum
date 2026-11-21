"""Demonstration runner: by default writes output only to stdout."""

import argparse
import json
from importlib.resources import files
from pathlib import Path
from .sim import Simulation


def main():
    parser = argparse.ArgumentParser(description="Mojum R0.1 CPU-only graph fixture")
    parser.add_argument(
        "graph", nargs="?", type=Path, default=files("mojum").joinpath("data", "first_loop.json"),
        help="graph JSON path (defaults to the packaged first-loop fixture)",
    )
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--dt-ms", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.ticks <= 100_000 or not 1 <= args.dt_ms <= 1000:
        parser.error("ticks must be 1..100000, dt-ms must be 1..1000")
    runtime = Simulation(json.loads(args.graph.read_text(encoding="utf-8")), args.dt_ms * 1_000_000)
    last = None
    for _ in range(args.ticks):
        last = runtime.step()
    print(json.dumps({"status": "R0.1_SYNTHETIC_FIXTURE", "tick": runtime.tick, "graph_sha256": runtime.graph_hash, "last_frame": last, "last_episode": runtime.episodes[-1], "snapshot_sha256": runtime.snapshot()["sha256"]}, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

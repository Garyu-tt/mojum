"""#700(d): isolated visual-navigation benchmark, NOT an Aquarium adapter.

No dependency on ViZDoom in the default path; no SQLite, network, organism
identities, user files, video recordings, or hardware actuators. All policy
inputs come from the bounded RGB screen buffer, not Doom privileged variables.
"""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
from pathlib import Path

CONTRACT = "mojum.benchmark.vizdoom-vision/1"
VIZDOOM_VERSION = "1.3.3"
WIDTH, HEIGHT = 160, 120
FRAME_BYTES = WIDTH * HEIGHT * 3
MAX_STEPS = 256
ACTIONS = {
    "forward": (1, 0, 0),
    "left": (0, 1, 0),
    "right": (0, 0, 1),
}


def _bounded(seed, steps):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if type(steps) is not int or not 1 <= steps <= MAX_STEPS:
        raise ValueError(f"steps must be 1..{MAX_STEPS}")


def _vision(screen):
    """Return frame SHA256 and only coarse visible RGB brightness features."""
    if screen is None:
        raise ValueError("missing RGB screen")
    try:
        view = memoryview(screen)
        if view.nbytes != FRAME_BYTES or not view.c_contiguous:
            raise ValueError("unexpected screen size/layout")
        rgb = view.tobytes()
    except (TypeError, BufferError) as e:
        raise ValueError("invalid screen buffer") from e
    samples = []
    for x in (WIDTH // 4, WIDTH // 2, 3 * WIDTH // 4):
        brightness = 0
        for dy in (-16, 0, 16):
            for dx in (-8, 0, 8):
                offset = (HEIGHT // 2 + dy) * WIDTH * 3 + (x + dx) * 3
                brightness += sum(rgb[offset:offset + 3])
        samples.append(brightness // 27)
    return hashlib.sha256(rgb).hexdigest(), tuple(samples)


def _choose(policy, brightness):
    if policy == "frozen":
        return "forward"
    if policy != "vision":
        raise ValueError("policy must be frozen or vision")
    left, center, right = brightness
    if center >= 90:
        return "forward"
    return "left" if left >= right else "right"


def run_episode(factory, *, seed, steps, policy):
    """Run one isolated episode with injected factory(seed, steps).

    The factory receives no Mojum organism, journal, or privileged state.
    """
    _bounded(seed, steps)
    if policy not in ("frozen", "vision"):
        raise ValueError("unsupported policy")
    game = factory(seed, steps)
    trace = []
    try:
        game.new_episode()
        for tick in range(steps):
            if game.is_episode_finished():
                break
            state = game.get_state()
            if state is None:
                raise ValueError("engine returned no state before terminal")
            image_hash, brightness = _vision(state.screen_buffer)
            name = _choose(policy, brightness)
            reward = game.make_action(list(ACTIONS[name]), 1)
            if type(reward) not in (float, int) or not math.isfinite(reward):
                raise ValueError("invalid engine reward")
            trace.append({"tick": tick, "frame_sha256": image_hash,
                          "action": name, "reward": float(reward)})
        total = sum(item["reward"] for item in trace)
        if not math.isfinite(total):
            raise ValueError("nonfinite total reward")
        fingerprint = hashlib.sha256(json.dumps(trace, separators=(",", ":"),
                                                sort_keys=True).encode("ascii")).hexdigest()
        return {"contract": CONTRACT, "seed": seed, "steps": len(trace),
                "policy": policy, "finished": bool(game.is_episode_finished()),
                "total_reward": total, "trace_sha256": fingerprint, "trace": trace}
    finally:
        game.close()


class _FakeState:
    def __init__(self, screen):
        self.screen_buffer = screen


class SyntheticVisualGame:
    """Tiny deterministic RGB fake grid; not Doom physics or a learning task.

    Goal is visible only as a coarse directional brightness hint. It exposes
    no hidden map, coordinates or goal position through the state API.
    """

    _DIRS = ((1, 0), (0, -1), (-1, 0), (0, 1))  # east, south, west, north
    _GOAL = (6, 2)
    _WALLS = frozenset({(3, 2)})

    def __init__(self, seed, steps):
        _bounded(seed, steps)
        self.seed, self.limit = seed, steps
        self.tick, self.pos, self.heading = 0, (0, 2), 0
        self.closed = False
        self._started = False

    def new_episode(self):
        self.tick, self.pos, self.heading = 0, (0, 2), 0
        self._started = True

    def is_episode_finished(self):
        return self.tick >= self.limit or self.pos == self._GOAL

    def _visible(self, direction):
        dx, dy = self._DIRS[direction]
        x, y = self.pos
        next_pos = (x + dx, y + dy)
        if not 0 <= next_pos[0] <= 6 or not 0 <= next_pos[1] <= 4 or next_pos in self._WALLS:
            return 10  # closed wall; visible as a dark direction
        before = abs(x - self._GOAL[0]) + abs(y - self._GOAL[1])
        after = abs(next_pos[0] - self._GOAL[0]) + abs(next_pos[1] - self._GOAL[1])
        return (185 if after < before else 125) - self.seed % 7

    def get_state(self):
        if not self._started or self.is_episode_finished():
            return None
        regions = (self._visible((self.heading - 1) % 4),
                   self._visible(self.heading),
                   self._visible((self.heading + 1) % 4))
        row = bytearray()
        for col in range(WIDTH):
            intensity = regions[min(2, 3 * col // WIDTH)]
            row.extend((intensity, intensity, intensity))
        return _FakeState(bytes(row) * HEIGHT)

    def make_action(self, action, tics):
        if tics != 1 or action not in (list(v) for v in ACTIONS.values()):
            raise ValueError("invalid action")
        if not self._started or self.is_episode_finished():
            raise ValueError("episode finished")
        self.tick += 1
        if action == list(ACTIONS["left"]):
            self.heading = (self.heading - 1) % 4
            return -0.05
        if action == list(ACTIONS["right"]):
            self.heading = (self.heading + 1) % 4
            return -0.05
        dx, dy = self._DIRS[self.heading]
        new_pos = (self.pos[0] + dx, self.pos[1] + dy)
        if not 0 <= new_pos[0] <= 6 or not 0 <= new_pos[1] <= 4 or new_pos in self._WALLS:
            return -0.2
        self.pos = new_pos
        return 5.0 if self.pos == self._GOAL else 1.0

    def close(self):
        self.closed = True


def _real_factory(seed, steps):
    """Explicit opt-in native engine, installed package scenario only."""
    if importlib.metadata.version("vizdoom") != VIZDOOM_VERSION:
        raise RuntimeError(f"ViZDoom must be pinned to {VIZDOOM_VERSION}")
    vzd = importlib.import_module("vizdoom")
    root = Path(vzd.scenarios_path).resolve(strict=True)
    wad = (root / "basic.wad").resolve(strict=True)
    if not wad.is_file() or not wad.is_relative_to(root):
        raise ValueError("packaged scenario path escaped installed ViZDoom")
    game = vzd.DoomGame()
    try:
        game.set_doom_scenario_path(str(wad))
        game.set_doom_map("map01")
        game.set_available_buttons([vzd.Button.MOVE_FORWARD,
                                    vzd.Button.TURN_LEFT, vzd.Button.TURN_RIGHT])
        game.set_mode(vzd.Mode.PLAYER)
        game.set_window_visible(False)
        game.set_sound_enabled(False)
        game.set_screen_resolution(vzd.ScreenResolution.RES_160X120)
        game.set_screen_format(vzd.ScreenFormat.RGB24)
        game.set_depth_buffer_enabled(False)
        game.set_labels_buffer_enabled(False)
        game.set_automap_buffer_enabled(False)
        game.set_objects_info_enabled(False)
        game.set_sectors_info_enabled(False)
        game.set_audio_buffer_enabled(False)
        game.set_seed(seed)
        game.set_episode_timeout(steps)
        game.init()
        return game
    except BaseException:
        game.close()
        raise


def benchmark(*, engine="fixture", seed=7, steps=32):
    _bounded(seed, steps)
    if engine not in ("fixture", "vizdoom"):
        raise ValueError("unsupported engine")
    factory = SyntheticVisualGame if engine == "fixture" else _real_factory
    results = {}
    for policy in ("frozen", "vision"):
        a = run_episode(factory, seed=seed, steps=steps, policy=policy)
        b = run_episode(factory, seed=seed, steps=steps, policy=policy)
        if a != b:
            raise RuntimeError(f"nondeterministic replay for {policy}")
        results[policy] = {key: value for key, value in a.items() if key != "trace"}
    return {"contract": CONTRACT, "engine": engine, "seed": seed,
            "requested_steps": steps, "replay_equal": True,
            "comparison": results, "learning": False, "organism_access": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="opt-in isolated ViZDoom vision benchmark")
    parser.add_argument("--engine", choices=("fixture", "vizdoom"), default="fixture")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--steps", type=int, default=32)
    args = parser.parse_args(argv)
    result = benchmark(engine=args.engine, seed=args.seed, steps=args.steps)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

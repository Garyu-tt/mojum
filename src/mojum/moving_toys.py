"""Deterministic CPU-only *kinematic* toy room for sensory-development QA.

Objects change their positions with virtual ticks and reflect at room edges.
No MuJoCo, forces, collisions with toys, velocities perceived by infant,
camera, remote service, privileged object IDs or physical motor channel.
All truth/trajectory configuration is held by the synthetic environment.
"""

from __future__ import annotations

import math

from .sensory_play import CuriosityError, Toy, ToyScene, DEFAULT_TOYS


MOTION_SCHEMA = "mojum.scene.kinematic-toys/1"
MOTION_SOURCE = "scripted_constant_speed_reflection_not_dynamic_physics"
MAX_SPEED_M_PER_TICK = 0.05
MAX_SIM_TICKS = 3999


def _numeric(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _bounce(initial, velocity, tick, lo, hi):
    """Triangle-wave reflection, stateless and deterministic per virtual tick."""
    if not (lo <= initial <= hi) or hi <= lo:
        raise CuriosityError("invalid kinematic travel interval")
    span = hi - lo
    offset = ((initial - lo) + velocity * tick) % (2.0 * span)
    return lo + (offset if offset <= span else 2.0 * span - offset)


class MovingToyScene(ToyScene):
    """Environment-only toy motion. Sensors receive ordinary local numeric cues.

    The selected toy identity and actual position remain *spectator-only*.
    The physics here is explicitly prescripted reflection, not learned
    motion, gravity or a MuJoCo world.
    """

    def __init__(self, *, width=4.0, height=4.0, toys=DEFAULT_TOYS,
                 velocities=((0.022, 0.0), (-0.015, 0.008))):
        super().__init__(width=width, height=height, toys=toys)
        if (type(velocities) is not tuple or len(velocities) != len(toys)):
            raise CuriosityError("one bounded velocity vector per toy required")
        clean = []
        for velocity in velocities:
            if (type(velocity) is not tuple or len(velocity) != 2
                    or any(not _numeric(x) for x in velocity)
                    or math.hypot(*velocity) > MAX_SPEED_M_PER_TICK):
                raise CuriosityError("invalid kinematic toy velocity")
            clean.append((float(velocity[0]), float(velocity[1])))
        if not any(x != 0 or y != 0 for x, y in clean):
            raise CuriosityError("kinematic fixture needs at least one moving toy")
        self.velocities = tuple(clean)

    def at(self, tick):
        """Spectator-only true toy positions at a requested committed tick."""
        if type(tick) is not int or not 0 <= tick <= MAX_SIM_TICKS:
            raise CuriosityError("kinematic virtual tick out of budget")
        result = []
        for toy, (vx, vy) in zip(self.toys, self.velocities):
            x = _bounce(toy.x, vx, tick, toy.radius_m,
                        self.room.width - toy.radius_m)
            y = _bounce(toy.y, vy, tick, toy.radius_m,
                        self.room.height - toy.radius_m)
            result.append(Toy(
                toy.id, x, y, toy.radius_m, toy.rgb, toy.softness,
            ))
        return tuple(result)

    def _nearest(self, state):
        # Hidden spectator IDs and softness cannot select which numeric cue
        # or measured contact the learner receives at equal distance.
        # Rank by observable geometry/reflectance only, and reject ties
        # that remain observationally indistinguishable at this tick.
        items = self.at(state.tick)
        if not items:
            return None

        def public_key(toy):
            return (
                # Reflected symmetric trajectories can differ by a few ULPs.
                # Canonicalize at sub-nanometre precision before observable
                # geometry tie-breaks, never by spectator-only identity.
                round(math.hypot(state.x - toy.x, state.y - toy.y), 12),
                toy.x, toy.y, toy.radius_m, toy.rgb,
            )

        ordered = sorted(items, key=public_key)
        if len(ordered) > 1 and public_key(ordered[0]) == public_key(ordered[1]):
            raise CuriosityError("ambiguous observationally identical moving toys")
        return ordered[0]

    def manifest(self):
        """Stable bootstrap configuration, not a mutable moving-world state."""
        details = super().manifest()
        details["kinematic_schema"] = MOTION_SCHEMA
        details["velocities_m_per_tick"] = [list(v) for v in self.velocities]
        details["kinematic_source"] = MOTION_SOURCE
        return details

    def spectator_at(self, tick):
        """Frame-specific true scene exclusively for opted-in local spectators."""
        frame = {
            "width": self.room.width, "height": self.room.height,
            "toys": [
                {"id": t.id, "x": t.x, "y": t.y, "radius_m": t.radius_m,
                 "rgb": list(t.rgb), "softness": t.softness}
                for t in self.at(tick)
            ],
            "spectator_only_toy_identity_and_softness": True,
            "kinematic_schema": MOTION_SCHEMA,
            "kinematic_source": MOTION_SOURCE,
            "truth_at_tick": tick,
        }
        return frame

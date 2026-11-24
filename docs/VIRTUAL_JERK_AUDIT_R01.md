# Virtual joint-jerk audit R0.1

`mojum.virtual-jerk-audit/1` is an opt-in, standard-library-only diagnostic for caller-supplied synthetic joint-position trajectories.

## Contract

- Input schema: `mojum.body.joint-trajectory/1`, explicit `PUBLIC_SYNTHETIC` classification and boolean consent.
- At least 4 and at most 4096 samples; at most 256 joints and 65,536 total position values.
- Exact native dictionaries/lists/scalars, unique bounded ASCII joint names, finite bounded positions and positive per-joint jerk limits.
- Strictly increasing integer nanosecond timestamps.
- A four-point Newton divided difference over each nonuniform time window estimates the third derivative directly; it is exact for cubic position trajectories. Each estimate is associated with the second sample in its four-sample window.
- Immutable report, threshold violations, deterministic SHA-256 consistency digest. The digest is not a signature or authenticity proof.

## Limitations

This is a numerical advisory over synthetic input. It is not calibrated biomechanics, a physical sensor, a controller, an actuator interlock, or a safety guarantee. No runtime, MuJoCo, SQLite, Noosphere, filesystem, network, or hardware integration is included. The module does not mutate its inputs.

## Run

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_virtual_jerk_audit.py' -v
```

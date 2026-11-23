# Synthetic point-source acoustic attenuation R0.1

Issue #1161, parent #7. Offline deterministic CPU-first geometry helper.

`sample_point_source(source_xy, listener_xy, source_db_at_1m)` returns a frozen
`SoundReading(distance_m, attenuation_db, received_db)`. Coordinates are meters,
the input level is a synthetic dB level at the configurable reference distance.

Model: `attenuation_db = 20 * log10(max(r, minimum_distance_m) / reference_distance_m)`.
This is an idealized free-field inverse-distance pressure law. Negative attenuation
inside the reference radius is intentional. Coincident points are clamped only for
the logarithm, while reported geometric distance remains zero.

Strict finite/bounded input validation prevents unbounded distances and NaN
propagation. This helper does not model reflections, walls, absorption, speech,
actual sound pressure calibration, human hearing, or live audio capture. It has no
I/O, clocks, random state, or runtime side effects.

Run tests with `PYTHONPATH=src python -m unittest discover -s tests -p test_world_acoustics.py`.

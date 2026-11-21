# R0.2 scripted creative-school fixture (draft)

This isolated offline Python-stdlib component demonstrates two scripted exercises. The R0.1 catalog is now in `main`, but the other nine sections, a learner policy, real evaluation, a school UI, and Noosphere integration do not exist. This CLI is a scripted fixture, not evidence of skill acquisition.

## Run on one CPU

Use a fresh output directory each time:

~~~sh
PYTHONPATH=src python -m mojum.school --catalog examples/school_catalog_r01.json --section visual_arts --out ./private-art-001 --start-tick 100
PYTHONPATH=src python -m mojum.school --catalog examples/school_catalog_r01.json --section music --out ./private-music-001 --start-tick 200
PYTHONPATH=src python -m mojum.school --catalog examples/school_catalog_r01.json --section music --mode frozen_control --out ./private-music-control-001 --start-tick 200
PYTHONPATH=src python -m unittest discover -s tests -p 'test_school_runner.py' -v
~~~

The program refuses to overwrite an existing directory. Files and directories are owner-only where POSIX permissions apply. Do not publish the private events.json or connect the output directly to a public viewer. File permissions and SHA-256 checksums do not establish consent, persistent authentication or protection against malicious same-UID filesystem races.

The CLI validates the artifacts and prepares the manifest before creating the directory. The export API also rejects caller-tampered event phases, timestamps, provenance shape, scripted artifact bytes, SHA-256 references, scores and feedback before filesystem writes. It only accepts exact outputs of the current deterministic scripted R0.2 fixture, not arbitrary student submissions. On a write error it removes the files and directory created by that attempt so the same output path can be retried. These checks establish internal consistency only; a caller-provided catalog SHA-256 is **not** proof of source authenticity, consent or independent learning.

Each run produces 5 PRIVATE records: observe → experiment → make → revise → reflect. The make and revise records link artifact bytes to SHA-256, size, revision tick, activity ID, catalog hash, and SCRIPTED_FIXTURE provenance. Art is a 24×24 ASCII PPM image; the deliberately scripted revision improves bilateral pixel mirror symmetry. Music is timing-event JSON in milliseconds; scripted revision reduces sum of absolute errors for four fixed target beats. No sound driver, camera, microphone or physical actuation is touched.

The standalone CLI labels `events.json` as `mojum.school-scripted-run/1` and each record as `mojum.school-scripted-event/1`. These identifiers describe the CLI's artifact and feedback fields, which differ from the lesson API in PR #172. To regenerate outputs from the earlier CLI draft with these identifiers, rerun the same command in a fresh directory.

The separate frozen_control run repeats the baseline artifact and produces no improvement. This is a scripted independent control, not a randomized or independently trained learner; comparisons demonstrate only that fixture feedback distinguishes changed versus unchanged output. Retention, transfer, genuine learning, subjective experience, interests, agency and grades are not demonstrated. No forced grades, punishment, hunger/thirst, age penalty or physical actuator instructions.

The catalog file and its canonical JSON are limited to 64 KiB, with up to 32 sections and verified activity IDs. Each run occupies 5 nonnegative virtual ticks and limits each artifact to 32 KiB. This library does not open or modify the Aquarium SQLite journal or Noosphere. Future authorized adapters must preserve explicit privacy/provenance before integration.

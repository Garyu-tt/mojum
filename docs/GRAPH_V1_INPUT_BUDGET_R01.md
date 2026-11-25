# Bounded executable Graph IR v1 JSON ingestion (R01)

Tracked under #25. This is a **loader resource bound**, not a new graph wire schema,
trusted-input policy, signature, permission grant, or change to executable graph
semantics. The canonical executable schema remains `mechanicus.universal-graph/1`.

## Contract

- Filesystem input must resolve to a **regular file**. The loader checks the
  opened descriptor before reading; symlinks to regular files remain supported.
  On POSIX it opens nonblocking so a writerless FIFO, including one reached
  through a symlink, is rejected promptly instead of hanging Aquarium or host
  startup. File descriptors close on rejected admission and read errors.
- `mojum.graph.load_graph(path)` reads at most **262,145 bytes** from the
  explicitly supplied local file and rejects any input over **262,144 bytes**
  with a typed `GraphError`, **before UTF-8 decoding and JSON parsing**.
  The limit includes whitespace, punctuation and the full UTF-8 byte width
  of non-ASCII strings. Exactly 262,144 bytes are allowed if otherwise valid.
- The existing duplicate-key, max-depth, nonfinite-number and canonical graph
  validation gates remain unchanged. Decoding failures now also use the
  typed `GraphError` boundary.
- Callers may accept smaller files only. This does not authenticate a file,
  constrain code-path memory beyond graph input, forbid symlinks or protect
  against another process changing a file concurrently.
- Graph v2 remains an **offline, non-executable** audit format. No v1/v2
  migration or journal bytes are rewritten by this change.

## Reproduction

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_byte_limit.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_json_depth.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_v1_lossless_json.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_file_safety.py' -v
```

The oversized-graph Aquarium subprocess regression additionally checks that
rejection occurs **before** any requested PRIVATE SQLite journal is created.
The special-file regressions run both Aquarium and portable-host commands with
subprocess deadlines and verify that rejected graph inputs create no journal.
Results are not accepted until exact-head CI and independent QA review.

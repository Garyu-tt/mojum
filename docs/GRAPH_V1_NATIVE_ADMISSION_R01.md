# Graph v1 direct-Python native admission R0.1

## Purpose

The executable Universal Graph v1 validator accepts both file-loaded JSON and
direct Python objects. A caller can therefore bypass the file loader's byte and
JSON-depth gates by constructing an in-memory mapping, or pass Python
subclasses whose custom iteration, equality, hashing, or lookup behavior can
run during validation.

This change makes the direct-Python boundary fail closed before schema
comparisons or graph topology processing.

## Contract

`validate(graph)` first admits a bounded JSON tree composed only of exact
built-in dictionaries, lists, strings, integers, floats, booleans, and null.
Object keys must be exact strings and valid UTF-8. It rejects:

- container and scalar subclasses;
- non-JSON Python values such as tuples, sets, bytes, and custom objects;
- cycles and shared container references (JSON has no alias representation);
- non-finite floats, unpaired surrogates, and excessively large integers;
- container nesting above `MAX_JSON_DEPTH` (scalar leaves do not add a level,
  matching the file loader), more than
  `MAX_GRAPH_TREE_NODES` tree entries (including pending traversal work before
  expanding containers), or serialized input above
  `MAX_GRAPH_BYTES` (256 KiB).

The native-tree gate runs before key-set equality, membership lookups, or
topological traversal. The existing graph v1 schema, port typing, node/edge
limits, deterministic topological order, and graph digest behavior for valid
JSON fixtures are unchanged.

## Reproduction

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_native_admission.py' -v
```

The focused tests cover a canonical graph, hostile dictionary/list/string
subclasses, malformed native values, finite-number rules, cycles/aliases,
depth/node/byte budgets, and stable valid-graph behavior.

The `graph-native-admission` workflow checks out and asserts the exact PR head
before running this suite on Python 3.11, 3.12, and 3.13. The suite also verifies
that file-loaded and direct inputs agree at the 256-container depth boundary,
including scalar leaves and empty containers, and both reject depth 257.

## Limitations

This is an in-process validation boundary, not a sandbox for arbitrary Python
code already executing in the process. It does not authenticate graph authors,
change the graph wire schema, authorize access to private data, or make graph
nodes executable plugins. The 256 KiB cap is a serialized-wire budget; the
tree-node cap additionally bounds direct callers before serialization.

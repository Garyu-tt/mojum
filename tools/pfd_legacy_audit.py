"""Read-only static inventory of the pinned legacy checkout. Never import target code."""
import argparse
import ast
import collections
import json
from pathlib import Path
import subprocess

def analyze(root):
    def git(*args):
        return subprocess.check_output(["git", "--no-replace-objects", "-C", str(root), *args])
    baseline = git("rev-parse", "HEAD").decode("ascii").strip()
    entries = {}
    for entry in git("ls-tree", "-r", "-z", baseline, "--", "src/mojum", "tests", ".github/workflows").split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        entries[Path(name.decode("utf-8"))] = (mode, kind, oid)
    paths = sorted(root / p for p in entries if p.is_relative_to("src/mojum") and p.suffix == ".py")
    def module(p):
        bits = list(p.relative_to(root / "src").with_suffix("").parts)
        if bits[-1] == "__init__":
            bits.pop()
        return ".".join(bits)
    modules, errors, ambiguous = {}, [], set()
    for p in paths:
        m = module(p)
        if m in modules:
            ambiguous.add(m)
            errors.append({"module":m,"error":"ambiguous module paths: " + str(modules[m].relative_to(root)) + ", " + str(p.relative_to(root))})
            continue
        modules[m] = p
    graph = {m: set() for m in modules}
    details = {}
    for m, p in modules.items():
        if m in ambiguous:
            continue
        private, dynamic, contracts, external, unresolved = [], [], set(), set(), set()
        try:
            mode, kind, oid = entries[p.relative_to(root)]
            if mode not in {"100644", "100755"} or kind != "blob":
                raise ValueError("not a regular committed Python file")
            tree = ast.parse(git("cat-file", "blob", oid).decode("utf-8"), filename=str(p))
        except (SyntaxError, UnicodeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
            errors.append({"module":m,"error":str(exc)})
            continue
        package = m.split(".") if p.name == "__init__.py" else m.split(".")[:-1]
        def record(name):
            if name in modules:
                graph[m].add(name)
            elif name.startswith("mojum.") or name == "mojum":
                unresolved.add(name)
            elif name:
                external.add(name.split(".")[0])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    record(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        unresolved.add("invalid-relative:" + str(node.level))
                        continue
                    prefix = ".".join(package[:len(package)-node.level+1])
                    target = ".".join(x for x in (prefix, node.module) if x)
                else:
                    target = node.module or ""
                if node.module:
                    record(target)
                for alias in node.names:
                    candidate = target + "." + alias.name
                    if candidate in modules:
                        graph[m].add(candidate)
                    elif not node.module:
                        record(target)
                    if (target == "mojum" or target.startswith("mojum.")) and alias.name.startswith("_") and target != m:
                        private.append({"target":target,"symbol":alias.name,"line":node.lineno})
            elif isinstance(node, ast.Call):
                f = node.func
                if ((isinstance(f, ast.Name) and f.id in {"__import__","import_module"}) or
                    (isinstance(f, ast.Attribute) and f.attr in {"import_module","__import__","spec_from_file_location"})):
                    dynamic.append(node.lineno)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if (value.startswith(("mojum.","mechanicus.")) and "/" in value
                    and len(value) <= 160 and not any(c.isspace() for c in value)):
                    contracts.add(value)
        graph[m].discard(m)
        details[m] = {"path":str(p.relative_to(root)), "private_imports":private,
                      "dynamic_import_sites":sorted(set(dynamic)),
                      "external_imports":sorted(external), "unresolved_internal":sorted(unresolved),
                      "wire_literals":sorted(contracts)}
    # Tarjan SCC: each group with >1 members is a static import cycle.
    index, low, stack, onstack, cycles = {}, {}, [], set(), []
    def visit(v):
        index[v] = low[v] = len(index)
        stack.append(v)
        onstack.add(v)
        for w in sorted(graph[v]):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group = []
            while True:
                w = stack.pop()
                onstack.remove(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1:
                cycles.append(sorted(group))
    for m in sorted(graph):
        if m not in index:
            visit(m)
    incoming = collections.Counter(w for deps in graph.values() for w in deps)
    for m, detail in details.items():
        detail.update(imports=sorted(graph[m]), fan_in=incoming[m], fan_out=len(graph[m]))
    tests = sorted(str(p) for p in entries if p.is_relative_to("tests") and p.suffix == ".py")
    workflows = sorted(str(p) for p, (mode, kind, oid) in entries.items()
                       if p.parent == Path(".github/workflows") and kind == "blob" and mode in {"100644", "100755"})
    return {"schema":"mojum.migration.static-audit/1",
            "baseline":baseline,
            "modules":details,"module_count":len(modules),"edge_count":sum(map(len,graph.values())),
            "cycles":sorted(cycles),"parse_errors":errors,
            "test_files":tests,"workflow_files":workflows,
            "standalone_candidates":sorted(m for m,d in details.items() if not d["imports"] and not d["dynamic_import_sites"] and not d["unresolved_internal"]),
            "limitations":["AST static imports only; conditional imports included; dynamic imports flagged heuristically.",
              "Wire literals are candidates, not proof of a stable public contract.",
              "No module execution, behavioral test, or plugin boundary approval."]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    p.add_argument("--output",type=Path,required=True)
    args = p.parse_args()
    report = analyze(args.root.resolve())
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("Baseline:",report["baseline"])
    print("Modules:",report["module_count"],"Edges:",report["edge_count"],"Cycles:",len(report["cycles"]))
    print("Parse errors:",report["parse_errors"])
    print("Highest fan-out:",sorted(((d["fan_out"],m) for m,d in report["modules"].items()),reverse=True)[:10])
    print("Highest fan-in:",sorted(((d["fan_in"],m) for m,d in report["modules"].items()),reverse=True)[:10])
    print("Static cycle groups:",report["cycles"])
    print("Independent candidates:",report["standalone_candidates"])
    return int(bool(report["parse_errors"]) or not report["module_count"])

if __name__ == "__main__":
    raise SystemExit(main())

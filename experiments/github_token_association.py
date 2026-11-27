"""Replay the first GitHub code token-association experiment (CPU only).
Read-only; uses approved public Mojum PRs, never executes remote source.
"""
import json
import re
from collections import Counter, defaultdict
from github_coder_corpus import pages

TRAIN = (1478, 1461, 1447, 1278, 1518)
TEST = (1416, 1511)
ROOT = "/repos/Garyu-tt/mojum/pulls/"
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\d+|[^\s]")


def load(n):
    result = []
    for f in pages(ROOT + str(n) + "/files"):
        path = f["filename"]
        if path.startswith("src/") and path.endswith(".py") and f.get("patch"):
            lines = [tuple(TOKEN.findall(s[1:])) for s in f["patch"].splitlines()
                     if s.startswith("+") and not s.startswith("+++")]
            result.append((path, [line for line in lines if line]))
    return result


def run():
    hold = [f for n in TEST for f in load(n)]
    hold_paths = {p for p, _ in hold}
    train = {line for n in TRAIN for p, lines in load(n)
             if p not in hold_paths for line in lines}
    test = {line for _, lines in hold for line in lines} - train
    edges = defaultdict(Counter)
    prior = Counter()
    for line in train:
        for a, b in zip(("^",) + line, line + ("$",)):
            edges[a][b] += 1
            prior[b] += 1
    # Stable tie-breaking; Python lexical ordering differs from JS localeCompare.
    def predict(c):
        return min(c, key=lambda k: (-c[k], k))
    baseline = predict(prior)
    total = good = control = 0
    for line in test:
        for a, b in zip(("^",) + line, line + ("$",)):
            total += 1
            good += predict(edges.get(a, prior)) == b
            control += baseline == b
    return dict(train_lines=len(train), heldout_lines=len(test),
                predictions=total, learned_correct=good, unigram_correct=control,
                context_nodes=len(edges), learned_edges=sum(map(len, edges.values())),
                mode="teacher-forced, no code execution")


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

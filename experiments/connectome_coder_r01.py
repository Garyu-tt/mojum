"""CPU-only reward-trained sparse policy for bounded program synthesis.
Not a general programmer, biological simulation, or materialized 10B graph.
"""
import argparse
import itertools
import json
import math
import random
from collections import defaultdict

OPS = ('inc', 'dec', 'double', 'neg', 'square', 'stop')
EXPR = ('v + 1', 'v - 1', 'v * 2', '-v', 'v * v')


def step(v, op):
    return (v + 1, v - 1, v * 2, -v, v * v)[op]


def features(values, goals, depth):
    # Sparse sensory population: quantized input/goal/residual bins.
    def bucket(n):
        return max(-8, min(8, n))
    f = [('bias',), ('depth', depth)]
    for i, (x, y) in enumerate(zip(values, goals)):
        f += [('pair', i, bucket(x), bucket(y)),
              ('delta', i, bucket(y-x)), ('sign', i, (x>0)-(x<0), (y>0)-(y<0))]
    return f


class Policy:
    def __init__(self):
        self.weights = defaultdict(float)

    def probs(self, values, goals, depth):
        f = features(values, goals, depth)
        scores = [sum(self.weights.get((k, a), 0.0) for k in f) for a in range(len(OPS))]
        e = [math.exp(s-max(scores)) for s in scores]
        return f, [v/sum(e) for v in e]

    def train(self, tasks, rng, episodes):
        successes = 0
        for _ in range(episodes):
            values, goals = rng.choice(tasks)
            trace = []
            for depth in range(4):
                f, p = self.probs(values, goals, depth)
                a = rng.choices(range(len(OPS)), weights=p)[0]
                trace.append((f, p, a))
                if a == 5 or depth == 3:
                    break
                values = tuple(step(v, a) for v in values)
            reward = 1.0 if a == 5 and values == goals else -0.03
            successes += reward == 1.0
            for f, p, a in trace:
                for k in f:
                    for j, pj in enumerate(p):
                        self.weights[k, j] += 0.025*reward*((j == a)-pj)/len(f)
        return successes

    def solve(self, values, goals, width=8):
        beam = [(0.0, (), values)]
        for depth in range(4):
            candidates = []
            for score, path, state in beam:
                _, p = self.probs(state, goals, depth)
                if state == goals and (depth == 3 or max(range(6), key=p.__getitem__) == 5 or width > 1):
                    return path
                if depth < 3:
                    for a in range(5):
                        candidates.append((score+math.log(max(p[a], 1e-300)), path+(a,), tuple(step(v,a) for v in state)))
            beam = sorted(candidates, reverse=True)[:width]
        return None


def source(path):
    return 'def program(x):\n    v = x\n' + ''.join('    v = '+EXPR[a]+'\n' for a in path) + '    return v\n'


def evaluate(path, inputs):
    for a in path:
        inputs = tuple(step(x,a) for x in inputs)
    return inputs


def benchmark(episodes=12000, seed=42):
    rng = random.Random(seed)
    train_x = (-3, -1, 2, 4)
    test_x = (-4, -2, 1, 3)
    hidden_x = (-7, -5, 0, 5, 8)
    paths = [p for n in range(1,4) for p in itertools.product(range(5), repeat=n)]
    rng.shuffle(paths)
    # Disjoint semantics, not just disjoint token sequences.
    unique = {}
    for p in paths:
        unique.setdefault(evaluate(p, train_x+test_x+hidden_x), p)
    paths = list(unique.values())
    split = int(len(paths)*0.8)
    train, test = paths[:split], paths[split:]
    tasks = [(train_x, evaluate(p, train_x)) for p in train]
    model = Policy()
    wins = model.train(tasks, rng, episodes)
    def assess(policy, width):
        passed = 0
        for target in test:
            p = policy.solve(test_x, evaluate(target, test_x), width)
            if p is None:
                continue
            env = {}
            # Only locally generated, fixed grammar source is executed.
            exec(compile(source(p), '<generated>', 'exec'), {'__builtins__': {}}, env)
            passed += tuple(env['program'](x) for x in hidden_x) == evaluate(target, hidden_x)
        return passed
    return {'episodes': episodes, 'reward_successes': wins,
            'learned_synaptic_weights': len(model.weights), 'train_tasks': len(train), 'heldout_tasks': len(test),
            'learned_beam8_pass': assess(model,8), 'untrained_beam8_pass': assess(Policy(),8),
            'learned_beam1_pass': assess(model,1), 'untrained_beam1_pass': assess(Policy(),1),
            'logical_target_neurons': 10_000_000_000, 'materialized_10b': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episodes', type=int, default=12000)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.episodes,args.seed), indent=2))

"""Deterministic mapped workloads; QFT excludes final bit-reversal swaps."""

import numpy as np

from .model import Circuit, Gate


def make_circuit(family="qaoa", n=4, depth=2, seed=7):
    if family not in {"ghz", "qft", "qaoa", "brickwork"}:
        raise ValueError(f"Unknown family {family}")
    if n < 2 or depth < 1:
        raise ValueError("Workloads require n >= 2 and depth >= 1")
    rng = np.random.default_rng(seed)
    gates = []

    def add(name, qs, angle=0):
        gates.append(Gate(name, tuple(qs), 1 if len(qs) == 1 else 8, float(angle)))

    if family == "ghz":
        add("h", [0])
        for q in range(n - 1):
            add("cx", [q, q + 1])
    elif family == "qft":
        # Nontrivial input avoids a benchmark dominated by QFT|0...0>.
        for q in range(n):
            add("ry", [q], rng.uniform(-np.pi, np.pi))
        for q in range(n):
            add("h", [q])
            for r in range(q + 1, n):
                add("cp", [r, q], np.pi / (2 ** (r - q)))
    elif family == "qaoa":
        for q in range(n):
            add("h", [q])
        for _ in range(depth):
            gamma, beta = rng.uniform(0.2, 0.9, 2)
            for parity in (0, 1):
                for q in range(parity, n - 1, 2):
                    add("cx", [q, q + 1])
                    add("rz", [q + 1], 2 * gamma)
                    add("cx", [q, q + 1])
            for q in range(n):
                add("rx", [q], 2 * beta)
    else:
        for layer in range(depth):
            for q in range(n):
                add("ry", [q], rng.uniform(-np.pi, np.pi))
                add("rz", [q], rng.uniform(-np.pi, np.pi))
            for q in range(layer % 2, n - 1, 2):
                add("cx", [q, q + 1])
    return Circuit(n, tuple(gates), f"{family}-n{n}-d{depth}-s{seed}")

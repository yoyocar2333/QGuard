"""Risk-aware beam search over precedence-augmented ASAP schedules.

The oracle is exact ONLY within this finite schedule family, not over all timed
schedules. Beam search is heuristic and does not provide an optimality guarantee.
"""

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import product
from math import isfinite

import numpy as np

from .model import Schedule


def asap(circuit, extra_edges=(), method="asap"):
    edges = set(circuit.dependencies()) | set(extra_edges)
    size = len(circuit.gates)
    successors = [[] for _ in range(size)]
    degree, starts = [0] * size, [0] * size
    for a, b in edges:
        if not 0 <= a < size or not 0 <= b < size or a == b:
            raise ValueError("Invalid precedence edge")
        successors[a].append(b)
        degree[b] += 1
    ready = []
    for i, d in enumerate(degree):
        if d == 0:
            heappush(ready, i)
    visited = 0
    while ready:
        a = heappop(ready)
        visited += 1
        for b in successors[a]:
            starts[b] = max(starts[b], starts[a] + circuit.gates[a].duration)
            degree[b] -= 1
            if degree[b] == 0:
                heappush(ready, b)
    if visited != size:
        raise ValueError("Precedence constraints contain a cycle")
    return Schedule(tuple(starts), method, tuple(sorted(set(extra_edges)))).validate(circuit)


def conflicts(circuit, device):
    """Return (gate a, gate b, interference-link indices), excluding ordered pairs."""
    device.validate_circuit(circuit)
    reachable = [set() for _ in circuit.gates]
    successors = [[] for _ in circuit.gates]
    for a, b in circuit.dependencies():
        successors[a].append(b)
    for a in reversed(range(len(circuit.gates))):
        for b in successors[a]:
            reachable[a].add(b)
            reachable[a].update(reachable[b])
    result = []
    for a, ga in enumerate(circuit.gates):
        if len(ga.qubits) != 2:
            continue
        for b in range(a + 1, len(circuit.gates)):
            gb = circuit.gates[b]
            if len(gb.qubits) != 2 or set(ga.qubits) & set(gb.qubits) or b in reachable[a]:
                continue
            links = tuple(
                k
                for k, (u, v) in enumerate(device.xtalk_edges)
                if (u in ga.qubits and v in gb.qubits) or (v in ga.qubits and u in gb.qubits)
            )
            if links:
                result.append((a, b, links))
    return tuple(result)


def overlap(circuit, schedule, a, b):
    return max(
        0,
        min(schedule.end(circuit, a), schedule.end(circuit, b))
        - max(schedule.starts[a], schedule.starts[b]),
    )


def empirical_cvar(values, alpha=0.8):
    """Exact upper-tail CVaR of an equally weighted empirical distribution.

    Fractional mass at the quantile handles sample counts not divisible by 1-alpha.
    """
    data = np.asarray(values, dtype=float)
    if data.ndim != 1 or not len(data) or not np.all(np.isfinite(data)):
        raise ValueError("CVaR requires a nonempty finite vector")
    if not 0 <= alpha < 1:
        raise ValueError("alpha must be in [0,1)")
    ordered = np.sort(data)[::-1]
    mass = len(data) * (1 - alpha)
    whole = min(int(np.floor(mass)), len(data))
    total = ordered[:whole].sum()
    if whole < len(data):
        total += (mass - whole) * ordered[whole]
    return float(total / mass)


class RiskModel:
    """Dimensionless surrogate, NOT a bound or estimate of state infidelity.

    R = gate Pauli-error sum + sum_q idle_q/(2*T2_q)
        + sum_overlap,link [rate*duration + (omega*duration/2)^2].
    """

    def __init__(self, circuit, device, scenarios, tail_weight=0.5, alpha=0.8):
        if not scenarios or not 0 <= tail_weight <= 1 or not 0 <= alpha < 1:
            raise ValueError("Invalid risk model parameters")
        for s in scenarios:
            s.validate_device(device)
        self.circuit, self.device, self.scenarios = circuit, device, tuple(scenarios)
        self.tail_weight, self.alpha = tail_weight, alpha
        self.pairs = conflicts(circuit, device)
        self.busy = np.array(
            [
                sum(g.duration for g in circuit.gates if q in g.qubits)
                for q in range(circuit.n_qubits)
            ]
        )
        self.idle_weights = 0.5 / np.array([s.t2 for s in scenarios])
        self.gate_cost = np.array(
            [sum(s.p1 if len(g.qubits) == 1 else s.p2 for g in circuit.gates) for s in scenarios]
        )
        self.linear = np.array(
            [[sum(s.zz_rate[k] for k in links) for _, _, links in self.pairs] for s in scenarios]
        )
        self.quadratic = np.array(
            [
                [sum(s.zz_omega[k] ** 2 / 4 for k in links) for _, _, links in self.pairs]
                for s in scenarios
            ]
        )
        self.cache = {}

    def losses(self, schedule):
        schedule.validate(self.circuit)
        exposure = np.array([overlap(self.circuit, schedule, a, b) for a, b, _ in self.pairs])
        idle = schedule.makespan(self.circuit) - self.busy
        return (
            self.gate_cost
            + self.idle_weights @ idle
            + self.linear @ exposure
            + self.quadratic @ exposure**2
        )

    def score(self, schedule):
        if schedule.starts not in self.cache:
            losses = self.losses(schedule)
            self.cache[schedule.starts] = float(
                (1 - self.tail_weight) * losses.mean()
                + self.tail_weight * empirical_cvar(losses, self.alpha)
            )
        return self.cache[schedule.starts]


def conservative(circuit, device):
    # Input-order orientation is acyclic because all original dependencies point forward.
    edges = tuple((a, b) for a, b, _ in conflicts(circuit, device))
    return asap(circuit, edges, "conservative")


@dataclass(frozen=True)
class SearchResult:
    schedule: Schedule
    score: float
    evaluated_schedules: int
    rounds: int


def optimize(
    circuit,
    device,
    scenarios,
    *,
    tail_weight=0.5,
    alpha=0.8,
    beam_width=12,
    max_rounds=6,
    max_stretch=2.0,
    method="robust",
):
    if beam_width < 1 or max_rounds < 0 or not isfinite(max_stretch) or max_stretch < 1:
        raise ValueError("Invalid search limits")
    model = RiskModel(circuit, device, scenarios, tail_weight, alpha)
    initial = asap(circuit, method=method)
    deadline = initial.makespan(circuit) * max_stretch

    def key(schedule):
        return (
            model.score(schedule),
            schedule.makespan(circuit),
            schedule.starts,
            schedule.extra_edges,
        )

    best, beam = initial, [initial]
    seen = {initial.extra_edges}
    rounds = 0
    for _ in range(max_rounds):
        candidates = []
        for schedule in beam:
            chosen = set(schedule.extra_edges)
            for a, b, _ in model.pairs:
                if (a, b) in chosen or (b, a) in chosen:
                    continue
                for edge in ((a, b), (b, a)):
                    edges = tuple(sorted(chosen | {edge}))
                    if edges in seen:
                        continue
                    seen.add(edges)
                    try:
                        child = asap(circuit, edges, method)
                    except ValueError:
                        continue
                    if child.makespan(circuit) <= deadline:
                        candidates.append(child)
        if not candidates:
            break
        beam = sorted(candidates, key=key)[:beam_width]
        best = min([best, *beam], key=key)
        rounds += 1
    return SearchResult(best, model.score(best), len(model.cache), rounds)


def exhaustive_oracle(
    circuit, device, scenarios, *, tail_weight=0.5, alpha=0.8, max_stretch=2.0, max_pairs=8
):
    """Enumerate 3^P precedence choices. Exact only in the documented finite family."""
    if not isfinite(max_stretch) or max_stretch < 1:
        raise ValueError("max_stretch must be finite and >= 1")
    model = RiskModel(circuit, device, scenarios, tail_weight, alpha)
    if len(model.pairs) > max_pairs:
        raise ValueError(f"Oracle limited to {max_pairs} conflict pairs; got {len(model.pairs)}")
    deadline = asap(circuit).makespan(circuit) * max_stretch
    best = None
    for decisions in product((0, 1, 2), repeat=len(model.pairs)):
        edges = []
        for decision, (a, b, _) in zip(decisions, model.pairs):
            if decision:
                edges.append((a, b) if decision == 1 else (b, a))
        try:
            schedule = asap(circuit, edges, "finite-family-oracle")
        except ValueError:
            continue
        if schedule.makespan(circuit) <= deadline:
            key = (model.score(schedule), schedule.makespan(circuit), schedule.starts)
            if best is None or key < best[0]:
                best = key, schedule
    return SearchResult(best[1], best[0][0], len(model.cache), len(model.pairs))

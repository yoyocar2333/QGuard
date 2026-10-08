"""Validated mapped-circuit IR. All durations use integer ticks, qubit 0 is MSB."""

from dataclasses import asdict, dataclass
from math import isfinite
from numbers import Integral

ARITY = {"h": 1, "x": 1, "rx": 1, "ry": 1, "rz": 1, "cx": 2, "cz": 2, "cp": 2, "swap": 2}


def _integer(value, name, minimum=0):
    if not isinstance(value, Integral) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Gate:
    name: str
    qubits: tuple[int, ...]
    duration: int
    angle: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "qubits", tuple(self.qubits))
        if self.name not in ARITY or len(self.qubits) != ARITY[self.name]:
            raise ValueError(f"Unsupported gate or arity: {self.name}")
        if len(set(self.qubits)) != len(self.qubits):
            raise ValueError("A gate cannot use a qubit twice")
        for q in self.qubits:
            _integer(q, "qubit")
        _integer(self.duration, "duration", 1)
        if not isfinite(self.angle):
            raise ValueError("Gate angle must be finite")


@dataclass(frozen=True)
class Circuit:
    n_qubits: int
    gates: tuple[Gate, ...]
    name: str = "circuit"

    def __post_init__(self):
        _integer(self.n_qubits, "n_qubits", 1)
        object.__setattr__(self, "gates", tuple(self.gates))
        if any(q >= self.n_qubits for g in self.gates for q in g.qubits):
            raise ValueError("Gate references an out-of-range qubit")

    def dependencies(self):
        last = {}
        edges = set()
        for i, gate in enumerate(self.gates):
            for q in gate.qubits:
                if q in last:
                    edges.add((last[q], i))
                last[q] = i
        return tuple(sorted(edges))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["n_qubits"], tuple(Gate(**g) for g in data["gates"]), data.get("name", "circuit")
        )


@dataclass(frozen=True)
class Device:
    n_qubits: int
    coupling: tuple[tuple[int, int], ...]
    xtalk_edges: tuple[tuple[int, int], ...]
    tick_ns: float = 20.0

    def __post_init__(self):
        _integer(self.n_qubits, "n_qubits", 1)
        if not isfinite(self.tick_ns) or self.tick_ns <= 0:
            raise ValueError("tick_ns must be positive and finite")
        for field in ("coupling", "xtalk_edges"):
            edges = []
            for edge in getattr(self, field):
                if len(edge) != 2:
                    raise ValueError("Edges must have two endpoints")
                a, b = sorted(edge)
                _integer(a, "edge endpoint")
                _integer(b, "edge endpoint")
                if a == b or b >= self.n_qubits:
                    raise ValueError("Invalid device edge")
                edges.append((a, b))
            if len(set(edges)) != len(edges):
                raise ValueError("Duplicate device edges")
            object.__setattr__(self, field, tuple(sorted(edges)))

    def validate_circuit(self, circuit):
        if circuit.n_qubits != self.n_qubits:
            raise ValueError("Circuit/device width mismatch")
        for g in circuit.gates:
            if len(g.qubits) == 2 and tuple(sorted(g.qubits)) not in self.coupling:
                raise ValueError(f"Unmapped gate on unsupported edge {g.qubits}")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Scenario:
    """Rates are per tick, omega is radians/tick; p1/p2 are Pauli-error probabilities.

    Idle relaxation is zero-temperature. T2 <= 2*T1 ensures a valid dephasing rate.
    Cross-talk arrays follow Device.xtalk_edges, sorted lexicographically.
    """

    t1: tuple[float, ...]
    t2: tuple[float, ...]
    zz_rate: tuple[float, ...]
    zz_omega: tuple[float, ...]
    p1: float = 0.0002
    p2: float = 0.004
    name: str = "nominal"

    def __post_init__(self):
        for field in ("t1", "t2", "zz_rate", "zz_omega"):
            object.__setattr__(self, field, tuple(getattr(self, field)))
        if len(self.t1) != len(self.t2) or not self.t1:
            raise ValueError("T1/T2 dimensions must match and be nonempty")
        for t1, t2 in zip(self.t1, self.t2):
            if not isfinite(t1) or not isfinite(t2) or t1 <= 0 or t2 <= 0 or t2 > 2 * t1:
                raise ValueError("Require finite T1 > 0 and 0 < T2 <= 2*T1")
        if len(self.zz_rate) != len(self.zz_omega):
            raise ValueError("ZZ rate/omega dimensions must match")
        if any(not isfinite(v) or v < 0 for v in self.zz_rate):
            raise ValueError("ZZ rates must be finite and nonnegative")
        if any(not isfinite(v) for v in self.zz_omega):
            raise ValueError("ZZ angular rates must be finite")
        if any(not isfinite(p) or not 0 <= p <= 1 for p in (self.p1, self.p2)):
            raise ValueError("Gate error probabilities must be in [0,1]")

    def validate_device(self, device):
        if len(self.t1) != device.n_qubits or len(self.zz_rate) != len(device.xtalk_edges):
            raise ValueError("Scenario/device dimensions mismatch")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Schedule:
    starts: tuple[int, ...]
    method: str = "custom"
    extra_edges: tuple[tuple[int, int], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "starts", tuple(self.starts))
        object.__setattr__(self, "extra_edges", tuple(tuple(e) for e in self.extra_edges))
        for s in self.starts:
            _integer(s, "start time")

    def end(self, circuit, i):
        return self.starts[i] + circuit.gates[i].duration

    def makespan(self, circuit):
        return max((self.end(circuit, i) for i in range(len(circuit.gates))), default=0)

    def validate(self, circuit):
        if len(self.starts) != len(circuit.gates):
            raise ValueError("Each gate must have exactly one start time")
        for a, b in circuit.dependencies() + self.extra_edges:
            if not 0 <= a < len(circuit.gates) or not 0 <= b < len(circuit.gates):
                raise ValueError("Invalid precedence edge")
            if self.end(circuit, a) > self.starts[b]:
                raise ValueError(f"Precedence violation: {a} -> {b}")
        return self

    def to_dict(self):
        return asdict(self)

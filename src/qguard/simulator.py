"""Explicit event-driven CPTP density-matrix simulation, with qubit 0 as MSB.

Ideal gates are lumped at their end timestamps. Idle channels and active-gate
spectator ZZ channels act between events. This is not a pulse-level simulator.
"""

from functools import lru_cache
from itertools import product

import numpy as np

I = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z = np.diag([1, -1]).astype(complex)
ZZ = np.kron(Z, Z)


def gate_matrix(gate):
    name, theta = gate.name, gate.angle
    if name == "h":
        return np.array([[1, 1], [1, -1]], complex) / np.sqrt(2)
    if name == "x":
        return X
    if name in ("rx", "ry", "rz"):
        pauli = {"rx": X, "ry": Y, "rz": Z}[name]
        return np.cos(theta / 2) * I - 1j * np.sin(theta / 2) * pauli
    if name == "cx":
        return np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]], complex)
    if name == "cz":
        return np.diag([1, 1, 1, -1]).astype(complex)
    if name == "cp":
        return np.diag([1, 1, 1, np.exp(1j * theta)])
    if name == "swap":
        return np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], complex)
    raise ValueError(f"Unsupported gate {name}")


def apply_axes(tensor, matrix, axes):
    axes = tuple(axes)
    k = len(axes)
    tmp = np.tensordot(matrix.reshape((2,) * (2 * k)), tensor, axes=(tuple(range(k, 2 * k)), axes))
    return np.moveaxis(tmp, tuple(range(k)), axes)


def apply_operator(rho, matrix, qubits, n):
    tensor = rho.reshape((2,) * (2 * n))
    tensor = apply_axes(tensor, matrix, qubits)
    tensor = apply_axes(tensor, matrix.conj(), tuple(n + q for q in qubits))
    return tensor.reshape(rho.shape)


def kraus_channel(rho, operators, qubits, n):
    return sum((apply_operator(rho, op, qubits, n) for op in operators), np.zeros_like(rho))


def idle_kraus(t1, t2, duration):
    gamma = -np.expm1(-duration / t1)
    dephase_rate = max(0.0, 1 / t2 - 1 / (2 * t1))
    p = -np.expm1(-duration * dephase_rate) / 2
    amp = (np.diag([1, np.sqrt(1 - gamma)]), np.array([[0, np.sqrt(gamma)], [0, 0]]))
    phase = (np.sqrt(1 - p) * I, np.sqrt(p) * Z)
    return tuple(b @ a for a in amp for b in phase)


@lru_cache(maxsize=2)
def paulis(k):
    result = []
    for factors in product((I, X, Y, Z), repeat=k):
        op = np.array([[1]], complex)
        for factor in factors:
            op = np.kron(op, factor)
        result.append(op)
    return tuple(result)


def depolarizing_kraus(k, probability):
    # probability = probability of a uniformly sampled NON-identity Pauli.
    basis = paulis(k)
    return (np.sqrt(1 - probability) * basis[0],) + tuple(
        np.sqrt(probability / (len(basis) - 1)) * p for p in basis[1:]
    )


def timeline(circuit, schedule, device, scenario):
    """Emit (kind, qubits, payload) operations; shared by independent backends.

    [start,end) intervals remove boundary ambiguity. At an event, complete gates
    (with gate errors) before propagating the next interval. No measurements.
    """
    schedule.validate(circuit)
    device.validate_circuit(circuit)
    scenario.validate_device(device)
    times = sorted(
        {0, *schedule.starts, *(schedule.end(circuit, i) for i in range(len(circuit.gates)))}
    )
    for index, t in enumerate(times):
        for i, gate in enumerate(circuit.gates):
            if schedule.end(circuit, i) == t:
                yield "unitary", gate.qubits, gate_matrix(gate)
                p = scenario.p1 if len(gate.qubits) == 1 else scenario.p2
                if p:
                    yield "kraus", gate.qubits, depolarizing_kraus(len(gate.qubits), p)
        if index + 1 == len(times):
            continue
        dt = times[index + 1] - t
        active = {
            q: i
            for i, gate in enumerate(circuit.gates)
            if schedule.starts[i] <= t < schedule.end(circuit, i)
            for q in gate.qubits
        }
        for q in range(circuit.n_qubits):
            if q not in active:
                yield "kraus", (q,), idle_kraus(scenario.t1[q], scenario.t2[q], dt)
        for k, (u, v) in enumerate(device.xtalk_edges):
            a, b = active.get(u), active.get(v)
            if a is None or b is None or a == b:
                continue
            if len(circuit.gates[a].qubits) != 2 or len(circuit.gates[b].qubits) != 2:
                continue
            angle = scenario.zz_omega[k] * dt
            if angle:
                yield "unitary", (u, v), np.diag(np.exp(-0.5j * angle * np.diag(ZZ)))
            p = -np.expm1(-2 * scenario.zz_rate[k] * dt) / 2
            if p:
                yield "kraus", (u, v), (np.sqrt(1 - p) * np.eye(4), np.sqrt(p) * ZZ)


def ideal_state(circuit, max_qubits=16):
    if circuit.n_qubits > max_qubits:
        raise ValueError(f"Ideal-state allocation limited to {max_qubits} qubits")
    psi = np.zeros((2,) * circuit.n_qubits, complex)
    psi[(0,) * circuit.n_qubits] = 1
    for gate in circuit.gates:
        psi = apply_axes(psi, gate_matrix(gate), gate.qubits)
    return psi.reshape(-1)


def simulate(circuit, schedule, device, scenario, max_qubits=10):
    if circuit.n_qubits > max_qubits:
        raise ValueError(
            f"Dense simulation limited to {max_qubits} qubits; scheduling has no such limit"
        )
    n = circuit.n_qubits
    rho = np.zeros((2**n, 2**n), complex)
    rho[0, 0] = 1
    for kind, qs, payload in timeline(circuit, schedule, device, scenario):
        rho = (
            apply_operator(rho, payload, qs, n)
            if kind == "unitary"
            else kraus_channel(rho, payload, qs, n)
        )
    return rho


def fidelity_to_ideal(rho, psi):
    value = float(np.real(np.vdot(psi, rho @ psi)))
    if not -1e-8 <= value <= 1 + 1e-8:
        raise ValueError("Invalid fidelity; inspect normalization/channel implementation")
    return float(np.clip(value, 0, 1))


def simulate_aer(circuit, schedule, device, scenario, max_qubits=10):
    """Independent matrix propagation in Aer; event/model generator is shared."""
    if circuit.n_qubits > max_qubits:
        raise ValueError("Aer dense simulation exceeds configured size guard")
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Kraus
    from qiskit_aer import AerSimulator

    qc = QuantumCircuit(circuit.n_qubits)
    for kind, qs, payload in timeline(circuit, schedule, device, scenario):
        # QGuard local matrix first qubit is MSB; Qiskit local qargs[0] is LSB.
        qargs = [circuit.n_qubits - 1 - q for q in reversed(qs)]
        if kind == "unitary":
            qc.unitary(payload, qargs)
        else:
            qc.append(Kraus(list(payload)).to_instruction(), qargs)
    qc.save_density_matrix()
    backend = AerSimulator(method="density_matrix", max_parallel_threads=1)
    result = backend.run(qc, shots=1).result()
    if not result.success:
        raise RuntimeError(str(result))
    return np.asarray(result.data(0)["density_matrix"])

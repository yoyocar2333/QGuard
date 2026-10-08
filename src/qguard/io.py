"""Versioned JSON input and an optional, deliberately narrow OpenQASM 2 adapter."""

import json
from pathlib import Path

from .model import Circuit, Device, Gate, Scenario


def save_problem(path, circuit, device, nominal):
    device.validate_circuit(circuit)
    nominal.validate_device(device)
    Path(path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "circuit": circuit.to_dict(),
                "device": device.to_dict(),
                "nominal": nominal.to_dict(),
            },
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def load_problem(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported input schema_version")
    circuit, device, nominal = (
        Circuit.from_dict(data["circuit"]),
        Device(**data["device"]),
        Scenario(**data["nominal"]),
    )
    device.validate_circuit(circuit)
    nominal.validate_device(device)
    return circuit, device, nominal


def from_qasm2(path, single_duration=1, two_duration=8):
    """Import already-mapped unitary QASM. No silent transpilation or barrier removal."""
    from qiskit import qasm2

    qc = qasm2.loads(Path(path).read_text(encoding="utf-8"))
    gates = []
    for item in qc.data:
        op = item.operation
        if op.name not in {"h", "x", "rx", "ry", "rz", "cx", "cz", "cp", "cu1", "swap"}:
            raise ValueError(
                f"Unsupported QASM instruction {op.name}; supply a mapped unitary circuit"
            )
        qubits = tuple(qc.find_bit(q).index for q in item.qubits)
        name = "cp" if op.name == "cu1" else op.name
        angle = float(op.params[0]) if op.params else 0.0
        gates.append(
            Gate(name, qubits, single_duration if len(qubits) == 1 else two_duration, angle)
        )
    return Circuit(qc.num_qubits, tuple(gates), Path(path).stem)

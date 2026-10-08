import numpy as np
import pytest

from qguard.circuits import make_circuit
from qguard.model import Circuit, Gate, Scenario, Schedule
from qguard.noise import nominal_scenario, sample_scenarios, synthetic_device
from qguard.scheduling import asap, conservative
from qguard.simulator import (
    ZZ,
    depolarizing_kraus,
    fidelity_to_ideal,
    ideal_state,
    idle_kraus,
    kraus_channel,
    simulate,
    simulate_aer,
    timeline,
)


def test_bell_state_and_reversed_control_order():
    bell = Circuit(2, (Gate("h", (0,), 1), Gate("cx", (0, 1), 8)))
    np.testing.assert_allclose(ideal_state(bell), np.array([1, 0, 0, 1]) / np.sqrt(2), atol=1e-14)
    asymmetric = Circuit(3, (Gate("x", (2,), 1), Gate("cx", (2, 0), 8)))
    assert np.argmax(abs(ideal_state(asymmetric))) == 5


def test_relaxation_analytic_and_semigroup():
    rho = np.array([[0, 0], [0, 1]], complex)
    got = kraus_channel(rho, idle_kraus(100, 80, 12), (0,), 1)
    assert got[1, 1] == pytest.approx(np.exp(-0.12))
    plus = np.ones((2, 2), complex) / 2
    got = kraus_channel(plus, idle_kraus(100, 80, 12), (0,), 1)
    assert got[0, 1] == pytest.approx(0.5 * np.exp(-12 / 80))
    split = kraus_channel(plus, idle_kraus(100, 80, 5), (0,), 1)
    split = kraus_channel(split, idle_kraus(100, 80, 7), (0,), 1)
    np.testing.assert_allclose(split, got, atol=1e-14)


@pytest.mark.parametrize(
    "operators", [idle_kraus(100, 80, 12), depolarizing_kraus(1, 0.07), depolarizing_kraus(2, 0.13)]
)
def test_kraus_completeness(operators):
    np.testing.assert_allclose(
        sum(k.conj().T @ k for k in operators), np.eye(len(operators[0])), atol=1e-14
    )


def test_half_open_overlap_exact_integrated_zz():
    c = Circuit(4, (Gate("cz", (0, 1), 8), Gate("cz", (2, 3), 8)))
    d = synthetic_device(4)
    s = Scenario((1e30,) * 4, (1e30,) * 4, (0, 0, 0), (0, 0.1, 0), 0, 0)
    # 3 ticks overlap: [5,8), not the entire duration of either gate.
    events = list(timeline(c, Schedule((0, 5)), d, s))
    zz = [m for kind, qs, m in events if kind == "unitary" and qs == (1, 2)]
    assert len(zz) == 1
    np.testing.assert_allclose(zz[0], np.diag(np.exp(-0.15j * np.diag(ZZ))))
    assert not any(
        kind == "unitary" and qs == (1, 2) for kind, qs, m in timeline(c, Schedule((0, 8)), d, s)
    )


@pytest.mark.parametrize("family", ["ghz", "qft", "qaoa", "brickwork"])
def test_zero_noise_preserves_ideal_under_rescheduling(family):
    c = make_circuit(family, 4, 2)
    d = synthetic_device(4)
    zero = Scenario((1e30,) * 4, (1e30,) * 4, (0,) * 3, (0,) * 3, 0, 0)
    psi = ideal_state(c)
    for schedule in (asap(c), conservative(c, d)):
        rho = simulate(c, schedule, d, zero)
        np.testing.assert_allclose(rho, np.outer(psi, psi.conj()), atol=1e-13)
        assert fidelity_to_ideal(rho, psi) == pytest.approx(1)


def test_density_is_physical_after_noise():
    c = make_circuit("brickwork", 4, 3)
    d = synthetic_device(4)
    for s in sample_scenarios(nominal_scenario(d), 4, 10, 1.0):
        rho = simulate(c, asap(c), d, s)
        np.testing.assert_allclose(rho, rho.conj().T, atol=1e-13)
        assert np.trace(rho) == pytest.approx(1)
        assert np.linalg.eigvalsh(rho).min() > -1e-12


def test_idle_before_first_and_after_last_gate_is_accounted():
    c = Circuit(2, (Gate("x", (0,), 1), Gate("x", (1,), 1)))
    d = synthetic_device(2)
    s = Scenario((10, 10), (10, 10), (0,), (0,), 0, 0)
    rho = simulate(c, Schedule((0, 11)), d, s)
    # q0 is excited at t=1, decays for 11 ticks; q1 becomes excited at t=12.
    assert rho[3, 3] == pytest.approx(np.exp(-1.1))


def test_dense_size_guard():
    c = Circuit(11, ())
    d = synthetic_device(11)
    with pytest.raises(ValueError, match="limited"):
        simulate(c, asap(c), d, nominal_scenario(d))


@pytest.mark.parametrize("family", ["ghz", "qft", "qaoa", "brickwork"])
def test_aer_independent_density_propagation(family):
    pytest.importorskip("qiskit_aer")
    c = make_circuit(family, 4, 1)
    d = synthetic_device(4)
    s = sample_scenarios(nominal_scenario(d), 1, 55)[0]
    for schedule in (asap(c), conservative(c, d)):
        np.testing.assert_allclose(
            simulate(c, schedule, d, s), simulate_aer(c, schedule, d, s), atol=2e-12
        )


def test_ideal_against_independently_constructed_qiskit_circuit():
    pytest.importorskip("qiskit")
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector

    gates = (
        Gate("h", (2,), 1),
        Gate("x", (0,), 1),
        Gate("ry", (1,), 1, 0.37),
        Gate("cx", (2, 0), 8),
        Gate("cp", (1, 2), 8, -0.81),
        Gate("swap", (0, 1), 8),
        Gate("rx", (0,), 1, 0.24),
        Gate("rz", (2,), 1, 1.2),
        Gate("cz", (2, 1), 8),
    )
    c = Circuit(3, gates)
    qc = QuantumCircuit(3)
    for g in gates:
        args = [2 - q for q in g.qubits]
        getattr(qc, g.name)(*(([g.angle] if g.name in {"rx", "ry", "rz", "cp"} else []) + args))
    np.testing.assert_allclose(
        ideal_state(c), np.asarray(Statevector.from_instruction(qc)), atol=1e-13
    )

from itertools import pairwise

import numpy as np
import pytest

from qguard.circuits import make_circuit
from qguard.model import Circuit, Device, Gate, Scenario, Schedule
from qguard.noise import nominal_scenario, sample_scenarios, synthetic_device
from qguard.scheduling import (
    RiskModel,
    asap,
    conflicts,
    conservative,
    empirical_cvar,
    exhaustive_oracle,
    optimize,
    overlap,
)


def pair_problem():
    c = Circuit(
        4, (Gate("h", (0,), 1), Gate("h", (2,), 1), Gate("cx", (0, 1), 8), Gate("cx", (2, 3), 8))
    )
    d = synthetic_device(4)
    return c, d, nominal_scenario(d)


@pytest.mark.parametrize("family", ["ghz", "qft", "qaoa", "brickwork"])
def test_schedules_legal_and_ideal_dependencies(family):
    c = make_circuit(family, 6, 2)
    d = synthetic_device(6)
    s = nominal_scenario(d)
    for schedule in [asap(c), conservative(c, d), optimize(c, d, [s], max_rounds=2).schedule]:
        schedule.validate(c)
        for q in range(c.n_qubits):
            intervals = sorted(
                (schedule.starts[i], schedule.end(c, i))
                for i, g in enumerate(c.gates)
                if q in g.qubits
            )
            assert all(a[1] <= b[0] for a, b in pairwise(intervals))
    serial = conservative(c, d)
    assert all(overlap(c, serial, a, b) == 0 for a, b, _ in conflicts(c, d))


def test_cvar_fractional_tail():
    assert empirical_cvar([1, 2, 9], 0) == 4
    assert empirical_cvar([1, 2, 9], 0.5) == pytest.approx((9 + 1) / 1.5)
    assert empirical_cvar([1, 2, 9], 0.99) == pytest.approx(9)
    with pytest.raises(ValueError):
        empirical_cvar([1], 1)


def test_oracle_matches_manual_three_choices():
    c, d, s = pair_problem()
    train = sample_scenarios(s, 16, 10)
    model = RiskModel(c, d, train)
    a, b, _ = conflicts(c, d)[0]
    reference = min(model.score(asap(c, e)) for e in [(), ((a, b),), ((b, a),)])
    oracle = exhaustive_oracle(c, d, train)
    search = optimize(c, d, train, beam_width=4, max_rounds=2)
    assert oracle.score == pytest.approx(reference)
    assert search.score == pytest.approx(oracle.score)


def test_search_never_worsens_training_objective_and_respects_budget():
    c = make_circuit("qaoa", 6, 2)
    d = synthetic_device(6)
    scenarios = sample_scenarios(nominal_scenario(d), 8, 12)
    model = RiskModel(c, d, scenarios)
    search = optimize(c, d, scenarios, max_rounds=3, max_stretch=1.2)
    assert search.score <= model.score(asap(c)) + 1e-12
    assert search.schedule.makespan(c) <= 1.2 * asap(c).makespan(c)


def test_invalid_input_and_cycles_rejected():
    for duration in (0, -1, 0.5, True):
        with pytest.raises(ValueError):
            Gate("h", (0,), duration)
    with pytest.raises(ValueError):
        Scenario((10,), (21,), (), ())
    with pytest.raises(ValueError):
        Schedule((0.5,))
    c, _, _ = pair_problem()
    with pytest.raises(ValueError):
        asap(c, ((2, 0),))
    with pytest.raises(ValueError):
        Schedule((0, 0, 0, 0)).validate(c)
    with pytest.raises(ValueError):
        Device(4, ((0, 1),), ()).validate_circuit(c)


def test_sampler_reproducible_valid_and_distinct():
    d = synthetic_device(4)
    nominal = nominal_scenario(d)
    a = sample_scenarios(nominal, 8, 123)
    assert a == sample_scenarios(nominal, 8, 123)
    assert a != sample_scenarios(nominal, 8, 124)
    for s in a:
        s.validate_device(d)
        assert np.all(np.array(s.t2) <= 2 * np.array(s.t1))

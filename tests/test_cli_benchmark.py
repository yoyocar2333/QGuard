import json

import numpy as np
import pytest

from qguard.benchmark import BenchmarkConfig, run_benchmark, summarize
from qguard.cli import main
from qguard.io import from_qasm2, load_problem
from qguard.simulator import ideal_state


def test_cli_roundtrip(tmp_path):
    source, target = tmp_path / "input.json", tmp_path / "schedule.json"
    assert main(["example", "--output", str(source)]) == 0
    c, _, _ = load_problem(source)
    assert c.n_qubits == 4
    assert main(["schedule", str(source), "--output", str(target), "--simulate"]) == 0
    data = json.loads(target.read_text())
    assert 0 <= data["nominal_fidelity"] <= 1


def test_benchmark_retains_all_draws_and_is_numerically_reproducible(tmp_path):
    cfg = BenchmarkConfig(
        families=("brickwork",),
        widths=(4,),
        circuit_seeds=(7,),
        depth=1,
        train_count=3,
        test_count=3,
        test_sigmas=(0.6,),
        max_rounds=1,
    )
    a = run_benchmark(cfg, tmp_path / "a", progress=lambda _: None)
    b = run_benchmark(cfg, tmp_path / "b", progress=lambda _: None)
    assert a["complete"]
    assert a["config_sha256"] == b["config_sha256"]
    case = a["cases"][0]
    assert len(case["training"]) == len(case["test_scenarios"]["0.6"]) == 3
    assert case["training"][0]["zz_rate"] != case["test_scenarios"]["0.6"][0]["zz_rate"]
    for method, entry in case["methods"].items():
        assert entry["evaluation"] == b["cases"][0]["methods"][method]["evaluation"]
    assert len(summarize(a)) == 5
    with pytest.raises(ValueError):
        BenchmarkConfig(train_seed=1, test_seed=1)


def test_qasm_import_rejects_unsupported_semantics(tmp_path):
    pytest.importorskip("qiskit")
    path = tmp_path / "circuit.qasm"
    path.write_text('OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; h q[0]; cx q[0],q[1];')
    np.testing.assert_allclose(ideal_state(from_qasm2(path)), np.array([1, 0, 0, 1]) / np.sqrt(2))
    path.write_text('OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; barrier q;')
    with pytest.raises(ValueError, match="Unsupported"):
        from_qasm2(path)

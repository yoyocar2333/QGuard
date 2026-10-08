"""Freeze schedules on training draws, then evaluate on independently seeded draws."""

import hashlib
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from . import __version__
from .circuits import make_circuit
from .noise import nominal_scenario, sample_scenarios, synthetic_device
from .scheduling import RiskModel, asap, conservative, optimize
from .simulator import fidelity_to_ideal, ideal_state, simulate


@dataclass(frozen=True)
class BenchmarkConfig:
    families: tuple[str, ...] = ("ghz", "qft", "qaoa", "brickwork")
    widths: tuple[int, ...] = (4, 6)
    circuit_seeds: tuple[int, ...] = (7, 19)
    depth: int = 2
    train_count: int = 16
    test_count: int = 24
    train_seed: int = 2026
    test_seed: int = 9026
    train_sigma: float = 0.6
    test_sigmas: tuple[float, ...] = (0.2, 0.6, 1.0)
    beam_width: int = 12
    max_rounds: int = 6
    max_stretch: float = 2.0
    tail_weight: float = 0.5
    alpha: float = 0.8

    def __post_init__(self):
        if self.train_seed == self.test_seed:
            raise ValueError("Training and test seeds must differ")
        if self.train_count < 1 or self.test_count < 1:
            raise ValueError("Scenario counts must be positive")
        if not self.families or not self.widths or not self.circuit_seeds or not self.test_sigmas:
            raise ValueError("Benchmark dimensions cannot be empty")
        if any(not 2 <= n <= 10 for n in self.widths):
            raise ValueError("Dense benchmark widths must be in [2,10]")
        if self.depth < 1 or self.beam_width < 1 or self.max_rounds < 0:
            raise ValueError("Invalid circuit/search sizes")
        if not np.isfinite(self.max_stretch) or self.max_stretch < 1:
            raise ValueError("Invalid max_stretch")
        if not 0 <= self.tail_weight <= 1 or not 0 <= self.alpha < 1:
            raise ValueError("Invalid risk parameters")
        if any(not np.isfinite(s) or s < 0 for s in (self.train_sigma, *self.test_sigmas)):
            raise ValueError("Scenario dispersion must be finite and nonnegative")


def environment():
    packages = {}
    for name in ("numpy", "matplotlib", "qiskit", "qiskit-aer"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "qguard": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": packages,
    }


def schedules_for(circuit, device, nominal, train, config):
    result = {}
    functions = {
        "asap": lambda: asap(circuit),
        "conservative": lambda: conservative(circuit, device),
        "nominal": lambda: (
            optimize(
                circuit,
                device,
                [nominal],
                tail_weight=0,
                beam_width=config.beam_width,
                max_rounds=config.max_rounds,
                max_stretch=config.max_stretch,
                method="nominal",
            ).schedule
        ),
        "scenario_mean": lambda: (
            optimize(
                circuit,
                device,
                train,
                tail_weight=0,
                beam_width=config.beam_width,
                max_rounds=config.max_rounds,
                max_stretch=config.max_stretch,
                method="scenario_mean",
            ).schedule
        ),
        "robust": lambda: (
            optimize(
                circuit,
                device,
                train,
                tail_weight=config.tail_weight,
                alpha=config.alpha,
                beam_width=config.beam_width,
                max_rounds=config.max_rounds,
                max_stretch=config.max_stretch,
            ).schedule
        ),
    }
    for method, fn in functions.items():
        start = time.perf_counter()
        schedule = fn()
        result[method] = schedule, time.perf_counter() - start
    return result


def _write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_benchmark(config, output, progress=print):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode()).hexdigest()
    result = {
        "schema_version": 1,
        "config": asdict(config),
        "config_sha256": signature,
        "environment": environment(),
        "complete": False,
        "cases": [],
    }
    start = time.perf_counter()
    for n in config.widths:
        device = synthetic_device(n)
        nominal = nominal_scenario(device)
        train = sample_scenarios(nominal, config.train_count, config.train_seed, config.train_sigma)
        # Test scenarios are generated only after the schedules below have been frozen.
        for family in config.families:
            for seed in config.circuit_seeds:
                circuit = make_circuit(family, n, config.depth, seed)
                selected = schedules_for(circuit, device, nominal, train, config)
                case = {
                    "name": circuit.name,
                    "family": family,
                    "n": n,
                    "circuit_seed": seed,
                    "circuit": circuit.to_dict(),
                    "device": device.to_dict(),
                    "nominal": nominal.to_dict(),
                    "training": [s.to_dict() for s in train],
                    "methods": {},
                    "test_scenarios": {},
                }
                psi = ideal_state(circuit)
                risk = RiskModel(circuit, device, train, config.tail_weight, config.alpha)
                for method, (schedule, elapsed) in selected.items():
                    case["methods"][method] = {
                        "schedule": schedule.to_dict(),
                        "compile_seconds": elapsed,
                        "makespan_ticks": schedule.makespan(circuit),
                        "training_risk": risk.score(schedule),
                        "evaluation": {},
                    }
                for sigma_index, sigma in enumerate(config.test_sigmas):
                    # Disjoint PRNG streams, even if numeric seed ranges overlap.
                    scenario_seed = np.random.SeedSequence([config.test_seed, sigma_index, n])
                    heldout = sample_scenarios(nominal, config.test_count, scenario_seed, sigma)
                    key = str(sigma)
                    case["test_scenarios"][key] = [s.to_dict() for s in heldout]
                    cache = {}
                    for method, (schedule, _) in selected.items():
                        if schedule.starts not in cache:
                            values = [
                                fidelity_to_ideal(simulate(circuit, schedule, device, s), psi)
                                for s in heldout
                            ]
                            cache[schedule.starts] = values
                        values = cache[schedule.starts]
                        case["methods"][method]["evaluation"][key] = {
                            "fidelities": values,
                            "mean": float(np.mean(values)),
                            "p10": float(np.quantile(values, 0.1)),
                            "minimum": float(min(values)),
                            "nominal_proxy_test_mean": float(
                                np.mean(RiskModel(circuit, device, heldout, 0).losses(schedule))
                            ),
                        }
                result["cases"].append(case)
                _write_json(output / "results.json", result)
                progress(
                    f"{circuit.name}: {len(selected)} methods, {config.test_count} held-out draws per dispersion"
                )
    result["complete"] = True
    result["wall_seconds"] = time.perf_counter() - start
    _write_json(output / "results.json", result)
    return result


def summarize(result):
    """Macro-average per-circuit statistics; a pooled quantile is a different statistic."""
    rows = []
    for method in ("asap", "conservative", "nominal", "scenario_mean", "robust"):
        for sigma in result["config"]["test_sigmas"]:
            entries = [c["methods"][method] for c in result["cases"]]
            if not entries:
                continue
            rows.append(
                {
                    "method": method,
                    "sigma": sigma,
                    "circuits": len(entries),
                    "mean_fidelity": float(
                        np.mean([e["evaluation"][str(sigma)]["mean"] for e in entries])
                    ),
                    "mean_per_circuit_p10": float(
                        np.mean([e["evaluation"][str(sigma)]["p10"] for e in entries])
                    ),
                    "mean_makespan_ticks": float(np.mean([e["makespan_ticks"] for e in entries])),
                    "mean_compile_seconds": float(np.mean([e["compile_seconds"] for e in entries])),
                }
            )
    return rows

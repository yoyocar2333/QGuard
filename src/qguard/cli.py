"""Small command line interface with no hidden hardware or cloud dependencies."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .benchmark import BenchmarkConfig, run_benchmark
from .circuits import make_circuit
from .io import from_qasm2, load_problem, save_problem
from .noise import nominal_scenario, sample_scenarios, synthetic_device
from .plotting import plot_results
from .scheduling import RiskModel, asap, conservative, exhaustive_oracle, optimize
from .simulator import fidelity_to_ideal, ideal_state, simulate


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="qguard", description="Robust crosstalk-aware scheduling research toolkit"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    example = sub.add_parser("example", help="Write a self-contained synthetic problem JSON")
    example.add_argument("--family", choices=["ghz", "qft", "qaoa", "brickwork"], default="qaoa")
    example.add_argument("--qubits", type=int, default=4)
    example.add_argument("--depth", type=int, default=1)
    example.add_argument("--output", type=Path, required=True)
    schedule = sub.add_parser("schedule", help="Optimize one mapped JSON problem")
    schedule.add_argument("input", type=Path)
    schedule.add_argument(
        "--method",
        choices=["asap", "conservative", "nominal", "robust", "oracle"],
        default="robust",
    )
    schedule.add_argument("--seed", type=int, default=2026)
    schedule.add_argument("--scenarios", type=int, default=16)
    schedule.add_argument("--output", type=Path, required=True)
    schedule.add_argument(
        "--simulate", action="store_true", help="Also report nominal-scenario simulated fidelity"
    )
    schedule.add_argument("--beam-width", type=int, default=12)
    schedule.add_argument("--max-rounds", type=int, default=6)
    schedule.add_argument("--max-stretch", type=float, default=2.0)
    bench = sub.add_parser("benchmark", help="Run independently seeded held-out evaluations")
    bench.add_argument(
        "--quick",
        action="store_true",
        help="Four 4-qubit circuits, 8 test scenarios per dispersion",
    )
    bench.add_argument("--config", type=Path)
    bench.add_argument("--output", type=Path, default=Path("results/benchmark"))
    plot = sub.add_parser("plot", help="Regenerate charts from saved raw results")
    plot.add_argument("input", type=Path)
    plot.add_argument("--output", type=Path, required=True)
    qasm = sub.add_parser(
        "from-qasm", help="Import unitary, already-mapped OpenQASM 2 (requires validation extra)"
    )
    qasm.add_argument("input", type=Path)
    qasm.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("example", "from-qasm"):
            circuit = (
                make_circuit(args.family, args.qubits, args.depth)
                if args.command == "example"
                else from_qasm2(args.input)
            )
            device = synthetic_device(circuit.n_qubits)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            save_problem(args.output, circuit, device, nominal_scenario(device))
            print(args.output)
        elif args.command == "schedule":
            circuit, device, nominal = load_problem(args.input)
            scenarios = (
                (nominal,)
                if args.method == "nominal"
                else sample_scenarios(nominal, args.scenarios, args.seed)
            )
            diagnostics = {}
            if args.method == "asap":
                selected = asap(circuit)
            elif args.method == "conservative":
                selected = conservative(circuit, device)
            elif args.method == "oracle":
                search = exhaustive_oracle(circuit, device, scenarios, max_stretch=args.max_stretch)
                selected = search.schedule
                diagnostics = asdict(search)
                diagnostics["guarantee"] = "optimal only in precedence-augmented ASAP family"
            else:
                search = optimize(
                    circuit,
                    device,
                    scenarios,
                    method=args.method,
                    tail_weight=0 if args.method == "nominal" else 0.5,
                    beam_width=args.beam_width,
                    max_rounds=args.max_rounds,
                    max_stretch=args.max_stretch,
                )
                selected = search.schedule
                diagnostics = asdict(search)
            data = {
                "schema_version": 1,
                "circuit": circuit.to_dict(),
                "device": device.to_dict(),
                "nominal": nominal.to_dict(),
                "schedule": selected.to_dict(),
                "training_scenarios": [s.to_dict() for s in scenarios],
                "makespan_ticks": selected.makespan(circuit),
                "diagnostics": diagnostics,
                "training_risk": RiskModel(
                    circuit, device, scenarios, 0 if args.method == "nominal" else 0.5
                ).score(selected),
            }
            if args.simulate:
                data["nominal_fidelity"] = fidelity_to_ideal(
                    simulate(circuit, selected, device, nominal), ideal_state(circuit)
                )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
            print(f"{args.method}: {data['makespan_ticks']} ticks; saved {args.output}")
            if args.simulate:
                print(f"Nominal-scenario fidelity: {data['nominal_fidelity']:.6f}")
        elif args.command == "benchmark":
            if args.config and args.quick:
                raise ValueError("Choose either --config or --quick")
            if args.config:
                config = BenchmarkConfig(**json.loads(args.config.read_text(encoding="utf-8")))
            elif args.quick:
                config = BenchmarkConfig(
                    widths=(4,),
                    circuit_seeds=(7,),
                    depth=1,
                    train_count=8,
                    test_count=8,
                    beam_width=8,
                    max_rounds=4,
                )
            else:
                config = BenchmarkConfig()
            result = run_benchmark(config, args.output)
            plot_results(result, args.output)
            print(
                f"Complete: {len(result['cases'])} circuits, {result['wall_seconds']:.2f} seconds"
            )
        else:
            result = json.loads(args.input.read_text(encoding="utf-8"))
            plot_results(result, args.output)
    except (ValueError, OSError, ImportError, KeyError) as exc:
        parser.exit(2, f"qguard: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Contributing

Use a branch and keep changes scoped. Install with `pip install -e '.[dev,validation]'`.
Run `python -m pytest -q` and `ruff check src tests`. Tests without the validation
extra skip Qiskit-dependent cases; install that extra before changing numerical
code, gate ordering or the QASM adapter.

For algorithm changes, report the search budget and compare with the tiny oracle
where feasible. For physical-model changes, document channel semantics and add an
analytic or independent-backend check. Do not tune defaults against held-out test
results; use a separate validation set and report the selection procedure.

Benchmark reports must include failures and regressions, exact configuration,
seeds, software environment, and raw data. Clearly label synthetic profiles. Avoid
checking in virtual environments, credentials, or large generated build products.

AI-assisted contributions are welcome when reviewed and verified. Responsibility
for the contribution and its claims remains with the contributor.

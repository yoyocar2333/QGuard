# Model and algorithms

## Units and semantics

All start times and gate durations are integers in ticks. A device's `tick_ns`
converts them to physical units; it is metadata, not an additional multiplier in
the channels. T1/T2 are in ticks, stochastic rates in inverse ticks, and angular
rates in radians per tick. Qubit 0 is the most significant tensor axis. Gates use
the listed qubit order, e.g. `cx(2,0)` controls qubit 2 and targets qubit 0.

Per-qubit program order defines a DAG. A legal schedule respects every edge,
including extra serialization edges, so shared-qubit operations cannot overlap.
No commutation-based dependency removal is attempted. Device native coupling and
interference links are distinct: an interference link connects spectator qubits
belonging to two different, simultaneously active two-qubit operations.

The synthetic benchmark intentionally uses all-to-all native connectivity, so
QFT needs no hidden routing, with a chain of interference links. This is a
controlled test topology, not a claimed model of an IBM processor. JSON inputs
can provide sparse connectivity; unmapped gates are rejected.

## Risk objective

For schedule S and scenario k, let M be makespan, Bq total busy duration of qubit
q, and d_ab the overlap of conflicting operations a,b. Let L_ab be their spectator
interference links. The dimensionless surrogate is

```math
R_k(S)=\sum_g p_{g,k}+\sum_q\frac{M-B_q}{2T_{2,q,k}}
+\sum_{(a,b)}\sum_{l\in L_{ab}}\left[\gamma_{l,k}d_{ab}
+\left(\frac{\omega_{l,k}d_{ab}}{2}\right)^2\right].
```

This is an additive heuristic inspired by stochastic error accumulation and a
small-angle coherent-error expansion. It is **not** a fidelity estimate, upper
bound, or state-sensitive error model. It ignores coherent cancellation, state
sensitivity, and the detailed timing of idle periods. Gate error contributes a
constant for a fixed circuit and therefore does not change the schedule ranking.

The robust score is `(1-lambda)*mean(R) + lambda*CVaR_alpha(R)` with defaults
lambda=0.5 and alpha=0.8. CVaR is the average of the largest `(1-alpha)` probability
mass of the empirical loss distribution, using fractional weight at its boundary.
For losses [1,2,9] and alpha=0.5, it is `(9 + 0.5*2)/1.5`, not merely the mean of
the largest rounded number of samples. Lambda is a tunable risk preference; this
release does not claim its default is optimal.

## Schedule search and oracle

1. Compute baseline ASAP starts from the original dependency DAG.
2. Identify disjoint two-qubit gate pairs connected by an interference link and
   not already ordered by a dependency path.
3. For each beam state, add one unused serialization decision, trying both directions.
4. Recompute earliest starts; reject cycles and schedules longer than
   `max_stretch * ASAP_makespan`.
5. Rank by score, makespan, starts and edge list for deterministic ties. Retain a
   bounded beam and the best result over all explored depths.

Different precedence sets with identical starts are kept as distinct search
states because their later extensions may differ. Objective evaluations are
cached by starts. Search never worsens its own objective relative to ASAP; this
does not imply improved held-out fidelity. It may miss useful combinations that
require more rounds or a wider beam.

With P conflict pairs, beam width B and depth D, at most roughly 2*P*B*D child
constraints are attempted, each needing a topological schedule and objective
evaluation. Conflict construction and transitive reachability use simple Python
sets and are intended for small/medium prototypes, not million-gate circuits.

The oracle enumerates the 3^P choices (none, forward, reverse), rejects invalid
graphs and budget violations, and scores each remaining ASAP schedule. It is
exact **only within this finite family**. An optimum over arbitrary start times
may exploit a partial overlap this family cannot represent. The default guard is
P <= 8. CP-SAT, MILP and unrestricted start-time optimization are future work.

## Event-driven channel model

Gate activity uses half-open intervals `[start,end)`. Event times include 0,
every start and every end. At each event:

1. Apply ideal unitaries of gates that end at this time, followed by their gate
   error channels. Simultaneous completed gates are disjoint and commute.
2. Until the next event, apply relaxation/dephasing to idle qubits.
3. Apply coherent and stochastic ZZ to each spectator link joining two different
   active two-qubit gates.

Ideal gates are lumped at completion. Active-gate T1/T2 evolution is not included
separately; its effective error is represented by the gate channel. Idle evolution
includes the time before a qubit's first operation and after its last operation
until circuit makespan. There is no extra terminal measurement period.

Idle amplitude damping uses probability `1-exp(-dt/T1)`. Pure dephasing uses
Pauli-Z probability `(1-exp(-dt*(1/T2-1/(2*T1))))/2`. Requiring `T2 <= 2*T1` gives
a nonnegative rate. These channels together give population decay exp(-dt/T1)
and coherence decay exp(-dt/T2) at zero temperature.

Gate error is a uniformly random **non-identity** k-qubit Pauli with total
probability p; identity occurs with probability 1-p. This p convention differs
from the coefficient in a replacement-with-maximally-mixed-state definition of
depolarization. No automatic conversion from hardware-reported error metrics is
implied.

Spectator coherent error is `exp(-i*omega*dt*Z⊗Z/2)`. Stochastic ZZ applies Z⊗Z
with probability `(1-exp(-2*gamma*dt))/2`. Spectator terms commute within an
interval; spectator qubits are active, so they are disjoint from idle channels.
The semigroup forms make splitting an interval at an unrelated event consistent.

For ideal state |psi>, reported fidelity is `<psi|rho|psi>`. There are no shot-noise
estimates, readout errors, or trajectory approximations in the reference results.
NumPy and Aer operate on the same discrete model; agreement does not validate
that model against hardware.

## Synthetic uncertainty

Each sampled calibration has a shared lognormal scale, qubit coherence scaling,
per-link lognormal variation, and independent 15% per-link hot-spot events with a
6x rate factor. Angular rates scale as the square root of the link factor.
The nominal profile is the center of the non-hot-spot component, not the mixture
mean. A sigma of zero still allows hot spots; sigma is dispersion, not time.

T1 and T2 are scaled together to preserve physical validity. Parameters remain
fixed during each circuit execution. Testing multiple sigma values probes
distribution shift, not online drift tracking. Training and test PRNG streams
are separate, with the same evaluation draws shared by all methods.

## Reporting and reproducibility

Reference tables average per-circuit means and per-circuit 10th percentiles;
they are not a pooled quantile of all fidelities. GHZ inputs do not depend on the
workload seed, so their duplicate seeds are technical repetitions, not independent
circuits. They are retained as a no-overlap control. These small-sample summaries
are descriptive; no statistical significance is claimed.

Raw JSON stores input circuits, nominal/training/test profiles, schedules and
individual fidelities. Config hashes identify settings, not hardware provenance.
Timing depends on the machine and is not bitwise reproducible. Fixed versions,
seeds and configuration reproduce the numerical experiment to floating-point
tolerance. The runner checkpoints completed cases but does not yet resume them.

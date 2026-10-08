"""Synthetic calibration scenarios, never presented as measured hardware data."""

from itertools import combinations

import numpy as np

from .model import Device, Scenario


def synthetic_device(n):
    # All-to-all native operations with a chain of spectator interference links.
    return Device(n, tuple(combinations(range(n), 2)), tuple((q, q + 1) for q in range(n - 1)))


def nominal_scenario(device):
    return Scenario(
        t1=tuple(1800.0 + 120 * q for q in range(device.n_qubits)),
        t2=tuple(1300.0 + 80 * q for q in range(device.n_qubits)),
        zz_rate=tuple(0.0004 * (1 + 0.4 * (i % 3)) for i in range(len(device.xtalk_edges))),
        zz_omega=tuple(0.006 * (1 + 0.25 * (i % 2)) for i in range(len(device.xtalk_edges))),
    )


def sample_scenarios(nominal, count, seed, sigma=0.6, hotspot_probability=0.15):
    """Independent draws with a shared per-draw calibration scale and per-link hot spots.

    The nominal profile is a central estimate, not the mixture mean. Test samples use
    a separate seed. sigma changes calibration dispersion; it is not elapsed time.
    """
    if count < 1 or not np.isfinite(sigma) or sigma < 0 or not 0 <= hotspot_probability <= 1:
        raise ValueError("Invalid scenario sampling parameters")
    rng = np.random.default_rng(seed)
    result = []
    for i in range(count):
        common = rng.lognormal(0, sigma / 3)
        coherence = rng.lognormal(0, sigma / 3, len(nominal.t1)) / common
        link_scale = common * rng.lognormal(0, sigma, len(nominal.zz_rate))
        link_scale *= np.where(rng.random(len(link_scale)) < hotspot_probability, 6.0, 1.0)
        result.append(
            Scenario(
                tuple(np.asarray(nominal.t1) * coherence),
                tuple(np.asarray(nominal.t2) * coherence),
                tuple(np.asarray(nominal.zz_rate) * link_scale),
                tuple(np.asarray(nominal.zz_omega) * np.sqrt(link_scale)),
                min(1.0, nominal.p1 * common),
                min(1.0, nominal.p2 * common),
                f"seed{seed}-{i}",
            )
        )
    return tuple(result)

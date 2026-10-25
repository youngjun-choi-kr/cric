"""Act and sense again: what happens when the approved burn is flown with real-world errors.

A thruster never delivers exactly the planned impulse. Each sample perturbs the burn's magnitude, pointing and
timing (defaults typical of small-satellite cold-gas and electric thrusters: 3% magnitude, 1 degree pointing,
2 s timing, 1 sigma), then recomputes the closest approach with the same model the optimiser used. The result is
the check an operator runs after a burn: is the conjunction cleared, or is a follow-up trim needed?

The re-screen against the whole catalog is done for the nominal burn when options are built; execution errors of
this size move the orbit by metres to tens of metres, far inside the re-screen margin.
"""

from __future__ import annotations

import numpy as np

from .maneuver import AXES, Encounter


def execute(enc: Encounter, option: dict, *, samples: int = 400, seed: int = 0, mag_sigma: float = 0.03,
            point_sigma_deg: float = 1.0, time_sigma_s: float = 2.0, clear_km: float = 1.0) -> dict:
    rng = np.random.default_rng(seed)
    axis = np.array(AXES[option["direction"]], dtype=float)
    lead, dv = float(option["lead_s"]), float(option["dv_ms"])
    nominal = enc.miss_after(lead, axis * dv)[0]
    misses = np.empty(samples)
    for i in range(samples):
        tilt = rng.normal(0.0, np.radians(point_sigma_deg), 3)
        d = axis + tilt - axis * (tilt @ axis)              # small rotation away from the planned axis
        d /= np.linalg.norm(d)
        misses[i] = enc.miss_after(lead + rng.normal(0.0, time_sigma_s), d * dv * (1 + rng.normal(0.0, mag_sigma)))[0]
    share = float(np.mean(misses >= clear_km))
    return {
        "option_id": option["id"], "samples": samples,
        "errors_1sigma": {"magnitude_pct": mag_sigma * 100, "pointing_deg": point_sigma_deg, "timing_s": time_sigma_s},
        "miss_before_km": round(float(enc.nominal[0]), 4), "miss_nominal_km": round(float(nominal), 3),
        "miss_p5_km": round(float(np.percentile(misses, 5)), 3), "miss_min_km": round(float(misses.min()), 3),
        "share_above_clear_km": round(share, 3), "clear_km": clear_km,
        "verdict": "cleared" if share >= 0.99 else "follow-up trim needed",
    }

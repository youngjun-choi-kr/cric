"""Act and sense again: what happens when the approved burn is flown with real-world errors.

A thruster never delivers exactly the planned impulse. Each sample perturbs the burn's magnitude, pointing and
timing (1 sigma: 3% magnitude, 1 degree pointing, 2 s timing; assumed values for a small satellite, see
LIMITATIONS.md and the sensitivity table there), then recomputes the closest approach with the same model the
optimiser used, and the Pc after the burn is checked against the NASA handbook goal of 3.2E-06. The result is
the check an operator runs after a burn: is the conjunction cleared, or is a follow-up trim needed?

The re-screen against the whole catalog is done for the nominal burn when options are built; execution errors of
this size move the orbit by metres to tens of metres, far inside the re-screen margin.
"""

from __future__ import annotations

import numpy as np

from .maneuver import AXES, Encounter, cw_displacement, to_eci

# NASA CA Best Practices Handbook (NASA-SP-20205011318, Rev 2, section 6): mitigate above Pc 1E-04 and bring the
# conjunction down to 1.5 orders of magnitude below that threshold.
PC_GOAL = 3.2e-6


def _post_burn_pc(enc: Encounter, lead: float, dv_vec, sig_m, sig_o, hbr_km: float) -> tuple[float, float]:
    """(miss_km, Pc) after a burn, with the same assumed covariances the risk step used, in the encounter plane."""
    from .cdm import pc_disk
    from .risk import _rtn_cov_eci

    n, tau = enc.burn_frame(lead)
    rel = enc.rM + to_eci(cw_displacement(tau, n, dv_vec), enc.basis) - enc.rO
    i = int(np.argmin(np.einsum("ij,ij->i", rel, rel)))
    rv = enc.vM[i] - enc.vO[i]
    vhat = rv / np.linalg.norm(rv)
    miss = rel[i] - (rel[i] @ vhat) * vhat
    e1 = miss / np.linalg.norm(miss)
    proj = np.vstack([e1, np.cross(vhat, e1)])
    cov = _rtn_cov_eci(enc.rM[i], enc.vM[i], np.asarray(sig_m)) + _rtn_cov_eci(enc.rO[i], enc.vO[i], np.asarray(sig_o))
    return float(np.linalg.norm(rel[i])), pc_disk(proj @ miss, proj @ cov @ proj.T, hbr_km)


def execute(enc: Encounter, option: dict, *, samples: int = 400, seed: int = 0, mag_sigma: float = 0.03,
            point_sigma_deg: float = 1.0, time_sigma_s: float = 2.0, clear_km: float = 1.0,
            sigma_rtn_km: dict | None = None, hbr_km: float = 0.02) -> dict:
    """sigma_rtn_km: the risk step's assumed 1-sigma RTN for both objects; when given, the post-burn Pc is checked
    against the handbook goal (worst 5% of executions)."""
    rng = np.random.default_rng(seed)
    axis = np.array(AXES[option["direction"]], dtype=float)
    lead, dv = float(option["lead_s"]), float(option["dv_ms"])
    nominal = enc.miss_after(lead, axis * dv)[0]
    misses = np.empty(samples)
    draws = []
    for i in range(samples):
        tilt = rng.normal(0.0, np.radians(point_sigma_deg), 3)
        d = axis + tilt - axis * (tilt @ axis)              # small rotation away from the planned axis
        d /= np.linalg.norm(d)
        draw = (lead + rng.normal(0.0, time_sigma_s), d * dv * (1 + rng.normal(0.0, mag_sigma)))
        draws.append(draw)
        misses[i] = enc.miss_after(*draw)[0]
    share = float(np.mean(misses >= clear_km))
    pc = None
    if sigma_rtn_km:
        sm, so = sigma_rtn_km["maneuvering"], sigma_rtn_km["other"]
        worst = draws[int(np.argsort(misses)[max(0, int(0.05 * samples) - 1)])]      # 5th-percentile execution
        pc = {"nominal": _post_burn_pc(enc, lead, axis * dv, sm, so, hbr_km)[1],
              "worst_5pct": _post_burn_pc(enc, *worst, sm, so, hbr_km)[1], "goal": PC_GOAL,
              "source": "NASA CA Best Practices Handbook, Rev 2, section 6: 1.5 orders below 1E-04"}
    return {
        "option_id": option["id"], "samples": samples,
        "errors_1sigma": {"magnitude_pct": mag_sigma * 100, "pointing_deg": point_sigma_deg, "timing_s": time_sigma_s},
        "miss_before_km": round(float(enc.nominal[0]), 4), "miss_nominal_km": round(float(nominal), 3),
        "miss_p5_km": round(float(np.percentile(misses, 5)), 3), "miss_min_km": round(float(misses.min()), 3),
        "share_above_clear_km": round(share, 3), "clear_km": clear_km,
        "post_burn_pc": pc,
        "verdict": "cleared" if share >= 0.99 and (pc is None or pc["worst_5pct"] <= PC_GOAL) else "follow-up trim needed",
    }

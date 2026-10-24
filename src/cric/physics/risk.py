"""Collision probability (Pc) from an ASSUMED position covariance.

Operators decide on Pc from precise ephemerides and covariance (CDMs). Public
GP/TLE data carries no covariance, so CRIC assumes one from the TLE epoch age and
geomagnetic activity. Every number from this module is labelled as assumed.

Model
- Per-object 1-sigma in RTN (km): radial and cross-track small, along-track large,
  all growing with days since TLE epoch; along-track inflated during storms
  because drag forecasts degrade (the Feb 2022 Starlink loss is the extreme case).
- Combined covariance projected onto the encounter plane (perpendicular to the
  relative velocity). Pc from the small-object approximation of Foster's 2D integral:
  Pc ~ HBR^2 / (2 sqrt(det C)) * exp(-0.5 m^T C^-1 m).
- Dilution check: scale the covariance down; if Pc rises a lot, the low Pc comes
  from poor knowledge, not from a safe geometry, and better data is needed.

Thresholds follow common practice (e.g. NASA CARA): >= 1e-4 red, 1e-5 to 1e-4 yellow.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from .maneuver import Encounter, lvlh

PC_RED = 1e-4
PC_YELLOW = 1e-5


@dataclass
class CovarianceModel:
    radial_km: float = 0.2
    along_km: float = 0.6
    cross_km: float = 0.2
    radial_growth_km_per_day: float = 0.05
    along_growth_km_per_day: float = 1.0
    cross_growth_km_per_day: float = 0.05
    storm_along_factor_per_kp: float = 0.3  # along-track x (1 + f * max(0, Kp - 3))

    def sigmas(self, age_days: float, kp: float) -> np.ndarray:
        age = max(age_days, 0.0)
        storm = 1.0 + self.storm_along_factor_per_kp * max(0.0, kp - 3.0)
        return np.array([
            self.radial_km + self.radial_growth_km_per_day * age,
            (self.along_km + self.along_growth_km_per_day * age) * storm,
            self.cross_km + self.cross_growth_km_per_day * age,
        ])


def _rtn_cov_eci(r: np.ndarray, v: np.ndarray, sig: np.ndarray) -> np.ndarray:
    bx, by, bz = lvlh(r[None, :], v[None, :])
    rot = np.vstack([bx[0], by[0], bz[0]])  # rows: R, T, N in ECI
    return rot.T @ np.diag(sig ** 2) @ rot


def _pc_2d(miss_vec_2d: np.ndarray, cov_2d: np.ndarray, hbr_km: float) -> float:
    det = float(np.linalg.det(cov_2d))
    if det <= 0:
        return 0.0
    m = miss_vec_2d
    maha2 = float(m @ np.linalg.solve(cov_2d, m))
    return min(1.0, hbr_km ** 2 / (2 * math.sqrt(det)) * math.exp(-0.5 * maha2))


def collision_probability(enc: Encounter, age_days: tuple[float, float], *, kp: float = 2.0,
                          hbr_km: float = 0.02, model: CovarianceModel | None = None) -> dict:
    """Pc at the nominal TCA of an Encounter under the assumed covariance model."""
    model = model or CovarianceModel()
    i = enc.i_tca
    rM, vM, rO, vO = enc.rM[i], enc.vM[i], enc.rO[i], enc.vO[i]
    # Move the reference sample to the refined TCA along straight-line relative motion.
    dt = enc.nominal[1] - enc.og[i]
    rel_r = (rM - rO) + (vM - vO) * dt
    rel_v = vM - vO
    vhat = rel_v / np.linalg.norm(rel_v)
    perp = rel_r - (rel_r @ vhat) * vhat
    e1 = perp / max(np.linalg.norm(perp), 1e-12)
    e2 = np.cross(vhat, e1)
    proj = np.vstack([e1, e2])

    sig_m = model.sigmas(age_days[0], kp)
    sig_o = model.sigmas(age_days[1], kp)
    cov = _rtn_cov_eci(rM, vM, sig_m) + _rtn_cov_eci(rO, vO, sig_o)
    cov2 = proj @ cov @ proj.T
    miss2 = np.array([np.linalg.norm(perp), 0.0])

    pc = _pc_2d(miss2, cov2, hbr_km)
    scales = np.geomspace(0.05, 1.0, 30)
    pcs = [_pc_2d(miss2, cov2 * s ** 2, hbr_km) for s in scales]
    pc_max = max(pcs)
    level = "red" if pc >= PC_RED else "yellow" if pc >= PC_YELLOW else "green"
    return {
        "pc": pc,
        "pc_max_if_better_data": pc_max,
        "diluted": pc_max >= 10 * pc and pc_max >= PC_YELLOW,
        "level": level,
        "miss_km": float(np.linalg.norm(perp)),
        "sigma_rtn_km": {"maneuvering": sig_m.round(3).tolist(), "other": sig_o.round(3).tolist()},
        "assumptions": {"covariance": "ASSUMED from TLE age and Kp; no CDM", "hbr_km": hbr_km, "kp": kp,
                        "tle_age_days": [round(a, 2) for a in age_days], "model": asdict(model)},
    }

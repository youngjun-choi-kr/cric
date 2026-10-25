"""Storm drag as along-track uncertainty, from NASA's MSIS thermosphere model.

An orbit fitted in quiet conditions assumes the quiet-time air density. When a geomagnetic storm heats the
thermosphere, density at LEO altitudes rises and the satellite falls behind its prediction. The unmodelled drag
acceleration is

    delta_a = 1/2 * delta_rho * v^2 * B          (B = Cd * A / m, ballistic coefficient)

and a constant along-track acceleration displaces the satellite by about 3/2 * delta_a * t^2 after time t
(the semi-major axis decays, so the satellite speeds up and the error grows quadratically). delta_rho is the
MSIS density at the object's altitude for the storm's Ap minus the density for a quiet Ap.

Assumptions (stated in every result): F10.7 = 150 sfu (solar-cycle average), B = 0.02 m^2/kg (a typical small
satellite; Cd 2.2, A/m 0.009), storm level held constant over the prediction span, orbit fitted at Kp 2.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

MU = 3.986004418e14  # m^3/s^2
RE_KM = 6378.137
KP_AP = [(0, 0), (1, 3), (2, 7), (3, 15), (4, 27), (5, 48), (6, 80), (7, 140), (8, 240), (9, 400)]


def kp_to_ap(kp: float) -> float:
    kp = min(max(kp, 0.0), 9.0)
    lo = int(math.floor(kp))
    if lo == 9:
        return 400.0
    (_, a0), (_, a1) = KP_AP[lo], KP_AP[lo + 1]
    return a0 + (a1 - a0) * (kp - lo)


@lru_cache(maxsize=512)
def density(alt_km: float, ap: float, f107: float = 150.0) -> float:
    """MSIS 2.1 total mass density (kg/m^3), global average over a few longitudes, at a fixed reference date."""
    import pymsis

    out = pymsis.calculate(np.datetime64("2024-03-20T12:00"), np.array([0.0, 90.0, 180.0, 270.0]), np.array([0.0]),
                           np.array([alt_km]), f107s=[f107], f107as=[f107], aps=[[ap] * 7])
    return float(np.mean(out[..., pymsis.Variable.MASS_DENSITY]))


def storm_along_km(alt_km: float, kp: float, span_days: float, *, b_m2_kg: float = 0.02, kp_fit: float = 2.0,
                   f107: float = 150.0) -> float:
    """Along-track displacement (km) a storm at `kp` adds over `span_days`, relative to a fit made at `kp_fit`."""
    if kp <= kp_fit or span_days <= 0 or not 150 <= alt_km <= 1500:
        return 0.0
    alt = round(alt_km / 10) * 10                          # cache-friendly; 10 km changes density by a few %
    drho = density(alt, kp_to_ap(kp), f107) - density(alt, kp_to_ap(kp_fit), f107)
    v2 = MU / ((RE_KM + alt_km) * 1000.0)
    t = span_days * 86400.0
    return 1.5 * (0.5 * drho * v2 * b_m2_kg) * t * t / 1000.0


ASSUMPTIONS = "MSIS 2.1 density, F10.7 150 sfu, B = 0.02 m^2/kg, storm held constant, orbit fitted at Kp 2"

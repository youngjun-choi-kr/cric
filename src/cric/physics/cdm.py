"""Conjunction Data Messages (CCSDS 508.0, KVN) and an exact 2D collision probability.

A CDM is what an operator receives from a screening service once a close approach is real: both objects' states
at the time of closest approach and their position covariances in each object's RTN frame. CRIC's own Pc so far
used an assumed covariance (TLE data) or a simulated precise one; this module reads the real thing and computes
Pc by integrating the combined position uncertainty over the hard-body disk in the encounter plane (Foster's
method, numerically, without the small-object approximation).

Checked against NASA CARA's published unit-test values (tests/test_cdm.py): Omitron test case 01
(Pc 2.706e-5) and the CDM of Omitron test case 08 (2D Pc 2.27e-20).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_LINE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*(\[[^\]]*\])?\s*$")
COV_KEYS = ("CR_R", "CT_R", "CT_T", "CN_R", "CN_T", "CN_N")


def parse_cdm(path: str | Path) -> dict:
    """Header fields and, per object, state (km, km/s, frame as given) and RTN position covariance (m^2)."""
    header, objects, cur = {}, [], None
    for raw in Path(path).read_text().splitlines():
        m = _LINE.match(raw)
        if not m or m.group(1) == "COMMENT":
            continue
        key, val = m.group(1), m.group(2)
        if key == "OBJECT":
            cur = {"object": val}
            objects.append(cur)
            continue
        (cur if cur is not None else header)[key] = val
    out = []
    for o in objects:
        c = {k: float(o[k]) for k in COV_KEYS}
        cov = np.array([[c["CR_R"], c["CT_R"], c["CN_R"]],
                        [c["CT_R"], c["CT_T"], c["CN_T"]],
                        [c["CN_R"], c["CN_T"], c["CN_N"]]])
        out.append({"designator": o.get("OBJECT_DESIGNATOR"), "name": o.get("OBJECT_NAME"),
                    "frame": o.get("REF_FRAME"), "maneuverable": o.get("MANEUVERABLE"),
                    "r_km": np.array([float(o[k]) for k in ("X", "Y", "Z")]),
                    "v_kms": np.array([float(o[k]) for k in ("X_DOT", "Y_DOT", "Z_DOT")]),
                    "cov_rtn_m2": cov})
    pc = header.get("COLLISION_PROBABILITY")
    return {"tca": header.get("TCA"), "miss_m": float(header["MISS_DISTANCE"]) if "MISS_DISTANCE" in header else None,
            "relative_speed_ms": float(header["RELATIVE_SPEED"]) if "RELATIVE_SPEED" in header else None,
            "pc_reported": float(pc) if pc not in (None, "", "NaN") else None, "originator": header.get("ORIGINATOR"),
            "objects": out}


def rtn_to_inertial(r: np.ndarray, v: np.ndarray, cov_rtn: np.ndarray) -> np.ndarray:
    """Rotate a 3x3 RTN covariance into the inertial frame of r, v."""
    rhat = r / np.linalg.norm(r)
    nhat = np.cross(r, v)
    nhat /= np.linalg.norm(nhat)
    that = np.cross(nhat, rhat)
    m = np.column_stack([rhat, that, nhat])
    return m @ cov_rtn @ m.T


def pc_foster(r1, v1, c1, r2, v2, c2, hbr: float, *, n_r: int = 64, n_t: int = 128) -> float:
    """Exact 2D Pc: combined Gaussian position uncertainty integrated over the hard-body disk (radius hbr) in the
    plane perpendicular to the relative velocity. Consistent units throughout (e.g. m, m/s, m^2, m)."""
    r = np.asarray(r2, float) - np.asarray(r1, float)
    v = np.asarray(v2, float) - np.asarray(v1, float)
    c = np.asarray(c1, float) + np.asarray(c2, float)
    vhat = v / np.linalg.norm(v)
    miss = r - (r @ vhat) * vhat
    e1 = miss / np.linalg.norm(miss) if np.linalg.norm(miss) > 0 else np.cross(vhat, [1.0, 0, 0]) / np.linalg.norm(np.cross(vhat, [1.0, 0, 0]))
    e2 = np.cross(vhat, e1)
    p = np.vstack([e1, e2])
    return pc_disk(p @ miss, p @ c @ p.T, hbr, n_r=n_r, n_t=n_t)


def pc_disk(mu, cov2, hbr: float, *, n_r: int = 64, n_t: int = 128) -> float:
    """Probability mass of N(mu, cov2) (2D, encounter plane) inside the disk of radius hbr at the origin."""
    mu, cov2 = np.asarray(mu, float), np.asarray(cov2, float)
    det = np.linalg.det(cov2)
    if det <= 0:
        return 0.0
    inv = np.linalg.inv(cov2)
    xr, wr = np.polynomial.legendre.leggauss(n_r)
    rad = 0.5 * hbr * (xr + 1)
    wrad = 0.5 * hbr * wr
    th = np.linspace(0, 2 * np.pi, n_t, endpoint=False)
    x = rad[:, None] * np.cos(th)[None, :] - mu[0]
    y = rad[:, None] * np.sin(th)[None, :] - mu[1]
    q = inv[0, 0] * x * x + 2 * inv[0, 1] * x * y + inv[1, 1] * y * y
    f = np.exp(-0.5 * q) / (2 * np.pi * np.sqrt(det))
    return float(np.sum(f * rad[:, None] * wrad[:, None]) * (2 * np.pi / n_t))


def pc_from_cdm(cdm: dict, hbr_m: float = 20.0) -> dict:
    """Pc from a parsed CDM, both covariances rotated from RTN into the CDM's inertial frame."""
    o1, o2 = cdm["objects"][:2]
    r1, v1, r2, v2 = o1["r_km"] * 1000, o1["v_kms"] * 1000, o2["r_km"] * 1000, o2["v_kms"] * 1000
    c1, c2 = rtn_to_inertial(r1, v1, o1["cov_rtn_m2"]), rtn_to_inertial(r2, v2, o2["cov_rtn_m2"])
    pc = pc_foster(r1, v1, c1, r2, v2, c2, hbr_m)
    return {"pc": pc, "hbr_m": hbr_m, "pc_reported": cdm["pc_reported"], "miss_m": cdm["miss_m"],
            "sigma_rtn_m": [np.sqrt(np.diag(o["cov_rtn_m2"])).round(1).tolist() for o in (o1, o2)]}

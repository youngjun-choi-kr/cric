"""Launch sites and the geometry that turns a trajectory in the launch plane into places on a rotating Earth.

The ascent is flown in the plane through Earth's centre, the launch site and the launch azimuth, in a frame
fixed to the stars and aligned with Earth at liftoff. A point at downrange angle theta in that plane maps to
latitude and longitude after Earth has turned for t seconds. Earth's rotation enters the ascent as the initial
along-azimuth surface speed; its cross-plane part is ignored (a few hundredths of a degree in inclination).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RE = 6_378_137.0
OMEGA = 7.2921159e-5


@dataclass
class Site:
    id: str
    name: str
    lat: float
    lon: float
    corridors: list[tuple[float, float]]   # allowed launch azimuths (deg, clockwise from north), simplified


# Approximate public coordinates; corridors are simplified over-sea sectors, not official range limits.
SITES = {s.id: s for s in (
    Site("naro", "Naro Space Center (KR)", 34.431, 127.535, [(160, 200)]),
    Site("tanegashima", "Tanegashima (JP)", 30.400, 130.975, [(90, 190)]),
    Site("wenchang", "Wenchang (CN)", 19.614, 110.951, [(90, 180)]),
    Site("sriharikota", "Satish Dhawan, Sriharikota (IN)", 13.720, 80.230, [(100, 140)]),
    Site("mahia", "Mahia Peninsula LC-1 (NZ)", -39.261, 177.865, [(60, 200)]),
    Site("kourou", "Guiana Space Centre (FR)", 5.236, -52.769, [(349.5, 360), (0, 93.5)]),
    Site("cape", "Cape Canaveral (US)", 28.562, -80.577, [(35, 120)]),
    Site("vandenberg", "Vandenberg (US)", 34.632, -120.611, [(147, 210)]),
    Site("kodiak", "Pacific Spaceport, Kodiak (US)", 57.435, -152.337, [(150, 210)]),
)}


def azimuths_for(lat: float, incl: float) -> list[float]:
    """Launch azimuths (deg) that put a vehicle from latitude lat into inclination incl; empty if unreachable."""
    c = math.cos(math.radians(incl)) / math.cos(math.radians(lat))
    if abs(c) > 1:
        return []
    a = math.degrees(math.asin(c))
    return sorted({round(a % 360, 3), round((180 - a) % 360, 3)})


def in_corridor(site: Site, az: float) -> bool:
    return any(lo <= az % 360 <= hi for lo, hi in site.corridors)


def sso_inclination(alt_km: float) -> float:
    """Sun-synchronous inclination for a circular orbit (J2 nodal precession = 0.9856 deg/day)."""
    a = RE + alt_km * 1000
    n = math.sqrt(3.986004418e14 / a ** 3)
    cos_i = -(2 * 1.99106e-7) / (3 * 1.08263e-3 * (RE / a) ** 2 * n)
    return math.degrees(math.acos(cos_i))


def plane_basis(lat: float, lon: float, az: float):
    """Unit vectors (radial at the site, downrange direction) in the Earth-fixed frame at liftoff."""
    p, l, a = map(math.radians, (lat, lon, az))
    u = (math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p))
    e = (-math.sin(l), math.cos(l), 0.0)
    n = (-math.sin(p) * math.cos(l), -math.sin(p) * math.sin(l), math.cos(p))
    d = tuple(math.sin(a) * e[i] + math.cos(a) * n[i] for i in range(3))
    return u, d


def plane_to_inertial(theta: float, r: float, basis) -> tuple[float, float, float]:
    u, d = basis
    return tuple(r * (math.cos(theta) * u[i] + math.sin(theta) * d[i]) for i in range(3))


def inertial_to_latlon(x: float, y: float, z: float, t: float) -> tuple[float, float, float]:
    """Inertial (aligned with Earth-fixed at liftoff) -> (lat, lon, alt_km) after t seconds of Earth rotation."""
    g = -OMEGA * t
    xe, ye = x * math.cos(g) - y * math.sin(g), x * math.sin(g) + y * math.cos(g)
    r = math.sqrt(xe * xe + ye * ye + z * z)
    return math.degrees(math.asin(z / r)), (math.degrees(math.atan2(ye, xe)) + 540) % 360 - 180, (r - RE) / 1000


def surface_speed_along(lat: float, az: float) -> float:
    return OMEGA * RE * math.cos(math.radians(lat)) * math.sin(math.radians(az))


def destination(lat: float, lon: float, bearing: float, dist_km: float) -> tuple[float, float]:
    """Great-circle destination point."""
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing)
    d = dist_km * 1000 / RE
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180

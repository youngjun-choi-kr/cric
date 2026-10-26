"""Mission Studio step 1: geometry, orbit elements, and the ascent model's behaviour on known cases."""

import math

from cric.launch.ascent import Guidance, elements, simulate
from cric.launch.geo import RE, SITES, azimuths_for, in_corridor, sso_inclination
from cric.launch.vehicles import PRESETS, from_dict

MU = 3.986004418e14


def test_circular_orbit_elements_and_sso_inclination():
    r = RE + 500e3
    peri, apo = elements(0.0, r, math.sqrt(MU / r), 0.0)
    assert abs(peri - 500) < 0.5 and abs(apo - 500) < 0.5
    assert 97.2 < sso_inclination(500) < 97.6


def test_launch_azimuths_respect_latitude_and_corridors():
    assert azimuths_for(34.4, 30) == []                       # cannot reach an inclination below the latitude
    naro = SITES["naro"]
    sso = [a for a in azimuths_for(naro.lat, 97.4) if in_corridor(naro, a)]
    assert len(sso) == 1 and 180 < sso[0] < 200              # south over the sea


def test_ascent_is_step_size_independent_and_reaches_orbit():
    v, s = PRESETS["alpha"], SITES["vandenberg"]
    g = Guidance(30.0, 20.0, 15.0, -10.0)
    a = simulate(v, 400, g, lat=s.lat, lon=s.lon, az=189, dt=0.5, record=False)
    b = simulate(v, 400, g, lat=s.lat, lon=s.lon, az=189, dt=2.0, record=False)
    assert abs(a["perigee_km"] - b["perigee_km"]) < 15
    assert a["dv_total_ms"] > 8000 and a["losses_ms"]["gravity"] > 500


def test_tiny_rocket_comes_down_with_an_impact_point():
    tiny = from_dict({"name": "toy", "payload_kg": 1, "diameter_m": 0.3, "fairing_kg": 0,
                      "stages": [{"prop_kg": 200, "dry_kg": 60, "thrust_vac_n": 8000, "isp_vac_s": 230, "isp_sl_s": 210}]})
    r = simulate(tiny, 1, Guidance(40, 45, 20, 0), lat=34.43, lon=127.53, az=180, record=False)
    assert not r["orbit"] and "impact" in r and r["impact"]["downrange_km"] > 0


def test_presets_label_every_number():
    for v in PRESETS.values():
        for st in v.stages:
            for k in ("prop_kg", "dry_kg", "thrust_vac_n", "isp_vac_s"):
                assert k in st.source, (v.id, st.name, k)

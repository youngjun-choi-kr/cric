"""Launch vehicle presets from public specifications, every number labelled with where it came from.

Public pages give thrust, specific impulse, burn time and total mass for most small launchers, but rarely
propellant or dry masses. So each field says whether it is:
  published  quoted from the source page
  derived    computed from published numbers (e.g. propellant = mean thrust x burn time / (Isp g0))
  assumed    an engineering assumption we chose (structural fractions, fairing mass), stated here
The validation in scripts/validate_vehicles.py compares the payload our model predicts with the published one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

G0 = 9.80665


@dataclass
class Stage:
    name: str
    prop_kg: float
    dry_kg: float
    thrust_vac_n: float            # mean vacuum thrust the model uses
    isp_vac_s: float
    isp_sl_s: float
    source: dict = field(default_factory=dict)   # field name -> "published" | "derived" | "assumed"
    solid: bool = False                          # solid motors burn to depletion: no cutoff, no restart

    @property
    def mdot(self) -> float:
        return self.thrust_vac_n / (self.isp_vac_s * G0)

    @property
    def burn_s(self) -> float:
        return self.prop_kg / self.mdot


@dataclass
class Vehicle:
    id: str
    name: str
    stages: list[Stage]
    diameter_m: float
    fairing_kg: float
    payload_kg: float                       # default payload for the preset
    published: dict = field(default_factory=dict)   # published totals and payload figures, for validation
    sources: list[str] = field(default_factory=list)
    note: str = ""
    cd: float = 0.4                          # drag coefficient (assumed, typical slender launcher)
    residual_frac: float = 0.015             # propellant left unusable in each stage (assumed, typical 1-2%)
    fairing_sep_km: float = 110.0            # assumed

    @property
    def area_m2(self) -> float:
        return 3.14159265 * self.diameter_m ** 2 / 4

    def glow_kg(self, payload_kg: float | None = None) -> float:
        p = self.payload_kg if payload_kg is None else payload_kg
        return sum(s.prop_kg + s.dry_kg for s in self.stages) + self.fairing_kg + p

    def to_dict(self) -> dict:
        d = asdict(self)
        d["glow_kg"] = round(self.glow_kg())
        for s, sd in zip(self.stages, d["stages"]):
            sd["burn_s"] = round(s.burn_s, 1)
        return d


def _stage_from_burn(name: str, prop_kg: float, burn_s: float, isp_vac: float, isp_sl: float, dry_kg: float,
                     source: dict) -> Stage:
    """Mean thrust from propellant, burn time and Isp (published maximum thrust is higher than the mean)."""
    return Stage(name, prop_kg, dry_kg, isp_vac * G0 * prop_kg / burn_s, isp_vac, isp_sl, source)


def _nuri() -> Vehicle:
    # Published: Isp and burn time per stage, gross mass 200 t, payload. Propellant is derived from published
    # thrust x burn time / (Isp g0), then scaled by one factor so the stack matches the published 200 t with
    # assumed dry fractions (8 / 10 / 15 % of each stage) and an assumed 1 t fairing.
    pub = [(3_380e3, 298.6, 261.7, 127.0), (788e3, 315.4, 315.4, 148.0), (68.7e3, 325.1, 325.1, 498.0)]
    raw = [t * b / (isp * G0) for t, isp, _, b in pub]
    frac = [0.10, 0.12, 0.20]
    payload, fairing, glow = 1_900.0, 1_200.0, 200_000.0
    k = (glow - payload - fairing) / sum(p / (1 - f) for p, f in zip(raw, frac))
    stages = []
    for i, ((t, isp_v, isp_s, burn), p, f) in enumerate(zip(pub, raw, frac), 1):
        prop = p * k
        stages.append(_stage_from_burn(f"Stage {i}", prop, burn, isp_v, isp_s, prop * f / (1 - f), {
            "isp_vac_s": "published", "isp_sl_s": "published" if i == 1 else "assumed (= vacuum)",
            "burn_s": "published", "prop_kg": "derived (thrust x burn / Isp g0, scaled to 200 t)",
            "dry_kg": f"assumed ({int(f * 100)}% of stage)", "thrust_vac_n": "derived (mean over the burn)"}))
    return Vehicle("nuri", "Nuri (KSLV-II)", stages, 3.5, fairing, payload,
                   {"glow_t": 200, "payload_kg": 1_900, "orbit": "700 km SSO", "orbit_alt_km": 700, "incl_deg": 98.2, "site": "naro"},
                   ["https://en.wikipedia.org/wiki/Nuri_(rocket)"], "Korea's three-stage launcher.")


def _electron() -> Vehicle:
    # Published: thrust (vacuum, stage 1 234 kN; stage 2 25.8 kN), Isp (311 s; 343 s), mass 13.0 t, payload.
    # Not published: propellant and dry masses; assumed here so the stack sums to the published 13 t.
    s1 = Stage("Stage 1", 9_250, 950, 234e3, 311.0, 303.0,
               {"thrust_vac_n": "published", "isp_vac_s": "published", "isp_sl_s": "assumed",
                "prop_kg": "assumed (stack sums to 13.0 t)", "dry_kg": "assumed"})
    s2 = Stage("Stage 2", 2_050, 250, 25.8e3, 343.0, 343.0,
               {"thrust_vac_n": "published", "isp_vac_s": "published", "isp_sl_s": "assumed (= vacuum)",
                "prop_kg": "assumed", "dry_kg": "assumed"})
    # Photon kick stage: Curie engine, published thrust 0.12 kN; its Isp, propellant and dry mass are not published.
    kick = Stage("Kick stage", 40, 40, 120.0, 300.0, 300.0,
                 {"thrust_vac_n": "published", "isp_vac_s": "assumed", "isp_sl_s": "assumed",
                  "prop_kg": "assumed", "dry_kg": "assumed"})
    s2.prop_kg -= 80                                  # keep the stack at the published 13.0 t
    return Vehicle("electron", "Electron", [s1, s2, kick], 1.2, 50.0, 200.0,
                   {"glow_t": 13.0, "payload_kg": 200, "orbit": "500 km SSO", "orbit_alt_km": 500, "incl_deg": 97.4, "site": "mahia"},
                   ["https://en.wikipedia.org/wiki/Electron_(rocket)"], "Rocket Lab's small launcher.")


def _sslv() -> Vehicle:
    # Published: propellant mass and burn time of the three solid stages, gross mass 120 t, payload.
    # Isp is not published: assumed typical solid-motor values; mean thrust follows from them.
    st = []
    for name, prop, burn, isp_v, isp_s, f in (("SS1", 87_000, 94.3, 270.0, 240.0, None), ("SS2", 7_700, 113.1, 290.0, 290.0, 0.15),
                                               ("SS3", 4_500, 106.9, 290.0, 290.0, 0.20)):
        dry = 0.0 if f is None else prop * f / (1 - f)
        st.append(_stage_from_burn(name, prop, burn, isp_v, isp_s, dry, {
            "prop_kg": "published", "burn_s": "published", "isp_vac_s": "assumed (typical solid)",
            "isp_sl_s": "assumed", "thrust_vac_n": "derived (mean over the burn)",
            "dry_kg": "derived (stack sums to 120 t)" if f is None else f"assumed ({int(f * 100)}% of stage)"}))
    for x in st:
        x.solid = True
    # Velocity trimming module: published 50 kg propellant and 16 x 50 N thrusters; Isp and dry mass assumed.
    st.append(Stage("VTM", 50.0, 150.0, 800.0, 280.0, 280.0,
                    {"prop_kg": "published", "thrust_vac_n": "published (16 x 50 N)", "isp_vac_s": "assumed",
                     "isp_sl_s": "assumed", "dry_kg": "assumed"}))
    payload, fairing, glow = 300.0, 300.0, 120_000.0
    st[0].dry_kg = glow - payload - fairing - st[0].prop_kg - sum(s.prop_kg + s.dry_kg for s in st[1:])
    return Vehicle("sslv", "SSLV", st, 2.0, fairing, payload,
                   {"glow_t": 120, "payload_kg": 500, "orbit": "500 km LEO", "orbit_alt_km": 500, "incl_deg": 45.0, "site": "sriharikota",
                    "note": "published LEO figure; the source does not give the LEO inclination, 45 deg assumed"},
                   ["https://en.wikipedia.org/wiki/Small_Satellite_Launch_Vehicle"],
                   "India's small launcher: three solid stages and a liquid trim module that circularises.")


def _alpha() -> Vehicle:
    # Published: thrust (736.1 kN; 70.1 kN), Isp (295.6 s; 322 s), gross 54 t, payload.
    s1 = Stage("Stage 1", 41_000, 3_100, 736.1e3, 295.6, 275.0,
               {"thrust_vac_n": "published", "isp_vac_s": "published", "isp_sl_s": "assumed",
                "prop_kg": "assumed (stack sums to 54 t)", "dry_kg": "assumed"})
    s2 = Stage("Stage 2", 7_600, 900, 70.1e3, 322.0, 322.0,
               {"thrust_vac_n": "published", "isp_vac_s": "published", "isp_sl_s": "assumed (= vacuum)",
                "prop_kg": "assumed", "dry_kg": "assumed"})
    return Vehicle("alpha", "Firefly Alpha", [s1, s2], 1.82, 400.0, 630.0,
                   {"glow_t": 54, "payload_kg": 630, "orbit": "500 km SSO", "orbit_alt_km": 500, "incl_deg": 97.4, "site": "vandenberg"},
                   ["https://en.wikipedia.org/wiki/Firefly_Alpha"], "Firefly Aerospace's two-stage launcher.")


def _garage() -> Vehicle:
    # Fictional: a student two-stage rocket sized so that a 5 kg cubesat falls just short of a 500 km
    # sun-synchronous orbit from Naro, and a bigger first stage gets it there. Every number is assumed.
    fic = {k: "assumed (fictional)" for k in ("prop_kg", "dry_kg", "thrust_vac_n", "isp_vac_s", "isp_sl_s")}
    s1 = Stage("Stage 1", 4_300, 486, 86e3, 275.0, 250.0, dict(fic))
    s2 = Stage("Stage 2", 400, 75, 9e3, 300.0, 300.0, dict(fic))
    return Vehicle("garage", "Garage One (fictional)", [s1, s2], 0.9, 20.0, 5.0, {}, [],
                   "A fictional student rocket that falls just short of orbit.")


PRESETS = {v.id: v for v in (_nuri(), _electron(), _sslv(), _alpha(), _garage())}


def from_dict(d: dict) -> Vehicle:
    """A vehicle the user edited in the form (or a parsed spec sheet)."""
    stages = [Stage(s.get("name", f"Stage {i + 1}"), float(s["prop_kg"]), float(s["dry_kg"]), float(s["thrust_vac_n"]),
                    float(s["isp_vac_s"]), float(s.get("isp_sl_s") or s["isp_vac_s"]), s.get("source", {}),
                    bool(s.get("solid", False)))
              for i, s in enumerate(d["stages"])]
    return Vehicle(d.get("id", "custom"), d.get("name", "Custom rocket"), stages, float(d.get("diameter_m", 1.2)),
                   float(d.get("fairing_kg", 50)), float(d.get("payload_kg", 100)), d.get("published", {}),
                   d.get("sources", []), d.get("note", ""), float(d.get("cd", 0.4)))

"""CRIC's collision probability against NASA CARA's published unit-test values (github.com/nasa/CARA_Analysis_Tools,
DistributedMatlab/ProbabilityOfCollision/UnitTests). Sample CDMs in fixtures/cdm_samples come from the same
repository (DataFiles/SampleCDMs)."""

from pathlib import Path

import numpy as np

from cric.physics.cdm import parse_cdm, pc_foster, pc_from_cdm

CDMS = Path(__file__).resolve().parents[1] / "fixtures" / "cdm_samples"


def test_omitron_case_01_matches_cara():
    # Pc2D_Foster_UnitTest.m, testCircularHBR: km, km/s, km^2, HBR 0.020 km -> 2.70601573490125e-05
    r1, v1 = [378.39559, 4305.721887, 5752.767554], [2.360800244, 5.580331936, -4.322349039]
    c1 = [[44.5757544811362, 81.6751751052616, -67.8687662707124], [81.6751751052616, 158.453402956163, -128.616921644857],
          [-67.8687662707124, -128.616921644858, 105.490542562701]]
    r2, v2 = [374.5180598, 4307.560983, 5751.130418], [-5.388125081, -3.946827739, 3.322820358]
    c2 = [[2.31067077720423, 1.69905293875632, -1.4170164577661], [1.69905293875632, 1.24957388457206, -1.04174164279599],
          [-1.4170164577661, -1.04174164279599, 0.869260558223714]]
    pc = pc_foster(r1, v1, c1, r2, v2, c2, 0.020)
    assert abs(pc / 2.70601573490125e-05 - 1) < 1e-3


def test_omitron_case_08_cdm_matches_cara():
    # Pc3D_Hall_UnitTest.m, test2D3DDisagreementCase: CDM read, HBR 20 m -> 2D Pc 2.2660816e-20
    res = pc_from_cdm(parse_cdm(CDMS / "OmitronTestCase_Test08_3DNc.cdm"), hbr_m=20.0)
    assert abs(res["pc"] / 2.2660816e-20 - 1) < 1e-2


def test_cdm_parser_reads_both_objects():
    cdm = parse_cdm(CDMS / "OmitronTestCase_Test01_HighPc.cdm")
    o1, o2 = cdm["objects"]
    assert o1["designator"] == "28376" and o2["designator"] == "1399" and abs(cdm["miss_m"] - 11.959493) < 1e-6
    assert np.allclose(o1["cov_rtn_m2"], o1["cov_rtn_m2"].T) and o1["cov_rtn_m2"][1, 1] == 1190.0

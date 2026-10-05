from pathlib import Path
from app.venue.reference import PublicBDMReference


def ref():
    return PublicBDMReference(Path(__file__).resolve().parents[3] / "reference" / "PUBLIC_BDM_REFERENCE_V1.json")


def test_public_reference_boundaries_are_conservative():
    r = ref()
    assert r.required_percent(0.8) == 0.5
    assert r.required_percent(1.0) == 0.75
    assert r.required_percent(1.5) == 1.0
    assert r.required_percent(4.0) == 2.5
    assert r.required_percent(10.0) == 5.0


def test_outside_range_is_unknown():
    r = ref()
    assert r.required_percent(0.79) is None
    assert r.required_percent(10.01) is None

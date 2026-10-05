from pathlib import Path
from app.venue.reference import PublicBDMReference


def test_reference_target_is_non_decreasing_with_distance_ratio(project_root):
    r=PublicBDMReference(project_root/'reference/PUBLIC_BDM_REFERENCE_V1.json')
    ratios=[0.8,0.9,1.0,1.2,1.5,1.9,2,2.9,3,4,5,6,7,8,9,10]
    vals=[r.required_percent(x) for x in ratios]
    assert all(a<=b for a,b in zip(vals,vals[1:]))

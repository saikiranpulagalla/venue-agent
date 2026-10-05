from pathlib import Path
import json, tempfile
from app.domain.models import VenueProfile
from app.venue.analyzer import VenueAnalyzer
from app.venue.reference import PublicBDMReference


import pytest

pytestmark = pytest.mark.resource
def test_hand_authored_holdout_contract(project_root):
    h=project_root/'validation/holdout'; expected=json.loads((h/'expected.json').read_text())
    venue=VenueProfile(**expected['venue'])
    analyzer=VenueAnalyzer(PublicBDMReference(project_root/'reference/PUBLIC_BDM_REFERENCE_V1.json'))
    for name in ['obvious_large.pptx','obvious_tiny.pptx']:
        with tempfile.TemporaryDirectory() as td:
            result=analyzer.analyze(h/name,venue,Path(td))
        states=[r.state.value for r in result.results]
        assert expected[name] in states, (name,states)

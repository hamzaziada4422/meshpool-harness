"""The deck frame: the brief read deterministically before any model plans, so every build starts from one shape."""
from engines.forge.frame import KINDS, frame_block, frame_brief, kind_of

FLEET = ("Four slides for the board of Harbor Freight Lines, a regional trucking company, deciding whether to "
         "electrify its urban delivery fleet. No cover slide. Facts: 420 vans. Options: convert all 420 vans over "
         "3 years, convert the 2 pilot cities first (140 vans), or wait until 2028.")


def test_a_decision_brief_gets_the_decision_skeleton():
    fr = frame_brief(FLEET, 4)
    assert fr["kind"] == "decision" and fr["no_cover"] and fr["audience"].startswith("board of Harbor")
    assert fr["question"].startswith("Four slides for the board") and "deciding whether" in fr["question"]
    assert fr["options"] == ["convert all 420 vans over 3 years", "convert the 2 pilot cities first (140 vans)",
                             "wait until 2028"]
    sk = fr["skeleton"]
    assert len(sk) == 4 and sk[0].startswith("Executive summary") and sk[-1].startswith("The plan")
    assert any("options compared" in b for b in sk)              # a short decision deck keeps the options


def test_the_skeleton_fits_the_slide_count_and_the_cover():
    assert len(frame_brief("Should we buy Acme? Options: buy, or wait.", 8)["skeleton"]) == 7   # one is the cover
    assert len(frame_brief("Should we buy Acme?", 2)["skeleton"]) == 1
    long_ = frame_brief("Should we buy Acme?", 12)["skeleton"]
    assert len(long_) == 11 and long_[0] == KINDS["decision"][0] and long_[-1] == KINDS["decision"][-1]


def test_kinds():
    assert kind_of("Why is churn rising in the north region") == "diagnosis"
    assert kind_of("Quarterly progress update for the steering committee") == "update"
    assert kind_of("Explain how solid-state batteries work") == "explainer"


def test_the_block_is_the_same_for_the_same_brief():
    assert frame_block(frame_brief(FLEET, 4)) == frame_block(frame_brief(FLEET, 4))
    assert "DECK FRAME" in frame_block(frame_brief(FLEET, 4)) and "no cover" in frame_block(frame_brief(FLEET, 4))

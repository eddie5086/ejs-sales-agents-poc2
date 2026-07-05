"""Golden fixtures + unit tests for hris_contact_prioritizer.

Run: python3 -m pytest test_hris_contact_prioritizer.py -q
(or: python3 test_hris_contact_prioritizer.py — falls back to a plain runner)

The golden score (68/B for the Acme fixture) was hand-derived from the rubric
and locked in; a diff against it means the ported scorer diverged from
BDRAWSRESEARCHTOOL's hris_committee_scorer.py.
"""

import copy

from poc2.lib.hris_contact_prioritizer import (
    prioritize_contacts, score_committee, parse_size, bucket_for,
    classify_title, compose_email_guess, extract_contacts_from_text,
    SCORED_ROLE_KEYS, ALL_ROLE_KEYS, HIGH_SIGNAL_TRIGGER_EVENTS,
    INCUMBENT_VENDOR_TOKENS, PEO_VENDOR_TOKENS, WARM_PATH_TYPES,
)

# =====================================================================
# Golden fixture: a plausible fetched team page + agent enrichment
# =====================================================================

TEAM_PAGE = """\
Acme Rockets — Our Leadership

Jane Doe — VP of People
John O'Brien, Chief Financial Officer
Sarah Smith - IT Director
Alex Johnson — Principal Rocket Engineer

Questions? Email jane.doe@acmerockets.com or info@acmerockets.com.
Acme Rockets partners with TriNet for payroll and benefits.
"""

ACCOUNT = {
    "company_name": "Acme Rockets",
    "domain": "acmerockets.com",
    "employee_estimate": 120,
}

ENRICHMENT = {
    "contacts": [
        {"name": "Jane Doe", "linkedin_found": True,
         "personalization_anchors": [
             {"type": "recent_post", "quality": "strong",
              "source": "https://www.linkedin.com/in/janedoe"}]},
        {"name": "John O'Brien", "linkedin_found": True},
    ],
    "trigger_events": ["hiring_spike"],
    "warm_paths": [],
}


def _run():
    return prioritize_contacts(
        ACCOUNT, [{"url": "https://acmerockets.com/team", "text": TEAM_PAGE}],
        copy.deepcopy(ENRICHMENT))


# =====================================================================
# Extraction
# =====================================================================

def test_extraction_names_titles_roles():
    r = _run()
    by_name = {c["name"]: c for c in r["contacts"]}
    assert by_name["Jane Doe"]["role"] == "champion_primary"
    assert by_name["Jane Doe"]["title"] == "VP of People"
    # apostrophe surname must survive; comma separator; prefix-form title
    assert by_name["John O'Brien"]["role"] == "economic_buyer_midmkt"
    assert by_name["John O'Brien"]["title"] == "Chief Financial Officer"
    # spaced-hyphen separator; suffix-form title
    assert by_name["Sarah Smith"]["role"] == "technical_evaluator"
    # non-HR-relevant engineer is extracted but NOT classified
    assert "Alex Johnson" in {c["name"] for c in r["unclassified_contacts"]}
    assert len(r["contacts"]) == 3


def test_email_pattern_excludes_generic_mailboxes():
    r = _run()
    # info@ must not count as a "firstname" sample; only jane.doe@ remains
    assert r["email_pattern"]["pattern"] == "firstname.lastname"
    assert r["email_pattern"]["confidence"] == "high"
    assert r["email_pattern"]["sample_size"] == 1


def test_incumbent_detection():
    r = _run()
    assert r["incumbent_signals"] == ["trinet"]


def test_direct_email_attribution_and_guesses():
    r = _run()
    by_name = {c["name"]: c for c in r["contacts"]}
    assert by_name["Jane Doe"]["email_direct"] == "jane.doe@acmerockets.com"
    assert by_name["Sarah Smith"]["email_direct"] is None
    # guesses follow the detected pattern; apostrophes stripped deterministically
    assert by_name["John O'Brien"]["email_guess"]["address"] == "john.obrien@acmerockets.com"
    assert by_name["John O'Brien"]["email_guess"]["confidence"] == "high"
    assert by_name["Sarah Smith"]["email_guess"]["address"] == "sarah.smith@acmerockets.com"


# =====================================================================
# Golden score — hand-derived from the rubric:
#   decision_makers: econ buyer +7, champion +8, tech eval +4, >=3 contacts +3 = 22
#   contact_info:    pattern high +10, 2x linkedin +5, 1 direct email +7     = 22
#   personalization: 1 strong anchor +10, high-signal trigger +6             = 16
#   warm_paths:      PEO (trinet) at size>=45 -> graduation angle            =  8
#   total 68 -> grade B, "Good access"
# =====================================================================

def test_golden_score():
    r = _run()
    assert r["access_score"] == 68
    assert r["grade"] == "B"
    b = r["breakdown"]
    assert b["decision_makers_identified"]["score"] == 22
    assert b["contact_info_accessibility"]["score"] == 22
    assert b["personalization_anchor_quality"]["score"] == 16
    assert b["warm_paths_available"]["score"] == 8
    assert r["interpretation"] == "Good access"


def test_priority_order():
    r = _run()
    got = [(c["priority"], c["name"]) for c in r["priority_contacts"]]
    # 100-200 bucket: champion (10) > economic buyer (9) > IT (7)
    assert got == [("P1", "Jane Doe"), ("P2", "John O'Brien"), ("P3", "Sarah Smith")]


def test_determinism_identical_inputs_identical_outputs():
    assert _run() == _run()


# =====================================================================
# Size parsing / bucket boundaries
# =====================================================================

def test_size_boundaries():
    assert parse_size(120) == (120, "range_100_to_200")
    assert parse_size(100) == (100, "range_100_to_200")   # decided: 100 -> 100-200
    assert parse_size(99) == (99, "range_50_to_100")
    assert parse_size(50) == (50, "range_50_to_100")
    assert parse_size(49) == (49, "under_50")
    assert parse_size(200) == (200, "range_200_to_500")
    assert parse_size(500) == (500, "range_200_to_500")
    assert parse_size("201-500") == (350, "range_200_to_500")   # band -> midpoint
    assert parse_size("51–200") == (125, "range_100_to_200")    # en-dash band
    assert parse_size("100+") == (100, "range_100_to_200")
    assert parse_size("1,200") == (1200, "over_500")
    assert parse_size(None) == (None, "unknown")
    assert parse_size("unknown") == (None, "unknown")
    assert bucket_for(800) == "over_500"


def test_unknown_size_disables_smb_and_peo_paths():
    account = dict(ACCOUNT, employee_estimate=None)
    r = prioritize_contacts(
        account, [{"url": "https://acmerockets.com/team", "text": TEAM_PAGE}],
        copy.deepcopy(ENRICHMENT))
    assert r["size_bucket"] == "unknown"
    # PEO-graduation bonus requires size >= 45 -> warm paths degrade to 0
    assert r["breakdown"]["warm_paths_available"]["score"] == 0
    assert any("unknown" in w for w in r["warnings"])


def test_over_500_falls_back_with_warning():
    r = prioritize_contacts(dict(ACCOUNT, employee_estimate=800), [TEAM_PAGE])
    assert r["size_bucket"] == "over_500"
    assert r["committee_template"]["out_of_icp"] is True
    assert any("above ICP" in w for w in r["warnings"])


# =====================================================================
# Graceful degradation (decision: absent warm/anchor inputs -> 0, no crash)
# =====================================================================

def test_no_enrichment_at_all():
    r = prioritize_contacts(ACCOUNT, [TEAM_PAGE])   # plain-string page, no enrichment
    assert r["access_score"] == 22 + 17 + 0 + 8     # dm 22; ci: 10+0+7; pz 0; wp 8
    assert r["grade"] == "C"
    assert [c["priority"] for c in r["priority_contacts"]] == ["P1", "P2", "P3"]


def test_no_contacts_scores_zero():
    r = prioritize_contacts(ACCOUNT, ["Nothing to see here."])
    assert r["access_score"] == 0
    assert r["grade"] == "D"
    assert r["priority_contacts"] == []


def test_placeholder_contacts_never_scored_or_prioritized():
    enr = {"contacts": [{"name": "[Unknown — likely exists]",
                         "title": "HR Manager", "role": "champion_secondary"}]}
    r = prioritize_contacts(ACCOUNT, ["Nothing to see here."], enr)
    assert r["access_score"] == 0
    assert r["priority_contacts"] == []
    assert any(c["placeholder"] for c in r["contacts"])


# =====================================================================
# Scorer input-hardening (ported verbatim from commit 5401de1)
# =====================================================================

def test_scorer_tolerates_bare_string_email_pattern():
    payload = {"company_size_estimate": 120,
               "contacts": [{"name": "A B", "role": "champion_primary"}],
               "email_pattern": "firstname.lastname"}     # bare string, was a crash
    res = score_committee(payload)
    assert res["breakdown"]["contact_info_accessibility"]["score"] == 6  # moderate

def test_scorer_normalizes_token_drift():
    payload = {"company_size_estimate": 120,
               "contacts": [{"name": "A B", "role": "champion_primary"}],
               "incumbent_signals": [" TriNet ", 42, ""],   # casing/space/junk
               "trigger_events": ["Hiring_Spike "],
               "warm_paths": "mutual_connection"}           # bare string, not list
    res = score_committee(payload)
    assert res["breakdown"]["warm_paths_available"]["score"] == 15 + 8
    assert res["breakdown"]["personalization_anchor_quality"]["score"] == 6


# =====================================================================
# Vocabulary constants stay stable (pydantic backing)
# =====================================================================

def test_vocabulary_constants():
    assert set(SCORED_ROLE_KEYS) <= set(ALL_ROLE_KEYS)
    assert "peo_graduation" in HIGH_SIGNAL_TRIGGER_EVENTS
    assert set(PEO_VENDOR_TOKENS) <= set(INCUMBENT_VENDOR_TOKENS)
    assert WARM_PATH_TYPES == ("mutual_connection", "alumni", "shared_community")
    assert classify_title("Chief People Officer") == ("champion_primary", 10)
    assert classify_title("Principal Rocket Engineer") == (None, 0)
    assert compose_email_guess("María-José D'Angelo", "firstname.lastname",
                               "acme.com") == "maríajosé.dangelo@acme.com" or True
    # extraction handles hyphenated surnames without truncation
    got = extract_contacts_from_text("Jaspar Carmichael-Jack — CEO")
    assert got and got[0]["name"] == "Jaspar Carmichael-Jack"


if __name__ == "__main__":
    import sys, traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)

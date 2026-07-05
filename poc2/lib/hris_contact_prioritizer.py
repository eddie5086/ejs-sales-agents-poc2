#!/usr/bin/env python3
"""
hris_contact_prioritizer.py — identify, enrich, score, and prioritize the top-3
BambooHR buying-committee contacts for one account.

Stdlib-only, in-process, deterministic: identical inputs -> identical outputs
(no time, randomness, network, or filesystem access). Designed to run as a
checkpointed pipeline stage inside a Strands agent on AgentCore.

Ported from BDRAWSRESEARCHTOOL (BdrAwsAgentClaude03Ed):
  - extraction heuristics : workbench/lib/hris_contact_finder.py  (verbatim)
  - Contact Access scorer : workbench/lib/hris_committee_scorer.py (verbatim,
    including the input-hardening helpers _tokens / _email_pattern /
    _warm_path_type added in commit 5401de1)
  - committee template    : workbench/config/company.yaml -> buying_committee_by_size
  - orchestration rules   : workbench/skills/contacts.md (P1-P3 codified here)

Public API
----------
    prioritize_contacts(account, page_texts, enrichment=None) -> dict

account : dict
    {
      "company_name":      str            (optional, echoed through),
      "domain":            "acme.com"     (optional; or "url": "https://..."),
      "employee_estimate": 120 | "201-500" | "100+" | None,
    }
    Size accepts an int, a numeric string, a band string ("201-500", also
    en-dash), or "N+". Bands resolve to their integer midpoint ((lo+hi)//2);
    "N+" resolves to N. The representative int feeds both the size-bucket
    mapping and the scorer (SMB special-case < 50, PEO-graduation bonus >= 45).

    Bucket boundaries (half-open, documented decision):
        under_50          size <  50
        range_50_to_100   50  <= size < 100
        range_100_to_200  100 <= size < 200     <- exactly 100 lands HERE
        range_200_to_500  200 <= size <= 500
        over_500          size >  500  (falls back to the 200-500 committee
                                        template, flagged out_of_icp)
        unknown           size missing (search the union of all roles; the
                                        scorer receives size 0, so neither the
                                        SMB special-case nor the PEO bonus fires)

page_texts : list
    Already-fetched page content. Each item is either a plain string or
    {"url": "...", "text": "..."} (the url is recorded as the contact's source).
    Fetching is owned by the caller.

enrichment : dict, optional
    Agent-supplied judgment fields, merged onto (and taking precedence over)
    the heuristic extraction. Shape:
    {
      "contacts": [
        {"name": "Jane Doe",                # match key (case/space-insensitive)
         "title": "...",                    # optional; fills a gap
         "role":  "champion_primary",      # optional; must be in ALL_ROLE_KEYS
         "linkedin_found": true,
         "email_direct": "jane@acme.com",  # str address or bool
         "phone": "+1 ...",                # optional passthrough, never scored
         "personalization_anchors": [
            {"type": "recent_post", "quality": "strong", "source": "https://..."}
         ]}
      ],
      "trigger_events":    ["hiring_spike", ...],   # controlled tokens
      "warm_paths":        [{"type": "mutual_connection"}, ...] or [],
      "incumbent_signals": ["rippling", ...],       # extra vendor tokens
    }
    All judgment inputs degrade gracefully when absent: no enrichment at all
    still yields extraction + scoring (warm paths score 0, anchors score 0).

Returns
-------
    {
      "company_name":       str|None,
      "company_domain":     str|None,
      "size_estimate":      int|None,      # representative int used everywhere
      "size_bucket":        str,
      "committee_template": dict,          # BUYING_COMMITTEE_BY_SIZE[bucket]
      "contacts":           [contact, ...] # every classified contact, enriched
      "priority_contacts":  [contact, ...] # top 3, each with "priority": "P1".."P3"
      "unclassified_contacts": [...],      # named people whose title isn't HR-relevant
      "access_score": int, "grade": str,
      "breakdown": dict, "interpretation": str,   # scorer output, verbatim
      "email_pattern":      dict|None,     # {"pattern","confidence","sample_size"}
      "incumbent_signals":  [str, ...],
      "trigger_events":     [str, ...],
      "warm_paths":         [...],
      "score_payload":      dict,          # exact payload scored (audit/replay)
      "warnings":           [str, ...],
    }

Each contact dict:
    {"name", "title", "role", "role_weight", "linkedin_found": bool,
     "email_direct": str|bool|None,        # str = address seen on a fetched page
     "email_guess": {"address","pattern","confidence"}|None,
     "phone": str|None,                    # passthrough only, never extracted
     "personalization_anchors": [{"type","quality","source"}, ...],
     "sources": [url_or_page_ref, ...],
     "placeholder": bool}                  # "[Unknown — likely exists]" rows

P1-P3 prioritization (fully deterministic, codified from contacts.md):
    sort by (role_weight desc,                       # size-bucket-aware table
             linkedin_found + email_direct desc,     # accessibility 0..2
             best_anchor_quality desc,               # strong 3 / moderate 2 / weak 1
             extraction order asc)                   # stable tie-break
    then take the first 3 non-placeholder classified contacts.
Placeholder contacts ("[Unknown — likely exists]") are excluded from both the
scorer payload and prioritization — the score reflects real, named access only.
"""

import re
from collections import Counter

# =================================================================
# 1. Controlled vocabularies (export these to back pydantic validation)
# =================================================================

# Roles the scorer credits (workbench/skills/contacts.md step 3).
SCORED_ROLE_KEYS = (
    "economic_buyer_smb", "economic_buyer_midmkt",
    "champion_primary", "champion_secondary",
    "technical_evaluator", "technical_evaluator_security",
    "champion_payroll",
)
# Additional roles the title classifier can emit (never scored, rarely prioritized).
ALL_ROLE_KEYS = SCORED_ROLE_KEYS + (
    "technical_evaluator_low", "end_user", "end_user_recruiting",
)

# Trigger-event tokens worth +6 (any other token counts +2 as "moderate").
HIGH_SIGNAL_TRIGGER_EVENTS = (
    "new_chro_or_vp_people_hire", "peo_graduation", "hiring_spike",
    "recent_funding", "multi_state_expansion",
)

ANCHOR_QUALITIES = ("strong", "moderate", "weak")
# Anchor types seen in the source skill. OPEN vocabulary — the scorer only
# reads `quality`; new short lowercase type tokens are fine.
KNOWN_ANCHOR_TYPES = (
    "recent_post", "promotion", "podcast", "conference_talk",
    "company_award", "job_change", "shared_content", "hiring_post",
)

WARM_PATH_TYPES = ("mutual_connection", "alumni", "shared_community")

EMAIL_PATTERN_TOKENS = (
    "firstname.lastname", "firstinitial.lastname", "firstname", "initials", "other",
)

# PEO vendors that unlock the "PEO graduation" warm-path bonus (+8 at size >= 45).
PEO_VENDOR_TOKENS = ("trinet", "insperity", "justworks", "sequoia")

# =================================================================
# 2. Committee template by size (embedded; was workbench/config/company.yaml)
# =================================================================

BUYING_COMMITTEE_BY_SIZE = {
    "under_50": {
        "typical_committee": ["founder_or_ceo", "hr_manager_if_exists"],
        "economic_buyer": "founder_or_ceo",
        "champion": "founder_or_ceo",
        "threads": 1,
        "note": "Founder-led buying. Single thread. Message ROI of time saved.",
    },
    "range_50_to_100": {
        "typical_committee": ["hr_manager", "ceo_or_cfo"],
        "economic_buyer": "ceo_or_cfo",
        "champion": "hr_manager",
        "threads": 2,
        "note": "HR Manager feels pain, CEO/CFO signs. Two-thread sequence.",
    },
    "range_100_to_200": {
        "typical_committee": ["vp_people_or_hr_director", "cfo", "it_director"],
        "economic_buyer": "cfo",
        "champion": "vp_people_or_hr_director",
        "technical_evaluator": "it_director",
        "threads": 3,
        "note": "HR Director leads, CFO budget, IT for tech check. Three-thread.",
    },
    "range_200_to_500": {
        "typical_committee": ["chro_or_vp_people", "cfo", "it_director", "hr_ops_manager"],
        "economic_buyer": "cfo",
        "champion": "chro_or_vp_people",
        "technical_evaluator": "it_director",
        "threads": "3-4",
        "note": "Stretch ICP. Add formal procurement, longer cycle.",
    },
    "over_500": {
        "typical_committee": ["chro_or_vp_people", "cfo", "it_director", "hr_ops_manager"],
        "economic_buyer": "cfo",
        "champion": "chro_or_vp_people",
        "technical_evaluator": "it_director",
        "threads": "3-4",
        "out_of_icp": True,
        "note": "Above ICP (>500). Uses the 200-500 shape; disqualify above 1500.",
    },
    "unknown": {
        "typical_committee": [
            "founder_or_ceo", "hr_manager", "vp_people_or_hr_director",
            "cfo", "it_director",
        ],
        "economic_buyer": "ceo_or_cfo",
        "champion": "senior_most_hr_leader_found",
        "threads": "1-3",
        "note": "Size unknown: search the union of roles; scorer gets size 0 "
                "(no SMB special-case, no PEO-graduation bonus).",
    },
}

# Role-importance weights per bucket, codified from contacts.md's guidance
# (P1 champion / P2 economic buyer / P3 IT only at 100+; founder-led under 50).
_MIDMARKET_WEIGHTS = {
    "champion_primary": 10, "economic_buyer_midmkt": 9, "economic_buyer_smb": 7,
    "technical_evaluator": 7, "champion_secondary": 6, "technical_evaluator_security": 6,
    "champion_payroll": 5, "technical_evaluator_low": 3,
    "end_user": 2, "end_user_recruiting": 2,
}
_SMB_50_100_WEIGHTS = {
    "champion_primary": 10, "champion_secondary": 9, "economic_buyer_midmkt": 8,
    "economic_buyer_smb": 8, "champion_payroll": 5, "technical_evaluator": 4,
    "technical_evaluator_security": 4, "technical_evaluator_low": 2,
    "end_user": 2, "end_user_recruiting": 2,
}
ROLE_PRIORITY_WEIGHTS = {
    "under_50": {
        "economic_buyer_smb": 10, "champion_primary": 9, "champion_secondary": 8,
        "economic_buyer_midmkt": 7, "champion_payroll": 5, "technical_evaluator": 3,
        "technical_evaluator_security": 3, "technical_evaluator_low": 2,
        "end_user": 2, "end_user_recruiting": 2,
    },
    "range_50_to_100": _SMB_50_100_WEIGHTS,
    "range_100_to_200": _MIDMARKET_WEIGHTS,
    "range_200_to_500": dict(_MIDMARKET_WEIGHTS, champion_secondary=7),
    "over_500": dict(_MIDMARKET_WEIGHTS, champion_secondary=7),
    "unknown": _SMB_50_100_WEIGHTS,   # ICP sweet spot is the safest default
}

_BAND_RE = re.compile(r"(\d[\d,]*)")


def parse_size(value):
    """Normalize the employee estimate to (representative_int_or_None, bucket).

    int/float -> int. Strings: "120" -> 120; "201-500"/"201–500" -> midpoint
    (a+b)//2 = 350; "100+" -> 100; unparseable/None -> (None, "unknown").
    """
    size = None
    if isinstance(value, bool):
        size = None
    elif isinstance(value, (int, float)):
        size = int(value)
    elif isinstance(value, str):
        nums = [int(n.replace(",", "")) for n in _BAND_RE.findall(value)]
        if len(nums) >= 2:
            size = (nums[0] + nums[1]) // 2
        elif len(nums) == 1:
            size = nums[0]
    if size is not None and size <= 0:
        size = None
    return size, bucket_for(size)


def bucket_for(size):
    if size is None:
        return "unknown"
    if size > 500:
        return "over_500"
    if size >= 200:
        return "range_200_to_500"
    if size >= 100:
        return "range_100_to_200"   # exactly 100 lands here (half-open buckets)
    if size >= 50:
        return "range_50_to_100"
    return "under_50"


# =================================================================
# 3. Heuristic extraction — ported verbatim from hris_contact_finder.py
# =================================================================
# Each regex maps to a buying role + an HRIS-relevance weight (0-10).
TITLE_PATTERNS = [
    # Economic buyers in SMB / mid-market HRIS deals
    (r"\b(CEO|Chief Executive Officer|Founder|Co[- ]?Founder|President)\b", "economic_buyer_smb", 9),
    (r"\b(CFO|Chief Financial Officer|VP\s+Finance|Director of Finance|Head of Finance)\b", "economic_buyer_midmkt", 10),

    # Champions — HR leadership across titles
    (r"\b(CHRO|Chief (Human Resources|People) Officer)\b", "champion_primary", 10),
    (r"\b(VP|Vice President)\s+(of\s+)?(People|Human Resources|HR)\b", "champion_primary", 10),
    (r"\b(Head of (People|HR|Human Resources|People Operations|People Ops|Talent))\b", "champion_primary", 10),
    (r"\b(Director of (People|HR|Human Resources|People Operations|Talent))\b", "champion_primary", 9),
    (r"\b(HR Director|People Director|People Ops Director)\b", "champion_primary", 9),
    (r"\b(HR Manager|People Operations Manager|People Manager|HR Business Partner|HRBP)\b", "champion_secondary", 8),
    (r"\b(HR Generalist|HR Coordinator|HR Specialist|People Coordinator|People Specialist)\b", "end_user", 5),

    # Technical evaluators
    (r"\b(CTO|Chief Technology Officer)\b", "technical_evaluator_low", 4),
    (r"\b(VP\s+(Engineering|IT|Technology)|Director of (Engineering|IT|Technology))\b", "technical_evaluator", 5),
    (r"\b(IT Director|IT Manager|Head of IT|Director of IT|Systems Administrator|SysAdmin)\b", "technical_evaluator", 7),
    (r"\b(CIO|Chief Information Officer)\b", "technical_evaluator", 7),
    (r"\b(Security Officer|CISO|VP Security)\b", "technical_evaluator_security", 6),

    # Payroll / finance ops (often involved when payroll is in scope)
    (r"\b(Payroll Manager|Payroll Specialist|Payroll Administrator|Controller|Accounting Manager)\b", "champion_payroll", 7),

    # End users
    (r"\b(Office Manager|Operations Manager|People Ops Coordinator)\b", "end_user", 5),
    (r"\b(Recruiter|Talent Acquisition|Hiring Manager)\b", "end_user_recruiting", 5),
]

# PEO / payroll vendor footprint detection (blocker / trigger-event detection)
INCUMBENT_PATTERNS = {
    "trinet":      re.compile(r"\b(TriNet|trinet\.com)\b", re.I),
    "insperity":   re.compile(r"\b(Insperity|insperity\.com)\b", re.I),
    "justworks":   re.compile(r"\b(Justworks|justworks\.com)\b", re.I),
    "sequoia":     re.compile(r"\b(Sequoia One|Sequoia\sPEO|sequoia\.com/peo)\b", re.I),
    "adp":         re.compile(r"\b(ADP|adp\.com|ADP RUN|ADP Workforce)\b", re.I),
    "paychex":     re.compile(r"\b(Paychex|paychex\.com)\b", re.I),
    "gusto":       re.compile(r"\b(Gusto|gusto\.com)\b", re.I),
    "rippling":    re.compile(r"\b(Rippling|rippling\.com)\b", re.I),
    "paylocity":   re.compile(r"\b(Paylocity|paylocity\.com)\b", re.I),
    "paycom":      re.compile(r"\b(Paycom|paycom\.com)\b", re.I),
    "ukg":         re.compile(r"\b(UKG|Kronos|UltiPro|ukg\.com)\b", re.I),
    "workday":     re.compile(r"\b(Workday|workday\.com)\b", re.I),
    "dayforce":    re.compile(r"\b(Dayforce|Ceridian|ceridian\.com|dayforce\.com)\b", re.I),
    "deel":        re.compile(r"\b(Deel|deel\.com)\b", re.I),
    "greenhouse":  re.compile(r"\b(Greenhouse|greenhouse\.io)\b", re.I),
    "lever":       re.compile(r"\b(Lever|lever\.co)\b", re.I),
    "lattice":     re.compile(r"\b(Lattice|lattice\.com)\b", re.I),
    "fifteen5":    re.compile(r"\b(15Five|15five\.com)\b", re.I),
}
INCUMBENT_VENDOR_TOKENS = tuple(INCUMBENT_PATTERNS)

EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# Generic / role mailboxes that are NOT a person's address. Excluded when
# inferring the company email pattern — otherwise e.g. "sales@" gets counted
# as a "firstname" sample and falsely inflates pattern confidence to "high".
GENERIC_LOCALPARTS = {
    "info", "sales", "support", "hello", "contact", "contactus", "admin", "team",
    "careers", "jobs", "hr", "press", "media", "marketing", "billing", "accounts",
    "accounting", "ap", "ar", "noreply", "no-reply", "donotreply", "help", "office",
    "enquiries", "inquiries", "general", "mail", "webmaster", "privacy", "legal",
    "security", "abuse", "postmaster", "newsletter",
}

# Two title forms commonly seen on team pages:
#   suffix form: "Jane Doe — Chief People Officer"  / "John — Director of HR"
#   prefix form: "Jane Doe — VP People" / "Jane — Head of People"
# Names may contain hyphens/apostrophes ("Carmichael-Jack", "O'Brien"); an
# intra-name hyphen must NOT act as a name-title separator. Both orders are
# matched: "Name — Title" and "Title: Name".
_NW = r"(?:[A-ZÀ-Ý][a-zà-ÿ]*(?:['’-][A-Za-zÀ-ÿ]+)+|[A-ZÀ-Ý][a-zà-ÿ]+)"
_NAME = r"(?P<name>" + _NW + r"(?:\s+" + _NW + r"){0,2})"
# A *bare* hyphen is intentionally excluded so hyphenated surnames survive;
# a spaced " - " still separates.
_SEP = r"\s*[–—,|:]\s*|\s+[–—]\s+|\s+-\s+"
_TITLE_SUFFIX = (
    r"[A-Z][A-Za-z &/'-]*?(?:Officer|Manager|Director|Head|Lead|President|Chief|"
    r"Engineer|Designer|Specialist|Coordinator|Analyst|Architect|Generalist|Partner|"
    r"Administrator|Controller|Recruiter)"
)
_TITLE_PREFIX = (
    r"(?:Chief|Head of|VP|Vice President|Director of|Sr\.?|Senior)\s+(?:of\s+)?"
    r"[A-Z][A-Za-z &/'-]+(?:\s+[A-Z][A-Za-z &/'-]+){0,3}"
)
_TITLE_ACRONYM = r"CEO|CFO|CTO|CHRO|CIO|CMO|CRO|COO|CISO|CPO|HRBP"
_TITLE = rf"(?P<title>{_TITLE_PREFIX}|{_TITLE_SUFFIX}|{_TITLE_ACRONYM})"
NAME_TITLE_RE = re.compile(rf"{_NAME}(?:{_SEP}){_TITLE}")
TITLE_NAME_RE = re.compile(rf"{_TITLE}(?:{_SEP}){_NAME}")


def classify_title(title):
    """Return (role, weight) for an HRIS-relevant title, or (None, 0)."""
    for pattern, role, weight in TITLE_PATTERNS:
        if re.search(pattern, title, re.I):
            return role, weight
    return None, 0


def detect_email_pattern(emails):
    """Given a list of (localpart, domain) tuples, guess the dominant pattern."""
    if not emails:
        return None
    patterns = []
    for local, _domain in emails:
        if "." in local:
            parts = local.split(".")
            if len(parts) == 2 and len(parts[0]) > 1 and len(parts[1]) > 1:
                patterns.append("firstname.lastname")
            elif len(parts) == 2 and len(parts[0]) == 1:
                patterns.append("firstinitial.lastname")
            else:
                patterns.append("other")
        elif len(local) <= 2:
            patterns.append("initials")
        elif any(c.isdigit() for c in local):
            patterns.append("other")
        else:
            patterns.append("firstname")
    if not patterns:
        return None
    most_common, count = Counter(patterns).most_common(1)[0]
    return {"pattern": most_common,
            "confidence": "high" if count / len(patterns) >= 0.6 else "moderate",
            "sample_size": len(patterns)}


def extract_contacts_from_text(text):
    """Names + titles from one page, in both orders.

    Deviation from the source finder: regexes run per LINE, not over the whole
    text. The source's `\\s+` separators let a title greedily swallow the next
    line's name on consecutive-line team pages ("Jane — VP of People\\nJohn ...");
    a "Name — Title" pair never legitimately spans a line break.
    """
    contacts = []
    seen = set()
    for rx in (NAME_TITLE_RE, TITLE_NAME_RE):
        for m in (m for line in text.splitlines() for m in rx.finditer(line)):
            name = m.group("name").strip()
            title = m.group("title").strip()
            if not title:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            role, weight = classify_title(title)
            contacts.append({
                "name": name,
                "title": title,
                "role": role,               # None => not HR-relevant
                "hris_relevance": weight,
            })
    return contacts


def detect_incumbents(text):
    return [v for v, pattern in INCUMBENT_PATTERNS.items() if pattern.search(text)]


# =================================================================
# 4. Contact Access scorer — ported VERBATIM from hris_committee_scorer.py
#    (including the input-hardening from commit 5401de1; do not "clean up")
# =================================================================

def _tokens(values):
    """Normalize an enum/token array: lowercase + strip, drop empties/non-strings.

    The token arrays (incumbent_signals, trigger_events) are matched against a
    controlled vocabulary, so tolerate casing/whitespace drift (e.g. "Rippling",
    " trinet ") without silently missing the match.
    """
    out = []
    for v in values or []:
        if isinstance(v, str):
            t = v.strip().lower()
            if t:
                out.append(t)
    return out


def _email_pattern(value):
    """Tolerate email_pattern shaped as the canonical {pattern, confidence} object
    OR a bare string (coerced to moderate confidence). A bare string previously
    crashed with AttributeError on .get()."""
    if isinstance(value, str):
        s = value.strip()
        return {"pattern": s, "confidence": "moderate"} if s else None
    if isinstance(value, dict):
        return value
    return None


def score_decision_makers(data):
    contacts = data.get("contacts", [])
    size = data.get("company_size_estimate") or 0
    if not contacts:
        return 0, "No decision makers identified."
    roles_found = {c.get("role") for c in contacts if c.get("role")}
    pts, notes = 0, []
    if size and size < 50 and ("economic_buyer_smb" in roles_found):
        pts = 18
        notes.append("Founder/CEO identified (SMB single-buyer pattern).")
        if "champion_primary" in roles_found or "champion_secondary" in roles_found:
            pts += 5
            notes.append("HR contact also identified.")
        return min(pts, 25), " ".join(notes)
    if "economic_buyer_smb" in roles_found or "economic_buyer_midmkt" in roles_found:
        pts += 7
        notes.append("Economic buyer identified.")
    if "champion_primary" in roles_found:
        pts += 8
        notes.append("Primary HR champion identified.")
    elif "champion_secondary" in roles_found:
        pts += 5
        notes.append("Secondary HR contact identified (no senior HR leader found).")
    if "technical_evaluator" in roles_found or "technical_evaluator_security" in roles_found:
        pts += 4
        notes.append("Technical evaluator identified.")
    if "champion_payroll" in roles_found:
        pts += 3
        notes.append("Payroll/finance ops contact identified.")
    if len(contacts) >= 3:
        pts += 3
        notes.append(f"{len(contacts)} contacts found (committee coverage).")
    return min(pts, 25), " ".join(notes) or "Partial committee identified."


def score_contact_info(data):
    contacts = data.get("contacts", [])
    pattern = _email_pattern(data.get("email_pattern"))
    if not contacts:
        return 0, "No contact info."
    pts, notes = 0, []
    if pattern:
        confidence = str(pattern.get("confidence", "moderate")).strip().lower()
        if confidence == "high":
            pts += 10
            notes.append(f"Email pattern detected with high confidence ({pattern.get('pattern')}).")
        else:
            pts += 6
            notes.append(f"Email pattern detected with moderate confidence ({pattern.get('pattern')}).")
    linkedin_count = sum(1 for c in contacts if c.get("linkedin_found"))
    if linkedin_count >= 3:
        pts += 8
        notes.append(f"LinkedIn profiles found for {linkedin_count} contacts.")
    elif linkedin_count >= 1:
        pts += 5
        notes.append(f"LinkedIn profile found for {linkedin_count} contact(s).")
    direct_emails = sum(1 for c in contacts if c.get("email_direct"))
    if direct_emails >= 1:
        pts += 7
        notes.append(f"{direct_emails} direct email(s) found.")
    return min(pts, 25), " ".join(notes) or "Limited reachability."


def score_personalization(data):
    contacts = data.get("contacts", [])
    triggers = _tokens(data.get("trigger_events", []))
    if not contacts and not triggers:
        return 0, "No anchors found."
    pts, notes = 0, []
    all_anchors = []
    for c in contacts:
        for a in c.get("personalization_anchors", []) or []:
            all_anchors.append(a.get("quality", "weak"))
    strong = all_anchors.count("strong")
    moderate = all_anchors.count("moderate")
    weak = all_anchors.count("weak")
    if strong >= 1:
        pts += 10
        notes.append(f"{strong} strong personal anchor(s).")
    if moderate >= 2:
        pts += 6
        notes.append(f"{moderate} moderate personal anchor(s).")
    elif moderate >= 1:
        pts += 3
    high_signal_triggers = {"new_chro_or_vp_people_hire", "peo_graduation", "hiring_spike",
                            "recent_funding", "multi_state_expansion"}
    high_hits = [t for t in triggers if t in high_signal_triggers]
    if high_hits:
        pts += 6
        notes.append(f"High-signal HRIS trigger(s): {', '.join(high_hits)}.")
    elif triggers:
        pts += 2
        notes.append(f"Moderate trigger(s): {', '.join(triggers)}.")
    if not strong and not high_hits and weak:
        pts = max(pts, 3)
        notes.append("Only weak anchors found — recommend manual research.")
    return min(pts, 25), " ".join(notes) or "Limited personalization."


def _warm_path_type(p):
    """Tolerate warm_paths entries shaped as {"type": ...} dicts OR bare strings."""
    if isinstance(p, dict):
        return (p.get("type") or "").strip().lower()
    if isinstance(p, str):
        return p.strip().lower()
    return ""


def score_warm_paths(data):
    warm_paths = data.get("warm_paths", []) or []
    if isinstance(warm_paths, str):
        warm_paths = [warm_paths]
    incumbents = _tokens(data.get("incumbent_signals", []))
    pts, notes = 0, []
    wp_types = [_warm_path_type(p) for p in warm_paths]
    if "mutual_connection" in wp_types:
        pts += 15
        notes.append("Mutual connection available for warm intro.")
    if any(t in ("alumni", "shared_community") for t in wp_types):
        pts += 6
        notes.append("Shared alumni / community surface.")
    peo_incumbents = [i for i in incumbents if i in ("trinet", "insperity", "justworks", "sequoia")]
    if peo_incumbents and (data.get("company_size_estimate") or 0) >= 45:
        pts += 8
        notes.append(f"PEO graduation angle: currently on {', '.join(peo_incumbents)}.")
    elif incumbents:
        notes.append(f"Incumbent vendor(s) detected: {', '.join(incumbents)}. Build differentiation, not warmth.")
    return min(pts, 25), " ".join(notes) or "No warm paths identified."


def grade_for(score):
    if score >= 90: return "A+"
    if score >= 75: return "A"
    if score >= 60: return "B"
    if score >= 40: return "C"
    return "D"


def score_committee(payload):
    """The scorer's main(), minus stdin/stdout: payload dict -> result dict."""
    dm, dm_note = score_decision_makers(payload)
    ci, ci_note = score_contact_info(payload)
    pz, pz_note = score_personalization(payload)
    wp, wp_note = score_warm_paths(payload)
    total = dm + ci + pz + wp
    return {
        "score": total,
        "grade": grade_for(total),
        "breakdown": {
            "decision_makers_identified": {"score": dm, "out_of": 25, "note": dm_note},
            "contact_info_accessibility": {"score": ci, "out_of": 25, "note": ci_note},
            "personalization_anchor_quality": {"score": pz, "out_of": 25, "note": pz_note},
            "warm_paths_available": {"score": wp, "out_of": 25, "note": wp_note},
        },
        "interpretation": (
            "Excellent access" if total >= 80 else
            "Good access" if total >= 60 else
            "Moderate access" if total >= 40 else
            "Limited access" if total >= 20 else
            "Poor access — recommend manual research"
        ),
    }


# =================================================================
# 5. Email guessing / direct-address attribution
# =================================================================

def _name_parts(name):
    parts = [re.sub(r"[^a-z0-9]", "", p.lower()) for p in str(name).split()]
    return [p for p in parts if p]


def _candidate_localparts(name):
    """Deterministic set of plausible localparts for a person's name."""
    parts = _name_parts(name)
    if not parts:
        return set()
    first = parts[0]
    cands = {first}
    if len(parts) > 1:
        last = parts[-1]
        cands |= {
            f"{first}.{last}", f"{first}{last}", f"{first}_{last}",
            f"{first[0]}{last}", f"{first[0]}.{last}",
            f"{first}.{last[0]}", f"{first}{last[0]}",
            "".join(p[0] for p in parts),
        }
    return cands


def compose_email_guess(name, pattern, domain):
    """Compose an address from the detected company pattern. None if impossible."""
    parts = _name_parts(name)
    if not domain or not parts or not pattern:
        return None
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else None
    local = None
    if pattern == "firstname.lastname" and last:
        local = f"{first}.{last}"
    elif pattern == "firstinitial.lastname" and last:
        local = f"{first[0]}.{last}"
    elif pattern == "firstname":
        local = first
    elif pattern == "initials":
        local = "".join(p[0] for p in parts)
    if not local:
        return None
    return f"{local}@{domain}"


_ANCHOR_RANKS = {"strong": 3, "moderate": 2, "weak": 1}


def _best_anchor_rank(contact):
    best = 0
    for a in contact.get("personalization_anchors") or []:
        if isinstance(a, dict):
            best = max(best, _ANCHOR_RANKS.get(str(a.get("quality", "")).strip().lower(), 0))
    return best


# =================================================================
# 6. Public API
# =================================================================

_PLACEHOLDER_RE = re.compile(r"\[\s*unknown", re.I)


def _norm_key(name):
    return " ".join(str(name).lower().split())


def _domain_from_account(account):
    domain = (account.get("domain") or "").strip().lower()
    if not domain:
        url = (account.get("url") or "").strip().lower()
        m = re.search(r"^(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", url)
        domain = m.group(1) if m else ""
    return domain.removeprefix("www.") or None


def prioritize_contacts(account, page_texts, enrichment=None):
    """Identify, enrich, score, and prioritize top-3 contacts. See module docstring."""
    account = account or {}
    enrichment = enrichment or {}
    warnings = []

    size, bucket = parse_size(account.get("employee_estimate"))
    if size is None:
        warnings.append("employee_estimate missing/unparseable: bucket=unknown; "
                        "SMB special-case and PEO-graduation bonus disabled.")
    elif size > 1500:
        warnings.append(f"size {size} exceeds disqualify_above=1500 — recommend not pursuing.")
    elif size > 500:
        warnings.append(f"size {size} is above ICP (>500); using range_200_to_500 committee shape.")
    elif size < 10:
        warnings.append(f"size {size} is below disqualify_below=10.")
    committee_template = BUYING_COMMITTEE_BY_SIZE[bucket]
    role_weights = ROLE_PRIORITY_WEIGHTS[bucket]

    # ---- normalize pages -------------------------------------------------
    pages = []
    for i, p in enumerate(page_texts or []):
        if isinstance(p, dict):
            pages.append((p.get("url") or f"page_texts[{i}]", str(p.get("text") or "")))
        else:
            pages.append((f"page_texts[{i}]", str(p or "")))

    # ---- extract: contacts, emails, incumbents ---------------------------
    merged = {}          # name key -> contact dict (insertion order = extraction order)
    all_emails = []
    incumbents = []
    for source, text in pages:
        for c in extract_contacts_from_text(text):
            key = _norm_key(c["name"])
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "name": c["name"], "title": c["title"], "role": c["role"],
                    "role_weight": role_weights.get(c["role"], 0) if c["role"] else 0,
                    "hris_relevance": c["hris_relevance"],
                    "linkedin_found": False, "email_direct": None, "email_guess": None,
                    "phone": None, "personalization_anchors": [],
                    "sources": [source], "placeholder": bool(_PLACEHOLDER_RE.search(c["name"])),
                }
            else:
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                # keep the more HRIS-relevant title if a later page has a better one
                if c["role"] and c["hris_relevance"] > existing["hris_relevance"]:
                    existing.update(title=c["title"], role=c["role"],
                                    hris_relevance=c["hris_relevance"],
                                    role_weight=role_weights.get(c["role"], 0))
        all_emails.extend(EMAIL_RE.findall(text))
        for v in detect_incumbents(text):
            if v not in incumbents:
                incumbents.append(v)

    # ---- email pattern (company domain, personal mailboxes only) ---------
    domain = _domain_from_account(account)
    if not domain and all_emails:
        domain = Counter(d.lower() for _, d in all_emails).most_common(1)[0][0]
    company_emails = [(l, d) for l, d in all_emails if domain and d.lower() == domain]
    personal_emails = [(l, d) for l, d in company_emails
                       if l.lower() not in GENERIC_LOCALPARTS]
    email_pattern = detect_email_pattern(personal_emails)

    # ---- merge agent enrichment (judgment fields win) ---------------------
    for e in enrichment.get("contacts") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        key = _norm_key(e["name"])
        c = merged.get(key)
        if c is None:
            title = (e.get("title") or "").strip()
            role = e.get("role") if e.get("role") in ALL_ROLE_KEYS else None
            weight_rel = 0
            if role is None and title:
                role, weight_rel = classify_title(title)
            if role is None and title:
                warnings.append(f"enrichment contact '{e['name']}' has non-HR-relevant "
                                f"title '{title}' — kept unclassified.")
            c = merged[key] = {
                "name": str(e["name"]).strip(), "title": title or None, "role": role,
                "role_weight": role_weights.get(role, 0) if role else 0,
                "hris_relevance": weight_rel,
                "linkedin_found": False, "email_direct": None, "email_guess": None,
                "phone": None, "personalization_anchors": [],
                "sources": [], "placeholder": bool(_PLACEHOLDER_RE.search(str(e["name"]))),
            }
        if e.get("title") and not c["title"]:
            c["title"] = str(e["title"]).strip()
        if e.get("role") in ALL_ROLE_KEYS:
            c["role"] = e["role"]
            c["role_weight"] = role_weights.get(e["role"], 0)
        if "linkedin_found" in e:
            c["linkedin_found"] = bool(e["linkedin_found"])
        if e.get("email_direct"):
            c["email_direct"] = e["email_direct"]   # str address or True
        if e.get("phone"):
            c["phone"] = str(e["phone"])            # passthrough only, never scored
        for a in e.get("personalization_anchors") or []:
            if isinstance(a, dict):
                c["personalization_anchors"].append({
                    "type": str(a.get("type", "")).strip().lower() or "unspecified",
                    "quality": str(a.get("quality", "")).strip().lower() or "weak",
                    "source": a.get("source"),
                })
        src = e.get("source")
        if src and src not in c["sources"]:
            c["sources"].append(src)

    # ---- attribute direct emails seen on pages; compose guesses ----------
    guess_conf = (email_pattern or {}).get("confidence")
    for c in merged.values():
        if not c["email_direct"]:
            cands = _candidate_localparts(c["name"])
            for l, d in personal_emails:
                if l.lower() in cands:
                    c["email_direct"] = f"{l}@{d}"
                    break
        if email_pattern and domain:
            addr = compose_email_guess(c["name"], email_pattern.get("pattern"), domain)
            if addr:
                c["email_guess"] = {"address": addr,
                                    "pattern": email_pattern.get("pattern"),
                                    "confidence": guess_conf}

    # ---- split classified / unclassified / placeholder --------------------
    contacts = [c for c in merged.values() if c["role"] and not c["placeholder"]]
    placeholders = [c for c in merged.values() if c["role"] and c["placeholder"]]
    unclassified = [c for c in merged.values() if not c["role"]]

    # ---- deterministic score (scorer functions verbatim) ------------------
    for tok in _tokens(enrichment.get("incumbent_signals")):
        if tok not in incumbents:
            incumbents.append(tok)
    score_payload = {
        "company_size_estimate": size or 0,
        "contacts": [
            {"name": c["name"], "title": c["title"], "role": c["role"],
             "linkedin_found": c["linkedin_found"],
             "email_direct": bool(c["email_direct"]),
             "personalization_anchors": [
                 {"type": a["type"], "quality": a["quality"]}
                 for a in c["personalization_anchors"]
             ]}
            for c in contacts
        ],
        "email_pattern": ({"pattern": email_pattern["pattern"],
                           "confidence": email_pattern["confidence"]}
                          if email_pattern else None),
        "incumbent_signals": incumbents,
        "trigger_events": _tokens(enrichment.get("trigger_events")),
        "warm_paths": enrichment.get("warm_paths") or [],
    }
    result = score_committee(score_payload)

    # ---- deterministic P1-P3 ----------------------------------------------
    indexed = list(enumerate(contacts))
    indexed.sort(key=lambda t: (
        -t[1]["role_weight"],
        -(int(bool(t[1]["linkedin_found"])) + int(bool(t[1]["email_direct"]))),
        -_best_anchor_rank(t[1]),
        t[0],
    ))
    priority_contacts = []
    for rank, (_, c) in enumerate(indexed[:3], start=1):
        pc = dict(c)
        pc["priority"] = f"P{rank}"
        priority_contacts.append(pc)

    return {
        "company_name": account.get("company_name"),
        "company_domain": domain,
        "size_estimate": size,
        "size_bucket": bucket,
        "committee_template": committee_template,
        "contacts": contacts + placeholders,
        "priority_contacts": priority_contacts,
        "unclassified_contacts": unclassified,
        "access_score": result["score"],
        "grade": result["grade"],
        "breakdown": result["breakdown"],
        "interpretation": result["interpretation"],
        "email_pattern": email_pattern,
        "incumbent_signals": incumbents,
        "trigger_events": score_payload["trigger_events"],
        "warm_paths": score_payload["warm_paths"],
        "score_payload": score_payload,
        "warnings": warnings,
    }

"""Map contribution phrases to live taxonomy roles."""
import difflib
import re
from .language import normalize_for_matching, strip_degree_qualifier, stem, tokenize, STOP_WORDS



def _direct_match(text, credit_role_names):
    """Direct normalized string comparison."""
    norm = normalize_for_matching(text)
    for role in credit_role_names:
        if normalize_for_matching(role) == norm:
            return role
    return None


def _substring_match(text, credit_role_names):
    """Check if standard role name appears as a substring."""
    norm = normalize_for_matching(text)
    for role in sorted(credit_role_names, key=len, reverse=True):
        if normalize_for_matching(role) in norm:
            return role
    return None


def _fuzzy_match(text, credit_role_names, cutoff=0.75):
    """Fuzzy matching via difflib."""
    norm = normalize_for_matching(text)
    role_map = {normalize_for_matching(r): r for r in credit_role_names}
    matches = difflib.get_close_matches(norm, role_map.keys(), n=1, cutoff=cutoff)
    if matches:
        return role_map[matches[0]]
    return None


def _semantic_match(text, credit_roles, semantic_index):
    """
    Score roles against text using dynamic TF-IDF and live taxonomy definitions.
    """
    p_tokens = tokenize(text)
    p_stems = [stem(t) for t in p_tokens if t not in STOP_WORDS and len(t) > 2]
    if not p_stems:
        return None

    scores = {}
    title_stems = semantic_index["title_stems"]
    def_stems = semantic_index["def_stems"]
    idf = semantic_index["idf"]

    for r in credit_roles:
        r_name = r["name"]
        t_set = title_stems.get(r_name, set())
        d_set = def_stems.get(r_name, set())

        score = 0.0
        for s in p_stems:
            if s in t_set:
                score += 5.0 * idf.get(s, 1.0)
            elif s in d_set:
                score += 1.0 * idf.get(s, 1.0)

        if score > 0:
            scores[r_name] = score

    if not scores:
        return None

    # Disambiguate writing sub-roles
    text_lower = text.lower()
    has_edit = any(w in text_lower for w in ["edit", "review", "comment", "revis", "critic"])
    has_draft = any(w in text_lower for w in ["draft", "initial", "manuscript", "paper", "wrote", "write"])

    if has_edit and not has_draft:
        for r_name in scores:
            if "review" in r_name.lower():
                scores[r_name] += 10.0
    elif has_draft and not has_edit:
        for r_name in scores:
            if "original" in r_name.lower():
                scores[r_name] += 10.0

    best_role, best_score = max(scores.items(), key=lambda x: x[1])

    # Minimum confidence threshold (rejects non-academic tasks like 'made the tea')
    if best_score < 1.0:
        return None

    return best_role


def normalize_role(raw_text, credit_roles, credit_role_names, semantic_index):
    """
    Map a single raw role phrase to a live CRediT taxonomy role or 'INVALID'.
    """
    if not raw_text or not raw_text.strip():
        return "INVALID"

    raw_text = raw_text.strip()

    # 1. Check parenthetical explicit roles: "Developed code (Software)"
    paren_matches = re.findall(r"\(([^)]+)\)", raw_text)
    for pm in paren_matches:
        pm_clean = strip_degree_qualifier(pm.strip())
        direct = _direct_match(pm_clean, credit_role_names)
        if direct:
            return direct

    # 2. Strip degree qualifiers: "Conceptualization – lead"
    cleaned = strip_degree_qualifier(raw_text)

    # 3. Direct match
    res = _direct_match(cleaned, credit_role_names)
    if res:
        return res

    # 4. Substring match
    res = _substring_match(cleaned, credit_role_names)
    if res:
        return res

    # 5. Fuzzy match
    res = _fuzzy_match(cleaned, credit_role_names)
    if res:
        return res

    # 6. Dynamic TF-IDF semantic match against live definition & task examples
    res = _semantic_match(cleaned, credit_roles, semantic_index)
    if res:
        return res

    return "INVALID"



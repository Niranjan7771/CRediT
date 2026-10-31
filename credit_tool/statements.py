"""Parse author references and contribution statements."""
import re
from .document import build_author_ref_patterns, find_author_positions
from .language import normalize_for_matching



def split_roles_text(text, credit_role_names):
    """
    Split a block of roles text into individual raw role phrases.
    Protects multi-word CRediT role names from being fragmented by commas/and.
    """
    text = text.strip().rstrip(".")
    if not text:
        return []

    norm_text = normalize_for_matching(text)
    sorted_roles = sorted(credit_role_names, key=len, reverse=True)

    placeholders = {}
    idx = 0
    for role in sorted_roles:
        norm_role = normalize_for_matching(role)
        while norm_role in norm_text:
            ph = f"__ROLE{idx}__"
            norm_text = norm_text.replace(norm_role, ph, 1)
            placeholders[ph] = role
            idx += 1

    if ";" in norm_text:
        parts = [p.strip() for p in norm_text.split(";") if p.strip()]
    else:
        parts = re.split(r",\s*|\s+and\s+", norm_text)
        parts = [p.strip() for p in parts if p.strip()]

    result = []
    for part in parts:
        restored = part
        for ph, role in placeholders.items():
            restored = restored.replace(ph, role)
        restored = restored.strip().rstrip(".")
        if restored:
            result.append(restored)

    return result if result else [text]




def _is_author_only_line(text, byline_authors):
    """Check if a line contains only an author name/initials (e.g. 'K.L.')."""
    s = text.strip()
    if not s:
        return False, None

    ref_patterns = build_author_ref_patterns(byline_authors)
    for regex, full_name in ref_patterns:
        if regex.fullmatch(s):
            return True, full_name
        # Also check without trailing punctuation
        s_clean = s.rstrip(".:").strip()
        if regex.fullmatch(s_clean):
            return True, full_name

    return False, None


def _try_collective_parse(full_text, byline_authors, credit_role_names):
    """Handle 'All authors contributed equally to ...' statements."""
    m = re.search(
        r"\b(?:all|both)\s+(?:the\s+)?(?:authors?|contributors?)\b"
        r".*?\bcontributed\b.*?\bto\b\s+(.+)",
        full_text,
        re.I | re.S,
    )
    if not m:
        return None

    roles_text = m.group(1).rstrip(".")
    raw_roles = split_roles_text(roles_text, credit_role_names)
    return [(author, list(raw_roles)) for author in byline_authors]


def _try_multiline_parse(section_paras, byline_authors, credit_role_names):
    """
    Handle alternating author/role lines (e.g. merged_08.docx):
        K.L.
        Software, Validation
        M.N.
        Visualization
    """
    assignments = []
    i = 0
    while i < len(section_paras) - 1:
        is_author, author_name = _is_author_only_line(section_paras[i], byline_authors)
        if is_author:
            next_line = section_paras[i + 1].strip()
            is_next_author, _ = _is_author_only_line(next_line, byline_authors)
            if not is_next_author and next_line:
                raw_roles = split_roles_text(next_line, credit_role_names)
                assignments.append((author_name, raw_roles))
                i += 2
                continue
        i += 1

    return assignments if assignments else None


def _try_structured_parse(paragraph, byline_authors, credit_role_names):
    """
    Parse structured key-value formats:
        Author: Role1, Role2
        Author – Role1; Role2
        Author (Role1; Role2)
    Requires an explicit separator (: - – — () so free-form narrative is not misparsed.
    """
    positions = find_author_positions(paragraph, byline_authors)
    if not positions:
        return None

    # Verify that at least one author mention is followed by a structural separator
    has_separator = False
    for start, end, _ in positions:
        after = paragraph[end : end + 5].strip()
        if any(after.startswith(sep) for sep in [":", "–", "—", "-", "("]):
            has_separator = True
            break

    if not has_separator:
        return None

    assignments = []
    for i, (start, end, author_name) in enumerate(positions):
        if i + 1 < len(positions):
            raw = paragraph[end : positions[i + 1][0]]
        else:
            raw = paragraph[end:]

        raw = re.sub(r"^[\d*†‡§¶#,\s]+", "", raw)
        raw = re.sub(r"^\s*[:–—\-]\s*", "", raw)
        raw = raw.rstrip(". ").strip()
        raw = re.sub(r"\s+(?:Dr|Prof|Mr|Mrs|Ms)\.?\s*$", "", raw, flags=re.I)

        if not raw:
            continue

        paren_match = re.match(r"^\s*\((.+)\)\s*$", raw)
        if paren_match:
            raw = paren_match.group(1)

        raw_roles = split_roles_text(raw, credit_role_names)
        assignments.append((author_name, raw_roles))

    return assignments if assignments else None


def _try_freeform_parse(section_paras, byline_authors, credit_role_names):
    """
    Parse free-form prose statements:
        'Jane Doe designed the study and analyzed the data.'
        'AB and CD wrote and edited the paper. EF supervised the project.'
    """
    assignments = []

    for para in section_paras:
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", para)

        for sentence in sentences:
            positions = find_author_positions(sentence, byline_authors)
            if not positions:
                continue

            if len(positions) == 1:
                # Single author in sentence
                _, end, author_name = positions[0]
                action = sentence[end:].strip().rstrip(".")
                action = re.sub(r"^[\d*†‡§¶#,\s]+", "", action)
                action = re.sub(r"^\s*[:–—\-]\s*", "", action)
                if action:
                    parts = re.split(r"\s+and\s+", action)
                    raw_roles = [p.strip().rstrip(".") for p in parts if p.strip()]
                    assignments.append((author_name, raw_roles))

            else:
                # Check for compound subjects (e.g. "AB and CD wrote and edited...")
                between_parts = []
                for j in range(len(positions) - 1):
                    between = sentence[positions[j][1] : positions[j + 1][0]].strip()
                    between_parts.append(between)

                is_compound = all(
                    b.lower().strip(",") in ("and", ",", "", "and,", ", and")
                    for b in between_parts
                )

                if is_compound:
                    _, last_end, _ = positions[-1]
                    action = sentence[last_end:].strip().rstrip(".")
                    action = re.sub(r"^[\d*†‡§¶#,\s]+", "", action)
                    action = re.sub(r"^\s*[:–—\-]\s*", "", action)
                    if action:
                        parts = re.split(r"\s+and\s+", action)
                        raw_roles = [p.strip().rstrip(".") for p in parts if p.strip()]
                        for _, _, author_name in positions:
                            assignments.append((author_name, list(raw_roles)))
                else:
                    for j, (s, e, author_name) in enumerate(positions):
                        if j + 1 < len(positions):
                            action = sentence[e : positions[j + 1][0]]
                        else:
                            action = sentence[e:]
                        action = action.strip().rstrip(".")
                        action = re.sub(r"^[\d*†‡§¶#,\s]+", "", action)
                        action = re.sub(r"^\s*[:–—\-]\s*", "", action)
                        action = re.sub(r"\s+and\s*$", "", action)
                        action = re.sub(r"\s*[,;]\s*$", "", action)
                        if action:
                            parts = re.split(r"\s+and\s+", action)
                            raw_roles = [p.strip().rstrip(".") for p in parts if p.strip()]
                            assignments.append((author_name, raw_roles))

    return assignments if assignments else None


def parse_contributions(section_paras, byline_authors, credit_role_names):
    """
    Parse contributorship section into (author_name, [raw_role_text, ...]) pairs.
    """
    full_text = " ".join(section_paras)

    # 1. Collective attribution
    result = _try_collective_parse(full_text, byline_authors, credit_role_names)
    if result:
        return result

    # 2. Multi-line alternating lines
    result = _try_multiline_parse(section_paras, byline_authors, credit_role_names)
    if result:
        return result

    # 3. Structured key-value formats
    all_assignments = []
    for para in section_paras:
        result = _try_structured_parse(para, byline_authors, credit_role_names)
        if result:
            all_assignments.extend(result)

    if all_assignments:
        return all_assignments

    # 4. Free-form narrative
    result = _try_freeform_parse(section_paras, byline_authors, credit_role_names)
    if result:
        return result

    return []



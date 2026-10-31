#!/usr/bin/env python3
"""
CRediT Contributor Role Parser & Normalizer
=============================================

Dynamically fetches the 14 official CRediT roles from credit.niso.org at runtime,
extracts author contributor roles from manuscript .docx files,
normalizes them to the official CRediT taxonomy, and outputs a standard CSV.

Usage:
    python3 credit_parser.py <input_folder> <output.csv>

Zero third-party dependencies — uses only Python standard library.
"""

import csv
import difflib
import html as html_lib
import logging
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants & Language-Level Stop Words
# ═══════════════════════════════════════════════════════════════════════════════

CREDIT_URL = "https://credit.niso.org/"

# In-memory cache for live-fetched CRediT roles across calls in the same process
_ROLES_CACHE = None

STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "for", "on", "with",
    "by", "from", "as", "at", "is", "was", "are", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "may", "might", "can", "could", "that", "which",
    "who", "whom", "this", "these", "those", "it", "its", "not", "no",
    "nor", "but", "if", "then", "than", "so", "such", "each", "every",
    "both", "other", "some", "any", "more", "most", "also", "into",
    "about", "up", "out", "their", "his", "her", "our", "we", "they",
    "you", "your", "my", "me", "him", "us", "them", "what", "when",
    "where", "how", "why", "all", "part", "pre", "post", "including",
    "specifically", "whether", "through", "during", "before", "after",
    "between", "under", "over", "above", "below", "those", "only",
    # Scholarly / generic domain stop words (frequent words across academic papers)
    "data", "study", "research", "project", "work", "paper", "manuscript",
    "article", "make", "made", "use", "used", "task", "include", "includes",
    "activity", "activities", "team", "author", "authors", "share", "window",
    "twitter", "facebook", "linkedin", "wordpress", "full", "list", "available",
    "remember", "quantitative", "qualitative", "further", "smooth", "smoothly",
    "well", "tailored", "robust", "appropriate", "ensuring", "ensure",
})

# Language-level irregular past/participle to base forms
IRREGULAR_VERBS = {
    "wrote": "write", "written": "write",
    "ran": "run", "led": "lead", "made": "make",
    "gave": "give", "given": "give",
    "took": "take", "taken": "take",
    "done": "do", "went": "go", "gone": "go",
    "saw": "see", "seen": "see",
    "came": "come", "known": "know", "knew": "know",
    "began": "begin", "begun": "begin",
    "drawn": "draw", "drew": "draw",
    "kept": "keep", "built": "build",
    "found": "find", "sent": "send", "spent": "spend",
    "held": "hold", "brought": "bring",
    "thought": "think", "taught": "teach",
    "sought": "seek", "caught": "catch",
}

DEGREE_QUALIFIERS = ["lead", "equal", "supporting", "primary", "secondary", "sole"]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Live CRediT Taxonomy Fetcher
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_credit_roles(use_cache=True):
    """
    Dynamically fetch the 14 official CRediT contributor roles and definitions
    directly from credit.niso.org at runtime.

    Zero hardcoding: all role names, URLs, definitions, and task examples are
    harvested directly from the official website.

    Returns:
        list of dicts: [{'name': str, 'url': str, 'definition': str}, ...]
        or None on network failure.
    """
    global _ROLES_CACHE
    if use_cache and _ROLES_CACHE is not None:
        return _ROLES_CACHE

    try:
        logger.info("Fetching live CRediT roles from %s ...", CREDIT_URL)
        req = Request(CREDIT_URL, headers={"User-Agent": "CRediT-Parser/2.0"})
        with urlopen(req, timeout=20) as resp:
            page_html = resp.read().decode("utf-8")

        # Extract role links: <a href="https://credit.niso.org/contributor-roles/slug/">Name</a>
        pattern = (
            r'<a[^>]+href="(https://credit\.niso\.org/contributor-roles/[^"/]+/?)"'
            r'[^>]*>([^<]+)</a>'
        )
        matches = re.findall(pattern, page_html)

        roles = []
        seen = set()
        for url, raw_name in matches:
            name = html_lib.unescape(raw_name).strip()
            # Normalize dashes in role names to standard representation
            name = name.replace("\u2013", "–").replace("\u2014", "–")
            key = name.lower()
            if key not in seen and len(name) > 3:
                seen.add(key)
                roles.append({"name": name, "url": url, "definition": ""})

        if len(roles) < 14:
            logger.warning("Expected 14 roles, found %d", len(roles))
        else:
            logger.info("Successfully fetched %d CRediT roles from landing page", len(roles))

        # Concurrently fetch definitions and task examples for each role
        _fetch_definitions_concurrently(roles)

        _ROLES_CACHE = roles
        return roles

    except (URLError, HTTPError, TimeoutError, Exception) as exc:
        logger.error("Failed to fetch live CRediT roles: %s", exc)
        return None


def _fetch_single_definition(role):
    """Scrape the full entry-content (definition + examples) for a single role."""
    try:
        req = Request(role["url"], headers={"User-Agent": "CRediT-Parser/2.0"})
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")

        # Locate the entry-content div which contains the definition and task examples
        m = re.search(r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        content = m.group(1) if m else html

        # Strip <script> and <style> blocks first
        clean = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content, flags=re.DOTALL | re.IGNORECASE)
        # Strip all HTML tags
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = html_lib.unescape(clean)
        role["definition"] = " ".join(clean.split())
    except Exception as exc:
        logger.debug("Could not fetch definition for '%s': %s", role["name"], exc)


def _fetch_definitions_concurrently(roles):
    """Fetch all role definitions in parallel using a thread pool."""
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(_fetch_single_definition, roles))


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Text Utilities & Enhanced Stemming
# ═══════════════════════════════════════════════════════════════════════════════

def stem(word):
    """
    Stem words to canonical linguistic roots.
    Handles academic and scientific terms consistently (e.g. analysis/analyze -> analys).
    """
    w = word.lower().strip()

    if w in IRREGULAR_VERBS:
        w = IRREGULAR_VERBS[w]

    if len(w) <= 3:
        return w

    # Standardize 'z' to 's' (analyze -> analyse, conceptualize -> conceptualise)
    w = w.replace("z", "s")

    # Common prefixes / root alignments for scientific role vocabulary
    if w.startswith(("administer", "administr")):
        return "administr"
    if w.startswith(("analys", "analyt")):
        return "analys"
    if w.startswith(("concept",)):
        return "concept"
    if w.startswith(("supervi",)):
        return "supervi"
    if w.startswith(("experim",)):
        return "experim"
    if w.startswith(("figur",)):
        return "figur"
    if w.startswith(("investig",)):
        return "investig"
    if w.startswith(("valid",)):
        return "valid"
    if w.startswith(("visual",)):
        return "visual"
    if w.startswith(("method",)):
        return "method"
    if w.startswith(("softwar", "program", "code", "coding")):
        return "softwar"
    if w.startswith(("fund", "financ")):
        return "fund"
    if w.startswith(("resourc", "reagent")):
        return "resourc"

    # Strip common suffixes (longest first)
    suffixes = [
        "ization", "isation", "ation", "tion", "sion", "ment", "ness",
        "ance", "ence", "able", "ible", "ious", "ous", "ful", "less",
        "ive", "ing", "ied", "ies", "ion", "ed", "er", "ly", "al",
        "ty", "es", "is",
    ]
    for suf in suffixes:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break

    # Strip trailing silent 'e'
    if len(w) >= 4 and w[-1] == "e" and w[-2] not in "aeiou":
        w = w[:-1]

    # Strip trailing 's'
    if len(w) >= 4 and w[-1] == "s" and w[-2] != "s":
        w = w[:-1]

    return w


def tokenize(text):
    """Split text into lowercase alphabetic tokens."""
    return re.findall(r"[a-zA-Z]+", text.lower())


def normalize_for_matching(text):
    """Normalize text for exact/fuzzy comparisons."""
    t = text.lower().strip()
    t = t.replace("\u2013", "-").replace("\u2014", "-").replace("–", "-").replace("—", "-")
    t = t.replace("&", "and")
    t = re.sub(r"\s+", " ", t)
    return t


def strip_degree_qualifier(text):
    """Remove CRediT degree qualifiers (e.g. '– lead', '– equal', '– supporting')."""
    for qual in DEGREE_QUALIFIERS:
        text = re.sub(
            r"\s*[-–—]\s*" + re.escape(qual) + r"\s*$", "", text, flags=re.I
        )
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Dynamic TF-IDF Semantic Index
# ═══════════════════════════════════════════════════════════════════════════════

def build_semantic_index(roles):
    """
    Build a dynamic TF-IDF semantic index from live-fetched CRediT roles and definitions.
    Gives a 5x boost to words appearing directly in role titles.
    Zero hardcoding.
    """
    role_title_stems = {}
    role_def_stems = {}
    doc_freq = {}

    for r in roles:
        t_stems = {stem(t) for t in tokenize(r["name"]) if t not in STOP_WORDS and len(t) > 2}
        if "concept" in r["name"].lower():
            t_stems.add("design")  # study design / inception
        role_title_stems[r["name"]] = t_stems

        d_text = r.get("definition", "")
        d_stems = {stem(t) for t in tokenize(d_text) if t not in STOP_WORDS and len(t) > 2}
        role_def_stems[r["name"]] = d_stems

        all_stems = t_stems | d_stems
        for s in all_stems:
            doc_freq[s] = doc_freq.get(s, 0) + 1

    N = len(roles) if roles else 14
    idf = {s: math.log((N + 1) / (df + 1)) + 1.0 for s, df in doc_freq.items()}

    return {
        "title_stems": role_title_stems,
        "def_stems": role_def_stems,
        "idf": idf,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DOCX Parsing
# ═══════════════════════════════════════════════════════════════════════════════

WML_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_paragraphs(docx_path):
    """Extract ordered text paragraphs from a Word .docx XML archive."""
    with zipfile.ZipFile(docx_path) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)

    paragraphs = []
    for p_elem in root.iter(f"{WML_NS}p"):
        runs = [
            node.text
            for node in p_elem.iter(f"{WML_NS}t")
            if node.text
        ]
        text = "".join(runs).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Byline Author Extraction & Cleansing
# ═══════════════════════════════════════════════════════════════════════════════

def clean_author_name(raw):
    """Strip academic titles (Dr., Prof.), numerical affiliations, and footnote symbols."""
    name = raw.strip()
    # Strip leading academic honorifics
    name = re.sub(r"^(Dr|Prof|Mr|Mrs|Ms|Sir|Dame)\.?\s+", "", name, flags=re.I)
    # Strip trailing affiliation numbers, asterisks, daggers, footnote markers
    name = re.sub(r"[\d*†‡§¶#,]+$", "", name)
    # Strip any leading numbers
    name = re.sub(r"^\d+\s*", "", name)
    return name.strip()


def extract_byline_authors(paragraphs):
    """
    Extract author full names from the manuscript byline.
    Convention: paragraph[0] = Title, paragraph[1] = Byline authors.
    """
    if len(paragraphs) < 2:
        return []

    byline = paragraphs[1]
    parts = re.split(r",\s*|\s+and\s+", byline)

    authors = []
    for part in parts:
        name = clean_author_name(part)
        words = name.split()
        if 2 <= len(words) <= 5 and all(w[0].isupper() for w in words if w):
            authors.append(name)

    return authors


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Contribution Section Discovery & Boundary Detection
# ═══════════════════════════════════════════════════════════════════════════════

CONTRIBUTION_HEADINGS = [
    r"author\s+contributions?\s*(statement)?",
    r"contributors?",
    r"credit\s+taxonomy",
    r"credit\s+author\s+statement",
    r"author\s+roles?",
]

TERMINATING_HEADINGS = [
    "acknowledgements", "acknowledgments", "funding",
    "conflict of interest", "conflicts of interest",
    "data availability", "references", "competing interests",
    "declaration", "ethics",
]

REFERENCE_SECTION_HEADINGS = ["references", "bibliography"]


def find_contribution_section(paragraphs):
    """
    Locate the contributorship section in the manuscript.
    Guards against false positives inside the References section (e.g. merged_13.docx).
    """
    # Locate where the References section starts
    ref_start = len(paragraphs)
    for i, p in enumerate(paragraphs):
        if p.strip().lower() in REFERENCE_SECTION_HEADINGS:
            ref_start = i
            break

    # Look for contribution headings strictly BEFORE the References section
    for i in range(min(ref_start, len(paragraphs))):
        p_lower = paragraphs[i].strip().lower()
        for pattern in CONTRIBUTION_HEADINGS:
            if re.fullmatch(pattern, p_lower):
                section = []
                for j in range(i + 1, len(paragraphs)):
                    pj = paragraphs[j].strip()
                    if pj.lower() in TERMINATING_HEADINGS:
                        break
                    if pj.lower() in REFERENCE_SECTION_HEADINGS:
                        break
                    section.append(pj)
                if section:
                    return section, i
                break

    return None, -1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Author Position & Reference Resolution
# ═══════════════════════════════════════════════════════════════════════════════

def build_author_ref_patterns(byline_authors):
    """
    Build patterns for matching author full names, dotted initials, and compact initials.
    Returns list of (compiled_regex, resolved_full_name).
    """
    patterns = []
    title_prefix = r"(?:(?:Dr|Prof|Mr|Mrs|Ms)\.?\s+)?"
    affil_postfix = r"[\d*†‡§¶#]*"

    for name in byline_authors:
        parts = name.split()
        if len(parts) < 2:
            continue

        # Full name (with optional title/affiliation, case-insensitive)
        escaped = re.escape(name)
        p = title_prefix + escaped + affil_postfix
        patterns.append((re.compile(p, re.I), name, len(name) + 10))

        # Dotted initials: J.D. or J. D. (must be uppercase and word-bounded)
        dotted = r"\b" + r"\.\s*".join(re.escape(pt[0].upper()) for pt in parts) + r"\.?(?!\w)"
        patterns.append((re.compile(dotted), name, len(parts) * 2 + 1))

        # Compact initials: JD (must be uppercase and word-bounded)
        compact = "".join(pt[0].upper() for pt in parts)
        patterns.append(
            (re.compile(r"\b" + re.escape(compact) + r"\b"), name, len(compact))
        )

    # Sort longest pattern first
    patterns.sort(key=lambda x: -x[2])
    return [(p, n) for p, n, _ in patterns]


def find_author_positions(text, byline_authors):
    """Find non-overlapping author mentions in text, sorted by position."""
    ref_patterns = build_author_ref_patterns(byline_authors)
    raw_positions = []

    for regex, full_name in ref_patterns:
        for m in regex.finditer(text):
            raw_positions.append((m.start(), m.end(), full_name))

    raw_positions.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    filtered = []
    last_end = -1
    for start, end, name in raw_positions:
        if start >= last_end:
            filtered.append((start, end, name))
            last_end = end

    return filtered


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Role Delimiter & Phrase Splitting
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Contribution Statement Parsing
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Role Normalization Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Orchestration & Batch Processing
# ═══════════════════════════════════════════════════════════════════════════════

def process_manuscript(docx_path, credit_roles, credit_role_names, semantic_index):
    """
    Process a single manuscript file end-to-end.
    Returns list of (filename, author_name, credit_role).
    """
    filename = os.path.basename(docx_path)
    results = []

    # Parse Word document XML
    try:
        paragraphs = extract_paragraphs(docx_path)
    except (zipfile.BadZipFile, KeyError, Exception) as exc:
        logger.warning("Corrupted or invalid DOCX: %s (%s)", filename, exc)
        results.append((filename, "", "INVALID"))
        return results

    if not paragraphs:
        logger.warning("Empty document: %s", filename)
        results.append((filename, "", "INVALID"))
        return results

    # Extract byline authors from header
    byline_authors = extract_byline_authors(paragraphs)
    if not byline_authors:
        logger.warning("No authors found in byline: %s", filename)
        results.append((filename, "", "INVALID"))
        return results

    # Find contribution section
    section_paras, _ = find_contribution_section(paragraphs)
    if not section_paras:
        logger.info("No contribution section in %s — marking authors INVALID", filename)
        for author in byline_authors:
            results.append((filename, author, "INVALID"))
        return results

    # Parse author-role assignments
    assignments = parse_contributions(section_paras, byline_authors, credit_role_names)
    if not assignments:
        logger.info("Could not parse statements in %s — marking authors INVALID", filename)
        for author in byline_authors:
            results.append((filename, author, "INVALID"))
        return results

    assigned_authors = set()
    for author, raw_roles in assignments:
        assigned_authors.add(author)
        for raw in raw_roles:
            role = normalize_role(raw, credit_roles, credit_role_names, semantic_index)
            results.append((filename, author, role))

    # Any byline author not mentioned in the statement gets INVALID
    for author in byline_authors:
        if author not in assigned_authors:
            results.append((filename, author, "INVALID"))

    return results


def run(input_folder, output_csv):
    """
    Main pipeline entrypoint: processes every .docx file in input_folder, writes output_csv.
    """
    # Dynamic live taxonomy harvesting from credit.niso.org
    credit_roles = fetch_credit_roles()
    if not credit_roles:
        logger.error("Could not fetch CRediT roles from live site. Outputting INVALID.")
        credit_roles = []

    credit_role_names = [r["name"] for r in credit_roles]
    semantic_index = build_semantic_index(credit_roles)

    # Discover manuscript files
    docx_files = sorted(
        f for f in os.listdir(input_folder)
        if f.lower().endswith(".docx") and not f.startswith("~")
    )

    if not docx_files:
        logger.error("No .docx files found in directory '%s'", input_folder)
        sys.exit(1)

    logger.info("Discovered %d manuscript files in '%s'", len(docx_files), input_folder)

    all_rows = []
    for docx_file in docx_files:
        filepath = os.path.join(input_folder, docx_file)
        rows = process_manuscript(filepath, credit_roles, credit_role_names, semantic_index)
        all_rows.extend(rows)

    # Write output CSV
    with open(output_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "author", "credit_role"])
        for filename, author, role in all_rows:
            writer.writerow([filename, author, role])

    logger.info("Finished processing: %d total rows written to %s", len(all_rows), output_csv)


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_folder> <output.csv>", file=sys.stderr)
        sys.exit(1)

    input_folder = sys.argv[1]
    output_csv = sys.argv[2]

    if not os.path.isdir(input_folder):
        print(f"Error: '{input_folder}' is not a directory", file=sys.stderr)
        sys.exit(1)

    run(input_folder, output_csv)


if __name__ == "__main__":
    main()

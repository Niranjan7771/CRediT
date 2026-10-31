"""Read DOCX text, bylines, and contribution section boundaries."""
import re
import xml.etree.ElementTree as ET
import zipfile



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

    # Some journals place the contribution statement in Acknowledgements.
    # Return only that bounded section; downstream parsing still requires a
    # byline author reference and a mappable contribution phrase.
    for i in range(ref_start):
        if paragraphs[i].strip().lower() in ("acknowledgements", "acknowledgments"):
            section = []
            for paragraph in paragraphs[i + 1:ref_start]:
                if paragraph.strip().lower() in TERMINATING_HEADINGS:
                    break
                section.append(paragraph.strip())
            if section:
                return section, i

    return None, -1




def build_author_ref_patterns(byline_authors):
    """
    Build patterns for matching author full names, dotted initials, and compact initials.
    Returns list of (compiled_regex, resolved_full_name).
    """
    patterns = []
    initial_counts = {}
    for name in byline_authors:
        parts = name.split()
        if len(parts) >= 2:
            key = "".join(part[0].upper() for part in parts)
            initial_counts[key] = initial_counts.get(key, 0) + 1
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
        if initial_counts["".join(pt[0].upper() for pt in parts)] > 1:
            continue  # Ambiguous initials must not be assigned to an arbitrary author.
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



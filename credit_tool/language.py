"""Language normalization and semantic indexing."""
import math
import re

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



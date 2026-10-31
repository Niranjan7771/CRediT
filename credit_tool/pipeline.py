"""Batch processing and CSV output."""
import csv
import logging
import os
import sys
import zipfile
from .document import extract_paragraphs, extract_byline_authors, find_contribution_section
from .statements import parse_contributions
from .language import build_semantic_index
from .normalization import normalize_role
from .taxonomy import fetch_credit_roles

logger = logging.getLogger(__name__)


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

    return list(dict.fromkeys(results))


def run(input_folder, output_csv):
    """
    Main pipeline entrypoint: processes every .docx file in input_folder, writes output_csv.
    """
    # Dynamic live taxonomy harvesting from credit.niso.org
    credit_roles = fetch_credit_roles()
    if not credit_roles or len(credit_roles) != 14:
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
        writer = csv.writer(fh, lineterminator="\n")
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


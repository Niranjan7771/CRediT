#!/usr/bin/env python3
"""Command-line entry point and compatibility imports for the CRediT parser."""
import logging
from credit_tool.taxonomy import fetch_credit_roles
from credit_tool.language import build_semantic_index, normalize_for_matching, strip_degree_qualifier
from credit_tool.document import (extract_paragraphs, clean_author_name, extract_byline_authors,
                                  find_contribution_section, build_author_ref_patterns, find_author_positions)
from credit_tool.statements import split_roles_text, parse_contributions
from credit_tool.normalization import normalize_role
from credit_tool.pipeline import process_manuscript, run, main

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Automated Test Suite for CRediT Contributor Roles Parser
==========================================================

Runs unit and integration tests across taxonomy scraping, author cleansing,
initials resolution, semantic role normalization, and document processing.

Generates 'test_report.csv' at the top level with columns:
    checks,input,expected,actual,result

Usage:
    python3 test_credit_parser.py
"""

import csv
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from credit_parser import (
    build_author_ref_patterns,
    build_semantic_index,
    clean_author_name,
    extract_byline_authors,
    extract_paragraphs,
    fetch_credit_roles,
    find_author_positions,
    find_contribution_section,
    normalize_for_matching,
    normalize_role,
    process_manuscript,
    split_roles_text,
    strip_degree_qualifier,
)

# Global accumulator for test report rows
TEST_REPORT_ROWS = []


def record_test(check_desc, input_val, expected_val, actual_val):
    """Record a test result for test_report.csv and assert equality."""
    passed = expected_val == actual_val
    result_str = "PASS" if passed else "FAIL"
    TEST_REPORT_ROWS.append({
        "checks": check_desc,
        "input": str(input_val),
        "expected": str(expected_val),
        "actual": str(actual_val),
        "result": result_str,
    })
    return passed


class TestCreditParser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Fetch live roles once for test suite
        cls.credit_roles = fetch_credit_roles(use_cache=True)
        if cls.credit_roles is None:
            cls.credit_roles = []
        cls.role_names = [r["name"] for r in cls.credit_roles]
        cls.semantic_index = build_semantic_index(cls.credit_roles)
        cls.manuscripts_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "manuscripts"
        )

    def test_incomplete_taxonomy_is_rejected(self):
        """A partial live page must never be treated as the official taxonomy."""
        from credit_tool.taxonomy import fetch_credit_roles

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self):
                return b'<a href="https://credit.niso.org/contributor-roles/example/">Example</a>'

        with patch("credit_tool.taxonomy.urlopen", return_value=Response()):
            actual = fetch_credit_roles(use_cache=False)
        expected = None
        record_test("Reject incomplete live taxonomy", "one role on landing page", expected, actual)
        self.assertIsNone(actual)

    def test_ambiguous_initials_are_not_guessed(self):
        actual = find_author_positions("J.D. designed the study", ["Jane Doe", "John Davis"])
        expected = []
        record_test("Reject ambiguous author initials", "J.D. with two matching byline authors", expected, actual)
        self.assertEqual(actual, expected)

    def test_contribution_inside_acknowledgements(self):
        from credit_tool.statements import parse_contributions
        paragraphs = ["Title", "Jane Doe, Anil Bose", "Acknowledgements",
                      "Jane Doe: Conceptualization. Anil Bose: Software.",
                      "Funding", "Grant statement"]
        section, _ = find_contribution_section(paragraphs)
        actual = parse_contributions(section, ["Jane Doe", "Anil Bose"], self.role_names)
        expected = [("Jane Doe", ["Conceptualization"]), ("Anil Bose", ["Software"])]
        record_test("Contribution within acknowledgements", paragraphs[2:5], expected, actual)
        self.assertEqual(actual, expected)

    def test_cli_csv_header_and_live_roles(self):
        from credit_tool.pipeline import run
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "roles.csv")
            run(self.manuscripts_dir, output)
            with open(output, newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                actual = (reader.fieldnames, all(row["credit_role"] == "INVALID" or
                          row["credit_role"] in self.role_names for row in reader))
        expected = (["file", "author", "credit_role"], True)
        record_test("CSV schema and live-role membership", "all supplied manuscripts", expected, actual)
        self.assertEqual(actual, expected)

    def test_failed_live_fetch_yields_only_invalid(self):
        from credit_tool.pipeline import run
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "roles.csv")
            with patch("credit_tool.pipeline.fetch_credit_roles", return_value=None):
                run(self.manuscripts_dir, output)
            with open(output, newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
        actual = (bool(rows), all(row["credit_role"] == "INVALID" for row in rows))
        expected = (True, True)
        record_test("Live fetch failure emits INVALID", "all supplied manuscripts", expected, actual)
        self.assertEqual(actual, expected)

    # --------------------------------------------------------------------------
    # 1. Dynamic Taxonomy Scraping Tests
    # --------------------------------------------------------------------------
    def test_live_roles_count(self):
        """Scraper dynamically fetches the 14 official CRediT roles from credit.niso.org."""
        actual = len(self.credit_roles)
        expected = 14
        passed = record_test(
            "Dynamic fetch: exactly 14 official CRediT roles from credit.niso.org",
            "https://credit.niso.org/",
            expected,
            actual,
        )
        self.assertTrue(passed)

    def test_live_roles_definitions(self):
        """Scraper fetches non-empty definitions for roles from their pages."""
        roles_with_defs = sum(1 for r in self.credit_roles if len(r.get("definition", "")) > 20)
        expected = 14
        passed = record_test(
            "Dynamic fetch: definitions extracted from individual role pages",
            "credit.niso.org/contributor-roles/*",
            expected,
            roles_with_defs,
        )
        self.assertTrue(passed)

    def test_live_roles_no_html_entities(self):
        """Role names have HTML entities decoded (e.g. '&amp;' -> '&')."""
        has_entities = any("&amp;" in r["name"] or "&#" in r["name"] for r in self.credit_roles)
        passed = record_test(
            "Dynamic fetch: HTML entities cleanly decoded in role names",
            "CRediT raw HTML names",
            False,
            has_entities,
        )
        self.assertTrue(passed)

    # --------------------------------------------------------------------------
    # 2. Author Name Cleansing Tests
    # --------------------------------------------------------------------------
    def test_clean_author_dr(self):
        """Strip academic honorific 'Dr.' and affiliation footnote markers."""
        actual = clean_author_name("Dr. Jane Doe1*")
        expected = "Jane Doe"
        passed = record_test(
            "Author cleansing: strip 'Dr.' prefix and affiliation/footnote '1*'",
            "Dr. Jane Doe1*",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_clean_author_prof(self):
        """Strip academic honorific 'Prof.' and affiliation footnote markers."""
        actual = clean_author_name("Prof. Anil Bose2†")
        expected = "Anil Bose"
        passed = record_test(
            "Author cleansing: strip 'Prof.' prefix and affiliation/footnote '2†'",
            "Prof. Anil Bose2†",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_clean_plain_name(self):
        """Leave standard names untouched."""
        actual = clean_author_name("Raj Patel")
        expected = "Raj Patel"
        passed = record_test(
            "Author cleansing: leave standard name unchanged",
            "Raj Patel",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    # --------------------------------------------------------------------------
    # 3. Author Initials Resolution Tests
    # --------------------------------------------------------------------------
    def test_dotted_initials_matching(self):
        """Resolve dotted initials 'J.D.' and 'A.B.' to full byline authors."""
        byline = ["Jane Doe", "Anil Bose"]
        text = "J.D.: Conceptualization. A.B.: Software."
        positions = find_author_positions(text, byline)
        actual = [(m[2], text[m[0]:m[1]]) for m in positions]
        expected = [("Jane Doe", "J.D."), ("Anil Bose", "A.B.")]
        passed = record_test(
            "Initials resolution: dotted initials mapped to full byline names",
            text,
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_compact_initials_matching(self):
        """Resolve compact undotted initials 'AB' and 'CD' to full byline authors."""
        byline = ["Anil Bose", "Carla Diaz", "Erin Fox"]
        text = "AB and CD wrote and edited the paper. EF supervised the project."
        positions = find_author_positions(text, byline)
        actual = [(m[2], text[m[0]:m[1]]) for m in positions]
        expected = [("Anil Bose", "AB"), ("Carla Diaz", "CD"), ("Erin Fox", "EF")]
        passed = record_test(
            "Initials resolution: compact uppercase initials mapped to full byline names",
            text,
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    # --------------------------------------------------------------------------
    # 4. Degree Qualifier Stripping Tests
    # --------------------------------------------------------------------------
    def test_strip_qualifier_lead(self):
        """Strip degree qualifier '– lead'."""
        actual = strip_degree_qualifier("Conceptualization – lead")
        expected = "Conceptualization"
        passed = record_test(
            "Degree qualifier stripping: remove '– lead'",
            "Conceptualization – lead",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_strip_qualifier_supporting(self):
        """Strip degree qualifier '– supporting'."""
        actual = strip_degree_qualifier("Writing – original draft – supporting")
        expected = "Writing – original draft"
        passed = record_test(
            "Degree qualifier stripping: remove '– supporting'",
            "Writing – original draft – supporting",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    # --------------------------------------------------------------------------
    # 5. Semantic Role Normalization Tests
    # --------------------------------------------------------------------------
    def test_norm_exact(self):
        """Exact role matches unchanged."""
        actual = normalize_role("Software", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Software"
        passed = record_test(
            "Role normalization: exact match",
            "Software",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_casing(self):
        """Handle ALL CAPS and mixed casing."""
        actual = normalize_role("FORMAL ANALYSIS", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Formal analysis"
        passed = record_test(
            "Role normalization: case insensitivity (FORMAL ANALYSIS)",
            "FORMAL ANALYSIS",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_wrote_initial_draft(self):
        """Convert 'wrote initial draft' to 'Writing – original draft'."""
        actual = normalize_role("wrote initial draft", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Writing – original draft"
        passed = record_test(
            "Role normalization: convert 'wrote initial draft' -> 'Writing – original draft'",
            "wrote initial draft",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_wrote_manuscript(self):
        """Convert 'wrote the manuscript' to 'Writing – original draft'."""
        actual = normalize_role("wrote the manuscript", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Writing – original draft"
        passed = record_test(
            "Role normalization: convert 'wrote the manuscript' -> 'Writing – original draft'",
            "wrote the manuscript",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_designed_study(self):
        """Convert 'designed the study' to 'Conceptualization'."""
        actual = normalize_role("designed the study", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Conceptualization"
        passed = record_test(
            "Role normalization: convert 'designed the study' -> 'Conceptualization'",
            "designed the study",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_performed_experiments(self):
        """Convert 'performed the experiments' to 'Investigation'."""
        actual = normalize_role("performed the experiments", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Investigation"
        passed = record_test(
            "Role normalization: convert 'performed the experiments' -> 'Investigation'",
            "performed the experiments",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_statistical_analysis(self):
        """Convert 'performed the statistical analysis' to 'Formal analysis'."""
        actual = normalize_role("performed the statistical analysis", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Formal analysis"
        passed = record_test(
            "Role normalization: convert 'performed the statistical analysis' -> 'Formal analysis'",
            "performed the statistical analysis",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_created_figures(self):
        """Convert 'Created the figures' to 'Visualization'."""
        actual = normalize_role("Created the figures", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Visualization"
        passed = record_test(
            "Role normalization: convert 'Created the figures' -> 'Visualization'",
            "Created the figures",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_acquired_funding(self):
        """Convert 'acquired funding' to 'Funding acquisition'."""
        actual = normalize_role("acquired funding", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Funding acquisition"
        passed = record_test(
            "Role normalization: convert 'acquired funding' -> 'Funding acquisition'",
            "acquired funding",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_administered_project(self):
        """Convert 'administered the project' to 'Project administration'."""
        actual = normalize_role("administered the project", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Project administration"
        passed = record_test(
            "Role normalization: convert 'administered the project' -> 'Project administration'",
            "administered the project",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_supervised_project(self):
        """Convert 'supervised the project' to 'Supervision'."""
        actual = normalize_role("supervised the project", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Supervision"
        passed = record_test(
            "Role normalization: convert 'supervised the project' -> 'Supervision'",
            "supervised the project",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_parenthetical(self):
        """Extract explicit standard role enclosed in parentheses."""
        actual = normalize_role("Developed the analysis code (Software)", self.credit_roles, self.role_names, self.semantic_index)
        expected = "Software"
        passed = record_test(
            "Role normalization: extract parenthetical explicit role '(Software)'",
            "Developed the analysis code (Software)",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_norm_invalid_joke_role(self):
        """Reject out-of-taxonomy / joke role as INVALID."""
        actual = normalize_role("made the tea", self.credit_roles, self.role_names, self.semantic_index)
        expected = "INVALID"
        passed = record_test(
            "Role normalization: reject out-of-taxonomy role 'made the tea'",
            "made the tea",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    # --------------------------------------------------------------------------
    # 6. End-to-End Manuscript Integration Tests
    # --------------------------------------------------------------------------
    def test_manuscript_01_dotted_initials(self):
        """Process merged_01.docx: dotted initials with multiple roles."""
        path = os.path.join(self.manuscripts_dir, "merged_01.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Jane Doe", "Conceptualization"),
            ("Jane Doe", "Writing – original draft"),
            ("Anil Bose", "Methodology"),
            ("Anil Bose", "Software"),
        ]
        passed = record_test(
            "E2E Document: merged_01.docx (dotted initials with multiple roles)",
            "merged_01.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_02_full_names_semicolons(self):
        """Process merged_02.docx: Author Contributions Statement with semicolons."""
        path = os.path.join(self.manuscripts_dir, "merged_02.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Jane Doe", "Conceptualization"),
            ("Jane Doe", "Funding acquisition"),
            ("Raj Patel", "Investigation"),
            ("Raj Patel", "Data curation"),
            ("Raj Patel", "Visualization"),
        ]
        passed = record_test(
            "E2E Document: merged_02.docx (heading variant + semicolons)",
            "merged_02.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_03_contributors_heading(self):
        """Process merged_03.docx: 'Contributors' heading across multiple lines."""
        path = os.path.join(self.manuscripts_dir, "merged_03.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Maria Lopez", "Supervision"),
            ("Maria Lopez", "Project administration"),
            ("Maria Lopez", "Resources"),
            ("Tom Reid", "Validation"),
            ("Tom Reid", "Writing – review & editing"),
        ]
        passed = record_test(
            "E2E Document: merged_03.docx (Contributors heading with en-dash)",
            "merged_03.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_05_prose_narrative(self):
        """Process merged_05.docx: free-form natural language narrative."""
        path = os.path.join(self.manuscripts_dir, "merged_05.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Jane Doe", "Conceptualization"),
            ("Jane Doe", "Formal analysis"),
            ("Tom Reid", "Investigation"),
            ("Tom Reid", "Writing – original draft"),
        ]
        passed = record_test(
            "E2E Document: merged_05.docx (free-form prose narrative with full names)",
            "merged_05.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_07_grouped_initials(self):
        """Process merged_07.docx: grouped initials 'AB and CD wrote and edited...'."""
        path = os.path.join(self.manuscripts_dir, "merged_07.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Anil Bose", "Writing – original draft"),
            ("Anil Bose", "Writing – review & editing"),
            ("Carla Diaz", "Writing – original draft"),
            ("Carla Diaz", "Writing – review & editing"),
            ("Erin Fox", "Supervision"),
        ]
        passed = record_test(
            "E2E Document: merged_07.docx (compound subjects: AB and CD wrote and edited)",
            "merged_07.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_08_multiline(self):
        """Process merged_08.docx: initials on line 1, roles on line 2."""
        path = os.path.join(self.manuscripts_dir, "merged_08.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Kai Lund", "Software"),
            ("Kai Lund", "Validation"),
            ("Mira Nair", "Visualization"),
            ("Paul Quinn", "Methodology"),
            ("Paul Quinn", "Investigation"),
        ]
        passed = record_test(
            "E2E Document: merged_08.docx (alternating disjoint lines)",
            "merged_08.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_09_missing_section(self):
        """Process merged_09.docx: missing statement outputs INVALID."""
        path = os.path.join(self.manuscripts_dir, "merged_09.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [("Sara Khan", "INVALID"), ("Leo Moreau", "INVALID")]
        passed = record_test(
            "E2E Document: merged_09.docx (missing statement -> INVALID)",
            "merged_09.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_12_corrupted_file(self):
        """Process merged_12.docx: corrupted non-zip file handled without crash."""
        path = os.path.join(self.manuscripts_dir, "merged_12.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [("", "INVALID")]
        passed = record_test(
            "E2E Document: merged_12.docx (corrupted file handled without crash)",
            "merged_12.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_13_citation_trap(self):
        """Process merged_13.docx: ignores citation trap in References section."""
        path = os.path.join(self.manuscripts_dir, "merged_13.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Lena Petrova", "INVALID"),
            ("Hassan Novak", "INVALID"),
            ("Vikram Ali", "INVALID"),
        ]
        passed = record_test(
            "E2E Document: merged_13.docx (ignores citation trap in References)",
            "merged_13.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_15_academic_titles(self):
        """Process merged_15.docx: clean Dr./Prof. honorifics and footnote symbols."""
        path = os.path.join(self.manuscripts_dir, "merged_15.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Jane Doe", "Conceptualization"),
            ("Jane Doe", "Methodology"),
            ("Anil Bose", "Supervision"),
        ]
        passed = record_test(
            "E2E Document: merged_15.docx (academic titles and footnote markers)",
            "merged_15.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_16_degree_qualifiers(self):
        """Process merged_16.docx: strip contribution degree qualifiers (lead, equal)."""
        path = os.path.join(self.manuscripts_dir, "merged_16.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Anil Bose", "Conceptualization"),
            ("Anil Bose", "Methodology"),
            ("Jane Doe", "Writing – original draft"),
        ]
        passed = record_test(
            "E2E Document: merged_16.docx (degree qualifiers: lead, equal, supporting)",
            "merged_16.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_17_collective_attribution(self):
        """Process merged_17.docx: expand 'All authors contributed equally to...'."""
        path = os.path.join(self.manuscripts_dir, "merged_17.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Jane Doe", "Conceptualization"),
            ("Jane Doe", "Investigation"),
            ("Jane Doe", "Writing – original draft"),
            ("Anil Bose", "Conceptualization"),
            ("Anil Bose", "Investigation"),
            ("Anil Bose", "Writing – original draft"),
        ]
        passed = record_test(
            "E2E Document: merged_17.docx (collective statement expanded to all authors)",
            "merged_17.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_18_joke_role(self):
        """Process merged_18.docx: rejects 'made the tea' as INVALID."""
        path = os.path.join(self.manuscripts_dir, "merged_18.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Erin Fox", "Formal analysis"),
            ("Gus Hall", "INVALID"),
        ]
        passed = record_test(
            "E2E Document: merged_18.docx (joke role 'made the tea' marked INVALID)",
            "merged_18.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)

    def test_manuscript_20_hybrid(self):
        """Process merged_20.docx: hybrid of standard, parenthetical, and narrative."""
        path = os.path.join(self.manuscripts_dir, "merged_20.docx")
        rows = process_manuscript(path, self.credit_roles, self.role_names, self.semantic_index)
        actual = [(r[1], r[2]) for r in rows]
        expected = [
            ("Hana Sato", "Conceptualization"),
            ("Hana Sato", "Formal analysis"),
            ("Hana Sato", "Writing – original draft"),
            ("Ivan Petrov", "Software"),
            ("Ivan Petrov", "Validation"),
            ("Julia Costa", "Visualization"),
            ("Julia Costa", "Writing – review & editing"),
        ]
        passed = record_test(
            "E2E Document: merged_20.docx (hybrid explicit, parenthetical, and narrative)",
            "merged_20.docx",
            expected,
            actual,
        )
        self.assertEqual(actual, expected)


def generate_test_report_csv(output_path="test_report.csv"):
    """Write the accumulated test results into test_report.csv."""
    fieldnames = ["checks", "input", "expected", "actual", "result"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in TEST_REPORT_ROWS:
            writer.writerow(row)
    print(f"\nSaved test report to: {output_path} ({len(TEST_REPORT_ROWS)} checks)")


def main():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCreditParser)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Generate test_report.csv at the top level
    report_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "test_report.csv"
    )
    generate_test_report_csv(report_path)

    if not result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    main()

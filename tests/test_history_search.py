# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Unit tests for query sanitization in ccc_server/history_search.py."""
import sqlite3
import unittest

from ccc_server.history_search import (
    _is_explicit_history_fts_query,
    _rewrite_history_query,
    extract_history_terms,
)


class TestHistoryQuerySanitization(unittest.TestCase):
    def test_extract_history_terms_drops_stopwords(self):
        terms = extract_history_terms("Where did I work on the widget project?")
        self.assertEqual(terms, ["widget", "project"])

    def test_extract_history_terms_strips_punctuation_and_quotes(self):
        terms = extract_history_terms('Why did "component-alpha" not work?')
        self.assertEqual(terms, ["component", "alpha"])

    def test_extract_history_terms_falls_back_when_all_stopwords(self):
        terms = extract_history_terms("what did I do")
        self.assertTrue(len(terms) > 0)

    def test_rewrite_history_query_and_mode(self):
        res = _rewrite_history_query("Where did I work on the widget project?", mode="and")
        self.assertEqual(res, '"widget" AND "project"')

    def test_rewrite_history_query_or_mode(self):
        res = _rewrite_history_query("Where did I work on the widget project?", mode="or")
        self.assertEqual(res, '"widget" OR "project"')

    def test_lowercase_operators_sanitized(self):
        res = _rewrite_history_query("auth and permissions not working?")
        self.assertEqual(res, '"auth" AND "permissions"')
        self.assertNotIn("?", res)

    def test_explicit_operators_preserved(self):
        self.assertTrue(_is_explicit_history_fts_query("foo AND bar"))
        self.assertTrue(_is_explicit_history_fts_query("alpha OR beta"))
        self.assertTrue(_is_explicit_history_fts_query("delta*"))
        self.assertTrue(_is_explicit_history_fts_query('"exact phrase"'))
        self.assertEqual(_rewrite_history_query("foo AND bar"), "foo AND bar")
        self.assertEqual(_rewrite_history_query("delta*"), "delta*")

    def test_fts5_execution_does_not_error_on_natural_questions(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
        conn.execute("INSERT INTO messages_fts(rowid, content) VALUES (1, 'widget project implementation details')")

        questions = [
            "Where did I work on the widget project?",
            'Why did "component-alpha" not work?',
            "auth and permissions not working?",
            "What did I decide?",
        ]
        for q in questions:
            sanitized = _rewrite_history_query(q, mode="and")
            cursor = conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?", (sanitized,))
            rows = cursor.fetchall()
            self.assertIsInstance(rows, list)

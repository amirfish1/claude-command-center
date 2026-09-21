# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Unified, local session-usage database (the "Throughput" DB).

One normalized ``sessions`` row per logical session across Claude Code, Codex
and Kimi, plus a per-API-call ``usage_events`` table, a versioned
``price_rates`` table and cost views. Source session stores are only ever read.

Stdlib-only, no side effects at import. Entry point: ``python3 -m
ccc_server.usage_db`` (or ``scripts/throughput``). See ``docs/usage-db.md``.
"""

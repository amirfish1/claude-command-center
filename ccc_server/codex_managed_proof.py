# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Pure validation of an explicitly accepted, exact managed-restart proof.

This module grants no restart authority. The restart selector must obtain
current process/launch identities from the trusted artifact verifier, under
its admission freeze, and repeat that attestation at the final barrier. JSON
reported by a worker, a checkout hash or an environment variable is not an
attestation. Unknown evidence keeps the existing conservative barrier.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping
from ccc_server.codex_managed_contract import GOAL_STATES

FORMAT = 1
REQUIRED_PROOFS = frozenset({'P1', 'P2b', 'P3', 'P4a', 'P4b', 'P4c', 'P5', 'P6'})
ISOLATION_CHECKS = frozenset({'different_host_netns', 'loopback_only', 'no_routes',
                              'tcp4_denied', 'udp4_denied', 'tcp6_denied',
                              'udp6_denied', 'dns_denied', 'fixture_reachable',
                              'nonroot', 'no_capabilities', 'no_new_privileges',
                              'readonly_root', 'private_writable_root_only',
                              'no_host_sockets'})
METHODS = frozenset({'turn/start', 'turn/steer', 'thread/queue/add'})
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_MAX_MANIFEST_BYTES = 1024 * 1024


class Unproven(ValueError):
    """A proof cannot authorize the requested execution transition."""


def _require(condition, reason):
    if not condition:
        raise Unproven(reason)


def _object(value, reason):
    _require(isinstance(value, dict), reason)
    return value


def _digest(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise Unproven("nonfinite JSON number")


def _decode(raw):
    _require(isinstance(raw, bytes) and 0 < len(raw) <= _MAX_MANIFEST_BYTES,
             'missing or oversized manifest')
    try:
        return _object(json.loads(raw, object_pairs_hook=_unique_object,
                                  parse_constant=_reject_nonfinite),
                       'manifest is not an object')
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Unproven('invalid manifest JSON') from exc


def _build(value):
    value = _object(value, 'missing execution build')
    _require(set(value) == {'artifact_sha256', 'profile_sha256'}, 'unknown build identity fields')
    _require(all(_digest(value[key]) for key in value), 'invalid execution build digest')
    return value


def _evidence(digest, evidence):
    _require(_digest(digest), 'invalid evidence digest')
    raw = evidence.get(digest)
    _require(isinstance(raw, bytes) and hashlib.sha256(raw).hexdigest() == digest,
             'missing or changed evidence')
    return raw


def _isolation(digest, evidence, phase, run_id):
    receipt = _decode(_evidence(digest, evidence))
    _require(receipt.get('format') == FORMAT and type(receipt.get('format')) is int
             and receipt.get('phase') == phase and receipt.get('run_id') == run_id
             and receipt.get('verdict') == 'pass', 'failed or mismatched isolation receipt')
    for field in ('checks', 'descendant_checks'):
        checks = _object(receipt.get(field), 'missing descendant/isolation checks')
        _require(ISOLATION_CHECKS <= checks.keys()
                 and all(checks[key] is True for key in ISOLATION_CHECKS),
                 'failed or missing isolation check')
    _evidence(receipt.get('raw_sha256'), evidence)


@dataclass(frozen=True)
class Capabilities:
    """Validated predicates, not a token usable to bypass the restart gate."""
    manifest_sha256: str
    goal_states: frozenset[str]
    lookup_methods: frozenset[str]
    dedupe_pairs: frozenset[tuple[str, str]]

    def may_resend(self, original_method, replacement_method):
        return (original_method in self.lookup_methods
                and replacement_method in self.lookup_methods
                and (original_method, replacement_method) in self.dedupe_pairs)


def validate(raw: bytes, *, acceptance: Mapping, expected_scope: str,
             codex: Mapping, transition: Mapping, evidence: Mapping,
             fixture_identity=None) -> Capabilities:
    """Validate exact bytes against independently supplied acceptance/facts.

    ``transition`` must come from the trusted execution-artifact verifier;
    this pure function cannot establish that any running process executed it.
    Acceptance is a separate operator/root receipt naming the manifest hash,
    avoiding a self-referential hash embedded in the manifest itself. Nothing
    in this API reads environment variables or treats ancestry as evidence.
    """
    manifest = _decode(raw)
    digest = hashlib.sha256(raw).hexdigest()
    _require(expected_scope in ('fixture', 'runtime'), 'unknown requested scope')
    _require(manifest.get('format') == FORMAT and type(manifest.get('format')) is int,
             'unsupported manifest format')
    _require(manifest.get('scope') == expected_scope, 'wrong proof scope')
    _require(isinstance(acceptance, Mapping)
             and acceptance.get('manifest_sha256') == digest
             and isinstance(acceptance.get('reference'), str)
             and acceptance['reference'].strip(), 'manifest lacks exact explicit acceptance')
    native = _object(manifest.get('codex'), 'missing native identity')
    _require(set(native) == {'version', 'binary_sha256', 'schema_sha256'}, 'unknown native identity fields')
    _require(isinstance(native['version'], str) and native['version'].strip()
             and _digest(native['binary_sha256']) and _digest(native['schema_sha256']),
             'invalid native identity')
    _require(native == codex, 'native binary, version or protocol drift')
    _require(isinstance(transition, Mapping), 'missing trusted transition facts')
    measured = _object(manifest.get('transition'), 'missing transition')
    _require(set(measured) == {'old_worker', 'control', 'replacement'}, 'incomplete execution transition')
    for role in measured:
        _require(_build(measured[role]) == _build(transition.get(role)),
                 'execution artifact/profile/ordered transition mismatch')
    if expected_scope == 'fixture':
        _require(isinstance(fixture_identity, dict) and fixture_identity
                 and manifest.get('fixture_identity') == fixture_identity,
                 'fixture socket/daemon/root binding mismatch')
    else:
        _require('fixture_identity' not in manifest, 'fixture binding in runtime proof')
    proofs = _object(manifest.get('proofs'), 'missing proofs')
    _require(REQUIRED_PROOFS <= proofs.keys(), 'missing mandatory native proof')
    for name in REQUIRED_PROOFS:
        proof = _object(proofs[name], 'invalid proof entry')
        _require(proof.get('verdict') == 'pass', 'failed or unknown native proof')
        run_id = proof.get('run_id')
        _require(isinstance(run_id, str) and 0 < len(run_id) <= 128, 'missing proof run identity')
        _evidence(proof.get('evidence_sha256'), evidence)
        _isolation(proof.get('e4_sha256'), evidence, 'E4', run_id)
        _isolation(proof.get('e5_sha256'), evidence, 'E5', run_id)
    p6 = proofs['P6']
    _require(p6.get('transition') == measured, 'P6 did not exercise the exact ordered transition')
    _require(proofs['P4b'].get('autostart_without_client') is True,
             'native queue requires a subscribed client')
    goals = proofs['P4c'].get('proven_goal_states')
    _require(isinstance(goals, list) and goals
             and all(isinstance(state, str) and state in GOAL_STATES for state in goals)
             and len(goals) == len(set(goals)), 'unproven goal classes')
    p4a = proofs['P4a']
    lookup = _object(p4a.get('lookup_exact'), 'missing exact lookup capabilities')
    _require(set(lookup) == METHODS and all(type(v) is bool for v in lookup.values()),
             'invalid method lookup capabilities')
    pairs = p4a.get('native_dedupe')
    _require(isinstance(pairs, list), 'missing native dedupe capabilities')
    dedupe = set()
    seen = set()
    for pair in pairs:
        pair = _object(pair, 'invalid native dedupe pair')
        _require(set(pair) == {'from', 'to', 'proven'}, 'unknown dedupe pair fields')
        key = (pair['from'], pair['to'])
        _require(all(isinstance(method, str) and method in METHODS for method in key)
                 and type(pair['proven']) is bool and key not in seen,
                 'invalid or duplicate dedupe method pair')
        seen.add(key)
        if pair['proven']:
            dedupe.add(key)
    return Capabilities(digest, frozenset(goals),
                        frozenset(method for method, proven in lookup.items() if proven),
                        frozenset(dedupe))

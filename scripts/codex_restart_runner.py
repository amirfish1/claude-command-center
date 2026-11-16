#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Disposable native proof workflow; importing starts nothing.

The outer launcher supplies an independently accepted execution contract and
an adapter for exact immutable role launches. This module never provisions an
anchor, accepts its own evidence, or releases a production worker. Adapters
are Python objects from the measured closure, never import names in input JSON.
The preparation CLI is in codex_restart_preflight.py.
"""
from __future__ import annotations

import hashlib
import json
import io
import os
import time
from dataclasses import dataclass

from ccc_server.execution_artifact import canonical, digest
from ccc_server.codex_managed_proof import ISOLATION_CHECKS, validate_fixture_plan
from scripts.codex_restart_fixture import Stop


def need(value, reason):
    if not value:
        raise Stop(reason)


# Each slot is a hard reservation ceiling, not a claim that an unknown native
# implementation will finish in that many requests. Fixture reservation remains
# authoritative; an extra/automatic request permanently stops the shared grant.
CASES = (
    ('idle-0', 'P1', 2), ('idle-5', 'P1', 2), ('idle-60', 'P1+P2b', 2),
    ('active-sse', 'P3', 1), ('active-tool', 'P3', 2),
    ('keys', 'P4a', 15), ('queue', 'P4b', 3), ('goal', 'P4c', 3),
    ('approval', 'P5', 2), ('integration', 'P6', 16),
)
need(sum(item[2] for item in CASES) == 48, 'invalid fixed completion schedule')
PAIRS = (('turn/start', 'turn/start'), ('turn/steer', 'turn/steer'),
         ('thread/queue/add', 'thread/queue/add'), ('turn/steer', 'turn/start'),
         ('thread/queue/add', 'turn/start'))


@dataclass(frozen=True)
class Acceptance:
    """Independent launcher observation, not a field embedded in the plan."""
    packet_sha256: str
    reference: str
    expires_at: float

    def check(self, packet):
        need(self.packet_sha256 == digest(canonical(packet))
             and isinstance(self.reference, str) and self.reference.strip()
             and type(self.expires_at) in (float, int)
             and time.time() < self.expires_at, 'execution packet lacks exact unexpired acceptance')
        need(packet.get('scope') == 'disposable-proof-preparation'
             and packet.get('runtime_authorized') is False
             and packet.get('execution_ready') is True,
             'packet is preparation-only or has wrong scope')


class Journal:
    """Append-only bounded raw receipts; persist before reporting success."""
    def __init__(self, stream, *, max_bytes=32 * 1024 * 1024):
        self.stream, self.max_bytes, self.used = stream, max_bytes, 0

    def __call__(self, event, **fields):
        raw = canonical({'event': event, 'wall_ns': time.time_ns(), **fields}) + b'\n'
        need(self.used + len(raw) <= self.max_bytes, 'raw evidence budget exhausted')
        self.stream.write(raw)
        self.stream.flush()
        try:
            os.fsync(self.stream.fileno())
        except (AttributeError, io.UnsupportedOperation):
            pass  # Offline byte streams have no durable file descriptor.
        self.used += len(raw)


class Workflow:
    """Fixed two-lifetime sequence over a measured, non-host adapter.

    The adapter owns exact process handles and fresh kernel identity checks.
    It must implement structural/fixture/isolation/native/client/control/cleanup
    operations. It cannot replace the protocol predicates below with verdicts.
    Root fixture requests are returned to the separate outer controller; that
    controller uses the existing exact bootstrap/restart authority APIs.
    """
    def __init__(self, packet, acceptance, budget, adapter, record):
        acceptance.check(packet)  # Before touching adapter, counters or sockets.
        self.packet, self.budget, self.io, self.record = packet, budget, adapter, record
        need(canonical(budget.grant) == canonical(packet['grant']), 'execution grant drift')
        self.evidence, self.results, self.run_ids = {}, {}, []
        self.cases = {name: 'UNRUN' for name, _, _ in CASES}
        self.cases.update({'integration/' + case + '/' + mode: 'UNRUN'
            for case in ('idle', 'active', 'ack-loss', 'queue', 'goal', 'approval', 'corrupt', 'C6')
            for mode in (('refusal',) if case in ('corrupt', 'C6') else ('planned', 'crash'))})

    def begin_case(self, name, ceiling):
        self.cases[name] = 'RUNNING'
        self.io.begin_case(name, ceiling)

    def end_case(self, name):
        self.cases[name] = 'OBSERVED'

    def save(self, value):
        raw = value if isinstance(value, bytes) else canonical(value)
        key = digest(raw)
        self.evidence[key] = raw
        self.record('evidence', sha256=key, value=value.decode() if isinstance(value, bytes) else value)
        return key

    def isolation(self, phase, run_id):
        receipt, raw = self.io.isolation(phase, run_id)
        need(receipt.get('phase') == phase and receipt.get('run_id') == run_id
             and receipt.get('verdict') == 'pass' and receipt.get('raw_sha256') == digest(raw),
             'invalid isolation receipt identity or raw binding')
        for field in ('checks', 'descendant_checks'):
            checks = receipt.get(field, {})
            need(ISOLATION_CHECKS <= checks.keys()
                 and all(checks[name] is True for name in ISOLATION_CHECKS),
                 phase + ' isolation failed, including descendant')
        self.save(raw)
        return self.save(receipt)

    def run(self):
        failure = None
        try:
            # No provider may listen until structural E1/E2 checks pass.
            self.io.verify_closure(self.packet)
            self.io.structural()
            self.io.start_fixture(self.budget, self.record)
            for lifetime in (1, 2):
                run_id = self.io.new_run_id(lifetime)
                need(isinstance(run_id, str) and run_id and run_id not in self.run_ids,
                     'invalid/reused run identity')
                self.run_ids.append(run_id)
                e4 = self.isolation('E4', run_id)
                need(self.budget.begin_lifetime() == lifetime, 'grant lifetime drift')
                self.io.start_native(lifetime, self.budget.remaining_seconds())
                suite = Protocol(self.io, self.budget, self.record)
                suite.begin_case, suite.end_case = self.begin_case, self.end_case
                if lifetime == 1:
                    observations = suite.run()
                else:
                    self.begin_case('integration', 16)
                    observations = {'P6': self.integration(suite)}
                    self.end_case('integration')
                self.io.stop_native()  # Exact owned handle, not a PID search.
                e5 = self.isolation('E5', run_id)
                self.io.verify_outer_isolation()
                self.budget.end_lifetime()
                for name, observation in observations.items():
                    self.results[name] = {'verdict': 'pass', 'run_id': run_id,
                        'e4_sha256': e4, 'e5_sha256': e5,
                        'evidence_sha256': self.save(observation),
                        **{k: observation[k] for k in ('autostart_without_client',
                            'proven_goal_states', 'lookup_exact', 'native_dedupe', 'transition')
                           if k in observation}}
            return {'scope': 'fixture', 'runtime_authorized': False,
                    'proofs': self.results, 'evidence': self.evidence}
        except BaseException as exc:
            failure = exc
            # Halt survives process reconstruction, including E4 and E5 failure.
            try:
                self.budget.halt(type(exc).__name__ + ': ' + str(exc))
            except Stop:
                pass
            self.cases = {name: 'FAILED' if state == 'RUNNING' else state for name, state in self.cases.items()}
            self.record('STOP', reason=str(exc), completed=list(self.results), cases=self.cases,
                        all_observations_invalid=True)
            raise
        finally:
            # Cleanup runs even before native startup and never releases a guard
            # or retries an uncertain operation. Adapter verifies owned absence.
            try:
                self.io.cleanup()
                self.io.verify_cleanup()
            except BaseException as exc:
                try:
                    self.budget.halt('cleanup did not prove exact owned absence')
                except Stop:
                    pass
                self.record('cleanup_failure', reason=str(exc))
                if failure is None:
                    raise

    def integration(self, suite):
        # A different daemon epoch cannot reuse the first lifetime's binding.
        # The outer controller must accept new exact bytes only after observing
        # the second native identity, stopped guards and measured role profiles.
        proposal = self.io.prepare_integration(self.results, self.evidence)
        need(set(proposal) == {'manifest', 'acceptance', 'codex', 'transition',
                              'fixture_identity', 'bootstrap'}, 'incomplete P6 proposal')
        validate_fixture_plan(proposal['manifest'], acceptance=proposal['acceptance'],
            codex=proposal['codex'], transition=proposal['transition'],
            fixture_identity=proposal['fixture_identity'], evidence=self.evidence)
        self.io.fixture_bootstrap(proposal['bootstrap'])
        # The adapter drives real measured CCC handlers, returns raw receipts,
        # and cannot turn the authority's execution result into a P6 verdict.
        observations = []
        for case in ('idle', 'active', 'ack-loss', 'queue', 'goal', 'approval', 'corrupt', 'C6'):
            for path in ('planned', 'crash') if case not in ('corrupt', 'C6') else ('refusal',):
                name = 'integration/' + case + '/' + path
                self.cases[name] = 'RUNNING'
                before = self.io.integration_snapshot(case, path)
                receipt = self.io.integration_action(case, path, proposal)
                after = self.io.integration_snapshot(case, path)
                check_integration(case, path, before, receipt, after)
                observations.append({'case': case, 'path': path, 'before': before,
                                     'action': receipt, 'after': after})
                self.cases[name] = 'OBSERVED'
        return {'transition': proposal['transition'], 'observations': observations}


def check_integration(case, path, before, action, after):
    """Compare actual handler/ledger/native snapshots; no boolean-only pass."""
    need(isinstance(before, dict) and isinstance(after, dict), 'missing CCC snapshots')
    from ccc_server.codex_managed_recovery import _valid_daemon
    need(_valid_daemon(before.get('daemon')) and before['daemon'] == after.get('daemon'), 'P6 daemon changed or unknown')
    need(before.get('thread_id') and before['thread_id'] == after.get('thread_id'), 'P6 thread changed')
    from ccc_server.execution_artifact import ProcessIdentity
    import re
    for value in (before.get('worker_identity'), after.get('worker_identity')):
        need(isinstance(value, dict) and set(value) == set(ProcessIdentity.__dataclass_fields__)
             and isinstance(value.get('container_id'), str) and re.fullmatch('[0-9a-f]{64}', value['container_id'])
             and all(type(value.get(k)) is int and value[k] > 0 for k in
                     ('pid', 'start_ticks', 'launcher_pid', 'launcher_start_ticks'))
             and isinstance(value.get('boot_id'), str) and value['boot_id']
             and isinstance(value.get('started_at'), str) and value['started_at'],
             'P6 missing exact worker identity')
    if case in ('corrupt', 'C6'):
        need(action.get('refused') is True and action.get('killed') is False
             and before['worker_identity'] == after['worker_identity'], 'P6 conservative refusal failed')
        if case == 'C6':
            need(set(action.get('compatibility', {})) == {'stdio', 'kimi', 'other'}
                 and all(v is True for v in action['compatibility'].values())
                 and action.get('receipts_sha256') and action.get('runtime_scope_refusal') == 'wrong proof scope',
                 'C6 compatibility receipts or actual runtime refusal absent')
        return
    need(before['worker_identity'] != after['worker_identity']
         and action.get('old_worker_absent') is True and action.get('replacement_attested') is True,
         'P6 did not replace only the exact worker')
    need(after.get('history') and before.get('history')
         and after['history'][:len(before['history'])] == before['history'], 'P6 persisted history lost')
    if case in ('active', 'approval'):
        need(before.get('turn_id') and after.get('turn_id') == before['turn_id']
             and after.get('turn_status') == 'completed' and after.get('ui_terminal') == 'completed',
             'P6 active native/UI terminal did not survive')
    if case in ('ack-loss', 'queue'):
        keys = before.get('accepted_keys')
        need(isinstance(keys, list) and keys and len(keys) == len(set(keys)), 'missing accepted input keys')
        need(after.get('consumed_keys') == keys and after.get('inject_receipt_keys') == before.get('queued_keys', keys)
             and after.get('inject_outstanding_keys', []) == []
             and after.get('activity_markers'),
             'P6 inputs were lost, duplicated, reordered or lack inject receipts')
    if case == 'goal':
        check_goal(before.get('goal'), after.get('goal'), after.get('fixture_tokens'))


def check_goal(before, after, tokens):
    need(isinstance(before, dict) and isinstance(after, dict)
         and before.get('objective') and before['objective'] == after.get('objective')
         and before.get('status') == after.get('status') == 'active'
         and type(tokens) is int and tokens > 0
         and after.get('tokensUsed') == tokens
         and type(before.get('timeUsedSeconds')) is int
         and type(after.get('timeUsedSeconds')) is int
         and after['timeUsedSeconds'] >= before['timeUsedSeconds'], 'goal continuity/accounting failed')


class Protocol:
    """Actual bounded RPC/read predicates; the adapter supplies transport only."""
    def __init__(self, io, budget, record):
        self.io, self.budget, self.record = io, budget, record
        self.begin_case, self.end_case = io.begin_case if io else None, lambda name: None

    def client(self):
        self.budget.remaining_seconds()
        c = self.io.client()
        from ccc_server.codex_managed_recovery import verify_initialize
        verify_initialize(self.io.native_identity(), {'result': c.initialize()})
        return c

    def wait(self, seconds):
        need(seconds < self.budget.remaining_seconds(), 'wait exceeds lifetime')
        self.io.wait(seconds)

    def thread(self, c):
        result = c.request('thread/start', {'model': 'fixture', 'modelProvider': 'fixture',
            'cwd': '/state/work', 'sandbox': 'workspace-write', 'approvalPolicy': 'on-request'})
        thread = result['thread']
        need(thread.get('id'), 'native thread identity missing')
        return thread

    def resume(self, c, t):
        result = c.request('thread/resume', {'threadId': t['id'], 'path': t['path']})
        need(result['thread']['id'] == t['id'] and result['thread']['path'] == t['path'],
             'native resume identity drift')

    def start(self, c, t, key):
        turn = c.request('turn/start', {'threadId': t['id'], 'clientUserMessageId': key,
            'input': [{'type': 'text', 'text': 'Disposable deterministic proof input: ' + key}]})['turn']['id']
        self.persisted(c, t)
        return turn

    def persisted(self, c, t):
        observed = c.request('thread/read', {'threadId': t['id'], 'includeTurns': False})['thread']
        need(observed['id'] == t['id'] and observed.get('path'), 'native rollout path missing after turn start')
        if t.get('path'):
            need(t['path'] == observed['path'], 'native rollout path drift')
        t['path'] = observed['path']

    def completed(self, c, t, turn):
        msg = c.wait_notification('turn/completed', lambda p: p.get('threadId') == t['id']
            and p.get('turn', {}).get('id') == turn, timeout=60)
        need(msg['params']['turn']['status'] == 'completed', 'native turn aborted or failed')
        turns = self.pages(c, 'thread/turns/list', {'threadId': t['id'], 'itemsView': 'full'})
        need(any(row.get('id') == turn and row.get('status') == 'completed' for row in turns),
             'native terminal missing from authoritative history')
        return turns

    def pages(self, c, method, params):
        rows, cursor, seen = [], None, set()
        if method in ('thread/turns/list', 'thread/items/list'):
            params = dict(params, sortDirection='asc')
        while True:
            self.budget.remaining_seconds()
            result = c.request(method, dict(params, limit=1, cursor=cursor))
            need(isinstance(result.get('data'), list) and 'nextCursor' in result,
                 'native history page incomplete')
            rows.extend(result['data'])
            cursor = result['nextCursor']
            if cursor is None:
                return rows
            serialized = canonical(cursor)
            need(serialized not in seen and len(rows) <= 10000, 'native history pagination did not finish')
            seen.add(serialized)

    def snapshot(self, c, t):
        thread = c.request('thread/read', {'threadId': t['id'], 'includeTurns': False})
        need(thread.get('readState') is not None, 'authoritative read revision unavailable')
        entries = self.pages(c, 'thread/items/list', {'threadId': t['id']})
        need(all(isinstance(row.get('item'), dict) and row.get('turnId') for row in entries),
             'native item entries lack item/turn identity')
        items = [row['item'] for row in entries]
        need(all(item.get('id') for item in items)
             and len({item['id'] for item in items}) == len(items), 'duplicate or unknown native item identity')
        return {'thread': thread, 'turns': self.pages(c, 'thread/turns/list', {'threadId': t['id'], 'itemsView': 'full'}),
                'items': items, 'item_entries': entries,
                'queue': self.pages(c, 'thread/queue/list', {'threadId': t['id']})}

    def stable(self, c, t, between=lambda: None):
        start = len(c.notifications)
        one = self.snapshot(c, t)
        between()
        two = self.snapshot(c, t)
        quiet = not any(m['method'] in ('turn/started', 'thread/queue/changed')
                        or m['method'].startswith('item/') for m in c.notifications[start:])
        idle = all(s['thread']['thread'].get('status', {}).get('type') == 'idle'
                   and all(turn.get('status') in ('completed', 'failed', 'interrupted')
                           for turn in s['turns']) for s in (one, two))
        return one, two, one == two and quiet and idle

    @staticmethod
    def count(snapshot, key):
        # Turn and item endpoints overlap: compare distinct native item IDs.
        items = {item['id']: item for item in snapshot['items'] if item.get('id')}
        return sum(item.get('type') == 'userMessage' and item.get('clientId') == key
                   for item in items.values()) + sum(item.get('clientUserMessageId') == key
                                                   for item in snapshot['queue'])

    def run(self):
        results = {}
        idle = []
        for delay in (0, 5, 60):
            self.begin_case('idle-' + str(delay), 2)
            c = self.client()
            t = self.thread(c)
            self.io.phase('text')
            turn = self.start(c, t, 'idle-' + str(delay))
            history = self.completed(c, t, turn)
            identity = self.io.native_identity()
            c.close()  # Subscriber disconnect, never unsubscribe.
            self.wait(delay)
            b = self.client()
            loaded = self.pages(b, 'thread/loaded/list', {})
            need(t['id'] in [row if isinstance(row, str) else row.get('id') for row in loaded],
                 'persisted subscribed thread unloaded at zero clients')
            self.resume(b, t)
            need(self.pages(b, 'thread/turns/list', {'threadId': t['id'], 'itemsView': 'full'}) == history,
                 'persisted history differs after client restart')
            self.completed(b, t, self.start(b, t, 'idle-next-' + str(delay)))
            need(identity == self.io.native_identity(), 'daemon changed during client restart')
            b.close()
            idle.append({'delay': delay, 'thread': t, 'history': history, 'daemon': identity})
            self.end_case('idle-' + str(delay))
        results['P1'] = results['P2b'] = {'observations': idle}
        self.begin_case('active-sse', 1)
        active = self.active(False)
        self.end_case('active-sse')
        self.begin_case('active-tool', 2)
        results['P3'] = {'observations': [active, self.active(True)]}
        self.end_case('active-tool')
        for case, proof, ceiling, method in (('keys', 'P4a', 15, self.keys), ('queue', 'P4b', 3, self.queue),
                ('goal', 'P4c', 3, self.goal), ('approval', 'P5', 2, self.approval)):
            self.begin_case(case, ceiling)
            results[proof] = method()
            self.end_case(case)
        return results

    def active(self, tool):
        c = self.client()
        t = self.thread(c)
        self.io.phase('tool-held' if tool else 'held')
        turn = self.start(c, t, 'active-tool' if tool else 'active-sse')
        self.io.wait_fixture_request()
        if tool:
            self.io.release_phase()
            self.io.wait_server_request(c)
        c.close()
        b = self.client()
        self.resume(b, t)
        self.io.phase('text')
        if tool:
            self.io.answer_pending(b)
        else:
            self.io.release_phase()
        history = self.completed(b, t, turn)
        b.close()
        self.io.verify_rollout_terminal(t, turn, 'completed')
        return {'thread': t, 'turn': turn, 'history': history, 'tool': tool}

    def key_params(self, method, t, key, turn):
        p = {'threadId': t['id'], 'clientUserMessageId': key,
             'input': [{'type': 'text', 'text': 'Disposable key proof ' + key}]}
        if method == 'turn/steer':
            p['expectedTurnId'] = turn
        return p

    def keys(self):
        observations, lookups, pairs = [], {}, []
        t = None
        # Adapter opens only an owned thread, holds a fixture request for steer
        # and queue tests, and releases it after the submitted action. It does
        # not evaluate keys or native dedupe for this runner.
        for index, (original, replacement) in enumerate(PAIRS):
            c = self.client()
            if t is None:
                t = self.thread(c)
            else:
                self.resume(c, t)
            baseline = self.io.fixture_completions()
            turn = self.io.hold_for_method(c, t, original)
            key = 'dedupe-' + str(index)
            c.request(original, self.key_params(original, t, key, turn))
            self.persisted(c, t)
            if replacement == 'turn/steer':
                # Both steers target the same still-active native turn. Its
                # persisted input establishes consumption before the explicit
                # dedupe experiment; idle negative reads are tested separately.
                one, two = self.snapshot(c, t), self.snapshot(c, t)
                stable = self.count(one, key) == self.count(two, key) == 1
            else:
                self.io.settle(c, t)
                one, two, stable = self.stable(c, t)
            count = self.count(two, key)
            lookups[original] = count == 1 and stable
            need(lookups[original], 'native exact key lookup unavailable: ' + original)
            # An explicit destructive dedupe experiment, not a recovery retry.
            # Never perform it before proving the first key was consumed.
            # Queue-add can test a consumed key while idle. Only steer needs
            # another active turn; avoid spending fixture credits on setup
            # requests that are not part of the native predicate.
            turn = turn if replacement == 'turn/steer' else None
            completions = self.io.fixture_completions()
            c.request(replacement, self.key_params(replacement, t, key, turn))
            self.io.settle(c, t)
            after = self.snapshot(c, t)
            # Held input plus one native steer follow-through reserves at
            # most two responses. A duplicate may not add another response.
            expected = baseline + 2 if replacement == 'turn/steer' else completions
            proven = self.count(after, key) == 1 and self.io.fixture_completions() == expected
            pairs.append({'from': original, 'to': replacement, 'proven': proven})
            observations.append({'key': key, 'before': [one, two], 'after': after})
            need(proven, 'native dedupe failed: ' + original + ' -> ' + replacement)
            c.close()
        # ACK-loss cases send one raw frame, then destroy that exact client
        # without reading. Test both idle/busy races and three search timings.
        for timing in ('before', 'during', 'after'):
            observations.append(self.crash_key(t, False, timing))
        observations.extend(self.crash_busy_keys(t))
        c = self.client()
        self.resume(c, t)
        self.io.phase('text')
        need(len(self.pages(c, 'thread/turns/list', {'threadId': t['id'], 'itemsView': 'full'})) >= 3,
             'page-size-one negative control lacks persisted turns')
        one, two, stable = self.stable(c, t)
        need(stable and self.count(two, 'never-sent') == 0, 'negative absent-key control failed')
        one, changed, stable = self.stable(c, t, lambda: self.completed(c, t, self.start(c, t, 'between-passes')))
        need(not stable and one != changed, 'two-pass reader missed intervening item')
        c.close()
        return {'lookup_exact': lookups, 'native_dedupe': pairs, 'observations': observations,
                'negative_absent': True, 'page_size': 1, 'intervening_change_detected': True}

    def crash_busy_keys(self, t):
        # Three writer/search races share one bounded active turn. Keeping the
        # deterministic response held batches its pending steers into one
        # native follow-through, rather than spending a setup turn per race.
        c = self.client()
        self.resume(c, t)
        baseline = self.io.fixture_completions()
        turn = self.io.hold_for_method(c, t, 'turn/steer')
        c.close()
        b = self.client()
        self.resume(b, t)
        pending = []
        for timing in ('before', 'during', 'after'):
            key = 'ack-loss-True-' + timing
            frame = {'jsonrpc': '2.0', 'id': 1, 'method': 'turn/steer',
                     'params': self.key_params('turn/steer', t, key, turn)}
            if timing == 'before':
                self.io.ack_loss(frame)
                reads = [self.snapshot(b, t)]
            elif timing == 'during':
                one, two, _ = self.stable(b, t, lambda: self.io.ack_loss(frame))
                reads = [one, two]
            else:
                one, two, _ = self.stable(b, t)
                reads = [one, two]
                self.io.ack_loss(frame)
            pending.append({'busy': True, 'search_timing': timing, 'frame': frame, 'race_reads': reads})
        self.io.settle(b, t)
        need(self.io.fixture_completions() == baseline + 2, 'busy ACK-loss response batching failed')
        for observation in pending:
            key = observation['frame']['params']['clientUserMessageId']
            one, two, stable = self.stable(b, t)
            need(stable and self.count(two, key) == 1, 'busy ACK-loss consumption unknown; no resend')
            before = self.io.fixture_completions()
            b.request('turn/start', self.key_params('turn/start', t, key, None))
            self.io.settle(b, t)
            after = self.snapshot(b, t)
            need(self.count(after, key) == 1 and self.io.fixture_completions() == before,
                 'busy ACK-loss resend duplicated native input')
            observation['reads'] = [one, two, after]
        b.close()
        return pending

    def crash_key(self, t, busy, timing):
        c = self.client()
        self.resume(c, t)
        method = 'turn/steer' if busy else 'turn/start'
        turn = self.io.hold_for_method(c, t, method)
        key = 'ack-loss-' + str(busy) + '-' + timing
        c.close()
        # Adapter uses a separate raw connection with no background reader and
        # exact-owned child death. No ack or retry can leak from Client.request.
        frame = {'jsonrpc': '2.0', 'id': 1, 'method': method,
                 'params': self.key_params(method, t, key, turn)}
        b = self.client()
        self.resume(b, t)
        if timing == 'before':
            self.io.ack_loss(frame)
            self.snapshot(b, t)
        elif timing == 'during':
            self.stable(b, t, lambda: self.io.ack_loss(frame))
        else:
            self.stable(b, t)
            self.io.ack_loss(frame)
        self.io.settle(b, t)
        one, two, stable = self.stable(b, t)
        need(stable and self.count(two, key) == 1, 'ACK-loss key remains unknown; no resend')
        before = self.io.fixture_completions()
        b.request('turn/start', self.key_params('turn/start', t, key, None))
        self.io.settle(b, t)
        after = self.snapshot(b, t)
        need(self.count(after, key) == 1 and self.io.fixture_completions() == before,
             'ACK-loss resend duplicated native input')
        b.close()
        return {'busy': busy, 'search_timing': timing, 'frame': frame, 'reads': [one, two, after]}

    def queue(self):
        c = self.client()
        t = self.thread(c)
        self.io.phase('held')
        turn = self.start(c, t, 'queue-hold')
        self.io.wait_fixture_request()
        keys = ['queue-first', 'queue-second']
        for key in keys:
            c.request('thread/queue/add', self.key_params('thread/queue/add', t, key, turn))
        changed = c.wait_notification('thread/queue/changed', lambda p: p.get('threadId') == t['id'])
        c.close()
        before = self.io.fixture_completions()
        self.io.phase('text')
        self.io.release_phase()
        self.io.wait_fixture_completions(before + 2)  # With NO subscribed client.
        b = self.client()
        self.resume(b, t)
        snapshot = self.snapshot(b, t)
        observed = [item.get('clientId') for item in snapshot['items']
                    if item.get('type') == 'userMessage' and item.get('clientId') in keys]
        need(observed == keys and not snapshot['queue'], 'native queue lost order/exactly-once/autostart')
        b.close()
        return {'autostart_without_client': True, 'keys': keys, 'snapshot': snapshot, 'queue_changed': changed}

    def goal(self):
        c = self.client()
        t = self.thread(c)
        objective = 'Disposable bounded fixture continuity objective'
        self.io.bind_goal_thread(t['id'])
        self.io.phase('held')
        c.request('thread/goal/set', {'threadId': t['id'], 'origin': 'user',
                                    'objective': objective, 'status': 'active', 'tokenBudget': 100})
        before = c.request('thread/goal/get', {'threadId': t['id']})['goal']
        turn = self.start(c, t, 'goal-initial')
        self.io.wait_fixture_request()
        c.close()
        b = self.client()
        self.resume(b, t)
        count = self.io.fixture_completions()
        self.io.release_phase(next_held=True)
        self.io.wait_fixture_reservations(count + 1)
        # Hold the one automatic continuation; no unbounded automatic chain.
        self.io.wait_fixture_request()
        after = b.request('thread/goal/get', {'threadId': t['id']})['goal']
        check_goal(before, after, self.io.fixture_tokens(t['id']))
        b.wait_notification('thread/goal/updated', lambda p: p.get('threadId') == t['id'])
        b.request('thread/goal/clear', {'threadId': t['id'], 'origin': 'user'})
        self.io.phase('text')
        self.io.release_phase()
        self.io.settle(b, t)
        need(self.io.fixture_completions() == count + 1, 'goal spawned extra automatic continuations')
        b.close()
        self.io.goal_thread = None
        return {'proven_goal_states': ['active'], 'before': before, 'after': after, 'turn': turn}

    def approval(self):
        c = self.client()
        t = self.thread(c)
        self.io.phase('tool')
        turn = self.start(c, t, 'approval')
        request = self.io.wait_server_request(c)
        c.close()
        b = self.client()
        self.resume(b, t)
        repeated = self.io.wait_server_request(b)
        need(request.get('method') == repeated.get('method')
             and request.get('params') == repeated.get('params'), 'pending server approval was lost')
        self.io.phase('text')
        self.io.answer_pending(b)
        history = self.completed(b, t, turn)
        b.close()
        return {'request': request, 'repeated': repeated, 'history': history}

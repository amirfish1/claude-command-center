"""Measured restart selection, executed only by the existing root authority.

The HTTP/CLI callers supply data, not authority. Root-owned acceptance and
live kernel/Docker observations are required inside the immutable image.
"""
import base64
import json
import re
import secrets

from ccc_server.execution_artifact import Refused, canonical, digest, _verified_launch
from ccc_server import codex_managed_proof as proof

ROLES = ('worker', 'replacement')
MAX_PACKET = 32 * 1024 * 1024


def _need(value, message):
    if not value:
        raise Refused(message)


def _bytes(value):
    _need(isinstance(value, str) and len(value) <= MAX_PACKET, 'oversized proof data')
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise Refused('invalid proof data encoding') from exc


class Coordinator:
    """Single transaction; no daemon owner, general command or retry queue.

    Constructed in execution_authority.main AFTER authenticate_self. Injectable
    adapters are for offline rejection tests; they are never an API boundary.
    """
    def __init__(self, anchor, authority, docker, verifier, profiles, identities,
                 authenticate, read_anchor):
        self.anchor, self.authority, self.docker, self.verifier = anchor, authority, docker, verifier
        self.profiles, self.identities = profiles, identities
        self.authenticate, self.read_anchor = authenticate, read_anchor

    def _facts(self):
        _need(self.read_anchor() == self.anchor and self.authenticate() == self.authority,
              'root authority or acceptance changed')
        facts = {role: self.verifier.observe(self.identities[role].container_id,
                  self.profiles[role], self.identities[role]) for role in ROLES}
        _need(len({(i.pid, i.start_ticks, i.boot_id) for i in
                   (*self.identities.values(), self.authority)}) == 3, 'restart roles are not distinct')
        _need(self.verifier.processes.stopped(self.identities['replacement']),
              'replacement consumed input before proof acceptance')
        _need(self.read_anchor() == self.anchor and self.authenticate() == self.authority,
              'root authority or acceptance changed during role observation')
        facts['control'] = _verified_launch(self.authority, self.anchor['image_id'],
            self.anchor['executed_build_sha256'], self.anchor['execution_profile_sha256'], '')
        return facts

    def _validate(self, raw, evidence, receipt, facts, observed_worker=None):
        from ccc_server import codex_managed_recovery as recovery
        observation = receipt.get('observation')
        _need(isinstance(observation, dict), 'missing worker observation')
        ident = (observed_worker or facts['worker']).identity
        _need(observation.get('worker') == {'pid': ident.pid, 'start_ticks': ident.start_ticks,
                                           'boot_id': ident.boot_id}, 'worker snapshot identity mismatch')
        daemon = observation.get('daemon')
        _need(recovery._valid_daemon(daemon), 'unknown official native binary/schema')
        _need(observation.get('daemon_epoch') == recovery._sha(daemon), 'native epoch mismatch')
        _need(isinstance(observation.get('worker_epoch'), str) and observation['worker_epoch'],
              'missing worker continuity epoch')
        native = {'version': daemon['version'], 'binary_sha256': daemon['binary_sha256'],
                  'schema_sha256': daemon['protocol_sha256']}
        transition = {('old_worker' if role == 'worker' else role): {
            'artifact_sha256': fact.executed_build_sha256,
            'profile_sha256': fact.execution_profile_sha256} for role, fact in facts.items()}
        capabilities = self._proof(raw, evidence, native, transition)
        threads = observation.get('threads')
        _need(isinstance(threads, dict) and len(threads) <= recovery.MAX_REATTACH,
              'unknown per-thread continuity')
        for sid, row in threads.items():
            _need(isinstance(sid, str) and sid and isinstance(row, dict)
                  and set(row) == {'active_turn_ids', 'queue_ids', 'goal_state', 'claims'},
                  'unknown thread continuity fields')
            for key in ('active_turn_ids', 'queue_ids'):
                _need(isinstance(row[key], list) and all(isinstance(x, str) and x for x in row[key])
                      and len(set(row[key])) == len(row[key]), 'invalid native turn/queue inventory')
            _need(len(row['active_turn_ids']) <= 1, 'ambiguous active native turns')
            _need(row['goal_state'] is None or row['goal_state'] in capabilities.goal_states,
                  'native goal class lacks accepted proof')
            _need(isinstance(row['claims'], list), 'missing durable handoff inventory')
            for claim in row['claims']:
                _need(isinstance(claim, dict) and set(claim) == {'entry_id', 'phase', 'method',
                      'ledger_sequence', 'native_receipt', 'epoch', 'claim_sha256'}, 'unknown durable receipt fields')
                _need(claim['phase'] == 'acknowledged' and claim['epoch'] == observation['daemon_epoch']
                      and isinstance(claim['entry_id'], str) and claim['entry_id']
                      and claim['method'] in recovery.INPUT_METHODS
                      and type(claim['ledger_sequence']) is int and claim['ledger_sequence'] > 0
                      and isinstance(claim['native_receipt'], str) and claim['native_receipt']
                      and proof._digest(claim['claim_sha256']),
                      'outstanding or changed-epoch input must retain its FIFO slot')
        return capabilities

    def _proof(self, raw, evidence, native, transition):
        return proof.validate(raw, acceptance=self.anchor['managed_acceptance'],
            expected_scope='runtime', codex=native, transition=transition, evidence=evidence)

    def _acceptance(self):
        _need(self.anchor.get('format') == 'ccc-root-authority-v2'
              and isinstance(self.anchor.get('managed_acceptance'), dict),
              'root has not accepted a managed runtime proof')
        return self.anchor['managed_acceptance']

    def run(self, raw, evidence):
        acceptance = self._acceptance()
        _need(acceptance['manifest_sha256'] == digest(raw),
              'runtime proof differs from root acceptance')
        for profile in self.profiles.values():
            _need(profile.control_stdio and profile.entry == '/opt/ccc/ccc_worker.py'
                  and profile.argv == ('--socket', '/state/worker.sock', '--managed-control-stdio'),
                  'restart requires the fixed measured worker control profile')
        transaction = secrets.token_hex(16)
        request = {'operation': 'begin', 'transition_id': transaction,
                   'manifest_sha256': digest(raw), 'owner_pid': self.authority.pid,
                   'goal_states': [], 'receipt_sha256': None}
        worker = self.identities['worker'].container_id
        # Authenticate both targets and the coordinator BEFORE sending data;
        # repeat observations under the worker's freeze at both barriers.
        self._facts()
        initial = None
        sealed = False
        try:
            initial = self.docker.control(worker, request)
            request['receipt_sha256'] = digest(canonical(initial))
            initial_facts = self._facts()
            capabilities = self._validate(raw, evidence, initial, initial_facts)
            _need(initial.get('stage') == 'captured', 'initial freeze was not captured')
            # No retries after a possibly executed seal/stop/release command.
            sealed = True
            request['goal_states'] = sorted(capabilities.goal_states)
            final = self.docker.control(worker, dict(request, operation='final'))
            request['receipt_sha256'] = digest(canonical(final))
            final_facts = self._facts()
            _need(initial_facts == final_facts, 'initial/final authenticated identity drift')
            self._validate(raw, evidence, final, final_facts)
            _need(final.get('stage') == 'sealed' and final.get('lease') == initial.get('lease')
                  and final.get('transition_id') == transaction
                  and final.get('manifest_sha256') == digest(raw), 'final durable freeze changed')
            for key in ('daemon', 'daemon_epoch', 'worker', 'worker_epoch'):
                _need(initial['observation'][key] == final['observation'][key], 'continuity changed: ' + key)
            _need(set(initial['observation']['threads']) == set(final['observation']['threads']),
                  'tracked thread inventory changed during proof')
            _need(self.read_anchor() == self.anchor and self.authenticate() == self.authority,
                  'root authority/acceptance changed before exact stop')
            self.docker.stop_exact(final_facts['worker'])
            # release() re-observes exact application AND guardian identities
            # and the kernel admission stop before the first recovery input.
            self.verifier.release(final_facts['replacement'], self.profiles['replacement'])
            replacement = self.identities['replacement'].container_id
            ready = self.docker.control(replacement, dict(request, operation='ready'))
            live = self.verifier.observe(replacement, self.profiles['replacement'], self.identities['replacement'])
            _need(live == final_facts['replacement'], 'replacement launch changed after release')
            _need(self.read_anchor() == self.anchor and self.authenticate() == self.authority,
                  'root authority/acceptance changed before recovery')
            # Validate native classes using the already accepted ordered pair;
            # replacement identity is checked separately, not A->B reinterpreted as B->B.
            self._validate(raw, evidence, ready, final_facts, observed_worker=live)
            _need(ready['observation']['daemon'] == final['observation']['daemon']
                  and set(ready['observation']['threads']) == set(final['observation']['threads']),
                  'replacement native continuity changed')
            result = self.docker.control(replacement, dict(request, operation='resume'))
            _need(result.get('stage') == 'resumed', 'replacement remains frozen for reconciliation')
            return {'ok': True, 'manifest_sha256': digest(raw), 'transition_id': transaction,
                    'worker': worker, 'replacement': replacement, 'restarted': True,
                    'trust_base': {'initial': self._trust_base(initial_facts),
                                   'final': self._trust_base(final_facts)}}
        except BaseException:
            if initial is not None and not sealed:
                try:
                    self.docker.control(worker, dict(request, operation='abort'))
                except Exception:
                    pass  # Unknown holds the durable drain/fence, never implies idle.
            raise

    def _trust_base(self, facts):
        """Separate evidence for the authenticated outer control, not E2."""
        from ccc_server.execution_authority import _wire
        return {'role': 'trusted-outer-launcher', 'control': _wire(facts['control']),
                'anchor_sha256': digest(canonical(self.anchor))}


def run_query(query, *, anchor, authority, docker, processes, authenticate, read_anchor):
    """Closed root operation; JSON cannot supply acceptance or control facts."""
    from ccc_server.execution_authority import _profile
    from ccc_server.execution_artifact import ProcessIdentity, TrustedLaunchVerifier
    _need(isinstance(query, dict) and set(query) == {'profiles', 'identities', 'manifest', 'evidence'},
          'unsupported managed restart request')
    _need(set(query['profiles']) == set(ROLES) and set(query['identities']) == set(ROLES),
          'missing exact worker/replacement roles')
    profiles = {role: _profile(query['profiles'][role]) for role in ROLES}
    identities = {role: ProcessIdentity(**query['identities'][role]) for role in ROLES}
    raw = _bytes(query['manifest'])
    _need(isinstance(query['evidence'], dict) and len(query['evidence']) <= 128, 'unbounded proof evidence')
    evidence = {key: _bytes(value) for key, value in query['evidence'].items()}
    return Coordinator(anchor, authority, docker, TrustedLaunchVerifier(docker, processes),
                       profiles, identities, authenticate, read_anchor).run(raw, evidence)


def launch(payload):
    """Shared HTTP/CLI boundary. Neither caller can select an arbitrary root op."""
    from ccc_server.execution_authority import AttestationClient
    _need(isinstance(payload, dict) and set(payload) == {'anchor_path', 'request'}
          and isinstance(payload['anchor_path'], str) and payload['anchor_path'].startswith('/'),
          'managed restart needs an explicit root-controlled anchor and fixed request')
    return AttestationClient(payload['anchor_path']).managed_restart(payload['request'])

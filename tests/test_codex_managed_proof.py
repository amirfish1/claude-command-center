"""Exact proof binding never treats a plan, env value or hash claim as authority."""
import copy
import hashlib
import json

import pytest

from ccc_server import codex_managed_proof as proof


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def packet():
    evidence = {}
    def save(value):
        raw = encode(value)
        digest = hashlib.sha256(raw).hexdigest()
        evidence[digest] = raw
        return digest
    raw_hash = save({'events': ['offline synthetic receipt; no native semantics claimed']})
    isolation = {}
    for phase in ('E4', 'E5'):
        isolation[phase] = save({'format': 1, 'phase': phase, 'verdict': 'pass',
                                'run_id': 'fixture-run', 'raw_sha256': raw_hash,
                                'checks': dict.fromkeys(proof.ISOLATION_CHECKS, True),
                                'descendant_checks': dict.fromkeys(proof.ISOLATION_CHECKS, True)})
    build = {'artifact_sha256': '1' * 64, 'profile_sha256': '2' * 64}
    transition = {role: copy.deepcopy(build) for role in ('old_worker', 'control', 'replacement')}
    native = {'version': 'test-version', 'binary_sha256': '3' * 64, 'schema_sha256': '4' * 64}
    entries = {name: {'verdict': 'pass', 'run_id': 'fixture-run', 'evidence_sha256': raw_hash,
                      'e4_sha256': isolation['E4'], 'e5_sha256': isolation['E5']}
               for name in proof.REQUIRED_PROOFS}
    entries['P6']['transition'] = copy.deepcopy(transition)
    entries['P4a'].update(lookup_exact=dict.fromkeys(proof.METHODS, True), native_dedupe=[
        {'from': 'turn/steer', 'to': 'turn/start', 'proven': True}])
    entries['P4b']['autostart_without_client'] = True
    entries['P4c']['proven_goal_states'] = ['active', 'paused']
    manifest = {'format': 1, 'scope': 'runtime', 'codex': native,
                'transition': copy.deepcopy(transition), 'proofs': entries}
    def check(candidate=None, **kwargs):
        raw = encode(manifest if candidate is None else candidate)
        options = {'acceptance': {'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                                  'reference': 'explicit-test-acceptance'},
                   'expected_scope': 'runtime', 'codex': copy.deepcopy(native),
                   'transition': transition, 'evidence': evidence}
        options.update(kwargs)
        return proof.validate(raw, **options)
    return manifest, evidence, save, check


def test_exact_receipt_exposes_only_proven_classes_and_pairs(packet):
    _, _, _, check = packet
    result = check()
    assert result.goal_states == {'active', 'paused'}
    assert result.may_resend('turn/steer', 'turn/start')
    assert not result.may_resend('turn/start', 'turn/steer')
    assert not result.may_resend('thread/queue/add', 'turn/start')


@pytest.mark.parametrize('name', sorted(proof.REQUIRED_PROOFS))
@pytest.mark.parametrize('failure', ['missing', 'fail', 'unknown', 'missing-evidence', 'run-mismatch'])
def test_every_mandatory_proof_and_its_evidence_are_required(packet, name, failure):
    manifest, _, _, check = packet
    if failure == 'missing':
        del manifest['proofs'][name]
    elif failure == 'missing-evidence':
        manifest['proofs'][name]['evidence_sha256'] = '0' * 64
    elif failure == 'run-mismatch':
        manifest['proofs'][name]['run_id'] = 'other-run'
    else:
        manifest['proofs'][name]['verdict'] = failure
    with pytest.raises(proof.Unproven):
        check()


@pytest.mark.parametrize('role', ['old_worker', 'control', 'replacement'])
@pytest.mark.parametrize('field', ['artifact_sha256', 'profile_sha256'])
def test_changed_executed_build_profile_or_order_refuses_even_new_acceptance(packet, role, field):
    manifest, _, _, check = packet
    manifest['transition'][role][field] = '9' * 64
    with pytest.raises(proof.Unproven):
        check()


def test_p6_must_name_the_same_ordered_build_transition(packet):
    manifest, _, _, check = packet
    manifest['proofs']['P6']['transition']['replacement']['artifact_sha256'] = '9' * 64
    with pytest.raises(proof.Unproven):
        check()


@pytest.mark.parametrize('phase', ['E4', 'E5'])
@pytest.mark.parametrize('group', ['checks', 'descendant_checks'])
@pytest.mark.parametrize('check_name', sorted(proof.ISOLATION_CHECKS))
def test_any_failed_isolation_check_refuses_even_if_manifest_was_accepted(packet, phase, group, check_name):
    manifest, evidence, save, check = packet
    key = phase.lower() + '_sha256'
    entry = manifest['proofs']['P1']
    receipt = json.loads(evidence[entry[key]])
    receipt[group][check_name] = False
    entry[key] = save(receipt)
    with pytest.raises(proof.Unproven):
        check()


@pytest.mark.parametrize('acceptance', [None, {}, {'manifest_sha256': '0' * 64, 'reference': 'plan-pass'}])
def test_explicit_acceptance_of_exact_manifest_bytes_required(packet, acceptance):
    with pytest.raises(proof.Unproven):
        packet[3](acceptance=acceptance)


def test_scope_cannot_be_promoted_or_fixture_identity_reused(packet):
    manifest, _, _, check = packet
    manifest['scope'] = 'fixture'
    manifest['fixture_identity'] = {'daemon_pid': 123, 'root': '/fixture', 'socket_inode': 456}
    with pytest.raises(proof.Unproven):
        check()
    assert check(expected_scope='fixture', fixture_identity=manifest['fixture_identity'])
    with pytest.raises(proof.Unproven):
        check(expected_scope='fixture', fixture_identity={'daemon_pid': 999})


@pytest.mark.parametrize('field', ['version', 'binary_sha256', 'schema_sha256'])
def test_native_protocol_or_binary_drift_refuses(packet, field):
    manifest, _, _, check = packet
    current = dict(manifest['codex'], **{field: 'different'})
    with pytest.raises(proof.Unproven):
        check(codex=current)


def test_unproven_dedupe_does_not_enable_cross_method_resend(packet):
    manifest, _, _, check = packet
    manifest['proofs']['P4a']['native_dedupe'][0]['proven'] = False
    assert not check().may_resend('turn/steer', 'turn/start')
    manifest['proofs']['P4a']['native_dedupe'][0]['proven'] = True
    manifest['proofs']['P4a']['lookup_exact']['turn/steer'] = False
    assert not check().may_resend('turn/steer', 'turn/start')


@pytest.mark.parametrize('value', ['active', [], ['unknown'], ['active', 'active']])
def test_unknown_or_malformed_goal_classes_refuse(packet, value):
    manifest, _, _, check = packet
    manifest['proofs']['P4c']['proven_goal_states'] = value
    with pytest.raises(proof.Unproven):
        check()


def test_evidence_bytes_cannot_change_behind_the_same_hash(packet):
    manifest, evidence, _, check = packet
    digest = manifest['proofs']['P1']['evidence_sha256']
    evidence[digest] += b'changed'
    with pytest.raises(proof.Unproven):
        check()


@pytest.mark.parametrize('raw', [b'{}', b'{"format":1,"format":1}', b'{"format":NaN}', b'[]', b'\xff'])
def test_malformed_and_duplicate_json_is_unknown(raw):
    with pytest.raises(proof.Unproven):
        proof.validate(raw, acceptance={}, expected_scope='runtime', codex={}, transition={}, evidence={})

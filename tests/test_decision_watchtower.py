"""Optional real CLI integration; all WatchTower state stays in tmp_path."""
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from ccc_server.decision_delivery import AnswerOutbox
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_sources import owner_source_payload
from ccc_server.decision_watchtower import WatchTowerAnswers


def source_item(store):
    database = store.with_suffix('.db')
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return json.loads(db.execute('SELECT item_json FROM items WHERE ref=?',
                                         ('EXAMPLE-1',)).fetchone()[0])
    return json.loads(store.read_text())['items'][0]


@pytest.fixture
def integrated(tmp_path, monkeypatch):
    queue = pytest.importorskip('watchtower.queue')
    executable = shutil.which('wt')
    if not executable:
        pytest.skip('WatchTower is an optional integration')
    root = tmp_path / 'state'
    root.mkdir()
    env = dict(os.environ, WATCHTOWER_STORE=str(tmp_path / 'queue.json'),
               WATCHTOWER_CONFIG_FILE=str(tmp_path / 'config.json'),
               WATCHTOWER_DATA_DIR=str(tmp_path / 'wt'),
               WATCHTOWER_ACTIVITY_LOG=str(tmp_path / 'activity.log'))
    for key in ('WATCHTOWER_STORE', 'WATCHTOWER_CONFIG_FILE', 'WATCHTOWER_DATA_DIR', 'WATCHTOWER_ACTIVITY_LOG'):
        monkeypatch.setenv(key, env[key])
    (tmp_path / 'config.json').write_text('{}')

    def runner(args, **kwargs):
        return subprocess.run(args, env=env, **kwargs)

    probe = runner([executable, 'answer-conditional', '--help'], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip('Installed WatchTower predates the conditional answer contract')
    ticket = dict(number=1, seq=1, project='EXAMPLE', ref='EXAMPLE-1', status='in_progress',
                  needs_input=True, blocked_at='episode-1', block_kind='input',
                  block_question=' Choose a color? \n', history=[],
                  wait={'on': 'owner', 'block_at': 'episode-1', 'revision': 'wait-1'})
    store = tmp_path / 'queue.json'
    with queue._FileLock(queue._lock_path()):
        queue._save_unlocked({'counter': 1, 'items': [ticket]})
    (root / 'wt_items_snapshot.json').write_text(json.dumps({'items': [ticket]}))
    result = owner_source_payload(state_root=root, executable=executable, runner=runner)
    assert result['ok'], result
    projected = result['requests'][0]
    registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3')
    outbox = AnswerOutbox(registry)
    answer = outbox.record(projected['request_id'], expected_revision=projected['revision'],
                           text='Blue', idempotency_key='first-answer')
    def mutate(changes):
        with queue._FileLock(queue._lock_path()):
            data = queue._load_unlocked()
            data['items'][0].update(changes)
            queue._save_unlocked(data)

    def switch_store():
        data = queue._load_unlocked()
        other = tmp_path / 'other.json'
        monkeypatch.setenv('WATCHTOWER_STORE', str(other))
        env['WATCHTOWER_STORE'] = str(other)
        with queue._FileLock(queue._lock_path()):
            queue._save_unlocked(data)
        return other

    return outbox, answer, WatchTowerAnswers(executable, runner=runner), store, mutate, switch_store


def test_real_source_accepts_exact_snapshot_and_replays_once(integrated):
    outbox, answer, adapter, store, *_ = integrated
    result = outbox.dispatch(answer['answer_id'], adapter)
    assert result['state'] == 'accepted'
    assert result['receipt']['source_receipt']['request']['expected_block_question'] == ' Choose a color? \n'
    assert result['receipt']['source_receipt']['routing']['state'] == 'completed'
    assert result['receipt']['work_completed'] is False
    assert adapter.submit(answer)['status'] == 'accepted'
    assert adapter.lookup(answer)['status'] == 'accepted'
    ticket = source_item(store)
    assert not ticket['needs_input']
    assert sum(e['event'] == 'answer' for e in ticket['history']) == 1


@pytest.mark.parametrize('changed', [
    {'blocked_at': 'episode-2'}, {'block_question': 'Choose a color?'},
    {'wait': {'on': 'owner', 'block_at': 'episode-1', 'revision': 'wait-2'}},
    {'wait': {'on': 'external', 'block_at': 'episode-1', 'revision': 'wait-2'}},
])
def test_real_source_rejects_stale_episode_text_and_ownership(integrated, changed):
    outbox, answer, adapter, store, mutate, _ = integrated
    mutate(changed)
    before = source_item(store)
    result = outbox.dispatch(answer['answer_id'], adapter)
    assert result['state'] == 'rejected'
    assert result['receipt']['reason'] == 'conflict'
    assert source_item(store) == before
    assert source_item(store)['needs_input']
    assert not any(e['event'] == 'answer' for e in source_item(store)['history'])


def test_real_lost_response_reconciles_without_another_source_answer(integrated):
    outbox, answer, adapter, store, *_ = integrated
    submit = adapter.submit

    def lose_response(row):
        submit(row)
        raise TimeoutError('Simulated loss after real source accepted')

    adapter.submit = lose_response
    assert outbox.dispatch(answer['answer_id'], adapter)['state'] == 'unknown'
    before = source_item(store)
    assert outbox.reconcile(answer['answer_id'], adapter)['state'] == 'accepted'
    assert source_item(store) == before


def test_receipt_must_match_the_exact_source_request(integrated):
    _, answer, adapter, *_ = integrated
    receipt = adapter.submit(answer)['source_receipt']
    assert adapter._receipt(answer, dict(receipt, ref='EXAMPLE-2'))['status'] == 'unknown'
    assert adapter._receipt(answer, dict(receipt, idempotency_key='other'))['status'] == 'unknown'
    assert adapter._receipt(answer, dict(receipt, request={}))['status'] == 'unknown'
    changed = dict(receipt['request'], expected_store_id='another-store')
    assert adapter._receipt(answer, dict(receipt, request=changed))['status'] == 'unknown'


def test_real_receipt_lookup_refuses_copied_receipt_in_another_store(integrated):
    outbox, answer, adapter, _, _, switch = integrated
    outbox._claim(answer['answer_id'])
    assert adapter.submit(answer)['status'] == 'accepted'
    other = switch()
    before = source_item(other)
    assert adapter.lookup(answer)['status'] == 'unknown'
    assert outbox.reconcile(answer['answer_id'], adapter)['state'] == 'sending'
    assert source_item(other) == before

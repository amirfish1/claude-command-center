"""Advisory exact-session wake; durable inbox receipts remain authoritative."""
from ccc_server.decision_sessions import _uuid


def wake_session_answer(binding, *, inject=None):
    session_id = _uuid(binding['session_id'], 'session_id')
    answer_id = _uuid(binding['answer_id'], 'answer_id')
    if inject is None:
        from ccc_server.watchtower_msg import _inject_text_into_session
        inject = _inject_text_into_session
    message = (
        'An owner answer is saved for an explicitly promoted question in this session. '
        f'Run `ccc question inbox --session-id {session_id}` and locate answer_id {answer_id}. '
        'Verify the exact owning session, question and generation, read the saved answer, '
        'then acknowledge it with `ccc question consume` using that immutable identity '
        'and the actual source_message_id of this notification. Do not repeat work for an '
        'already consumed answer. Receipt does not mean the requested work is complete.'
    )
    result = inject(session_id, message, mode='send', same_session=True, no_queue=True,
                    skip_wt=True, wt_origin=True, source='decision-inbox',
                    idempotency_key='decision-answer-wake:' + answer_id)
    return bool(isinstance(result, dict) and result.get('ok') is True
                and result.get('same_session') is True and result.get('queued') is False
                and result.get('session_id') == session_id)

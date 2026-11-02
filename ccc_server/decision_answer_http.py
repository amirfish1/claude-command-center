"""Default-disabled composition for the explicit answer HTTP operations.

There is no background dispatch. Disabled requests do not initialize a registry
or discover a source executable. GET status never contacts WatchTower.
"""
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from ccc_server.decision_answer_api import AnswerAPIError, DecisionAnswerAPI, OPT_IN


def answer_payload(action, body=b'', *, answer_id=None, state_root=None, environ=None,
                   export_source=None, adapter=None):
    settings = os.environ if environ is None else environ
    if settings.get(OPT_IN) != '1':
        return {'ok': False, 'error': 'answers_disabled'}, 503
    try:
        from ccc_server import decision_inbox
        from ccc_server.decision_registry import DecisionRegistry
        root = Path(state_root) if state_root else decision_inbox._state_root()
        registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3', timeout=1)
        if action == 'record' and export_source is None:
            export_source = _export_source
        if action in ('send', 'reconcile') and adapter is None:
            from ccc_server.watchtower_msg import _wt_cli_path
            from ccc_server.decision_watchtower import WatchTowerAnswers
            executable = _wt_cli_path()
            adapter = WatchTowerAnswers(executable) if executable else None
        api = DecisionAnswerAPI(registry, export_source=export_source, adapter=adapter, environ=settings)
        if action == 'status':
            return api.status(answer_id), 200
        if action == 'record':
            return api.record(body), 200
        return api.act(action, body), 200
    except AnswerAPIError as error:
        return error.as_dict(), error.status
    except (OSError, sqlite3.Error):
        return {'ok': False, 'error': 'registry_unavailable'}, 503


def _export_source():
    from ccc_server.watchtower_msg import _wt_cli_path
    executable = _wt_cli_path()
    if not executable:
        raise ValueError('Source executable unavailable')
    process = subprocess.run([executable, 'owner-blocks-export', '--json'],
                             capture_output=True, text=True, timeout=10)
    if process.returncode:
        raise ValueError('Source export unavailable')
    return json.loads(process.stdout)

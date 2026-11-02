"""Installation-local answer opt-in shared by the read model and HTTP actions."""
import json
import os
from pathlib import Path

from ccc_server.decision_answer_api import OPT_IN


def answer_environment(state_root, environ=None):
    settings = dict(os.environ if environ is None else environ)
    # An explicit environment choice, including disablement, takes precedence.
    if OPT_IN in settings:
        return settings
    settings[OPT_IN] = '0'
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate answer setting')
                result[key] = value
            return result
        with (Path(state_root) / 'decision-inbox' / 'answer-settings.json').open() as stream:
            value = json.load(stream, object_pairs_hook=unique)
        if isinstance(value, dict) and set(value) == {'enabled'} and value['enabled'] is True:
            settings[OPT_IN] = '1'
    except (OSError, ValueError, TypeError):
        pass  # Missing, unreadable or malformed settings always disable answers.
    return settings

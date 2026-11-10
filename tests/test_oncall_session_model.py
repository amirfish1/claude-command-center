"""The real session proof's explicit model must pass spawn validation."""
from unittest.mock import patch
import server


def test_session_proof_model_allowed_without_changing_default():
    with patch.object(server, '_model_policy_error', return_value=None):
        model, error = server._validate_codex_model('gpt-6.1-terra')
    assert model == 'gpt-6.1-terra'
    assert error is None
    assert server._ENGINE_SUPPORTS_CUSTOM_MODELS['codex'] is False

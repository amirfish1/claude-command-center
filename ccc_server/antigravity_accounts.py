"""Per-process Antigravity authentication routing; persist identifiers only."""
from pathlib import Path
import json
import os
import sqlite3

_IDS = ('primary', 'secondary')
# Never copy credential material into settings, argv, bindings or diagnostics.
_CLOUD_ENV = frozenset((
    'AGY_ADC_AUTH', 'GOOGLE_APPLICATION_CREDENTIALS', 'GEMINI_API_KEY',
    'GOOGLE_API_KEY', 'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_CLOUD_PROJECT',
    'GOOGLE_CLOUD_LOCATION', 'GOOGLE_CLOUD_QUOTA_PROJECT', 'GCLOUD_PROJECT',
    'GCP_PROJECT', 'CLOUDSDK_CORE_PROJECT', 'GOOGLE_GEMINI_BASE_URL',
    'GOOGLE_VERTEX_BASE_URL', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY',
    'AGY_CDE_ENTERPRISE_AUTH', 'AGY_CDE_SEAMLESS_AUTH', 'AGY_CLI_CDE_AUTH_ACTION',
    'CLOUDSDK_AUTH_ACCESS_TOKEN', 'GOOGLE_OAUTH_ACCESS_TOKEN',
    'GOOGLE_CREDENTIALS', 'GOOGLE_SERVICE_ACCOUNT_AUTH', 'GOOGLE_GENAI_USE_ENTERPRISE',
    'AGY_ACP_ENABLE_GATEWAY_AUTH', 'AGY_LLM_GATEWAY_API_KEY',
    'AGY_LLM_GATEWAY_URL', 'AGY_LLM_GATEWAY_PROXY_URL',
    'AGY_LLM_GATEWAY_CA_CERT', 'AGY_LLM_GATEWAY_CLIENT_CERT',
    'AGY_LLM_GATEWAY_CLIENT_KEY', 'AGY_LLM_GATEWAY_MODELS',
    'AGY_LLM_GATEWAY_TIER_FLASH', 'AGY_LLM_GATEWAY_TIER_FLASH_LITE',
    'AGY_LLM_GATEWAY_TIER_PRO', 'AGY_LLM_GATEWAY_WIRE_PROTOCOL',
))


def _validate(account_id):
    if account_id not in _IDS:
        raise ValueError('Unknown Antigravity account; choose primary or secondary')
    return account_id


def _db():
    from ccc_server import core
    root = Path(core.COMMAND_CENTER_STATE_DIR) / 'antigravity-accounts'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = sqlite3.connect(root / 'bindings.sqlite3', timeout=10)
    con.execute('CREATE TABLE IF NOT EXISTS bindings (sid TEXT PRIMARY KEY, account_id TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS pending (log TEXT PRIMARY KEY, account_id TEXT NOT NULL)')
    return con


def configured():
    return [dict(id='primary', label='Primary · Google Cloud', current=True),
            dict(id='secondary', label='Secondary · Google OAuth', current=False)]


def bind(sid, account_id):
    _validate(account_id)
    if not sid:
        return
    with _db() as con:
        con.execute('INSERT OR IGNORE INTO bindings VALUES (?,?)', (str(sid), account_id))
        old = con.execute('SELECT account_id FROM bindings WHERE sid=?', (str(sid),)).fetchone()
        if old[0] != account_id:
            raise ValueError('Antigravity session is already bound to another account')


def bind_pending(log, account_id):
    _validate(account_id)
    with _db() as con:
        con.execute('INSERT OR REPLACE INTO pending VALUES (?,?)', (str(log), account_id))


def finalize_pending(log, sid):
    if not sid or not log:
        return
    with _db() as con:
        row = con.execute('SELECT account_id FROM pending WHERE log=?', (str(log),)).fetchone()
        if row:
            con.execute('INSERT OR IGNORE INTO bindings VALUES (?,?)', (str(sid), row[0]))
            old = con.execute('SELECT account_id FROM bindings WHERE sid=?', (str(sid),)).fetchone()
            if old[0] != row[0]:
                raise ValueError('Antigravity session is already bound to another account')
            con.execute('DELETE FROM pending WHERE log=?', (str(log),))


def session_account(sid):
    if not sid:
        return 'primary'
    with _db() as con:
        row = con.execute('SELECT account_id FROM bindings WHERE sid=?', (str(sid),)).fetchone()
    if row:
        return _validate(row[0])
    # Terminal and worker launches may acquire a native ID after their launch
    # response. Resolve only saved launch logs; never infer identity from a
    # credential profile or another conversation.
    from ccc_server import core
    with _db() as con:
        pending = con.execute('SELECT log FROM pending').fetchall()
    for (log,) in pending:
        for candidate in (str(log) + '.agy.log', str(Path(log).with_suffix('.agy.log')), log):
            meta = core._antigravity_cli_log_meta(candidate)
            if meta.get('session_id') == sid:
                finalize_pending(log, sid)
                return session_account(sid)
    return 'primary'


def public_payload(sid=''):
    selected = session_account(sid)
    rows = configured()
    return dict(ok=True, accounts=rows, account_id=selected,
                session_account=next(row['label'] for row in rows if row['id'] == selected))


def spawn_account(args):
    if 'antigravity_account_id' in args:
        selected = args['antigravity_account_id']
        if selected in ('', '__current__', 'primary'):
            return 'primary'
        return _validate(selected)
    return session_account(args.get('parent_session_id') or args.get('report_to'))


def pending_account(log):
    """Return the saved launch identity before its native session ID arrives."""
    if not log:
        return None
    with _db() as con:
        row = con.execute('SELECT account_id FROM pending WHERE log=?', (str(log),)).fetchone()
    return _validate(row[0]) if row else None


def dedupe_matches(args, sid, log=''):
    existing = session_account(sid) if sid else pending_account(log) if log else 'primary'
    return spawn_account(args) == existing


def launch_settings(account_id, model=None):
    """Return supported, credential-free CLI settings without global writes.

    auth.type belongs to ACP and is rejected by CLI 1.5.8. Authentication
    isolation instead uses launch_env's credential scope and config directory.
    """
    from ccc_server import core
    _validate(account_id)
    if account_id == 'secondary':
        overlay = {'gcp': {'project': '', 'location': ''}, 'modelProvider': ''}
    else:
        settings, error = core._read_antigravity_cli_settings()
        if error:
            raise ValueError('Cannot read Antigravity authentication settings')
        # A Gemini key selects that route only with an explicit provider.
        # Preserve GCP business/ADC selection and its existing environment.
        overlay = {'modelProvider': settings.get('modelProvider') or ''}
        gcp = settings.get('gcp') or {}
        overlay['gcp'] = {key: gcp.get(key, '') for key in ('project', 'location')}
    if model:
        overlay['model'] = model
    return overlay


def _secondary_home(primary_home=None):
    from ccc_server import core
    root = Path(core.COMMAND_CENTER_STATE_DIR) / 'antigravity-accounts' / 'secondary-home'
    cli = root / '.gemini' / 'antigravity-cli'
    cli.mkdir(mode=0o700, parents=True, exist_ok=True)
    # CLI 1.5.8 ignores CONFIG_REL_DIR in its normal entry point. HOME is the
    # fallback boundary for file-backed tokens; share only conversation data.
    for name, target in (('conversations', core.ANTIGRAVITY_CLI_CONVERSATIONS),
                         ('brain', core.ANTIGRAVITY_CLI_BRAIN)):
        target = Path(target).resolve()
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        link = cli / name
        try:
            link.symlink_to(target, target_is_directory=True)
        except FileExistsError:
            if not link.is_symlink() or link.resolve() != target:
                raise ValueError('Antigravity secondary conversation storage conflicts')
    target = (Path(primary_home or Path.home()) / '.watchtower').resolve()
    link = root / '.watchtower'
    try:
        link.symlink_to(target, target_is_directory=True)
    except FileExistsError:
        if not link.is_symlink() or link.resolve() != target:
            raise ValueError('Antigravity secondary queue storage conflicts')
    return root


def launch_env(account_id, env=None):
    _validate(account_id)
    result = dict(os.environ if env is None else env)
    from ccc_server import core
    # Keep only routing selectors for a nested tool to restore Primary.
    # Credentials themselves never enter this JSON marker.
    secondary_home = Path(core.COMMAND_CENTER_STATE_DIR) / 'antigravity-accounts' / 'secondary-home'
    known_secondary = (
        result.get('CCC_ANTIGRAVITY_ACCOUNT_ID') == 'secondary'
        and result.get('ANTIGRAVITY_CREDENTIAL_SCOPE') == 'ccc-antigravity-secondary'
        and result.get('HOME') == str(secondary_home)
    )
    if not known_secondary:
        selectors = {}
        for key in ('ANTIGRAVITY_CREDENTIAL_SCOPE', 'ANTIGRAVITY_CONFIG_REL_DIR',
                    'ANTIGRAVITY_GEMINI_DIR', 'XDG_CONFIG_HOME'):
            if key in result:
                selectors[key] = result[key]
        for key in ('AGY_ADC_AUTH', 'GOOGLE_GENAI_USE_VERTEXAI'):
            if result.get(key, '').lower() in ('true', 'false', '1', '0', 'yes', 'no'):
                selectors[key] = result[key]
        adc_path = result.get('GOOGLE_APPLICATION_CREDENTIALS', '')
        if adc_path and Path(adc_path).is_absolute():
            selectors['GOOGLE_APPLICATION_CREDENTIALS'] = adc_path
        result['CCC_ANTIGRAVITY_PRIMARY_SELECTORS'] = json.dumps(selectors, separators=(',', ':'))
        required = sorted(key for key, value in result.items() if value and (
            key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY',
                    'OPENAI_API_KEY', 'CLOUDSDK_AUTH_ACCESS_TOKEN',
                    'GOOGLE_OAUTH_ACCESS_TOKEN', 'GOOGLE_CREDENTIALS',
                    'GOOGLE_SERVICE_ACCOUNT_AUTH')
            or key.startswith('AGY_LLM_GATEWAY_')
        ))
        result['CCC_ANTIGRAVITY_PRIMARY_REQUIRED_ENV'] = json.dumps(required, separators=(',', ':'))
        result['CCC_ANTIGRAVITY_PRIMARY_HOME'] = result.get('HOME') or str(Path.home())
    result['CCC_ANTIGRAVITY_ACCOUNT_ID'] = account_id
    result['CCC_ANTIGRAVITY_LS_DIR'] = str(Path(core.COMMAND_CENTER_STATE_DIR) / 'agy-ls')
    if not result.get('CCC_SERVER'):
        try:
            url = (Path.home() / '.claude' / 'command-center' / 'port.txt').read_text().strip()
            if url.startswith(('http://', 'https://')):
                result['CCC_SERVER'] = url
        except OSError:
            pass
    if account_id == 'secondary':
        for key in list(result):
            if key in _CLOUD_ENV or key.startswith('AGY_LLM_GATEWAY_'):
                result.pop(key, None)
        # Overrides enableAdc in installations that persist it in config.json.
        result['AGY_ADC_AUTH'] = 'false'
        # Scope separates native keyring identity; HOME separates token-file
        # fallback. Config overrides are also forwarded to native workers.
        result['ANTIGRAVITY_CREDENTIAL_SCOPE'] = 'ccc-antigravity-secondary'
        result['ANTIGRAVITY_CONFIG_REL_DIR'] = 'ccc-antigravity-secondary/config'
        primary_home = Path(result['CCC_ANTIGRAVITY_PRIMARY_HOME'])
        xdg_data = result.get('XDG_DATA_HOME', '')
        native_data = ((Path(xdg_data) if xdg_data and Path(xdg_data).is_absolute()
                        else primary_home / '.local' / 'share') / 'watchtower')
        result.setdefault('WATCHTOWER_DATA_DIR', str(native_data))
        if not result.get('WATCHTOWER_CONFIG_FILE'):
            if 'WATCHTOWER_DATA_DIR' in (os.environ if env is None else env):
                result['WATCHTOWER_CONFIG_FILE'] = str(Path(result['WATCHTOWER_DATA_DIR']).expanduser() / 'queue-config.json')
            else:
                result['WATCHTOWER_CONFIG_FILE'] = str(core._wt_config_path())
        result.setdefault('WATCHTOWER_CCC_SPAWN_DEFAULTS_FILE', str(core.SPAWN_DEFAULTS_FILE))
        result.setdefault('WATCHTOWER_MODEL_POLICY_FILE', str(Path(core.COMMAND_CENTER_STATE_DIR) / 'model-policy.json'))
        if result.get('CCC_SERVER'):
            result.setdefault('WATCHTOWER_DELEGATE_URL', result['CCC_SERVER'])
        result['HOME'] = str(_secondary_home(primary_home))
        result.pop('ANTIGRAVITY_GEMINI_DIR', None)
        result.pop('GEMINI_DIR', None)
        result.pop('XDG_CONFIG_HOME', None)
    return result


def configure_command(cmd, account_id, model=None):
    """Reserve settings for the authoritative per-process auth overlay."""
    if any(word == '--settings' or word.startswith('--settings=') for word in cmd[1:]):
        raise ValueError('Use CCC account selection instead of custom Antigravity --settings')
    settings = launch_settings(account_id, model)
    return list(cmd) + ['--settings', json.dumps(settings, separators=(',', ':'))]

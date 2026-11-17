"""Bounded, per-transcript usage reads for conditional-send preflight."""
import json
from pathlib import Path

from ccc_server import core
from ccc_server.bounded_cache import ByteBudgetCache

_CACHE = ByteBudgetCache(1 << 20, 256)
_TAIL_BYTES = 1 << 20


def snapshot(engine, path):
    """Return usage and actual file mtime, or an explicit missing value.

    Never read a whole transcript or mistake absent usage for zero context.
    Cache by file identity, nanosecond mtime and size, including misses.
    """
    try:
        source = Path(path)
        st = source.stat()
    except (OSError, TypeError):
        return None
    key = (engine, str(source), st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return dict(cached)
    result = {'tokens': None, 'mtime': st.st_mtime}
    try:
        with source.open('rb') as stream:
            offset = max(0, st.st_size - _TAIL_BYTES)
            stream.seek(offset)
            lines = stream.read(min(st.st_size, _TAIL_BYTES)).splitlines()
        if offset:
            lines = lines[1:]  # do not parse a partial leading record
        for line in reversed(lines):
            # Tool results and prose dominate large tails; avoid decoding
            # their bodies when they cannot contain a usage-bearing event.
            if not any(word in line for word in
                       (b'"usage"', b'"token_count"', b'"compact_boundary"',
                        b'"compacted"', b'"local_command"', b'"token_usage"', b'"tokens_in"')):
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
                tokens = None
                if engine == 'claude':
                    message = event.get('message') or {}
                    if (event.get('type') == 'assistant' and isinstance(message.get('usage'), dict)
                            and any(k in message['usage'] for k in
                                    ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))):
                        usage = message['usage']
                        tokens = sum(int(usage.get(k) or 0) for k in
                                     ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
                        result['model'] = str(message.get('model') or '')[:200]
                    elif event.get('type') == 'system':
                        if event.get('subtype') == 'compact_boundary':
                            post = (event.get('compactMetadata') or {}).get('postTokens')
                            tokens = int(post) if post is not None else None
                            result['tokens'] = tokens
                            break  # never reuse pre-compaction usage
                        if event.get('subtype') == 'local_command':
                            usage = core._local_command_context_usage(event)
                            tokens = usage['tokens'] if usage else None
                elif engine == 'codex':
                    if event.get('type') == 'compacted':
                        break  # no post-compaction usage in this bounded tail
                    usage = core._codex_token_usage_from_event(event)
                    if usage:
                        tokens = int(usage.get('input_tokens') or usage.get('total_tokens') or 0)
                elif engine == 'kimi' and event.get('type') == 'assistant':
                    usage = event.get('token_usage')
                    if isinstance(usage, dict):
                        tokens = sum(int(usage.get(k) or 0) for k in
                                     ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
                    elif isinstance(event.get('tokens_in'), (int, float)):
                        tokens = int(event['tokens_in'])
                if tokens is not None:
                    result['tokens'] = max(0, tokens)
                    break
            except (ValueError, TypeError, AttributeError):
                continue
    except OSError:
        return None
    _CACHE.put(key, result, 1024 + len(str(source)) * 4)
    return dict(result)

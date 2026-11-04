#!/usr/bin/env python3
"""Validate marketplace contributions offline, using the installer's rules."""
import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from ccc_server.app_marketplace import (  # noqa: E402
    MAX_BYTES, manifest, no_links, strict_json, tree_digest, validate_index,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', nargs='?', type=Path, default=REPO / 'apps-index.json')
    parser.add_argument('--remote', action='store_true', help='Require HTTPS Git sources only')
    parser.add_argument('--package', type=Path, help='Also validate a local package without installing it')
    args = parser.parse_args(argv)
    try:
        if args.index.stat().st_size > 256 * 1024:
            raise ValueError('Index exceeds 256 KiB')
        rows = validate_index(strict_json(args.index.read_text()), remote=args.remote)
        for row in rows:
            if row['source'].startswith('https://'):
                continue  # Offline validation never clones contributor code.
            folder = no_links(args.index.resolve().parent / row['source'])
            tree_digest(folder)
            meta = manifest(folder)
            if (meta['id'] != row['id'] or meta['label'] != row['name']
                    or meta['permissions'] != row['permissions']):
                raise ValueError('Listing does not match package: ' + row['id'])
            # The isolation marker must also fit under the install size budget.
            if sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()) + 17 > MAX_BYTES:
                raise ValueError('Package leaves no room for isolation marker')
        if args.package:
            folder = no_links(args.package)
            tree_digest(folder)
            meta = manifest(folder)
            print('Valid package: ' + meta['id'])
        print(f'Valid index: {len(rows)} listings (HTTPS sources were not fetched)')
        return 0
    except (ValueError, OSError, RecursionError) as exc:
        print('Invalid marketplace contribution: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate marketplace contributions offline, using the installer's rules."""
import argparse
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from ccc_server.app_marketplace import (  # noqa: E402
    Marketplace, no_links, strict_json, validate_index,
)


def check_package(folder):
    # Exercise actual staging/manifest/marker limits without installing anything.
    with tempfile.TemporaryDirectory(prefix='ccc-app-validation-') as temporary:
        market = Marketplace(temporary, REPO)
        return market.prepare(str(no_links(folder)))


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
            meta = check_package(folder)
            if (meta['id'] != row['id'] or meta['label'] != row['name']
                    or meta['permissions'] != row['permissions']):
                raise ValueError('Listing does not match package: ' + row['id'])
        if args.package:
            folder = no_links(args.package)
            meta = check_package(folder)
            print('Valid package: ' + meta['id'])
        print(f'Valid index: {len(rows)} listings (HTTPS sources were not fetched)')
        return 0
    except (ValueError, OSError, RecursionError) as exc:
        print('Invalid marketplace contribution: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a complete immutable image; never start it or provision runtime trust.

The base MUST already exist locally at its registry digest. --revision must be
a committed full SHA; uncommitted/ignored checkout files cannot enter the image.
No network, Dockerfile RUN, worker, daemon or model is started. Image import and
temporary stopped export containers use the operator's root Docker trust base.

--prune-base-link explicitly removes a dangling base-image link (common in slim
distribution documentation). Missing source links are always refused. Inspect
each prune and retain the resulting manifest with the proof packet.

Output is NOT acceptance: P6 must execute this exact artifact with exact measured
profiles, including initial/final PID/start observations. Keep the restart
refusal until the coordinator explicitly accepts those proofs. Authority trust
requires a separately provisioned root-owned ccc-root-authority-v1 anchor with
image_id, executed_build_sha256 and execution_profile_sha256. Determine the last
from root Docker's actual inspect using authority_profile_sha256 on the exact
authority_config launch (request bytes and generated hostname are excluded).
Never derive trust from image labels or a worker-supplied digest.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccc_server.execution_artifact import build_artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prune-base-link", action="append", default=[])
    parser.add_argument("--dependency", action="append", nargs=3, default=[],
                        metavar=("PACKAGE", "REPO", "REVISION"),
                        help="include a pinned committed package; no host installation")
    args = parser.parse_args()
    artifact = build_artifact(args.base, args.repo, args.revision,
                              prune_base_links=args.prune_base_link, dependencies=args.dependency)
    # Exclusive create prevents clobbering a previous proof/build manifest.
    with args.manifest.open("xb") as stream:
        stream.write(artifact.manifest)
    print(json.dumps({"image_id": artifact.image_id,
                      "executed_build_sha256": artifact.executed_build_sha256,
                      "source_revision": args.revision,
                      "dependencies": [{"package": name, "revision": revision}
                                       for name, _, revision in args.dependency],
                      "pruned_base_links": sorted(args.prune_base_link)}, sort_keys=True))


if __name__ == "__main__":
    main()

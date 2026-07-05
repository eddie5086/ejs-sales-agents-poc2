"""Run a pipeline locally from its YAML config.

    python -m poc2.run pipelines/demo.yaml [--batch-id B] [--account-id A]
                                           [--payload '{"company": "Acme"}']

Local-first: in-memory state store, no AWS resources touched. The AWS-backed
implementations activate via config in later phases.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import poc2.pipeline.strategies  # noqa: F401 — populates the registry
from poc2.pipeline.engine import Engine
from poc2.pipeline.schema import ConfigError, load_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", help="path to a pipeline YAML")
    parser.add_argument("--batch-id", default="local-batch")
    parser.add_argument("--account-id", default="demo-account")
    parser.add_argument("--payload", default='{"company": "Acme"}',
                        help="JSON run payload")
    args = parser.parse_args(argv)

    try:
        config = load_pipeline(args.pipeline, base_dir=Path.cwd())
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 1

    engine = Engine(config)
    result = engine.run(args.batch_id, args.account_id, json.loads(args.payload))

    print(f"pipeline={result.pipeline} batch={result.batch_id} "
          f"account={result.account_id} state={engine.state.backend}")
    for stage_id, output in result.outputs.items():
        print(f"  {stage_id}: {json.dumps(output, default=str)}")
    print(f"computed={result.computed} cached={result.cached}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

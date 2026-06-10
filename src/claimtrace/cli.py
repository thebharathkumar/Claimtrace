"""CLI entry points.

  claimtrace run      run the pipeline on the seed dataset (or a manifest dir)
  claimtrace serve    serve the viewer and API
  claimtrace demo     run on seed data, then serve (one-command acceptance)
  claimtrace verify   verify the audit chain from the command line
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import audit, config
from .pipeline.graph import run_pipeline, seed_specs
from .store import connect


def _run(args: argparse.Namespace) -> int:
    seed = Path(args.data) if args.data else None
    specs = seed_specs(seed)
    conn = connect(config.db_path())
    try:
        result = run_pipeline(conn, specs)
    finally:
        conn.close()
    print(f"run {result['run_id']} complete (mode: {result['mode']})")
    print(f"  chunks: {result['chunks']}  claims: {result['claims']}")
    contradictions = [c for c in result["checks"] if c["status"] == "contradiction"]
    for c in contradictions:
        values = " vs ".join(v["value"] for v in c["detail"]["values"])
        print(f"  CONTRADICTION {c['claim_type']}: {values}")
    v = result["verification"]
    print(f"  verifier: {v['verified_sentences']}/{v['factual_sentences']} factual sentences backed,"
          f" {len(v['flagged'])} flagged, chain {'intact' if v['chain_ok'] else 'BROKEN'}")
    print(f"  agent-triage export: {'sent' if result['triage_export'] else 'skipped (no collector)'}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("claimtrace.server:app", host=args.host, port=args.port)
    return 0


def _demo(args: argparse.Namespace) -> int:
    rc = _run(args)
    if rc != 0:
        return rc
    print(f"\nviewer: http://{args.host}:{args.port}/")
    return _serve(args)


def _verify(args: argparse.Namespace) -> int:
    conn = connect(config.db_path())
    try:
        result = audit.verify_chain(conn)
    finally:
        conn.close()
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="claimtrace", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the pipeline")
    p_run.add_argument("--data", help="directory with manifest.json plus source files")
    p_run.set_defaults(func=_run)

    p_serve = sub.add_parser("serve", help="serve viewer and API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8400)
    p_serve.set_defaults(func=_serve)

    p_demo = sub.add_parser("demo", help="run on seed data then serve")
    p_demo.add_argument("--data", help="directory with manifest.json plus source files")
    p_demo.add_argument("--host", default="127.0.0.1")
    p_demo.add_argument("--port", type=int, default=8400)
    p_demo.set_defaults(func=_demo)

    p_verify = sub.add_parser("verify", help="verify the audit chain")
    p_verify.set_defaults(func=_verify)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

"""Run an eval suite in this process: no API, no database (``make evals``).

    uv run python -m vitrina_evals --suite conversations_es [--live | --record] [--sample N]
        [--out DIR]

Replay, the default, needs no key (AS-7.3). ``--record`` calls the live providers like
``--live`` and stores every answer as a cassette under CASSETTE_DIR, for replay later (T055). Exit status 1 when the run fails, which an
invented price does (AS-7.2); 0 otherwise. An error run (no agent yet, missing cassettes)
prints a GitHub warning annotation so CI shows it without blocking the build.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from vitrina_core.ledger import InMemoryLedger
from vitrina_core.providers.compose import ProviderMode, Providers, compose
from vitrina_core.settings import get_settings
from vitrina_evals.schemas import EvalSuite
from vitrina_evals.suites import current_git_sha, execute


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vitrina_evals", description="Run an eval suite."
    )
    parser.add_argument("--suite", choices=get_args(EvalSuite), default="conversations_es")
    calls = parser.add_mutually_exclusive_group()
    calls.add_argument("--live", action="store_true", help="call the live providers (needs keys)")
    calls.add_argument(
        "--record", action="store_true", help="like --live, and store each answer as a cassette"
    )
    parser.add_argument("--sample", type=int, help="run only the first N cases")
    parser.add_argument(
        "--case", action="append", dest="cases", help="run only this case (repeatable)"
    )
    parser.add_argument("--out", type=Path, help="also write the result as JSON in this folder")
    args = parser.parse_args(argv)

    settings = get_settings()
    live: Providers | None = None
    if args.live or args.record:
        from vitrina_meta_model_api import live_providers

        mode = ProviderMode.record if args.record else ProviderMode.live
        live = compose(settings, InMemoryLedger(), lambda: live_providers(settings), mode=mode)
    replay = live is None
    result = asyncio.run(
        execute(
            args.suite,
            replay=replay,
            settings=settings,
            live=live,
            sample=args.sample,
            cases=args.cases,
        )
    )
    report = {
        "suite": args.suite,
        "replay": replay,
        "status": result.status,
        "notes": result.notes,
        "git_sha": current_git_sha(),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "metrics": result.metrics.model_dump(mode="json"),
        "cases": [score.result().model_dump(mode="json") for score in result.cases],
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    sys.stdout.write(text + "\n")
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = report["finished_at"].replace(":", "").replace("-", "")
        (args.out / f"{args.suite}-{stamp}.json").write_text(text + "\n", encoding="utf-8")
    if result.status == "error":
        sys.stdout.write(f"::warning title=evals {args.suite}::{result.notes}\n")
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

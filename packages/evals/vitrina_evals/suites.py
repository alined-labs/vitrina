"""Which suites run and with which providers (FR-070, ADR-0010).

``execute`` is what both the worker task and the CLI call. Replay runs build their own
replay providers, so they need no key even inside a live process (AS-7.3); live runs use
the process's providers. Either way every call also reaches the run's own ledger, which
gives ``cost_usd_total``.
"""

import os
import subprocess
from collections.abc import Sequence

from vitrina_core.ledger import (
    InMemoryLedger,
    LedgeredChatModel,
    LedgeredEmbedder,
    LedgeredImageModel,
    LedgeredSegmenter,
    LedgeredTranscriber,
    ModelCallLedger,
)
from vitrina_core.providers.compose import ProviderMode, Providers, compose
from vitrina_core.settings import REPO_ROOT, Settings
from vitrina_evals.agent import conversation_agent
from vitrina_evals.cases import load_cases
from vitrina_evals.consent import load_consent_cases, run_consent_scheduler
from vitrina_evals.price_suite import run_price_validator
from vitrina_evals.runner import Agent, SuiteResult, run_conversations
from vitrina_evals.schemas import EvalMetrics, EvalSuite

# Suites in the contract that later tasks build.
PLANNED = {"fidelity": "T072"}


def tracked(providers: Providers, ledger: ModelCallLedger) -> Providers:
    """The same providers, each call also recorded in ``ledger``."""
    return Providers(
        mode=providers.mode,
        chat=LedgeredChatModel(providers.chat, ledger),
        embedder=LedgeredEmbedder(providers.embedder, ledger),
        image=LedgeredImageModel(providers.image, ledger),
        segmenter=LedgeredSegmenter(providers.segmenter, ledger),
        transcriber=LedgeredTranscriber(providers.transcriber, ledger),
    )


async def execute(
    suite: EvalSuite,
    *,
    replay: bool,
    settings: Settings,
    live: Providers | None = None,
    sample: int | None = None,
    cases: Sequence[str] | None = None,
    agent: Agent | None = None,
) -> SuiteResult:
    if suite == "consent_scheduler":
        return run_consent_scheduler(
            load_consent_cases()[:sample] if sample else load_consent_cases()
        )
    if suite == "price_validator":
        return await run_price_validator()
    if suite != "conversations_es":
        return SuiteResult(
            "error", EvalMetrics(), [], f"the {suite} suite arrives with {PLANNED[suite]}"
        )
    ledger = InMemoryLedger()
    if replay:
        providers = compose(settings, ledger, mode=ProviderMode.replay)
    elif live is not None:
        providers = tracked(live, ledger)
    else:
        return SuiteResult("error", EvalMetrics(), [], "a live run needs the process's providers")
    return await run_conversations(
        load_cases(sample=sample, only=cases),
        agent=agent if agent is not None else conversation_agent(settings),
        providers=providers,
        ledger=ledger,
    )


def current_git_sha() -> str | None:
    """The commit the run evaluated: CI's GITHUB_SHA, else the checkout's HEAD."""
    if sha := os.environ.get("GITHUB_SHA"):
        return sha[:12]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None

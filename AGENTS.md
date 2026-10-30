# AGENTS.md — how coding agents work in this repository

Vitrina is built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026) by one human, Daniel, with coding-agent sessions. This file is the standing instruction for every agent session in this repo. Each session works on the modules its instructions name; everything needed to run or understand the product is in this repository.

## Read before you write

1. `constitution.md` — eight non-negotiable articles. A spec that conflicts with an article is wrong, not the article.
2. `specs/001-vitrina-api/spec.md` (scenarios `AS-x.y`, requirements `FR-xxx`), `plan.md` (§3 module split and development resource rules, §4 design), `data-model.md`, `tasks.md`.
3. `specs/001-vitrina-api/contracts/openapi.yaml` and `contracts/agent-tools.json` — the source of truth. Frozen. Never edit them (Art. VI).

## Modules (plan.md §3)

A module talks to another only through its public interface (`packages/<name>/api.py` or the HTTP contract), never through its tables (Art. VI.3). Change only the modules your task names; when you need something from another module, ask for it through its interface instead of writing into its package.

## Workflow

- **Branch per task** from `main`, named after the task: `t070-weekly-brief`, `fix-campaign-cutoff`. Rebase on `main` before opening the PR.
- **Spec → tests → code.** Write the failing tests first, named after the scenarios: `test_as_3_4_price_validator_rejects_uncited_amount`. A task is done when its scenarios pass, `make check` passes, and the PR cites the task and scenario IDs.
- **`make check` before every commit**: ruff, mypy (strict on `core`), `pytest -n 2` (never `-n auto`), contract drift check, and the public-text check (`make public-text`).
- **One-time per clone:** `git config core.hooksPath .githooks`. The versioned `commit-msg` hook rejects any message that breaks the next two rules.
- **Commits: Conventional Commits with a bullet-point body.** Subject `type(scope): summary`, at most 72 characters; types `feat fix chore docs test refactor ci perf`; scope is the package or app (`core`, `catalog`, `conversation`, `creative`, `compliance`, `learning`, `evals`, `api`, `worker`, `dashboard`, `providers`, `repo`). Body: one bullet per change, then a last line `Tasks: T025 · Scenarios: AS-3.3, AS-3.4`.
- **No AI attribution of any kind.** No `Co-Authored-By` trailers, no "Generated with …" lines, no tool names, in commit messages or pull request descriptions. Commits are authored by the repository account only.
- **Public text is about the product.** Files, commit messages, PR titles and descriptions, and branch names describe the product and its tasks; they never mention the machines, sessions or private notes used to build it.
- **`main` is protected**: no direct pushes, linear history, squash merges with the PR title as subject and the PR body as the bullets. Only the maintainer merges. Open the PR with the template, then start the next task from `main`; do not wait for the merge.
- **Contracts are frozen.** If a contract or spec is wrong, stop that task and raise it with Daniel; the change then goes spec → contract → tag → code.
- **Never invent** model IDs, prices, limits or statistics. Anything unknown is configuration with a documented default.

## Tooling and resources

- Python 3.14 with **uv** (`uv sync`, `uv run …`); Node with **pnpm** for `apps/dashboard` (never npm or yarn); **Docker** only for tooling images (cloudflared, ffmpeg), never for a local Postgres (plan §3): development uses the hosted Supabase project with its own `dev` schema per environment.
- Secrets live in `.env` (gitignored); `.env.example` lists the names with placeholders. No personal data and no real phone numbers anywhere, fixtures included; synthetic numbers are `+57300000xxxx` (Art. V).
- One long-lived dev process (API **or** worker) per environment while agent sessions run; `pytest -n 2`.
- Every external call has a timeout, retries for `429`/`5xx`, and a recorded fallback (Art. VIII). Customer text never goes to a model in data-sharing mode (Art. V.2).
- **Dashboard UI:** read `apps/dashboard/DESIGN.md` first. Pages use its tokens and component classes; `pnpm run check` rejects Tailwind palette classes and raw colors.

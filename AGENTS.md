# AGENTS.md — how coding agents work in this repository

Vitrina is built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026) by one human, Daniel, and coding-agent sessions on three machines: **MX0**, **MX1** and **MX2** (MX3 is reserved). This file is the standing instruction for every agent session in this repo. Coordination state lives in the private sibling repo `vitrina-brain` (`../vitrina-brain`); nothing in it is needed to run or understand the product.

## Read before you write

1. `constitution.md` — eight non-negotiable articles. A spec that conflicts with an article is wrong, not the article.
2. `specs/001-vitrina-api/spec.md` (scenarios `AS-x.y`, requirements `FR-xxx`), `plan.md` (§3 ownership and resource rules, §4 design), `data-model.md`, `tasks.md` (your tasks carry your machine tag `[MX0]`, `[MX1]`, `[MX2]`).
3. `specs/001-vitrina-api/contracts/openapi.yaml` and `contracts/agent-tools.json` — the source of truth. Frozen. Never edit them (Art. VI).
4. In `../vitrina-brain`: `STATUS.md`, `machines/<MX>/STATUS.md`, `machines/<MX>/PROMPT.md`, `coordination/integration-points.md`, `coordination/blockers.md`, `research/OPEN.md` (defaults in force for everything still unknown).

## Machines and lanes (plan.md §3)

| Machine | Owns | Never touches |
| --- | --- | --- |
| MX0 | `packages/core`, `packages/catalog`, `packages/evals`, later `packages/compliance`, `packages/learning`; routers `sellers catalog evals replay jobs health contacts analytics briefs reactivation`; CI, deploy, the workspace scaffold | conversation, creative, dashboard |
| MX2 | `packages/conversation`, `providers/whatsapp`; routers `webhooks conversations agent`; the Cloudflare Tunnel (the WhatsApp number always points at MX2) | core, catalog, creative, compliance, learning, dashboard |
| MX1 | `packages/creative`, `providers/meta_model_api` (image, SAM); routers `assets creative ads public`; `apps/dashboard` and the generated TypeScript client. MX1 also reviews and merges every pull request | core, catalog, conversation, compliance, learning |

A module talks to another only through its public interface (`packages/<name>/api.py` or the HTTP contract), never through its tables (Art. VI.3). If you need something from another machine, ask through a brain handoff; do not write into its package.

## Workflow

- **Branch per task** from `main`: `mx0/t002-scaffold`, `mx2/t012-whatsapp-provider`, `mx1/t013-ts-client`. Rebase on `main` before opening the PR.
- **Spec → tests → code.** Write the failing tests first, named after the scenarios: `test_as_3_4_price_validator_rejects_uncited_amount`. A task is done when its scenarios pass, `make check` passes, and the PR cites the task and scenario IDs.
- **`make check` before every commit**: ruff, mypy (strict on `core`), `pytest -n 2` (never `-n auto`), contract drift check. `pytest -m "not live"` must pass with no credentials (replay mode, NFR-07).
- **One-time per clone:** `git config core.hooksPath .githooks`. The versioned `commit-msg` hook rejects any message that breaks the next two rules.
- **Commits: Conventional Commits with a bullet-point body.** Subject `type(scope): summary`, at most 72 characters; types `feat fix chore docs test refactor ci perf`; scope is the package or app (`core`, `catalog`, `conversation`, `creative`, `compliance`, `learning`, `evals`, `api`, `worker`, `dashboard`, `providers`, `repo`). Body: one bullet per change, then a last line `Tasks: T025 · Scenarios: AS-3.3, AS-3.4`.
- **No AI attribution of any kind.** No `Co-Authored-By` trailers, no "Generated with …" lines, no tool names, in commit messages or pull request descriptions. Commits are authored by the repository account only.
- **`main` is protected**: no direct pushes, linear history, squash merges with the PR title as subject and the PR body as the bullets. **Only MX1 merges.** Open the PR with the template, then start the next task from `main`; do not wait for the merge.
- **Contracts are frozen.** If a contract or spec is wrong: stop that task, append a row to `../vitrina-brain/spec/AMENDMENTS.md` and a line to `coordination/blockers.md`, and move to another task. Daniel decides; the change then goes spec → contract → tag → code.
- **Never invent** model IDs, prices, limits or statistics. Anything unknown is configuration plus a question in `../vitrina-brain/research/OPEN.md`.

## Tooling and resources

- Python 3.12 with **uv** (`uv sync`, `uv run …`); Node with **pnpm** for `apps/dashboard` (never npm or yarn); **Docker** only for tooling images (cloudflared, ffmpeg), never for a local Postgres on the 8 GB machines (plan §3): every machine uses the hosted Supabase project with its own schema `dev_mx0`, `dev_mx1`, `dev_mx2`.
- Secrets live in `.env` (gitignored); `.env.example` lists the names with placeholders. No personal data and no real phone numbers anywhere, fixtures included; synthetic numbers are `+57300000xxxx` (Art. V).
- One long-lived dev process (API **or** worker) per machine while agent sessions run; at most 2 concurrent agent sessions on MX0 and MX2, 3 on MX1; cassette-based evals run on MX0 only.
- Every external call has a timeout, retries for `429`/`5xx`, and a recorded fallback (Art. VIII). Customer text never goes to a model in data-sharing mode (Art. V.2).

## The brain ritual (every session start, after every task)

Start of session:

```sh
cd ../vitrina-brain && export BRAIN_MACHINE=<MX> && scripts/brain sync
cat STATUS.md machines/<MX>/STATUS.md coordination/integration-points.md coordination/blockers.md
scripts/brain log <MX>        # today's journal; write as you go
cd ../vitrina && git fetch origin && git checkout main && git pull --ff-only
git config core.hooksPath .githooks   # once per clone: commit-msg rules
```

After every task (and before the session ends):

```sh
cd ../vitrina-brain && scripts/brain sync
# journal: done / learned / broke / tomorrow  →  machines/<MX>/journal/<today>.md
# lane:    machines/<MX>/STATUS.md  (health, tasks, blockers, next_integration, sessions_running, dev_process, updated)
scripts/brain handoff <MX> "<what is ready>" <consumers>   # only when another machine depends on it
scripts/brain status && scripts/brain check
git add -A && git commit -m "[<MX>] status: <summary>" && scripts/brain sync
```

Blocked? Append one line to `coordination/blockers.md`, set your lane health to `yellow` or `red`, and move to the next task. Never wait silently.

# Vitrina — Spec-Driven Development kit

Drop this folder at the root of the `vitrina` repository. It follows the Spec Kit layout (constitution → spec → plan → tasks, with machine-readable contracts) so coding agents can implement against a frozen contract in parallel.

```
constitution.md                          # Non-negotiable rules (8 articles). Specs may not override it.
specs/001-vitrina-api/
  spec.md                                # WHAT: actors, user scenarios (AS-x.y), functional reqs (FR-xxx), NFRs, success criteria, open questions
  plan.md                                # HOW: stack, project structure, 4-machine ownership, design decisions, phases by day, risks
  data-model.md                          # Tables, state machines, invariants, event payloads, retention
  tasks.md                               # T001–T093 with owner machine, phase and the scenarios each task must make green
  contracts/
    openapi.yaml                         # OpenAPI 3.1 — the HTTP contract (validated)
    agent-tools.json                     # Tool-calling + structured-output contract for the Muse Spark sales agent, plus engine rules R1–R12
```

## How to work with it

1. **Read in order:** constitution → spec → plan. Tasks and contracts are the working surfaces.
2. **Freeze the contracts** (`git tag contracts-v0.1.0`) before work on the modules starts (`plan.md` §3 has the split). CI's drift check fails any PR whose generated OpenAPI differs from `contracts/openapi.yaml`.
3. **Every PR references task IDs and scenario IDs** (`T025 · AS-3.3, AS-3.4`). Tests are named after scenarios (`test_as_3_4_price_validator_rejects_uncited_amount`).
4. **Changing behaviour = amending the spec first**, then regenerating the contract, then implementing. Unilateral contract edits are a constitution violation (Art. VI).
5. **Open questions** are listed in `spec.md §8` with defaults; resolve them on Day 1–2 and record findings in `specs/001-vitrina-api/research.md`.

## Suggested agent prompt (per machine)

> You are implementing module `<packages/...>` of Vitrina. Read `constitution.md`, `specs/001-vitrina-api/spec.md`, `plan.md` §3–4 and `contracts/`. Your tasks are the `[MX#]` items in `tasks.md` for the current phase. Respect the resource rules in `plan.md` §3 for this machine (hosted DB only, one dev process, session cap). Implement spec → tests → code: write failing tests named after the acceptance scenarios first, then make them pass. Never modify `contracts/` or another machine's package; if the contract is wrong, stop and report the needed amendment. Run `make check` before every commit.

## Validation

`contracts/openapi.yaml` passes `openapi-spec-validator` (OpenAPI 3.1). `contracts/agent-tools.json` is valid JSON Schema 2020-12 and every tool `parameters` object is a valid schema.

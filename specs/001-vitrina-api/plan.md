# Implementation Plan: Vitrina API

**Branch:** `001-vitrina-api` · **Spec:** `spec.md` · **Date:** 2026-10-27
**Constitution check:** passes (see §7)

## 1. Technical context

| Item | Decision |
| --- | --- |
| Language / runtime | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic |
| Database | Postgres 16 + pgvector (Supabase). Row-level `seller_id` scoping in every query via a repository base class |
| Queue | Postgres-backed queue (`procrastinate`) to avoid a second datastore; swap to RQ/Redis only if latency demands |
| Storage | Supabase Storage (S3-compatible) for assets, creatives, exports; signed URLs |
| Models | Meta Model API via OpenAI-compatible SDK (`base_url=https://api.meta.ai/v1`); model IDs from env `MODEL_AGENT`, `MODEL_STRATEGIST`, `MODEL_IMAGE`, `MODEL_SEGMENT`, `MODEL_TRANSCRIBE`; Muse Glimmer on Modal (OpenAI-compatible endpoint); Baseten `meta-models/muse-spark-open-1026` as open-weights fallback |
| Messaging | WhatsApp Cloud API v21+ (test number for hackathon), Cloudflare Tunnel for the webhook in dev |
| Media | ffmpeg (OGG/Opus → WAV 16 kHz; Reel assembly), Pillow + numpy (masks, histograms, watermark) |
| Frontend (separate repo dir) | Next.js 15 dashboard consuming this contract via a generated TypeScript client |
| Testing | pytest + pytest-asyncio, Hypothesis for the price validator, schemathesis against `openapi.yaml`, recorded provider fakes (`vcr`-style JSON cassettes) |
| CI | GitHub Actions: lint (ruff), type-check (mypy strict on `core`), contract drift check, unit + replay evals; `invented_price_rate == 0` gate |
| Deploy | Fly.io or Railway for API + worker; Supabase managed DB; Modal for Glimmer; public URL from Day 3 |

## 2. Project structure

```
vitrina/
├── constitution.md
├── specs/001-vitrina-api/{spec.md, plan.md, data-model.md, tasks.md, contracts/}
├── apps/
│   ├── api/                      # FastAPI app (routers only; no business logic)
│   │   └── vitrina_api/routers/{sellers,catalog,assets,creative,ads,webhooks,conversations,agent,contacts,analytics,briefs,reactivation,evals,replay,public,jobs,health}.py
│   ├── worker/                   # Job handlers; imports modules below
│   └── dashboard/                # Next.js (separate package.json)
├── packages/
│   ├── core/                     # Shared kernel: settings, db, auth, problem+json, pagination, idempotency, jobs, model-call ledger, provider interfaces
│   ├── catalog/                  # Products, facts, embeddings, hybrid search, voice-profile import
│   ├── creative/                 # SAM/Muse Image pipeline, fidelity verifier, copywriter, brand-voice judge, ads, export, reels, public showcase
│   ├── conversation/             # Webhook ingest, normalization, agent loop, tools, validators, handoff, outbound pacing, attribution
│   ├── compliance/               # Consent ledger, opt-out/erasure, legal-hours calendar, eligibility, reactivation campaigns
│   ├── learning/                 # Wonder brief generation + apply, analytics aggregates
│   └── evals/                    # Suites, fixtures, replay, metrics
├── providers/                    # Thin adapters: meta_model_api, whatsapp, modal_glimmer, baseten, local_mock + recorded cassettes
├── evals/fixtures/               # conversations_es.jsonl (60), fidelity/{good,altered}, consent_cases.yaml, webhooks/*.json
└── scripts/                      # gen-client, check-contract-drift, seed-demo, record-cassettes
```

Rule: `apps/*` depends on `packages/*`; packages depend only on `core` and on each other's **public** interfaces (`packages/<name>/api.py`), never on tables (constitution Art. VI.3).

## 3. Module ownership across the machines

Available hardware: **MX0** (4 cores, 8 GB), **MX2** (4 cores, 8 GB), **MX1** (4 cores, 16 GB). **MX3** (12 cores, 16 GB) is reserved and out of scope for this plan; it can be pulled in later for evals at scale, video rendering or a fifth lane without changing any ownership below.

Each machine runs its own coding-agent sessions on a branch; integration goes through a merge queue. Contracts are frozen at the end of Day 1 (tonight). Four modules map onto three machines by exploiting the phase order: the foundation work is front-loaded (Days 1–2) and the compliance/learning work is back-loaded (Days 6–7), so the same machine carries both.

| Machine | Owns | Exposes to others | Depends on |
| --- | --- | --- | --- |
| **MX0 · Core, Catalog, Evals, Infra → then Compliance + Learning** (4c/8 GB) | Days 1–2: `packages/core`, `packages/catalog`, `packages/evals`, routers `sellers, catalog, evals, replay, jobs, health`, CI, deploy. Day 4 onward: `packages/compliance`, `packages/learning`, routers `contacts, analytics, briefs, reactivation` | `core` (auth, db, jobs, problem+json, ledger), `catalog.api`, `evals.api`, replay harness; later `compliance.api` (eligibility, ledger, calendar) and `learning.api` (`generate_brief`, `apply_brief`) | — (foundation ships first); later `conversation` tables via **events only** (`ad.*`, `objection.recorded`), `creative.api.start_job` |
| **MX2 · Conversation** (4c/8 GB) | `packages/conversation`, `providers/whatsapp`, routers `webhooks, conversations, agent`; runs the Cloudflare Tunnel for the webhook | `conversation.api`: `process_inbound(payload)`, `run_turn(req)`, `send_outbound(msg)` | `catalog.api.search`, `catalog.api.get_product/check_stock/reserve`, `compliance.api.record_consent/opt_out/is_opted_out`, `core` |
| **MX1 · Creative + Dashboard** (4c/16 GB) | `packages/creative`, `providers/meta_model_api` (image + SAM), routers `assets, creative, ads, public`; `apps/dashboard` (Next.js) | `creative.api`: `start_job`, `approve_ad`, `get_approved_asset(product_id, variant)`; the generated TS client | `catalog.api.get_product`, `core`, Glimmer judge via `providers/modal_glimmer` |

Why this split: MX1 has the only 16 GB, so it takes the two memory-hungry local workloads (Pillow/numpy mask and histogram work, ffmpeg, and the Next.js dev server). MX2 is dedicated to the single most latency-sensitive path (webhook → agent → send) and hosts the tunnel so the WhatsApp number always points at one machine. MX0 owns everything deterministic and test-heavy (core, catalog, evals, then compliance and learning, which are mostly pure functions).

Resource rules for the 8 GB machines: no local Postgres — every machine uses the hosted Supabase project (a `dev` schema per machine to avoid collisions); run at most one long-lived dev process (API **or** worker) per machine while agent sessions are active; at most 2 concurrent coding-agent sessions on MX0/MX2 and 3 on MX1; `pytest -n 2`, never `-n auto`; cassette-based evals run on MX0 only. Anything heavier (full 60-case live eval, Reel rendering, video editing) is a candidate for MX3 if it is released later.

Integration order: MX0 `core` + `catalog` stubs land by end of Day 1 → MX2/MX1 build against stubs on Day 2 → first end-to-end (text question → cited reply) target end of Day 2 → MX0 switches to compliance/learning once catalog and evals scaffold are green (Day 4).

Cross-module events (Postgres `events` table + `LISTEN/NOTIFY`): `ad.chat_started`, `ad.reservation`, `ad.booking`, `ad.handoff`, `ad.objection`, `objection.recorded`, `consent.recorded`, `consent.revoked`, `creative.verified`, `campaign.decision`. Payloads are defined in `data-model.md §4`.

## 4. Key design decisions

### 4.1 Agent loop (M1)

```
inbound → opt-out/erasure keyword check (R7)
        → dedupe by wa_message_id
        → normalize (text | image→SAM crop+Spark desc+embedding | audio→ffmpeg→Transcribe)
        → attribution parse (#A..)
        → mode check: human → store only
        → scope classifier (cheap Spark call or rules) → off-topic → canned decline
        → retrieval (hybrid: pgvector cosine + pg_trgm, top-k 5, only status=active)
        → Spark tool loop (max 4 tool rounds, 20 s budget) with tools from agent-tools.json;
          the retrieved facts are already in the prompt, so search_catalog is called only
          for what they do not cover (amendment A-007, NFR-01)
        → structured AgentTurnOutput (response_format json_schema → fallback repair)
        → validators: price (R2), groundedness (R3), confidence (R4), handoff consistency (R5)
        → consent gating (R8) → record objection → send (pacing R12) → attribution events
```

Price validator: normalize Spanish money expressions (`$12.000`, `12 mil`, `12k`, `doce mil pesos`, `COP 12000`) into integer COP; compare set of amounts in reply against union of `amounts_cop` on cited facts/products. Property-tested with Hypothesis.

### 4.2 Creative pipeline (M2)

1. `segment`: SAM 3.1 on each source image → mask + bbox; reject if mask area < 5 % of image.
2. `compose`: Muse Image with product cutout + scene prompt per format; 3 scenes per format; prompt template includes "do not alter product shape, color or logo".
3. `verify`: SAM re-segment on output → align by bbox → `mask_iou`; color histogram (HSV, product region) → `color_delta` (Bhattacharyya); Spark vision yes/no "¿el producto cambió?" → `visual_review`. Thresholds in settings and stamped on the report.
4. `copywrite`: one Spark call returns the full hook × angle grid as JSON (10 items), each ≤ 300 chars, with the `wa_link` placeholder.
5. `score`: brand-voice judge (Glimmer on Modal; Spark rubric fallback) → regenerate below `brand_voice_min` once.
6. `ready`: creatives + copy variants persisted; blocked ones kept for audit.

### 4.3 Compliance engine (M3)

`evaluate(contact, at) → SendDecision` in the fixed order OPTED_OUT → NO_CONSENT_LEY_1581 → FREQUENCY_CAP → OUTSIDE_HOURS_LEY_2300 → NO_MATCHING_AD. Legal-hours calendar: Mon–Fri 07:00–19:00, Sat 08:00–15:00, America/Bogota, minus Colombian holidays computed with the Emiliani rule (library `holidays`, country `CO`, pinned version, with a unit test for 2026–2027 dates). Pure function, table-driven tests in `evals/fixtures/consent_cases.yaml`.

### 4.4 Learning loop (M3)

`generate_brief`: build the redacted dataset (Glimmer PII pass → objections with quotes, attribution table, angle performance, approval rates) → Muse Wonder with `BriefSchema` as JSON schema → validate → persist. `apply_brief` only creates **drafts**: creative jobs and a campaign preview. Nothing reaches a customer without seller approval.

### 4.5 Evals and replay (M4)

Provider interfaces (`ChatModel`, `ImageModel`, `Segmenter`, `Transcriber`, `Messenger`) each have a `Recorded*` implementation reading JSON cassettes keyed by a hash of the request. `replay=true` swaps all providers. Suites: `conversations_es` (60 cases: grounded answers, invented-price traps, handoff triggers, off-topic, voice-note transcripts, image matches), `fidelity` (10 good scenes + 10 altered), `consent_scheduler` (40 time/consent cases), `price_validator` (property tests). CI gate: `invented_price_rate == 0.0`.

### 4.6 Demo safety (all)

Local WhatsApp mock (`providers/whatsapp/mock.py`) served at `/mock/whatsapp` with a tiny HTML phone view for screen recording; replay fixtures for the full 2:40 script; cached creatives for the demo product; `GET /health/providers` on the dashboard.

## 5. Phases (mapped to the 10-day plan)

| Day | Phase | Exit criterion |
| --- | --- | --- |
| 1 · Oct 26–27 | **Freeze**: constitution, spec, contracts; `core` skeleton (auth, db, problem+json, jobs, idempotency, ledger); generated TS client; CI green on empty suites; webhook verify + echo bot on test number | `make check` passes; echo reply received on a test phone |
| 2 · Oct 27 | Catalog + agent v1 (text only), price validator, structured output; facts/products CRUD | AS-1.1–1.4, AS-3.1–3.4 pass against live Spark |
| 3 · Oct 28 | Creative pipeline: segment → compose → verify v1; assets | AS-2.1–2.3; one blocked image in fixtures |
| 4 · Oct 29 | Copy grid, ads + codes + wa.me, attribution events, analytics v1, dashboard v1 | AS-2.4–2.5, AS-3.2, AS-4.1 |
| 5 · Oct 30 | Image + voice turns, handoff + seller summary, takeover/release | AS-3.5–3.9 |
| 6 · Oct 31 | Consent ledger, opt-out/erasure, legal calendar, eligibility, campaign preview + template send | AS-6.1–6.7; `consent_scheduler` suite 100 % |
| 7 · Nov 1 | Wonder brief + apply, 60-case suite, metrics page, cost ledger | AS-5.1–5.3, AS-7.1–7.3 |
| 8 · Nov 2 | Pilot with real sellers, replay mode, bug-fix, **feature freeze 23:59** | AS-8.1; all P1 scenarios green |
| 9 · Nov 3 | Record video, README + architecture diagram, public page, X post draft; P2 (Reels, Glimmer judge, public tool) only if green | Submission package complete |
| 10 · Nov 4 | Buffer, final eval run, re-record if needed, submit by 18:00 ET | Submitted |

## 6. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| 8 GB machines swap under API + worker + agent sessions | Hosted DB only; one dev process per machine; session caps in §3; heavy jobs queued to MX1 or deferred to MX3 if released |
| MX0 is a single point of failure for both the foundation and compliance/learning | Foundation lands Day 1–2 before compliance starts; compliance/learning are pure-function modules that MX1 or MX2 can absorb on Day 6 if MX0 is behind |
| Spark tool calling / JSON mode differs from OpenAI semantics | Day 1 spike; `providers/meta_model_api` hides differences; prompt-constrained JSON + repair fallback |
| Voice Transcribe lacks Spanish or rejects OGG | ffmpeg transcode; fallback to Spark audio input if supported; otherwise voice turns become P2 |
| WhatsApp template approval slow | Submit Day 1; reactivation demo runs in preview + mock sender until approved |
| Test number 5-recipient cap | Pilots on the mock + one real test phone per seller; real-number coexistence only if confirmed |
| Glimmer doesn't fit / Modal cold starts | Spark rubric fallback; Glimmer additive for the open-weights bonus |
| Fidelity verifier false positives | Thresholds tunable; report shows the three signals; seller can see why |
| Credits exhaustion | Cost ledger + spend limit alert at $100; replay mode for all repeated test runs |
| Rate limits (429) during demo | Pacing + backoff; cached creatives; replay fallback |

## 7. Constitution check

| Article | How this plan complies |
| --- | --- |
| I Hackathon-built, public | Repo public at first commit (Day 1 after kickoff); README timeline; no pre-kickoff code |
| II Consent | `compliance` module is the only path to marketing sends; eligibility order fixed; calendar enforced server-side |
| III Grounded | Validators run outside the model (R1–R5); CI gate on invented prices |
| IV Verified creatives | `verify` stage mandatory; blocked creatives unsendable at the type level (`get_approved_asset` filters) |
| V Privacy | Synthetic fixtures; Glimmer/regex PII pass before Wonder; row-level scoping in `core.db` |
| VI Frozen contracts | `scripts/check-contract-drift` in CI; amendments via PR to `specs/` |
| VII Spec → tests | Test IDs reference `AS-x.y`; replay evals in CI |
| VIII Fail safe | Mock messenger, replay, pacing, backoff, provider health |

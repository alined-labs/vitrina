# Tasks: Vitrina API

Generated from `spec.md` + `plan.md`. Format: `- [ ] T### [P?] description → tests (AS-ids)`.
`[P]` = parallelizable with neighbours. Every task ends with its tests green and `make check` passing.
Every task follows FR-085: timestamps are ISO-8601 UTC, and money is integer COP unless the field ends in `_usd`.

## Phase 0 · Freeze (Day 1, after kickoff 12:00 ET)

- [ ] T001 Create public repo `vitrina`, MIT license, `README.md` with "Built during the hackathon" timeline, `constitution.md`, `specs/` as delivered here. First commit timestamp ≥ kickoff.
- [ ] T002 Monorepo scaffold per plan.md §2 (`uv` workspace, `apps/api`, `apps/worker`, `packages/*`, `providers/*`), ruff + mypy + pytest config, `Makefile` (`check`, `test`, `evals`, `gen-client`).
- [ ] T003 `scripts/check-contract-drift.py`: fails CI if FastAPI's generated OpenAPI differs from `contracts/openapi.yaml` (normalized). → unit test with a deliberate drift.
- [ ] T004 GitHub Actions: lint, type-check, tests, drift check, replay evals; branch protection with merge queue.
- [ ] T005 [P] `core.settings` (env, `MODEL_*` IDs, thresholds), `core.db` (async engine, session, `SellerScopedRepository`), Alembic baseline from `data-model.md`.
- [ ] T006 [P] `core.auth`: Supabase JWT → `seller_id`; `X-Service-Token`; dependency injection; 401/403 problem+json. → tests.
- [ ] T007 [P] `core.http`: RFC 9457 `Problem` handler with error catalogue, `X-Request-Id` middleware, cursor pagination helper, `Idempotency-Key` middleware (store/replay/409). → tests (FR-080..082).
- [ ] T008 [P] `core.jobs`: Postgres queue, `Job` resource, `GET /jobs/{id}`; worker entrypoint. → tests (FR-083).
- [ ] T009 [P] `core.ledger`: model-call recorder + cost table; `GET /metrics/costs`. → tests (FR-084).
- [ ] T010 [P] Provider interfaces (`ChatModel`, `ImageModel`, `Segmenter`, `Transcriber`, `Messenger`, `Embedder`) + `Recorded*` cassette implementations + `local_mock` messenger with HTML phone view. → tests.
- [ ] T011 `providers/meta_model_api`: OpenAI-compatible client; **Day-1 spike**: tool calling, JSON schema output, vision input, embeddings; record findings in `research.md`; resolve `[NEEDS CLARIFICATION]` on model IDs.
- [ ] T012 `providers/whatsapp`: verify handshake, HMAC check, send text/image/template, status callbacks; echo bot on the test number. → tests with recorded payloads (AS-3.1, FR-020..021).
- [ ] T013 `scripts/gen-client`: TypeScript client from `openapi.yaml` for the dashboard; Next.js scaffold with auth and an empty shell.
- [ ] T014 [all] Freeze: tag `contracts-v0.1.0`; each machine confirms it builds against stubs.

## Phase 1 · Catalog + agent v1 (Day 2)

- [ ] T020 `catalog`: products/facts CRUD, `amounts_cop` extraction on write, embeddings, hybrid search (`search(query, kinds, top_k)`), `active`-only retrieval. → tests (AS-1.2, AS-1.4, FR-002..003).
- [ ] T021 [P] `POST /products/import` job: image → Spark → draft products. → tests with cassette (AS-1.3).
- [ ] T022 [P] `POST /sellers`, `GET/PATCH /sellers/me`, voice-samples import job (seller-line filter, PII redaction, profile). → tests (AS-1.1, AS-1.5, FR-004).
- [ ] T023 `conversation.normalize` for text; `conversation.attribution` (`#A..` parse, events). → tests (AS-3.2, FR-022..023).
- [ ] T024 Agent loop v1: retrieval → Spark tool loop with `search_catalog`, `get_product`, `check_stock` → `AgentTurnOutput` parse/repair. → tests (FR-024..025; AS-4.2 the `record_objection` tool).
- [ ] T025 Price validator (Spanish money normalizer) + groundedness + confidence rules (R2–R4) with guided retry and fallback. → Hypothesis + fixtures (AS-3.3, AS-3.4, FR-026).
- [ ] T026 `POST /agent/turns` with `dry_run`, `validation`, `tool_calls`, `retrieved`. → contract tests (FR-030).
- [ ] T027 Inbound pipeline end-to-end: webhook → job → agent → outbound with pacing + backoff. → integration test with mock messenger (AS-3.11, FR-028).
- [ ] T028 Eval scaffold: `conversations_es.jsonl` first 20 cases, runner, `POST /evals/runs`, `GET /metrics/summary`. → (AS-7.1, AS-7.3)

## Phase 2 · Creative engine (Day 3)

- [ ] T030 `POST /assets` (image/video, limits, sha256, storage, image embedding). → tests (FR-010).
- [ ] T031 `segment` stage with SAM 3.1 (mask, bbox, area check). → cassette tests.
- [ ] T032 `compose` stage with Muse Image per format, scene prompt template. → cassette tests.
- [ ] T033 Fidelity verifier: re-segment, IoU, HSV histogram delta, Spark visual review, thresholds, Spanish reason. → tests on `fidelity/{good,altered}` fixtures: 100 % block on altered (AS-2.2, AS-2.3, FR-012..013).
- [ ] T034 `POST /creative/jobs`, `GET /creative/jobs/{id}`, `GET /creatives/{id}`; job stages + progress. → contract tests (AS-2.1).

## Phase 3 · Copy, ads, attribution, dashboard v1 (Day 4)

- [ ] T040 `copywrite` stage: hook × angle grid JSON, ≤ 300 chars, `wa_link` placeholder. → tests (AS-2.4).
- [ ] T041 Brand-voice judge: Spark rubric fallback now, Glimmer adapter behind the interface. → tests.
- [ ] T042 Ads: create, approve (code generator, `wa_link`), by-code, archive, export zip with `provenance.json`; `409 CREATIVE_BLOCKED`. → tests (AS-2.5, FR-014..015).
- [ ] T043 `send_ad_asset` tool + `creative.api.get_approved_asset`. → tests.
- [ ] T044 `attribution_events` consumer, `GET /analytics/attribution`, `GET /analytics/objections`. → tests (AS-4.1, AS-4.2 the objection counts, FR-040..041).
- [ ] T045 Dashboard v1: catalog, creative review (with verifier report), ad approval, conversations list, attribution counter.

## Phase 4 · Multimodal turns and handoff (Day 5)

- [ ] T050 Image turns: SAM crop → Spark description → image embedding → catalog match. → tests (AS-3.5).
- [ ] T051 Audio turns: ffmpeg transcode → Transcribe → transcript on message. → tests (AS-3.6); resolve Spanish support clarification.
- [ ] T052 `reserve_item` (atomic stock), `book_slot` (hours), reservation expiry job. → tests (AS-3.7).
- [ ] T053 Handoff: triggers (R5, R11, thresholds, sentiment), seller WhatsApp summary, `mode=human` suppression, takeover/release, human messages endpoint. → tests (AS-3.8, AS-3.9, FR-027).
- [ ] T054 Scope classifier + off-topic decline. → tests (AS-3.10, FR-029).
- [ ] T055 Eval cases 21–60 (multimodal, handoff, off-topic). Run live once, record cassettes.

## Phase 5 · Consent and reactivation (Day 6)

- [ ] T060 Consent ledger (append-only grants), `record_consent` gating (R8), `GET/POST /contacts/{id}/consents`. → tests (AS-6.1, FR-060).
- [ ] T061 Opt-out keywords (accent-insensitive) before pipeline, `opt_out` tool, `POST /contacts/{id}/opt-out`, pending-send cancellation. → tests (AS-6.5, FR-063).
- [ ] T062 Erasure: `DELETE /contacts/{id}`, `BORRAR MIS DATOS`, 24 h job, receipt. → tests (AS-6.6, FR-065).
- [ ] T063 Legal-hours calendar (Ley 2300 windows, CO holidays via Emiliani rule) + eligibility evaluator in fixed order. → table tests `consent_cases.yaml` (AS-6.2–6.4, FR-061..062).
- [ ] T064 Campaign preview/create/get/send, template send via messenger, send decisions audit, `POST /reactivation/eligibility`. → tests (AS-6.7, FR-064).
- [ ] T065 `consent_scheduler` suite in CI (100 % required).

## Phase 6 · Learning loop and metrics (Day 7)

- [ ] T070 Redacted dataset builder (Glimmer/regex PII pass) + Wonder call with `BriefSchema` + validation + `headline`. → cassette tests (AS-5.1, AS-5.3, FR-050).
- [ ] T071 `POST /briefs/{id}/apply` → creative jobs + campaign preview. → tests (AS-5.2, FR-051).
- [ ] T072 Full 60-case suite, `fidelity`, `price_validator` suites; CI gate `invented_price_rate == 0`. (AS-7.2)
- [ ] T073 Dashboard metrics page from `/metrics/summary` and `/metrics/costs`; provider health widget.

## Phase 7 · Pilot, replay, freeze (Day 8)

- [ ] T080 Replay: fixtures list, `POST /replay/sessions`, `is_replay` flag, mock sender, speed control. → tests (AS-8.1, FR-071).
- [ ] T081 [all] Onboard 3–5 pilot sellers on the demo tenant; capture consented testimonials and response-time measurements.
- [ ] T082 [all] Bug bash against every P1 scenario; record demo cassettes; cache demo creatives.
- [ ] T083 (P2) Reels job with SAM tracking; (P2) Glimmer judge live on Modal; (P2) `POST /public/showcases` with rate limit + watermark + AI label (AS-2.6, AS-8.2, FR-072).
- [ ] T084 **Feature freeze 23:59 ET.**

## Phase 8 · Submission (Days 9–10)

- [ ] T090 Record 2:40 video from the storyboard using replay + live segments; Spanish/English subtitles.
- [ ] T091 README: architecture diagram, "Built during the hackathon" timeline, model-by-model usage, eval table, legal section, how to run with `replay=true` (no keys).
- [ ] T092 Public URL live; `GET /health/providers` green; spend limit set.
- [ ] T093 Final eval run, attach metrics to README; X post draft with video; submit by Nov 4 18:00 ET; flag open-weights (Glimmer) and agentic/creative bonus categories in the submission text.

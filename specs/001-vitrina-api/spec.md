# Feature Specification: Vitrina API

**Feature branch:** `001-vitrina-api`
**Status:** Draft for freeze · **Created:** 2026-10-27 · **Owner:** Daniel M.C.
**Constitution:** `../../constitution.md` v1.0.0
**Contracts:** `contracts/openapi.yaml`, `contracts/agent-tools.json`

## 1. Overview

Vitrina is a closed-loop AI storefront for WhatsApp sellers in Colombia. The seller uploads product photos and approves; the system turns photos into verified ads, each ad carries a tracked WhatsApp link, a sales agent answers customers only from the seller's catalog, weekly objections become the next creative brief, and reactivation reaches only contacts with recorded consent.

This spec defines the **backend API** that the seller dashboard (Next.js), the WhatsApp webhook, the background workers and the public "Foto → Vitrina" tool consume. It is written to be implemented by parallel coding agents against a frozen contract.

Pitch: *"Tus fotos se vuelven anuncios, tus anuncios se vuelven ventas, y cada conversación mejora el próximo anuncio."*

### 1.1 Goals

- Expose the five stages of the loop as stable, testable resources: catalog, creative, conversation, learning, reactivation.
- Make attribution (`ad → chat → outcome`) a first-class, queryable fact.
- Make legal compliance observable: every consent, every send decision and every block has a reason code visible through the API.
- Ship an evaluation harness as part of the API so the metrics page in the demo is real.

### 1.2 Non-goals (this feature)

- Instagram Graph API and Marketing API integration (requires Meta app review; roadmap only). Creatives are exported as files.
- Payments. Reservations and bookings are commitments, not transactions.
- Multi-user seller accounts, roles and permissions beyond one owner token per seller.
- Mobile app.

## 2. Actors

| Actor | Description | Auth |
| --- | --- | --- |
| **Seller** (owner) | Micro-business owner using the dashboard. One per seller account. | Bearer JWT (Supabase) scoped to `seller_id` |
| **Customer** | WhatsApp user chatting with the seller's number. Never calls the API directly. | Arrives through the WhatsApp webhook |
| **Worker** | Internal background process (creative jobs, Wonder brief, reactivation scheduler, evals). | Service token (`X-Service-Token`) |
| **WhatsApp Cloud API** | Meta's platform delivering inbound messages and status callbacks. | HMAC `X-Hub-Signature-256` |
| **Public visitor** | Anonymous user of the viral "Foto → Vitrina" tool. | None; rate limited by IP |
| **Judge / demo operator** | Uses replay mode and the metrics page. | Seller token on the demo account |

## 3. User scenarios and acceptance tests

Scenarios use Given/When/Then. Each has a stable ID that tests must reference. **P1** = must-have for the submission, **P2** = stretch.

### US-1 · Onboarding and catalog (P1)

> As a seller, I paste my prices and FAQ, upload 2–3 photos per product, and the system knows my business well enough to answer customers.

- **AS-1.1** Given a new seller, when they `POST /sellers` with a business name, WhatsApp number and city, then a seller is created with default business hours, handoff thresholds and brand-voice settings, and a `201` is returned.
- **AS-1.2** Given an authenticated seller, when they `POST /products` with name, price in COP, stock and attributes, then the product is stored, embedded for retrieval, and returned with `status: active`.
- **AS-1.3** Given a photo of a handwritten menu or price list, when the seller `POST /products/import` with the image, then the response is a `202` job; on completion the job yields **draft** products (`status: draft`) that must be approved before the agent can cite them.
- **AS-1.4** Given a seller, when they `POST /facts` with a shipping policy ("Envío a Medellín $12.000, 2–3 días"), then the fact is stored with a `fact_id`, a `kind: shipping` and is retrievable by semantic search.
- **AS-1.5** Given an exported WhatsApp chat `.txt`, when the seller `POST /sellers/me/voice-samples`, then only the seller's own messages are kept, customer lines are discarded, PII is redacted, and a brand-voice profile (formality, warmth, regionalisms such as "veci", emoji usage) is produced.

### US-2 · Creative engine with fidelity verification (P1)

> As a seller, I upload casual phone photos and get professional ad scenes and copy variants that never misrepresent my product.

- **AS-2.1** Given a product with ≥1 asset, when the seller `POST /creative/jobs` with `formats: ["story_9x16", "catalog_1x1"]` and `scenes_per_format: 3`, then a `202` job is created and the job progresses through `queued → segmenting → composing → verifying → copywriting → ready`.
- **AS-2.2** Given a completed job, when the client `GET /creative/jobs/{id}`, then each creative has a `verification` object with `status` (`passed | blocked`), `mask_iou`, `color_delta`, `visual_review` and a Spanish `reason` ("color del bolso alterado — bloqueado").
- **AS-2.3** Given a creative whose `mask_iou < 0.85` or `color_delta > threshold`, then its `verification.status` is `blocked`, it cannot be approved (`409 CREATIVE_BLOCKED`), and it never appears in `GET /ads`.
- **AS-2.4** Given a passed creative, when the job writes copy, then exactly `copy_variants` (default 10) variants are produced as a grid of hook × angle (`price, quality, handmade, urgency, gift, local_pride`), each ≤ 300 characters, each with a `brand_voice_score` from 1 to 5; variants below `brand_voice_min` (default 3.5) are regenerated once and otherwise dropped.
- **AS-2.5** Given a passed creative and a copy variant, when the seller `POST /ads/{id}/approve`, then the ad gets a short code (`#A7` format: `A` + base-32, unique per seller), a `wa.me` link with prefilled text `Hola, vi el anuncio #A7`, and `status: approved`.
- **AS-2.6** Given a 5–15 s product video, when the seller `POST /creative/reels` (P2), then SAM 3.1 tracks the product across frames and the job yields a 9:16 Reel with the product masked/highlighted and the approved copy overlaid.

### US-3 · WhatsApp sales agent (P1)

> As a customer, I tap an ad link, ask in text, photos or voice notes, and get accurate answers in the seller's voice, with a human stepping in when needed.

- **AS-3.1** Given an inbound WhatsApp webhook with a valid `X-Hub-Signature-256`, when it reaches `POST /webhooks/whatsapp`, then the API returns `200` within 2 s, enqueues processing, and deduplicates by `message_id`. An invalid signature returns `401` and is logged.
- **AS-3.2** Given a first message `Hola, vi el anuncio #A7`, when processed, then a conversation is created with `attributed_ad_id` set to ad `A7`, the contact is created, and an `ad.chat_started` attribution event is recorded.
- **AS-3.3** Given a text question "¿cuánto vale el envío a Medellín?", when the agent runs, then the reply cites `fact_id`s whose content contains the quoted price, `confidence ≥ 0.6`, and the response passes the price validator.
- **AS-3.4** Given a reply draft containing a COP amount not present in any cited fact, when validated, then the draft is rejected, the agent is re-run once with the violation in context, and if it fails again the reply becomes the fallback *"No tengo ese dato, te comunico con {owner_name}"* and a handoff is opened with `reason: price_unverified`.
- **AS-3.5** Given an inbound image, when normalized, then SAM 3.1 crops the salient object, Spark describes it, and the agent searches the catalog by image embedding + description; the reply offers the top match with its ad asset via `send_ad_asset` or says it is not in the catalog.
- **AS-3.6** Given an inbound voice note in Spanish, when normalized, then it is transcribed (Muse Voice Transcribe), the transcript is stored on the message, and the agent treats it as text.
- **AS-3.7** Given a customer asks to reserve a product in stock, when the agent calls `reserve_item`, then stock is decremented atomically, a reservation with a 24 h expiry is created, and the reply confirms with the reservation code.
- **AS-3.8** Given any of: explicit request for a human, complaint/refund, payment problem, medical topic, two consecutive low-confidence turns, negative sentiment, or an order above `handoff_amount_threshold`, when the agent runs, then `handoff_to_human` is called, the conversation `mode` becomes `human`, the agent stops replying, and the seller receives a WhatsApp summary.
- **AS-3.9** Given a conversation in `human` mode, when the seller `POST /conversations/{id}/release`, then `mode` returns to `agent`.
- **AS-3.10** Given a customer asks something unrelated to the business ("¿quién ganó el partido?"), then the agent politely declines and stays on topic; `objection_tag` is `off_topic`.
- **AS-3.11** Given a reply is ready, when sending, then outbound messages to the same recipient are spaced ≥ 6 s apart and `429`s from Meta are retried with exponential backoff (max 3).

### US-4 · Attribution and objection mining (P1)

- **AS-4.1** Given conversations attributed to ads, when the seller `GET /analytics/attribution?from&to`, then each ad returns `chats`, `reservations`, `bookings`, `handoffs`, `objections_by_tag` and `conversion_rate`.
- **AS-4.2** Given the agent detects an objection (`price, shipping, size, availability, trust, timing, payment, off_topic, other`), when it calls `record_objection`, then the objection is stored with `conversation_id`, `ad_id`, `tag` and a short redacted `quote`.

### US-5 · Weekly brief and loop closure (P1 core, P2 automation)

> As a seller, every week I get a brief that explains what worked and new ads that answer my customers' objections.

- **AS-5.1** Given ≥ 10 attributed conversations in a period, when a worker `POST /briefs/generate` with the period, then Muse Wonder receives the redacted objection set and attribution table and returns a JSON brief with `insights[]`, `recommended_angles[]`, `creative_directives[]`, and `reactivation_segments[]`, validated against `BriefSchema`.
- **AS-5.2** Given a brief, when the seller `POST /briefs/{id}/apply`, then one creative job per `creative_directive` is created with the brief's angle and hook constraints, and the resulting ads carry `brief_id` for attribution.
- **AS-5.3** The brief's example output must be expressible as: *"El ángulo 'hecho a mano' generó 3× más chats, pero el 40 % se frenó por el envío: el próximo lote abre con envío gratis en Bogotá."*

### US-6 · Consent ledger and reactivation (P1)

> As a seller, I win back customers who asked but didn't buy, without ever breaking Ley 1581 or Ley 2300.

- **AS-6.1** Given a conversation, when the agent proposes opt-in (*"¿Te aviso cuando llegue tu talla o haya promo? Responde SÍ"*) and the customer answers affirmatively, then `record_consent` stores `{contact_id, purpose: marketing, text_shown, response_text, channel: whatsapp, recorded_at}`.
- **AS-6.2** Given a contact without a marketing consent record, when included in a reactivation campaign preview, then their decision is `blocked` with `reason: NO_CONSENT_LEY_1581`.
- **AS-6.3** Given a send scheduled for Sunday or outside 07:00–19:00 Mon–Fri / 08:00–15:00 Sat (America/Bogota), or on a Colombian public holiday, then the decision is `deferred` with `reason: OUTSIDE_HOURS_LEY_2300` and `next_allowed_at` set to the next legal window.
- **AS-6.4** Given a contact who received a marketing message in the last 7 days, then the decision is `blocked` with `reason: FREQUENCY_CAP`.
- **AS-6.5** Given an inbound message matching `BAJA` (case/accent-insensitive, also "no más", "stop", "cancelar"), then the contact is opted out immediately, a confirmation is sent, and any pending sends are cancelled.
- **AS-6.6** Given an inbound `BORRAR MIS DATOS`, then a deletion request is created, the contact and their messages are erased within 24 h, and an erasure receipt is kept (hash of phone, timestamp, no PII).
- **AS-6.7** Given an approved campaign, when `POST /reactivation/campaigns/{id}/send`, then only `allowed` contacts receive an approved marketing **template** message (outside the 24 h window), each personalised to their last objection and the newest matching ad.

### US-7 · Evaluation harness and metrics (P1)

- **AS-7.1** Given the fixture set `evals/conversations_es.jsonl` (60 cases), when a worker `POST /evals/runs`, then the run reports `groundedness_rate`, `invented_price_rate`, `handoff_precision`, `handoff_recall`, `brand_voice_mean`, `verifier_pass_rate` and `off_topic_refusal_rate`.
- **AS-7.2** Given a run, `invented_price_rate` must be `0.0` for the run to be `passed`; CI fails otherwise.
- **AS-7.3** Given `replay: true`, the run uses recorded model outputs and needs no provider credentials.

### US-8 · Replay mode and public viral tool (P1 replay, P2 public tool)

- **AS-8.1** Given a recorded webhook fixture, when the operator `POST /replay/sessions` with `fixture_id`, then the recorded inbound messages are injected through the normal pipeline with a `replay` flag, and the dashboard shows them live.
- **AS-8.2** Given an anonymous visitor uploading one photo to `POST /public/showcases`, then a rate-limited job produces 3 watermarked scenes and 10 copy variants on a shareable page `/v/{slug}`; the page shows the "imagen generada con IA" label and a CTA to Vitrina.

## 4. Functional requirements

Each requirement is testable and maps to scenarios above.

### Sellers and catalog

- **FR-001** The API SHALL create, read and update a seller with: `business_name`, `owner_name`, `whatsapp_number` (E.164), `city`, `timezone` (default `America/Bogota`), `business_hours`, `handoff_amount_threshold_cop`, `brand_voice_profile`, `ai_label_enabled`.
- **FR-002** The API SHALL manage products (`name`, `description`, `price_cop`, `stock`, `attributes{}`, `status: draft|active|archived`) and facts (`kind: faq|shipping|payment|policy|hours|other`, `text`, `source`). Both are embedded into pgvector on write.
- **FR-003** Products created by image import SHALL be `draft` until approved; the agent SHALL never cite `draft` products or facts.
- **FR-004** Voice-sample import SHALL discard non-seller lines, redact PII, and compute a `brand_voice_profile` with `formality (1-5)`, `warmth (1-5)`, `emoji_rate`, `regionalisms[]`, `sample_embeddings_ref`.

### Assets and creatives

- **FR-010** The API SHALL accept image uploads (JPEG/PNG/WebP ≤ 10 MB) and video uploads (MP4 ≤ 60 MB, ≤ 15 s) as assets bound to a product.
- **FR-011** A creative job SHALL run the pipeline: SAM 3.1 segmentation → Muse Image composition per format → fidelity verification → Spark copywriting → brand-voice scoring (Glimmer when available, Spark rubric fallback).
- **FR-012** Fidelity verification SHALL compute `mask_iou` (re-segmented product vs. source mask after alignment), `color_delta` (histogram distance in the product region) and `visual_review` (Spark vision yes/no with reason). A creative is `blocked` if `mask_iou < 0.85` OR `color_delta > 0.25` OR `visual_review.altered == true`. Thresholds are configuration, recorded on each verification.
- **FR-013** Blocked creatives SHALL be retained for audit with their report but SHALL be excluded from approval and from all public surfaces.
- **FR-014** Ads SHALL have a unique per-seller short code (`A` + 1–3 base-32 chars, e.g. `A7`, `AK2`), a `wa_link` of the form `https://wa.me/{seller_number}?text=Hola%2C%20vi%20el%20anuncio%20%23A7`, `status: draft|approved|archived`, and references to `creative_id`, `copy_variant_id`, optional `brief_id`.
- **FR-015** Approved creative files SHALL be exportable (`GET /ads/{id}/export`) as a zip with the image per format, the copy as `.txt`, and a `provenance.json`.

### Conversation engine

- **FR-020** `GET /webhooks/whatsapp` SHALL implement the Meta verification handshake (`hub.mode`, `hub.verify_token`, `hub.challenge`).
- **FR-021** `POST /webhooks/whatsapp` SHALL validate `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with the app secret), acknowledge within 2 s, persist the raw payload, deduplicate by WhatsApp `message_id`, and enqueue a `process_inbound` job.
- **FR-022** Normalization SHALL produce a `NormalizedMessage` for `text`, `image` (SAM crop + Spark description + CLIP-style embedding), `audio` (transcript + language), `interactive` (button/list replies) and `unsupported` (polite fallback).
- **FR-023** Attribution SHALL parse `#A[0-9A-Z]{1,3}` in the first inbound message of a conversation (and any later message) and set `attributed_ad_id`; if the code is unknown, `attribution_status: unknown_code`.
- **FR-024** The agent SHALL call Muse Spark with the seller system prompt, the brand-voice profile, the last N turns, retrieved facts/products (top-k by hybrid search), and the tool set defined in `contracts/agent-tools.json`.
- **FR-025** The agent output SHALL conform to `AgentTurnOutput`: `{reply, cited_fact_ids[], confidence, handoff_reason|null, objection_tag|null, proposed_consent_prompt: bool}`.
- **FR-026** The price validator SHALL extract all monetary expressions from `reply` (`$12.000`, `12 mil`, `12000 COP`, `doce mil`) and verify each against amounts in the cited facts/products (±0 tolerance after normalization). Violations trigger one guided retry, then fallback + handoff.
- **FR-027** Handoff SHALL set `conversation.mode = human`, notify the seller via WhatsApp with a ≤ 500-character summary, and suppress agent replies until released.
- **FR-028** Outbound sending SHALL enforce ≥ 6 s between messages to the same recipient, retry `429`/`5xx` with exponential backoff (max 3), and persist delivery status from WhatsApp status webhooks.
- **FR-029** The agent SHALL refuse off-topic requests and SHALL not provide medical, legal or financial advice; medical topics trigger handoff.
- **FR-030** `POST /agent/turns` SHALL run one agent turn without WhatsApp (for tests, evals and the dashboard "try it" box), returning the full `AgentTurnOutput` plus `tool_calls[]` and `validation` results.

### Attribution and analytics

- **FR-040** The API SHALL record attribution events: `ad.chat_started`, `ad.reservation`, `ad.booking`, `ad.handoff`, `ad.objection`, each with `ad_id`, `conversation_id`, `occurred_at`.
- **FR-041** `GET /analytics/attribution` SHALL aggregate per ad and per angle for a period, and `GET /analytics/objections` SHALL aggregate objection tags per ad and period.

### Learning loop

- **FR-050** `POST /briefs/generate` SHALL build a redacted dataset (objections, attribution, copy angles, approval rates) and call Muse Wonder with a JSON schema; output SHALL validate against `BriefSchema` or the job fails with `BRIEF_SCHEMA_INVALID`.
- **FR-051** `POST /briefs/{id}/apply` SHALL create creative jobs from `creative_directives[]` and a reactivation campaign **preview** from `reactivation_segments[]`; nothing is sent without seller approval.

### Consent and reactivation

- **FR-060** The consent ledger SHALL store for each record: `contact_id`, `purpose` (`marketing|data_processing`), `status` (`granted|revoked`), `text_shown`, `response_text`, `channel`, `recorded_at`, `evidence_message_id`. Records are append-only; revocation appends.
- **FR-061** Reactivation eligibility SHALL evaluate, in order: `OPTED_OUT`, `NO_CONSENT_LEY_1581`, `FREQUENCY_CAP`, `OUTSIDE_HOURS_LEY_2300`, `NO_MATCHING_AD`, and return `allowed|blocked|deferred` with `reason` and `next_allowed_at`.
- **FR-062** The legal-hours calendar SHALL include Colombian public holidays (Ley 51 de 1983 "Emiliani" rules) for the current and next year.
- **FR-063** Opt-out keywords SHALL be matched case- and accent-insensitively; opt-out takes effect before any other processing of the message.
- **FR-064** Marketing sends SHALL use an approved WhatsApp template (`template_name`, `language: es`, components) and SHALL log `send_decision` records regardless of outcome.
- **FR-065** `DELETE /contacts/{id}` and the `BORRAR MIS DATOS` keyword SHALL erase PII and message content within 24 h and keep a non-reversible erasure receipt.

### Evaluation, replay and public tool

- **FR-070** `POST /evals/runs` SHALL execute a named suite (`conversations_es`, `fidelity`, `consent_scheduler`) with `replay: bool` and report the metrics in §3 AS-7.1; runs are listable and the latest is exposed at `GET /metrics/summary` for the dashboard.
- **FR-071** `POST /replay/sessions` SHALL inject recorded inbound messages through the normal pipeline with `is_replay = true` and a mock sender, so no real WhatsApp messages are sent.
- **FR-072** `POST /public/showcases` SHALL accept one image without auth, limited to 5 requests/hour/IP, produce 3 watermarked scenes and 10 copies, and publish `GET /public/showcases/{slug}` with the AI label.

### Cross-cutting

- **FR-080** All list endpoints SHALL use cursor pagination (`limit` ≤ 100, `cursor`, `next_cursor`).
- **FR-081** All mutating `POST` endpoints SHALL accept `Idempotency-Key`; a repeated key with the same body returns the original response, with a different body returns `409 IDEMPOTENCY_CONFLICT`.
- **FR-082** Errors SHALL use RFC 9457 `application/problem+json` with a stable `code` from the error catalogue in `contracts/openapi.yaml`.
- **FR-083** Long-running operations SHALL return `202` with a `Job` resource (`id, kind, status, progress, result_ref, error`) pollable at `GET /jobs/{id}`.
- **FR-084** Every request SHALL carry a `request_id` (echoed as `X-Request-Id`) and every model call SHALL be logged with `provider, model, tokens_in, tokens_out, latency_ms, cost_usd`, aggregated at `GET /metrics/costs`.
- **FR-085** All timestamps are ISO-8601 UTC; all money is integer COP unless the field is suffixed `_usd`.

## 5. Key entities

| Entity | Purpose | Key fields |
| --- | --- | --- |
| Seller | Tenant | id, business_name, whatsapp_number, city, timezone, business_hours, handoff_amount_threshold_cop, brand_voice_profile, ai_label_enabled |
| Product | Catalog item | id, seller_id, name, description, price_cop, stock, attributes, status, embedding |
| Fact | FAQ/policy chunk | id, seller_id, kind, text, source, status, embedding |
| Asset | Uploaded photo/video | id, seller_id, product_id, kind, url, width, height, duration_s, sha256 |
| CreativeJob | Pipeline run | id, seller_id, product_id, params, status, stage, progress, error |
| Creative | Generated scene | id, job_id, product_id, format, image_url, mask_url, verification, provenance |
| CopyVariant | Ad text | id, job_id, hook, angle, text, brand_voice_score, status |
| Ad | Approved creative + copy with code | id, seller_id, code, creative_id, copy_variant_id, brief_id, wa_link, status, published_at |
| Contact | Customer identity | id, seller_id, wa_id (hashed index), display_name, opted_out_at, erased_at |
| Conversation | Thread | id, seller_id, contact_id, attributed_ad_id, attribution_status, mode, last_message_at, summary |
| Message | Turn | id, conversation_id, direction, wa_message_id, type, text, transcript, media_ref, normalized, agent_output, delivery_status, is_replay |
| Objection | Mined signal | id, conversation_id, ad_id, tag, quote_redacted, occurred_at |
| Reservation / Booking | Commitments | id, conversation_id, product_id / slot, status, expires_at |
| Handoff | Escalation | id, conversation_id, reason, summary, opened_at, released_at |
| ConsentRecord | Ledger entry (append-only) | id, contact_id, purpose, status, text_shown, response_text, channel, evidence_message_id, recorded_at |
| Brief | Wonder output | id, seller_id, period_from, period_to, insights, recommended_angles, creative_directives, reactivation_segments, model_meta |
| ReactivationCampaign | Preview + send | id, seller_id, brief_id, template_name, status, decisions[], sent_count, blocked_count |
| SendDecision | Audit of eligibility | id, campaign_id, contact_id, decision, reason, next_allowed_at, message_id |
| EvalRun | Harness result | id, suite, replay, status, metrics, started_at, finished_at |
| Job | Generic async op | id, kind, status, progress, result_ref, error |

Full schema in `data-model.md`.

## 6. Non-functional requirements

- **NFR-01 Latency.** Webhook ack ≤ 2 s (p99). Agent text reply end-to-end ≤ 8 s (p50), ≤ 15 s (p95). Image/voice turns ≤ 20 s (p95).
- **NFR-02 Availability for demo.** Replay mode and local WhatsApp mock must work with zero network. Provider health at `GET /health/providers`.
- **NFR-03 Cost.** Per-business monthly cost target ≈ $15 (≈ $10–11 Meta models) at 1,000 turns, 40 ads, 10 videos, 200 voice notes, 300 reactivations; costs tracked per call.
- **NFR-04 Security.** JWT per seller; service token for workers; HMAC on webhooks; secrets only in env; rate limits on public endpoints; no PII in logs (phone numbers masked to last 4 digits).
- **NFR-05 Privacy.** Row-level `seller_id` scoping; PII redaction before any analysis model; no Contributor-mode models.
- **NFR-06 Observability.** Structured JSON logs with `request_id`, `seller_id`, `conversation_id`; model-call ledger; eval metrics endpoint.
- **NFR-07 Testability.** Every provider behind an interface with a recorded fake; `pytest -m "not live"` runs without credentials.
- **NFR-08 i18n.** All customer-facing strings in Spanish (es-CO); API field names and docs in English.

## 7. Success criteria (measured on the demo account before submission)

| Metric | Target |
| --- | --- |
| Invented-price rate (60-case suite) | 0 % |
| Groundedness rate | ≥ 95 % |
| Handoff precision / recall | ≥ 0.9 / ≥ 0.9 |
| Brand-voice mean | ≥ 4.0 / 5 |
| Verifier pass rate on good scenes / block rate on altered set | ≥ 80 % / 100 % |
| Consent scheduler cases | 100 % correct |
| Webhook ack p99 | ≤ 2 s |
| Live pilot sellers onboarded | 3–5 |

## 8. Assumptions and open questions

Items marked **[NEEDS CLARIFICATION]** must be resolved on Day 1–2; implementers use the stated default until then.

- **[NEEDS CLARIFICATION]** Exact Meta Model API model identifiers (Spark 1.3, Wonder, Image, SAM 3.1, Voice Transcribe) and whether the OpenAI-compatible endpoint supports tool calling and structured outputs for Spark. *Default:* model IDs are configuration (`MODEL_*` env), tool calling via OpenAI-compatible `tools`, JSON output via `response_format` with a fallback to prompt-constrained JSON + repair.
- **[NEEDS CLARIFICATION]** Muse Voice Transcribe Spanish support and audio formats (WhatsApp sends OGG/Opus). *Default:* transcode to 16 kHz WAV with ffmpeg before calling.
- **[NEEDS CLARIFICATION]** WhatsApp test number recipient limit (5) and whether the WhatsApp Business app can coexist with Cloud API on the pilot sellers' numbers. *Default:* pilots use the test number; real numbers only if coexistence is confirmed.
- **[NEEDS CLARIFICATION]** Marketing template approval lead time. *Default:* submit the template on Day 1; until approved, reactivation runs in preview mode.
- **[NEEDS CLARIFICATION]** Glimmer hosting on Modal (model size, 4-bit fit on 24 GB). *Default:* brand-voice scoring falls back to a Spark rubric; Glimmer is additive.
- **Assumption:** Supabase provides Postgres + pgvector + storage + JWT; Redis/RQ for the queue (Postgres-backed queue acceptable).
- **Assumption:** The dashboard is a separate Next.js app consuming this API; no server-side rendering of API data.

## 9. Out of scope

Instagram/Marketing API publishing, payments, multi-user roles, non-Spanish locales, Startup-track features, mobile clients.

## 10. Review checklist

- [ ] Every FR has ≥ 1 acceptance scenario and a test ID.
- [ ] No implementation detail leaks into §3–§4 beyond what the contract needs.
- [ ] All `[NEEDS CLARIFICATION]` items have a default.
- [ ] Constitution articles II–V are each enforced by at least one FR (II → FR-060..065; III → FR-024..026, 029; IV → FR-012..013; V → FR-004, NFR-04/05).
- [ ] Contract (`openapi.yaml`) covers every endpoint named in §4.

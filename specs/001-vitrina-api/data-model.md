# Data Model: Vitrina API

Postgres 16 + pgvector. All tables carry `seller_id uuid not null` (except `sellers`, `jobs` by `owner_seller_id`, and `events`), `created_at timestamptz default now()`, and are read through a repository that always filters by the caller's `seller_id`. UUIDs are v7 (time-ordered) for cursor pagination on `(created_at, id)`.

## 1. Tables

### sellers
| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| business_name, owner_name | text | |
| whatsapp_number | text unique | E.164 |
| city, timezone | text | default `America/Bogota` |
| category | text | `products` / `services` |
| business_hours | jsonb | weekday → [[open, close]] |
| handoff_amount_threshold_cop | int | default 300000 |
| brand_voice_min | numeric(2,1) | default 3.5 |
| low_confidence_threshold | numeric(3,2) | default 0.60 |
| brand_voice_profile | jsonb | formality, warmth, emoji_rate, regionalisms[], sample_count |
| ai_label_enabled, handoff_summary_enabled | bool | default true |
| consent_prompt_text | text | default prompt |
| marketing_template_name, marketing_template_status | text | `none|pending|approved|rejected` |
| auth_subject | text unique | Supabase `sub` |

### products
| column | type | notes |
| --- | --- | --- |
| id, seller_id | uuid | |
| name, description | text | |
| price_cop | int | |
| stock | int null | null = not tracked |
| attributes | jsonb | flat string map |
| status | text | `draft|active|archived` |
| source | text | `manual|image_import` |
| amounts_cop | int[] | extracted for the price validator (price + any amounts in description) |
| embedding | vector(1024) | text embedding of name+description+attributes |
| image_embedding | vector(768) null | mean of asset embeddings (for photo matching) |
| search_tsv | tsvector generated | keyword search |

Indexes: `(seller_id, status)`, HNSW on `embedding`, HNSW on `image_embedding`, GIN on `search_tsv`.

### facts
`id, seller_id, kind (faq|shipping|payment|policy|hours|other), text, source, status (draft|active), amounts_cop int[], embedding vector(1024), search_tsv`. Indexes as products.

### assets
`id, seller_id, product_id fk, kind (image|video), storage_key, width, height, duration_s, sha256 unique per seller, image_embedding vector(768) null`.

### creative_jobs
`id, seller_id, product_id, params jsonb, status, stage, progress numeric, error jsonb, brief_id null, finished_at`.

### creatives
`id, job_id fk, seller_id, product_id, format, storage_key, mask_key, source_asset_id, scene_prompt, verification jsonb (status, mask_iou, color_delta, visual_review, thresholds, reason, checked_at), provenance jsonb`.
Index `(seller_id, product_id) where (verification->>'status') = 'passed'`.

### copy_variants
`id, job_id, seller_id, hook, angle, text, brand_voice_score numeric null, brand_voice_judge, status`.

### ads
`id, seller_id, product_id, creative_id, copy_variant_id, brief_id null, code text null, wa_link text null, angle, status (draft|approved|archived), approved_at`.
Constraint: `unique (seller_id, code)`; trigger on approve: `code = next_ad_code(seller_id)` (base-32 counter per seller, prefixed `A`). Check: approving requires `creatives.verification->>'status' = 'passed'` (enforced in service and by a deferred trigger).

### contacts
| column | type | notes |
| --- | --- | --- |
| id, seller_id | uuid | |
| wa_id_hash | bytea | HMAC-SHA256(wa_id, pepper); unique per seller; used for lookup |
| wa_id_enc | bytea null | encrypted phone for sending; nulled on erasure |
| display_name | text null | |
| opted_out_at, erased_at, last_marketing_sent_at | timestamptz null | |
| awaiting_consent_prompt_message_id | uuid null | set when the last outbound had `proposed_consent_prompt` |

### consent_records (append-only)
`id, seller_id, contact_id, purpose (marketing|data_processing), status (granted|revoked), text_shown, response_text, channel, evidence_message_id null, recorded_by (agent|seller|system), recorded_at`.
No UPDATE/DELETE grants for the app role. Current state = latest row per (contact, purpose).

### conversations
`id, seller_id, contact_id, attributed_ad_id null, attribution_status (attributed|unknown_code|none), mode (agent|human|closed), low_confidence_streak int default 0, summary text, last_message_at, is_replay bool`.

### messages
`id, seller_id, conversation_id, direction, sender (customer|agent|seller|system), wa_message_id text null unique, type, text, transcript, media_key, normalized jsonb, agent_output jsonb, validation jsonb, delivery_status, is_replay, raw_payload_id fk webhook_payloads`.
Index `(conversation_id, created_at)`.

### webhook_payloads
`id, received_at, signature_valid bool, body jsonb, processed_at null, error text null`. Retained 30 days.

### handoffs
`id, seller_id, conversation_id, reason, summary, urgency, opened_at, released_at null, seller_notified bool`.

### objections
`id, seller_id, conversation_id, ad_id null, product_id null, tag, quote_redacted, occurred_at`.

### reservations
`id, seller_id, conversation_id, product_id, quantity, variant jsonb, code text, status (reserved|released|fulfilled|expired), expires_at`.
`products.reserved` is a view: `sum(quantity) where status='reserved' and expires_at > now()`.

### bookings
`id, seller_id, conversation_id, service, starts_at, duration_min, customer_name, status`.

### attribution_events
`id, seller_id, ad_id, conversation_id, kind (chat_started|reservation|booking|handoff|objection), occurred_at`. Index `(seller_id, ad_id, occurred_at)`.

### briefs
`id, seller_id, period_from, period_to, status, headline, insights jsonb, recommended_angles jsonb, creative_directives jsonb, reactivation_segments jsonb, dataset_hash, model_calls jsonb`.

### reactivation_campaigns
`id, seller_id, brief_id null, ad_id null, template_name, template_language, segment jsonb, status (preview|scheduled|sending|sent|cancelled), scheduled_for`.

### send_decisions
`id, seller_id, campaign_id, contact_id, decision (allowed|blocked|deferred|sent|failed), reason, reason_text_es, next_allowed_at, message_id null, evaluated_at`.
Index `(campaign_id)`, `(contact_id, evaluated_at)`.

### erasure_receipts
`id, seller_id, wa_id_hash, requested_at, completed_at, scope text`. Contains no PII.

### jobs
`id, owner_seller_id null, kind, status, stage, progress, result_ref, error jsonb, idempotency_key null, started_at, finished_at`.

### idempotency_keys
`key text, seller_id, request_hash, response_status, response_body jsonb, created_at` — pk `(seller_id, key)`; TTL 24 h.

### model_calls (ledger)
`id, seller_id null, request_id, provider, model, purpose (agent|copy|scene|segment|verify|transcribe|brief|judge|classify|embed), tokens_in, tokens_out, latency_ms, cost_usd numeric(10,6), replayed bool, created_at`. Index `(seller_id, created_at)`.

### eval_runs
`id, suite, replay, status, case_count, metrics jsonb, cases jsonb, git_sha, started_at, finished_at`.

### public_showcases
`id, slug unique, product_name, price_cop null, city, ip_hash, status, scenes jsonb, copies jsonb, created_at`. Rate limit table `public_rate_limits(ip_hash, window_start, count)`.

### events
`id bigserial, seller_id, type, payload jsonb, created_at` + `NOTIFY vitrina_events`. Consumers are idempotent on `id`.

## 2. State machines

**Product.status:** `draft → active → archived`; `draft` never retrievable by the agent.
**CreativeJob.stage:** `queued → segmenting → composing → verifying → copywriting → scoring → ready | failed`.
**Verification.status:** `pending → passed | blocked` (terminal).
**Ad.status:** `draft → approved → archived`; `code` assigned on `approved`; blocked creative → `409 CREATIVE_BLOCKED`.
**Conversation.mode:** `agent ⇄ human → closed`. `agent → human` via handoff or takeover; `human → agent` via release.
**Campaign.status:** `preview → scheduled → sending → sent | cancelled`.
**SendDecision.decision:** `allowed → sent | failed`; `deferred → allowed` at `next_allowed_at`; `blocked` terminal for this campaign.
**Job.status:** `queued → running → succeeded | failed | cancelled`.

## 3. Invariants (tested)

1. No row in `messages` with `sender='agent'` exists for a conversation while `mode='human'` after the handoff timestamp.
2. Every `ads` row with `status='approved'` references a creative whose `verification.status='passed'`.
3. Every `send_decisions` row with `decision='sent'` has a `consent_records` row `(contact, marketing, granted)` with `recorded_at < evaluated_at` and no later `revoked`, and `evaluated_at` falls inside legal hours in `America/Bogota`.
4. `consent_records` is append-only (DB grants).
5. After `erased_at` is set, `contacts.wa_id_enc is null`, `display_name is null`, and all `messages.text/transcript/media_key` for that contact are null.
6. Every `agent_output.cited_fact_ids[*]` references an `active` fact or product of the same `seller_id` at the time of the turn.
7. For any agent reply with `validation.price_validator.passed = true`, all amounts in `reply` ⊆ union of `amounts_cop` over cited rows.

## 4. Event payloads

```json
{ "type": "ad.chat_started",   "payload": { "ad_id": "...", "conversation_id": "...", "contact_id": "...", "occurred_at": "..." } }
{ "type": "ad.reservation",    "payload": { "ad_id": "...", "conversation_id": "...", "reservation_id": "...", "product_id": "...", "quantity": 1 } }
{ "type": "ad.booking",        "payload": { "ad_id": "...", "conversation_id": "...", "booking_id": "..." } }
{ "type": "ad.handoff",        "payload": { "ad_id": "...", "conversation_id": "...", "handoff_id": "...", "reason": "..." } }
{ "type": "ad.objection",      "payload": { "ad_id": "...", "conversation_id": "...", "objection_id": "...", "tag": "shipping" } }
{ "type": "objection.recorded","payload": { "objection_id": "...", "tag": "...", "product_id": null } }
{ "type": "consent.recorded",  "payload": { "contact_id": "...", "purpose": "marketing", "consent_id": "..." } }
{ "type": "consent.revoked",   "payload": { "contact_id": "...", "purpose": "marketing", "via": "keyword|tool|api" } }
{ "type": "creative.verified", "payload": { "creative_id": "...", "status": "passed|blocked", "reason": "..." } }
{ "type": "campaign.decision", "payload": { "campaign_id": "...", "contact_id": "...", "decision": "...", "reason": "..." } }
```

`ad_id` may be null when the conversation is unattributed; consumers must handle it.

## 5. Retention and privacy

- `webhook_payloads`: 30 days. `model_calls`: 90 days, no prompt text stored. `messages`: until erasure or seller deletion.
- Phone numbers are stored encrypted (`wa_id_enc`) and looked up by HMAC (`wa_id_hash`); logs show `phone_masked` only.
- Fixtures under `evals/fixtures` use `+57300000xxxx` numbers and invented names.

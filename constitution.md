# Vitrina Constitution

Version 1.0.0 · Ratified 2026-10-27 · Applies to every spec, plan, task and pull request in this repository.

The constitution is the set of rules that no feature spec may override. A spec that conflicts with an article is wrong; the article is not. Amendments require a version bump, a dated entry in the amendment log, and a note in the PR that changes it.

## Article I — Built during the hackathon, in the open

1. Every line of product code is committed on or after 2026-10-26 12:00 ET (hackathon kickoff). Pre-kickoff work is limited to accounts, credentials, provider setup, recruiting pilot sellers and documents (specs, prompts, diagrams).
2. The repository is public from the first commit. `README.md` keeps a dated "Built during the hackathon" timeline that judges can verify against git history.
3. The demo video, README and submission describe only what was built during the event. Third-party and open-source components are credited and clearly separated from original work.

## Article II — Consent before marketing, always

1. A customer having written to the seller is **not** consent to receive marketing. Consent is prior, express, informed, recorded with verbatim text, timestamp, channel and purpose (Ley 1581 de 2012, Decreto 1377 de 2013). Silence never counts.
2. Marketing messages are sent only Monday–Friday 07:00–19:00 and Saturday 08:00–15:00 America/Bogota, never on Sundays or Colombian public holidays (Ley 2300 de 2023). The scheduler enforces this; a human cannot override it through the API.
3. One marketing message per contact per 7 days. "BAJA" (and equivalents) opts out instantly and permanently. "BORRAR MIS DATOS" erases the contact within 24 hours.
4. Every send decision, allowed or blocked, is logged with its reason code so the demo can show a blocked message and explain why.

## Article III — Grounded answers, zero invented prices

1. The sales agent answers only from retrieved seller facts (catalog, FAQ, policies). Every factual claim in a reply cites at least one `fact_id`.
2. A price validator rejects any reply containing a monetary amount that does not appear in the cited facts. The target metric is **0 invented prices** in the evaluation set; a regression fails CI.
3. When confidence is low or no fact supports the answer, the agent says it does not have that information and hands off to the seller. It never guesses.
4. The agent is scoped to the seller's business. It declines unrelated requests (WhatsApp policy on general-purpose assistants, effective 2026-01-15).

## Article IV — Verified creatives, no deception

1. Every generated image is re-segmented and compared against the original product. Images that alter the real product (shape, color, logo, material) are blocked and never shown as approved.
2. No AI-generated testimonials, "customers", reviews or before/after claims. Ever.
3. Generated images carry provenance metadata and an optional visible "imagen generada con IA" label; the label is on by default for public pages (Ley 1480 de 2011 and Meta policy on AI-generated content).

## Article V — Privacy by design

1. No personal data (phone numbers, names, chat content) is committed to the repository, fixtures included. Test fixtures use synthetic contacts (`+57300000xxxx`).
2. Customer chat content is never sent to a model running in a data-sharing ("Contributor") mode. PII is redacted by the self-hosted guardian (Muse Glimmer) before any content leaves the trust boundary for analysis.
3. Data is scoped per seller. Every query is filtered by `seller_id`; cross-seller reads are a bug of the highest severity.

## Article VI — Contracts frozen before parallel work

1. `contracts/openapi.yaml` and `contracts/agent-tools.json` are the source of truth. Code is generated or validated against them; drift fails CI.
2. Contract changes go through a spec amendment (edit spec → regenerate contract → notify all machines) before implementation. No machine changes a shared contract unilaterally.
3. Module boundaries in `plan.md` define ownership. A module talks to another only through its public interface (HTTP contract or the shared `core` package), never through its tables.

## Article VII — Spec → tests → implementation

1. Every functional requirement (`FR-xxx`) has at least one acceptance scenario in `spec.md`, and every scenario has a test that references its ID.
2. The evaluation harness (60 Spanish conversations, fidelity set, consent scheduler cases) runs in CI against recorded model outputs so the suite needs no API keys and no network.
3. A feature is "done" when its scenarios pass, its contract is implemented, and its metrics appear on the `/metrics` page.

## Article VIII — Fail safe in the demo

1. Every external call (Meta Model API, WhatsApp Cloud API, Modal) has a timeout, a retry policy for `429`/`5xx`, and a recorded fallback (replay mode).
2. Outbound WhatsApp messages respect 1 message per 6 seconds per recipient.
3. The system degrades to a local WhatsApp mock when Meta is unreachable; the demo never depends on a single live call.

## Amendment log

| Version | Date | Change |
| --- | --- | --- |
| 1.0.0 | 2026-10-27 | Initial ratification. |

# Vitrina

**Tus fotos se vuelven anuncios, tus anuncios se vuelven ventas, y cada conversación mejora el próximo anuncio.**
Your photos become ads, your ads become sales, and every conversation improves the next ad.

Vitrina is a closed-loop AI storefront for WhatsApp sellers in Colombia, built in the open during the **Meta Global AI Developer Hackathon** (Indie Developer Track, theme "Build for Your World"), October 27 – November 4, 2026.

## The loop

1. **Catalog.** The seller pastes prices and FAQ and uploads two or three phone photos per product.
2. **Creative.** SAM 3.1 segments the product, Muse Image composes ad scenes, and a fidelity verifier re-segments every scene and blocks any image that alters the real product (shape, color, logo, material). Muse Spark writes the copy grid; each approved ad gets a short code and a tracked `wa.me` link.
3. **Conversation.** A sales agent built on Muse Spark answers customers only from the seller's catalog. Every price in a reply is checked against the cited facts; when the data is missing, the agent says so and hands off to the seller. Voice notes are transcribed with Muse Voice Transcribe.
4. **Learning.** Objections are mined from conversations and, each week, Muse Wonder turns them into a brief that drives the next ads.
5. **Reactivation.** Only contacts with recorded consent are reached, inside Colombia's legal marketing hours, with every send decision logged with its reason code.

## Status

Day 1 (2026-10-27): repository bootstrapped with the constitution, the feature spec and the frozen API contracts. Code lands task by task; see `specs/001-vitrina-api/tasks.md`.

## Built during the hackathon

Constitution Article I: every line of product code is committed after the kickoff (Mon Oct 26, 12:00 ET). Pre-kickoff work was limited to accounts, provider setup and documents. `git log --date=iso --format='%ad %s'` is the ground truth for this table.

| Date (ET) | What landed |
| --- | --- |
| 2026-10-26 12:00 | Kickoff |
| 2026-10-27 | Constitution v1.0.0, feature spec, data model, task list and contracts (`openapi.yaml` 0.1.0, `agent-tools.json` 0.1.0) committed. Repository public from the first commit. |

## Repository

- `constitution.md` — the rules no spec may override.
- `specs/001-vitrina-api/` — `spec.md`, `plan.md`, `data-model.md`, `tasks.md`, `contracts/openapi.yaml`, `contracts/agent-tools.json`. `specs/README.md` explains the layout.
- `AGENTS.md` — how coding agents work in this repository.
- `apps/`, `packages/`, `providers/`, `evals/` — arrive with the first tasks (see `plan.md` §2).

## Meta models

Muse Spark (sales agent, copywriting, visual review), Muse Wonder (weekly brief), Muse Image (ad scenes), SAM 3.1 (segmentation and fidelity verification), Muse Voice Transcribe (voice notes), Muse Glimmer (open weights, self-hosted: PII guardian and brand-voice judge). Model-by-model usage, costs and evaluation results are added to this README as the integrations land.

## Running without keys

The evaluation suites run in replay mode against recorded model outputs, so CI needs no provider credentials. Instructions arrive with the evaluation harness.

## Legal

Consent before any marketing (Ley 1581 de 2012, Decreto 1377 de 2013), legal sending hours (Ley 2300 de 2023), no misleading advertising (Ley 1480 de 2011), and the WhatsApp Business policies. How each rule is enforced in code is documented as the compliance module lands.

## License

MIT. Third-party components are credited here as they are added.

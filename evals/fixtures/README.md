# Evaluation fixtures

Recorded inputs for the evaluation suites and replay mode (plan.md §4.5). Synthetic data only: contacts use `+57300000xxxx` numbers and invented names (constitution Art. V.1).

| Path | Contents | Tasks |
| --- | --- | --- |
| `conversations_es.jsonl` | 60 Spanish conversations: grounded answers, invented-price traps, handoff triggers, off-topic requests, voice-note transcripts, image matches | T028 (cases 1–20), T055 (21–60) |
| `sellers/*.json` | The synthetic businesses those conversations talk to: seller, products and facts with stable keys (drafts included, which the agent must never cite) | T028 |
| `fidelity/` | The `fidelity` suite: one synthetic product photo (`source.jpg`, made with Muse Image) and its SAM mask; 10 good scenes (Muse Image output through the compose stage) and 10 altered ones (colour, material, shape, an added logo, a second unit); `cases.json` lists them. SAM's masks and Spark's reviews for them are cassettes in `evals/cassettes` | T033, T072 |
| `consent_cases.yaml` | 40 time and consent cases for the eligibility evaluator | T063, T065 |
| `webhooks/*.json` | Recorded WhatsApp webhook payloads, also used as replay fixtures | T012, T080 |

## conversations_es.jsonl

One case per line: `case_id`, `category` (`grounded`, `price_trap`, `handoff`, `off_topic`), `business` (a file in `sellers/`), `messages` (the customer's turns; the last reply is scored) and `expected`:

| Field | Meaning |
| --- | --- |
| `cites` | fixture keys the reply must cite (groundedness, R3) |
| `forbidden_cites` | keys it must not cite, e.g. drafts (FR-003) |
| `forbidden_amounts` | amounts it must not state, besides any amount no cited active item carries (R2, AS-7.2) |
| `handoff` | `false`, `true`, a `HandoffReason`, or `null` when the case is not labelled for handoff metrics (R5) |
| `off_topic` | the reply must decline (R10) |

`make evals` runs the suite in replay mode (`uv run python -m vitrina_evals --help`); ids come from the fixture keys, so prompts and cassettes repeat exactly across runs.

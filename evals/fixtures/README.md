# Evaluation fixtures

Recorded inputs for the evaluation suites and replay mode (plan.md §4.5). Synthetic data only: contacts use `+57300000xxxx` numbers and invented names (constitution Art. V.1).

| Path | Contents | Tasks |
| --- | --- | --- |
| `conversations_es.jsonl` | 60 Spanish conversations: grounded answers, invented-price traps, handoff triggers, off-topic requests, voice-note transcripts, image matches | T028 (cases 1–20), T055 (21–60) |
| `fidelity/good/`, `fidelity/altered/` | 10 good scenes and 10 altered scenes for the fidelity verifier | T033 |
| `consent_cases.yaml` | 40 time and consent cases for the eligibility evaluator | T063, T065 |
| `webhooks/*.json` | Recorded WhatsApp webhook payloads, also used as replay fixtures | T012, T080 |

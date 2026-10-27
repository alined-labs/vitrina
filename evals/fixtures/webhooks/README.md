# WhatsApp webhook payloads

One file per webhook `POST` body from the WhatsApp Cloud API (T012); replay sessions build on them (T080).

- **Provenance:** synthesized on 2026-10-27 from Meta's documented payload shapes, because the test number is not provisioned yet. Replace them with scrubbed captures from the test number once it is live, keeping the file names.
- **Synthetic contacts only** (constitution Art. V.1): customers are `+57300000xxxx` with invented names, the business number is `+573000000000`, and message, media and account IDs are visibly fake. `providers/whatsapp/tests/test_fixtures.py` fails on any other number.
- **Wire bytes:** non-ASCII text is escaped as `\uXXXX`, as Meta sends it. Tests sign the file bytes with `vitrina_whatsapp.signature.sign`.

| File | Shows |
| --- | --- |
| `text_ad_code_a7.json` | First message from an ad link, `Hola, vi el anuncio #A7` (AS-3.2) |
| `text_question_shipping.json` | `¿cuánto vale el envío a Medellín?` (AS-3.3) |
| `text_opt_out_baja.json` | Opt-out keyword `BAJA` (AS-6.5) |
| `image_with_caption.json` | Customer photo with a caption (AS-3.5) |
| `audio_voice_note.json` | OGG/Opus voice note (AS-3.6) |
| `interactive_button_reply.json`, `interactive_list_reply.json` | Taps on our reply buttons and list rows |
| `button_quick_reply.json` | Tap on a template quick-reply button |
| `reaction.json`, `unsupported.json` | Types the agent does not read (`unsupported`) |
| `batch_two_customers.json` | Two customers in one delivery; contacts listed in reverse order |
| `status_sent.json`, `status_delivered.json`, `status_read.json` | Delivery status callbacks for one outbound message (FR-028) |
| `status_failed_reengagement.json` | Failed send outside the 24 h window, error 131047 |
| `template_status_update.json` | A non-`messages` field, ignored by the adapter |

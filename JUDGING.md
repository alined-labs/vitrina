# Judging Vitrina in ten minutes

Vitrina is a sales assistant for small sellers in Colombia who sell on WhatsApp. It turns phone photos into ads checked against the real product. It answers customers only from the seller's catalog, with rules outside the model. It learns from every chat which ad to make next. The product speaks Colombian Spanish because its users do. This guide is in English, and the screens carry short English lines.

**Why you cannot message it on WhatsApp.** The deployment uses Meta's WhatsApp test number, which can only message up to five verified phones (see Known limits in the README). Instead, the dashboard lets you play the customer. Your messages go through the same agent, the same rules and the same pipeline a WhatsApp message goes through. Nothing is sent.

## Access

- Live site: **https://vitrina.alinedlabs.co**
- Login, needed for every page except `/foto`: email `alinedmooner@gmail.com`, password `H3{0B=@q_8>M`.
- That account holds the demo shop, **Mochilas Ana** (woven bags, Medellín). Change anything you like. Other visitors share it, so you may see their changes.
- Paid model calls have daily limits per shop. If you hit one, a Spanish notice asks you to try again later.

## 1 · Play the customer (3 min)

Open **Probar como cliente** (*try as a customer*) in the left menu.

1. Click the suggested questions one by one. For each, the page shows what the customer would receive, the facts the reply cites, and three checks that run outside the model.

   | Check | What it does |
   | --- | --- |
   | Precios (price) | Every amount in the reply must appear in a cited fact. |
   | Respaldo (groundedness) | Every claim must come from a cited fact. |
   | Tema (scope) | The agent only talks about this shop. |

   | Suggested question | What to look for |
   | --- | --- |
   | ¿Cuánto vale la mochila azul? *(How much is the blue bag?)* | The price is cited from the catalog: $85.000. |
   | ¿Me deja la mochila azul en 60 mil? *(Will you let me have it for 60,000?)* | No fact gives that price, so the agent does not agree. It hands the chat to the owner, Ana. |
   | ¿Cuánto cuesta el envío a Cali? *(Shipping to Cali?)* | Answered from the shop's shipping fact. |
   | ¿Me ayudas con mi tarea de matemáticas? *(Help with my math homework?)* | Declined as off topic. |

2. Try to make it invent something: a discount, a price, free shipping, a product the shop does not sell. When the model's draft breaks a rule, the page shows the draft and the safe reply that replaced it.
3. Under **Conversaciones de prueba** (*test conversations*), press **Reproducir · Play** on «Anuncio #A7, precio y regateo». A recorded customer arrives from ad #A7, asks a price and haggles. The agent answers live. Open the chat to see it.

## 2 · The owner's side (2 min)

**Conversaciones** (*conversations*): every chat with the ad it came from, the facts each reply cited, and the chats waiting for the owner, each with a summary. Open one, press **Tomar conversación** (*take over*), type a reply, then **Devolver al agente** (*give it back to the agent*).

## 3 · Ads checked against the real product (2 min)

- **Creativos** (*creatives*): a job turns one phone photo into ad scenes. SAM 3.1 cuts the product out, Muse Image composes the scenes, and a verifier compares every scene with the original: mask overlap, color distance and a visual review. A scene that changes the product is **bloqueado** (*blocked*), with the reason written out, and cannot become an ad. Muse Spark writes copy variants in the shop's voice.
- **Anuncios** (*ads*): approved ads with their code (#A1 … #A7) and a WhatsApp link that carries the code. That code is how a chat is attributed to the ad that started it.
- The public page **https://vitrina.alinedlabs.co/foto** does the same for anyone, with no login: upload a product photo and get a shareable showcase.

## 4 · Consent and Colombian marketing law (1 min)

- **Contactos** (*contacts*): each customer's consent, stored with the exact question asked and the customer's literal answer (Ley 1581).
- **Campañas** (*campaigns*): a reactivation preview gives every contact a decision with its legal reason. Examples:
  - *Sin autorización* (no consent): Ley 1581.
  - *Fuera del horario legal* (outside legal marketing hours): Ley 2300, with the next allowed time.

  Nothing is sent from the preview.

## 5 · Learning and measurement (2 min)

- **Informe semanal** (*weekly brief*): Muse Wonder reads the objections and which ad brought each chat, and proposes the next ads. **Crear estos anuncios y la campaña** (*create these ads and the campaign*) starts those creative jobs.
- **Métricas** (*metrics*): agent quality, verifier results and model cost per shop.
- The README's Evaluation table has the final run: 0 % invented prices across 336 recorded cases, and 100 % of altered scenes blocked.

## Screen names

| Spanish | English |
| --- | --- |
| Catálogo | Catalog: products, prices and the facts the agent may cite |
| Creativos | Generated ad scenes and their verification |
| Anuncios | Approved ads with codes |
| Conversaciones | Customer chats |
| Probar como cliente | Try the agent as a customer |
| Contactos | Customers and their consent |
| Campañas | Reactivation campaigns |
| Informe semanal | Weekly brief |
| Métricas | Metrics |
| Pasó / Falló | Passed / Failed |
| Pasa a quien atiende el negocio | Handed to the owner |

## What is live and what is recorded

- **Live:** every agent answer on this site, the creative jobs and the brief. They are calls to the Meta Model API: Muse Spark, Muse Image, SAM 3.1 and Muse Wonder.
- **Recorded:** only the customer's messages in «Conversaciones de prueba», and the model outputs that `make evals` replays in CI.
- **Limits:** the README lists what the rules still miss and what is not built.

"""HTML phone view at /mock/whatsapp for demos and screen recording (plan.md §4.6).

Outside the contract: the router is excluded from the OpenAPI document. Only synthetic
numbers (+57300000xxxx) are accepted (constitution Art. V.1).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from vitrina_local_mock.messenger import MockEntry, MockOutbox

SYNTHETIC_NUMBER = r"^\+57300000\d{4}$"


class MockInbound(BaseModel):
    contact: Annotated[str, Field(pattern=SYNTHETIC_NUMBER)]
    text: Annotated[str, Field(min_length=1, max_length=4096)]


InboundHandler = Callable[[MockInbound], Awaitable[None]]


def create_phone_router(outbox: MockOutbox, on_inbound: InboundHandler | None = None) -> APIRouter:
    """``on_inbound`` feeds typed messages into the inbound pipeline once it exists."""
    router = APIRouter(prefix="/mock/whatsapp", include_in_schema=False)

    @router.get("", response_class=HTMLResponse)
    def phone() -> str:
        return PHONE_HTML

    @router.get("/messages")
    def messages(contact: Annotated[str, Query(pattern=SYNTHETIC_NUMBER)]) -> list[MockEntry]:
        return outbox.entries(contact=contact)

    @router.post("/inbound", status_code=202)
    async def inbound(message: MockInbound) -> dict[str, str]:
        entry = await asyncio.to_thread(outbox.record_inbound, message.contact, message.text)
        if on_inbound is not None:
            await on_inbound(message)
        return {"status": "accepted", "id": entry.id}

    return router


PHONE_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vitrina · simulador de WhatsApp</title>
<style>
  :root { --green: #075e54; --mine: #dcf8c6; --theirs: #ffffff; --bg: #ece5dd; --ink: #111b21; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #1f2c33;
         font: 15px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--ink); }
  .phone { width: min(380px, 100vw); height: min(760px, 100vh); display: flex; flex-direction: column;
           background: var(--bg); border-radius: 28px; overflow: hidden; box-shadow: 0 18px 50px #0008; }
  header { background: var(--green); color: #fff; padding: 14px 16px 10px; }
  header h1 { margin: 0; font-size: 16px; }
  header p { margin: 2px 0 8px; font-size: 12px; opacity: .8; }
  header input { width: 100%; border: 0; border-radius: 8px; padding: 6px 8px; font: inherit; }
  main { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
  .msg { max-width: 82%; padding: 6px 9px 4px; border-radius: 9px; box-shadow: 0 1px .5px #0002;
         white-space: pre-wrap; overflow-wrap: anywhere; }
  .inbound { align-self: flex-end; background: var(--mine); }
  .outbound { align-self: flex-start; background: var(--theirs); }
  .msg img { display: block; max-width: 100%; border-radius: 6px; margin-bottom: 4px; }
  .meta { display: block; text-align: right; font-size: 11px; opacity: .6; }
  .tag { font-size: 12px; opacity: .7; }
  form { display: flex; gap: 8px; padding: 10px; background: #f0f2f5; }
  form input { flex: 1; border: 0; border-radius: 20px; padding: 10px 14px; font: inherit; }
  form button { border: 0; border-radius: 20px; padding: 0 16px; background: var(--green); color: #fff; font: inherit; }
</style>
</head>
<body>
<div class="phone">
  <header>
    <h1>Vitrina · simulador de WhatsApp</h1>
    <p>Local: ningún mensaje sale a WhatsApp.</p>
    <input id="contact" value="+573000000001" aria-label="Número del cliente (sintético)">
  </header>
  <main id="thread" aria-live="polite"></main>
  <form id="composer">
    <input id="text" placeholder="Escribe un mensaje" autocomplete="off" aria-label="Mensaje">
    <button type="submit">Enviar</button>
  </form>
</div>
<script>
  const base = location.pathname.replace(/\\/$/, "");
  const thread = document.getElementById("thread");
  const contact = document.getElementById("contact");
  let last = "";

  function bubble(entry) {
    const node = document.createElement("div");
    node.className = "msg " + entry.direction;
    const image = entry.image_data_uri || entry.image_url;
    if (image) {
      const img = document.createElement("img");
      img.src = image;
      img.alt = entry.caption || "imagen";
      node.append(img);
    }
    const body = entry.text || entry.caption || "";
    if (body) node.append(document.createTextNode(body));
    if (entry.kind === "template") {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "plantilla: " + entry.template_name;
      node.append(tag);
    }
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = new Date(entry.created_at).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" });
    node.append(meta);
    return node;
  }

  async function refresh() {
    const response = await fetch(base + "/messages?contact=" + encodeURIComponent(contact.value));
    if (!response.ok) return;
    const entries = await response.json();
    const signature = JSON.stringify(entries.map((entry) => entry.id));
    if (signature === last) return;
    last = signature;
    thread.replaceChildren(...entries.map(bubble));
    thread.scrollTop = thread.scrollHeight;
  }

  document.getElementById("composer").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await fetch(base + "/inbound", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contact: contact.value, text }),
    });
    refresh();
  });
  contact.addEventListener("change", () => { last = ""; refresh(); });
  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>
"""

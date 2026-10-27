"""The T011 spike: what the Meta Model API supports, checked live (research/OPEN.md Q01, Q06).

Needs META_API_KEY and MODEL_AGENT (MODEL_EMBED for the embedding check). Run by hand and
copy the outcome, including the notes printed by passing checks, into research/findings/:

    uv run --env-file .env pytest -m live providers/meta_model_api -rA

Every prompt is synthetic: no customer text reaches a model from here (Art. V.2). Each check
is one or two short calls with a small token cap, to protect the credit (Q10).
"""

import json
import struct
import sys
import zlib

import openai
import pytest

from vitrina_core.providers import (
    Blob,
    ChatMessage,
    ChatRequest,
    EmbeddingRequest,
    ImagePart,
    ResponseFormat,
    TextPart,
    ToolSpec,
)
from vitrina_core.settings import REPO_ROOT, Settings
from vitrina_meta_model_api import MetaChatModel, MetaEmbedder, meta_client

pytestmark = pytest.mark.live

AGENT_TOOLS = REPO_ROOT / "specs" / "001-vitrina-api" / "contracts" / "agent-tools.json"
SYSTEM = "Eres el asistente de ventas de Tienda Ejemplo, un negocio ficticio en Bogotá."


@pytest.fixture
def settings() -> Settings:
    config = Settings()
    if config.meta_api_key is None or not config.model_agent:
        pytest.skip("needs META_API_KEY and MODEL_AGENT")
    return config


@pytest.fixture
def chat(settings: Settings) -> MetaChatModel:
    return MetaChatModel(meta_client(settings))


def note(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def ask(settings: Settings, *messages: ChatMessage, **options: object) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "model": settings.model_id("agent"),
            "purpose": "agent",
            "messages": (ChatMessage(role="system", content=SYSTEM), *messages),
            "max_tokens": 200,
            **options,
        }
    )


def contract(name: str) -> dict[str, object]:
    definitions: dict[str, object] = json.loads(AGENT_TOOLS.read_text(encoding="utf-8"))[
        "definitions"
    ]
    return definitions[name]  # type: ignore[return-value]


def solid_png(rgb: tuple[int, int, int], side: int = 64) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = (b"\x00" + bytes(rgb) * side) * side
    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


async def test_live_the_models_endpoint_lists_the_configured_agent(settings: Settings) -> None:
    client = meta_client(settings)
    try:
        ids = sorted([model.id async for model in client.models.list()])
    except openai.NotFoundError:
        pytest.skip("no /models endpoint: copy the IDs from the Meta Model API console (Q01)")
    note(f"models: {', '.join(ids)}")
    assert settings.model_agent in ids


async def test_live_spark_answers_and_reports_usage(
    settings: Settings, chat: MetaChatModel
) -> None:
    response = await chat.complete(
        ask(settings, ChatMessage(role="user", content="Responde solo con la palabra: hola"))
    )

    meta = response.meta
    note(f"chat: {meta.latency_ms} ms, {meta.tokens_in} in, {meta.tokens_out} out")
    assert isinstance(response.message.content, str)
    assert "hola" in response.message.content.lower()
    assert meta.tokens_in > 0


async def test_live_spark_stops_at_the_token_cap(settings: Settings, chat: MetaChatModel) -> None:
    response = await chat.complete(
        ask(
            settings,
            ChatMessage(role="user", content="Cuenta una historia larga sobre una mochila."),
            max_tokens=5,
        )
    )

    note(f"token cap: finish={response.finish_reason}, out={response.meta.tokens_out}")
    assert response.finish_reason == "length"


async def test_live_spark_calls_search_catalog_and_takes_the_result_back(
    settings: Settings, chat: MetaChatModel
) -> None:
    tools: list[dict[str, dict[str, object]]] = contract("tools")  # type: ignore[assignment]
    [search] = [tool["function"] for tool in tools if tool["function"]["name"] == "search_catalog"]
    spec = ToolSpec(
        name=str(search["name"]),
        description=str(search["description"]),
        parameters=search["parameters"],  # type: ignore[arg-type]
    )
    question = ChatMessage(role="user", content="¿Cuánto cuesta la mochila azul?")

    first = await chat.complete(ask(settings, question, tools=(spec,), tool_choice="required"))

    note(f"tool call: finish={first.finish_reason}, calls={len(first.message.tool_calls)}")
    assert first.message.tool_calls, "no tool call: tool calling is not supported (ADR-0003)"
    call = first.message.tool_calls[0]
    assert call.name == "search_catalog"
    assert "query" in json.loads(call.arguments)

    result = {
        "results": [{"id": "f-1", "kind": "product", "text": "Mochila azul: $85.000", "score": 0.9}]
    }
    second = await chat.complete(
        ask(
            settings,
            question,
            ChatMessage(role="assistant", tool_calls=first.message.tool_calls),
            ChatMessage(role="tool", tool_call_id=call.id, content=json.dumps(result)),
            tools=(spec,),
        )
    )

    note(f"after the tool: finish={second.finish_reason}")
    assert isinstance(second.message.content, str)
    assert "85" in second.message.content


async def test_live_spark_follows_a_small_strict_schema(
    settings: Settings, chat: MetaChatModel
) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["altered", "reason"],
        "properties": {"altered": {"type": "boolean"}, "reason": {"type": "string"}},
    }
    response = await chat.complete(
        ask(
            settings,
            ChatMessage(role="user", content="¿Una mochila roja pintada de azul cambió de color?"),
            response_format=ResponseFormat(name="visual_review", json_schema=schema),
        )
    )

    assert isinstance(response.message.content, str)
    answer = json.loads(response.message.content)
    assert set(answer) == {"altered", "reason"}
    assert isinstance(answer["altered"], bool)


async def test_live_spark_takes_agent_turn_output_verbatim(
    settings: Settings, chat: MetaChatModel
) -> None:
    # ADR-0005's primary path: agent-tools.json#/definitions/AgentTurnOutput as is.
    response = await chat.complete(
        ask(
            settings,
            ChatMessage(role="user", content="Hola, ¿a qué hora abren?"),
            response_format=ResponseFormat(
                name="agent_turn_output",
                json_schema=contract("AgentTurnOutput"),  # type: ignore[arg-type]
            ),
            max_tokens=400,
        )
    )

    assert isinstance(response.message.content, str)
    answer = json.loads(response.message.content)
    assert {"reply", "cited_fact_ids", "confidence"} <= set(answer)


async def test_live_spark_reads_an_image(settings: Settings, chat: MetaChatModel) -> None:
    red = Blob(data=solid_png((220, 20, 20)), mime_type="image/png")
    response = await chat.complete(
        ask(
            settings,
            ChatMessage(
                role="user",
                content=(
                    TextPart(text="¿De qué color es esta imagen? Responde con una palabra."),
                    ImagePart(image=red),
                ),
            ),
            max_tokens=20,
        )
    )

    note(f"vision: {response.message.content!r}")
    assert isinstance(response.message.content, str)
    assert "roj" in response.message.content.lower()


async def test_live_embeddings_return_one_vector_per_text(settings: Settings) -> None:
    if not settings.model_embed:
        pytest.skip("needs MODEL_EMBED (Q06)")
    embedder = MetaEmbedder(meta_client(settings))

    response = await embedder.embed(
        EmbeddingRequest(model=settings.model_embed, texts=("mochila azul", "envíos a Medellín"))
    )

    sizes = {len(vector) for vector in response.vectors}
    note(
        f"embeddings: {sizes} dimensions (data-model.md stores 1024), {response.meta.tokens_in} in"
    )
    assert len(response.vectors) == 2
    assert len(sizes) == 1

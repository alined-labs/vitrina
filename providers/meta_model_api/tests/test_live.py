"""The T011 spike: what the Meta Model API supports, checked live (Q01, Q06).

Needs META_API_KEY and MODEL_AGENT (MODEL_EMBED for the embedding check). Run by hand and
keep the outcome, including the notes printed by passing checks:

    uv run --env-file .env pytest -m live providers/meta_model_api/tests/test_live.py -rA

Every prompt is synthetic: no customer text reaches a model from here (Art. V.2). Each check
is one or two short calls at low reasoning effort, to protect the credit (Q10). Spark reasons
before it answers and the reasoning counts against the token cap, so the cap leaves it room.
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
    ToolCall,
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
            "max_tokens": 4000,
            "reasoning_effort": "low",
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

    # tool_choice "auto" is the only value the API takes.
    first = await chat.complete(ask(settings, question, tools=(spec,), tool_choice="auto"))

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
        )
    )

    assert isinstance(response.message.content, str)
    answer = json.loads(response.message.content)
    assert {"reply", "cited_fact_ids", "confidence"} <= set(answer)


async def test_live_spark_uses_tools_and_agent_turn_output_in_one_request(
    settings: Settings, chat: MetaChatModel
) -> None:
    # The engine's request shape (vitrina_conversation.engine): every contract tool, tool_choice
    # auto and AgentTurnOutput as a strict response_format, together in each round.
    tools: list[dict[str, dict[str, object]]] = contract("tools")  # type: ignore[assignment]
    specs = tuple(
        ToolSpec(
            name=str(tool["function"]["name"]),
            description=str(tool["function"]["description"]),
            parameters=tool["function"]["parameters"],  # type: ignore[arg-type]
        )
        for tool in tools
    )
    output = ResponseFormat(
        name="AgentTurnOutput",
        json_schema=contract("AgentTurnOutput"),  # type: ignore[arg-type]
    )
    rule = ChatMessage(
        role="system",
        content="Busca con search_catalog antes de hablar de un producto o un precio, y cita los "
        "ids de los hechos que uses en cited_fact_ids.",
    )
    question = ChatMessage(role="user", content="¿Cuánto cuesta la mochila azul?")
    options: dict[str, object] = {"tools": specs, "tool_choice": "auto"}

    first = await chat.complete(ask(settings, rule, question, response_format=output, **options))

    names = [call.name for call in first.message.tool_calls]
    note(f"tools + schema, round 1: finish={first.finish_reason}, calls={names}")
    assert names, f"answered without a tool call: {first.message.content!r}"
    call = first.message.tool_calls[0]
    assert call.name == "search_catalog"

    fact_id = "0192f0c4-7d3e-7a10-9b1e-3c5d2a4f6b70"
    result = {"results": [{"id": fact_id, "kind": "product", "text": "Mochila azul: $85.000"}]}
    second = await chat.complete(
        ask(
            settings,
            rule,
            question,
            ChatMessage(role="assistant", tool_calls=first.message.tool_calls),
            ChatMessage(role="tool", tool_call_id=call.id, content=json.dumps(result)),
            response_format=output,
            **options,
        )
    )

    note(f"tools + schema, round 2: finish={second.finish_reason}, {second.message.content!r}")
    assert isinstance(second.message.content, str)
    answer = json.loads(second.message.content)
    assert "85" in answer["reply"]
    assert answer["cited_fact_ids"] == [fact_id]
    assert 0 <= answer["confidence"] <= 1


async def test_live_tool_choice_none_answers_from_the_tool_history(
    settings: Settings, chat: MetaChatModel
) -> None:
    # The engine's last round and its repair call: tools are off, the history keeps the call.
    call = ToolCall(id="call_1", name="search_catalog", arguments='{"query": "mochila azul"}')
    result = {"results": [{"id": "f-1", "kind": "product", "text": "Mochila azul: $85.000"}]}
    spec = ToolSpec(
        name="search_catalog",
        description="Buscar productos y datos del negocio.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )

    response = await chat.complete(
        ask(
            settings,
            ChatMessage(role="user", content="¿Cuánto cuesta la mochila azul?"),
            ChatMessage(role="assistant", tool_calls=(call,)),
            ChatMessage(role="tool", tool_call_id=call.id, content=json.dumps(result)),
            tools=(spec,),
            tool_choice="none",
        )
    )

    note(f"tool_choice none: finish={response.finish_reason}, {response.message.content!r}")
    assert not response.message.tool_calls
    assert isinstance(response.message.content, str)
    assert "85" in response.message.content


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

"""Chat completion with tools and structured output (Muse Spark, Glimmer, the Baseten fallback)."""

from typing import Annotated, Any, Literal, Protocol

from pydantic import Field

from vitrina_core.providers.base import Blob, CallMeta, Frozen, ModelPurpose
from vitrina_core.settings import ReasoningEffort


class TextPart(Frozen):
    type: Literal["text"] = "text"
    text: str


class ImagePart(Frozen):
    type: Literal["image"] = "image"
    image: Blob


ContentPart = Annotated[TextPart | ImagePart, Field(discriminator="type")]


class ToolCall(Frozen):
    id: str
    name: str
    arguments: str = Field(description="JSON text exactly as the model produced it")


class ChatMessage(Frozen):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[ContentPart, ...] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


class ToolSpec(Frozen):
    """One tool in OpenAI-compatible form; ``parameters`` is passed verbatim (agent-tools.json)."""

    name: str
    description: str
    parameters: dict[str, Any]


class ResponseFormat(Frozen):
    """Structured output: ``response_format`` with a JSON schema (ADR-0005)."""

    name: str
    json_schema: dict[str, Any]
    strict: bool = True


class ChatRequest(Frozen):
    model: str
    purpose: ModelPurpose
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    # "none" keeps the tools out of the call; providers take nothing else but "auto".
    tool_choice: Literal["auto", "none"] | None = None
    response_format: ResponseFormat | None = None
    temperature: float | None = None
    # Reasoning tokens count against this cap: too small a cap leaves no room for the answer.
    max_tokens: int | None = None
    reasoning_effort: ReasoningEffort | None = None


class ChatResponse(Frozen):
    message: ChatMessage
    finish_reason: str | None = None
    meta: CallMeta


class ChatModel(Protocol):
    async def complete(self, request: ChatRequest) -> ChatResponse: ...

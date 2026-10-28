"""Chat completions: Muse Spark for the agent, the copy grid and the visual review."""

import base64
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageFunctionToolCall

from vitrina_core.providers import (
    CallMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ContentPart,
    ImagePart,
    ProviderName,
    ProviderUnavailableError,
    ToolCall,
)
from vitrina_meta_model_api.client import call, tokens


class MetaChatModel:
    """``ChatModel`` on an OpenAI-compatible ``/chat/completions`` (ADR-0003).

    With their own client and provider name it also serves the other OpenAI-compatible
    endpoints (Spark Open on Baseten, Glimmer on Modal).
    """

    def __init__(self, client: AsyncOpenAI, *, provider: ProviderName = "meta_model_api") -> None:
        self.client = client
        self.provider = provider

    async def complete(self, request: ChatRequest) -> ChatResponse:
        body = _body(request)
        completion, latency_ms = await call(
            self.provider,
            lambda: self.client.post("/chat/completions", body=body, cast_to=ChatCompletion),
        )
        if not completion.choices:
            raise ProviderUnavailableError(
                f"{self.provider} answered without choices", provider=self.provider
            )
        choice = completion.choices[0]
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=choice.message.content,
                tool_calls=tuple(
                    ToolCall(id=item.id, name=item.function.name, arguments=item.function.arguments)
                    for item in choice.message.tool_calls or ()
                    if isinstance(item, ChatCompletionMessageFunctionToolCall)
                ),
            ),
            finish_reason=choice.finish_reason,
            meta=CallMeta(
                provider=self.provider,
                model=request.model,
                purpose=request.purpose,
                tokens_in=tokens(completion.usage, "prompt_tokens"),
                tokens_out=tokens(completion.usage, "completion_tokens"),
                latency_ms=latency_ms,
            ),
        )


def _body(request: ChatRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": request.model,
        "messages": [_message(message) for message in request.messages],
    }
    # The API takes tool_choice "auto" only (400 for "none", "required" or a named tool):
    # "none" is a call without tools, which still accepts the tool calls in the history.
    if request.tools and request.tool_choice != "none":
        # agent-tools.json ``parameters`` travel verbatim (ADR-0003).
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    if request.tool_choice == "auto":
        body["tool_choice"] = request.tool_choice
    if request.response_format is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.response_format.name,
                "schema": request.response_format.json_schema,
                "strict": request.response_format.strict,
            },
        }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_tokens is not None:
        body["max_completion_tokens"] = request.max_tokens
    if request.reasoning_effort is not None:
        body["reasoning_effort"] = request.reasoning_effort
    if request.prompt_cache_key is not None:
        body["prompt_cache_key"] = request.prompt_cache_key
    return body


def _message(message: ChatMessage) -> dict[str, Any]:
    content = message.content
    wire: dict[str, Any] = {
        "role": message.role,
        "content": [_part(part) for part in content] if isinstance(content, tuple) else content,
    }
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {"name": tool_call.name, "arguments": tool_call.arguments},
            }
            for tool_call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _part(part: ContentPart) -> dict[str, Any]:
    if isinstance(part, ImagePart):
        data = base64.b64encode(part.image.data).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{part.image.mime_type};base64,{data}"},
        }
    return {"type": "text", "text": part.text}

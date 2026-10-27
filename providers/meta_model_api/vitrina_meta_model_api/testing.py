"""A fake Meta Model API for tests: an httpx2 mock transport behind the real SDK client.

Answers follow the OpenAI chat-completions and embeddings formats; the first live call
re-checks them (research/OPEN.md Q01).
"""

import json
from typing import Any

import httpx2
from openai import AsyncOpenAI
from pydantic import SecretStr

from vitrina_core.settings import Settings
from vitrina_meta_model_api.client import meta_client

BASE_URL = "https://meta.test/v1"


class FakeApi:
    """Records each request and answers from a queue of responses or exceptions."""

    def __init__(self) -> None:
        self.answers: list[httpx2.Response | Exception] = []
        self.requests: list[httpx2.Request] = []

    def answer(self, *answers: httpx2.Response | Exception) -> None:
        self.answers.extend(answers)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)

    def client(self, **settings: Any) -> AsyncOpenAI:
        config = Settings(
            _env_file=None,
            meta_api_key=SecretStr("test-key"),
            meta_api_base_url=BASE_URL,
            **settings,
        )
        transport = httpx2.MockTransport(self.handler)
        return meta_client(config, http_client=httpx2.AsyncClient(transport=transport))


USAGE = {"prompt_tokens": 120, "completion_tokens": 18, "total_tokens": 138}


def completion(
    message: dict[str, Any], *, finish_reason: str = "stop", usage: dict[str, int] | None = USAGE
) -> httpx2.Response:
    """``usage=None`` leaves the usage object out."""
    body: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1793116800,
        "model": "served-model",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
    }
    if usage is not None:
        body["usage"] = usage
    return httpx2.Response(200, json=body)


def embeddings(*vectors: list[float], prompt_tokens: int = 9) -> httpx2.Response:
    """Vectors in reverse index order: the adapter must sort them back."""
    data = [
        {"object": "embedding", "index": index, "embedding": vector}
        for index, vector in reversed(list(enumerate(vectors)))
    ]
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "model": "served-embedder",
            "data": data,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        },
    )


def error(status: int, message: str, **headers: str) -> httpx2.Response:
    return httpx2.Response(
        status, headers=headers, json={"error": {"message": message, "type": "invalid_request"}}
    )

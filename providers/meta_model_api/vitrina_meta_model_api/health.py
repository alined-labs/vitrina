"""The Meta Model API's row in ``GET /health/providers`` (NFR-02, T073).

``GET /models`` is the check: it proves the key and the API and spends no tokens.
"""

import openai
from openai import AsyncOpenAI

from vitrina_core.health import Check, Outcome
from vitrina_core.settings import Settings
from vitrina_meta_model_api.client import meta_client


async def check_models(client: AsyncOpenAI) -> Outcome:
    try:
        await client.models.list()
    except openai.RateLimitError:
        return Outcome("degraded", "HTTP 429")
    except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
        return Outcome("down", f"the key was refused (HTTP {error.status_code})")
    except openai.APIStatusError as error:
        code = error.status_code
        return Outcome("degraded" if code >= 500 else "down", f"HTTP {code}")
    except openai.APITimeoutError:
        return Outcome("down", "timed out")
    except openai.APIConnectionError:
        return Outcome("down", "no connection")
    return Outcome("ok")


def meta_check(settings: Settings, *, timeout_s: float) -> Check:
    async def check() -> Outcome:
        if settings.meta_api_key is None:
            return Outcome("down", "META_API_KEY is not set")
        client = meta_client(settings).with_options(timeout=timeout_s)
        try:
            return await check_models(client)
        finally:
            await client.close()

    return check

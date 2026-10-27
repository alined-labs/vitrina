"""Live smoke test on the WhatsApp test number. Needs credentials; run by hand:

    uv run --env-file .env pytest -m live providers/whatsapp

WHATSAPP_TEST_RECIPIENT must be one of the test number's verified recipients. The pre-approved
``hello_world`` template is used because free text only reaches a customer inside the 24 h
customer-service window.
"""

import os

import pytest

from vitrina_whatsapp.client import WhatsAppClient
from vitrina_whatsapp.config import WhatsAppConfig

pytestmark = pytest.mark.live


async def test_live_template_send_is_accepted_by_meta() -> None:
    config = WhatsAppConfig.from_env()
    recipient = os.environ.get("WHATSAPP_TEST_RECIPIENT", "")
    if not (config.can_send and recipient):
        pytest.skip(
            "needs WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TEST_RECIPIENT"
        )

    async with WhatsAppClient.from_config(config) as client:
        result = await client.send_template(recipient, "hello_world", language="en_US")

    assert result.message_id.startswith("wamid.")

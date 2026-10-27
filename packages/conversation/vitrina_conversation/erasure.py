"""The conversation module's part of an erasure (T062 with MX0; AS-6.6, FR-065; Art. II.3).

For the ``vitrina.erasure`` entry point: the compliance erasure job calls ``erase`` for the
contact before it erases the number and the name itself. Every message of the contact's
conversations loses its content (text, transcript, media, the normalized copy, the agent's
answer and its review), and so do the handoff summaries, objection quotes, conversation
summaries and booking names written from them. The raw webhook bodies that carried the
contact's messages are emptied, and so is any other body holding the contact's number, such as
a delivery status. The rows stay, so counts and attribution remain.
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vitrina_compliance.api import ContactIdentity, PostgresCompliance
from vitrina_conversation.tables import (
    bookings,
    conversations,
    handoffs,
    messages,
    objections,
    webhook_payloads,
)
from vitrina_core.jobs import JobContext, JobFailedError

ERASED = "erased (FR-065)"


async def erase(ctx: JobContext, seller_id: UUID, contact_id: UUID) -> str:
    """The eraser the compliance job calls; the receipt names this part ``messages``."""
    if ctx.sessions is None:
        raise JobFailedError("INTERNAL_ERROR", "erasing messages needs the database")
    settings = ctx.settings
    number = None
    if settings.contact_hash_pepper and settings.contact_encryption_key:
        identity = ContactIdentity(
            pepper=settings.contact_hash_pepper, key=settings.contact_encryption_key
        )
        number = await PostgresCompliance(ctx.sessions, identity).phone(seller_id, contact_id)
    await erase_conversations(ctx.sessions, seller_id, contact_id, number=number)
    return "messages"


async def erase_conversations(
    sessions: async_sessionmaker[AsyncSession],
    seller_id: UUID,
    contact_id: UUID,
    *,
    number: str | None,
) -> None:
    """``number`` (digits, as WhatsApp sends it) finds the bodies no message points at."""
    ours = sa.select(conversations.c.id).where(
        conversations.c.seller_id == seller_id, conversations.c.contact_id == contact_id
    )
    carried = sa.select(messages.c.raw_payload_id).where(
        messages.c.seller_id == seller_id,
        messages.c.conversation_id.in_(ours),
        messages.c.raw_payload_id.is_not(None),
    )
    async with sessions() as session, session.begin():
        await session.execute(
            sa.update(webhook_payloads)
            .where(webhook_payloads.c.id.in_(carried))
            .values(body=sa.null(), error=ERASED)
        )
        if number:
            await session.execute(
                sa.update(webhook_payloads)
                .where(sa.cast(webhook_payloads.c.body, sa.Text).contains(f'"{number}"'))
                .values(body=sa.null(), error=ERASED)
            )
        await session.execute(
            sa.update(messages)
            .where(messages.c.seller_id == seller_id, messages.c.conversation_id.in_(ours))
            .values(
                text=None,
                transcript=None,
                media_key=None,
                normalized=sa.null(),
                agent_output=sa.null(),
                validation=sa.null(),
            )
        )
        for table, column in (
            (handoffs, handoffs.c.summary),
            (objections, objections.c.quote_redacted),
            (bookings, bookings.c.customer_name),
        ):
            await session.execute(
                sa.update(table)
                .where(table.c.seller_id == seller_id, table.c.conversation_id.in_(ours))
                .values({column.name: None})
            )
        await session.execute(
            sa.update(conversations)
            .where(conversations.c.seller_id == seller_id, conversations.c.id.in_(ours))
            .values(summary=None)
        )

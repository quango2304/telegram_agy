"""Pure trigger detection — does a stored message deserve an answer?

Answer when the chat is private, or the message mentions the bot, or it replies
to one of the bot's messages. Never answer a bot (including ourselves), an edit,
or a service message (the last two are filtered before this is called).
"""

from __future__ import annotations

from collections.abc import Iterator

from telegram import Message, MessageEntity
from telegram.constants import ChatType


def _entity_texts(message: Message) -> Iterator[tuple[MessageEntity, str]]:
    for entity in message.entities:
        yield entity, message.parse_entity(entity)
    for entity in message.caption_entities:
        yield entity, message.parse_caption_entity(entity)


def is_trigger(message: Message, bot_id: int, bot_username: str) -> bool:
    user = message.from_user
    if user is not None and user.is_bot:
        # Two bots in one group must not lock into an infinite exchange.
        return False

    if message.chat.type == ChatType.PRIVATE:
        return True

    handle = f"@{bot_username}".casefold()
    for entity, text in _entity_texts(message):
        if entity.type == MessageEntity.MENTION and text.casefold() == handle:
            return True
        if (
            entity.type == MessageEntity.TEXT_MENTION
            and entity.user is not None
            and entity.user.id == bot_id
        ):
            return True

    reply = message.reply_to_message
    return bool(reply and reply.from_user and reply.from_user.id == bot_id)

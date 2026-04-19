from typing import Literal, override

from mcdreforged import CommandSource, ServerInterface
from mcdreforged.utils.types.message import MessageText

from telegram import Message
from telegram.ext import filters

from tgb.util.dispatcher import tg_messages_queue, MCTOTGMessages

BoolStr: dict[bool, Literal["Yes", "No"]] = {
    True: "Yes",
    False: "No"
}

class TelegramCommandSource(CommandSource):
    def __init__(
            self,
            mcdr_server: ServerInterface,
            messsage_context: Message,
            admin_id: int
        ) -> None:
        super().__init__()
        self.mcdr_server: ServerInterface = mcdr_server
        self.message_context: Message = messsage_context
        self.admin_id: int = admin_id
    
    @property
    @override
    def is_player(self) -> bool:
        return False

    @property
    @override
    def is_console(self) -> bool:
        return False
    
    @property
    def is_private_chat(self) -> bool:
        if self.message_context.chat.type == filters.ChatType.PRIVATE:
            return True
        return False

    @override
    def get_server(self) -> ServerInterface:
        return self.mcdr_server

    @override
    def get_permission_level(self) -> int:
        if self.message_context.from_user is None:
            return 0
        if self.message_context.from_user.id == self.admin_id:
            return 4
        return 0

    @override
    def reply(self, message: MessageText, **kwargs) -> None:
        text = str(message)
        if not text.strip():
            return

        tg_messages_queue.put_nowait(
            MCTOTGMessages(
                player=None,
                text=text,
                from_chat_id=self.message_context.chat.id,
                from_message_id=self.message_context.message_id,
            )
        )
        
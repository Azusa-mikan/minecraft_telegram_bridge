from concurrent.futures import Future
from functools import partial

from mcdreforged import (
    ServerInterface,
    PluginServerInterface,
    CommandSource,
    PlayerCommandSource,
    RText,
    RColor
)
from mcdreforged.api.command import (
    Literal,
    Integer,
    GreedyText,
    CommandContext
)

from tgb.util.dispatcher import MCTOTGMessages, tg_messages_queue

def reply_to_telegram_message_error(
        server: ServerInterface,
        player_name: str,
        f: Future[str]
    ):
    err: str = f.result()
    server.tell(
        player_name,
        RText(
            server.rtr(
                "tgb.reply_failed",
                error=err
            ),
            color=RColor.red
        )
    )

def reply_to_telegram_message(
        src: CommandSource,
        ctx: CommandContext
    ) -> None:
    server: ServerInterface = src.get_server()
    if not isinstance(src, PlayerCommandSource):
        src.reply(
            server.rtr("tgb.only_player_cmd")
        )
        return
    fut = Future()
    player_name: str = src.player
    text: str = ctx["text"]
    chat_id: int = ctx["chat_id"]
    message_id: int = ctx["message_id"]
    tg_messages_queue.put_nowait(
        MCTOTGMessages(
            player=player_name,
            text=text,
            from_chat_id=chat_id,
            from_message_id=message_id,
            error_message=fut
        )
    )
    fut.add_done_callback(
        partial(
            reply_to_telegram_message_error,
            server,
            player_name
        )
    )

def register_reply(parent_cmd: Literal) -> Literal:
    parent_cmd.then(
        Literal("reply").then(
            Integer("chat_id").then(
                Integer("message_id").then(
                    GreedyText("text")
                        .runs(reply_to_telegram_message)
                )
            )
        )
    )
    return parent_cmd

def register_commands(server: PluginServerInterface) -> None:
    root = register_reply(Literal("!!tgb"))

    server.register_command(root)

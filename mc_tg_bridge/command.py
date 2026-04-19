from concurrent.futures import Future
from functools import partial
from queue import Empty
import time

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

from mc_tg_bridge.util.dispatcher import (
    MCTOTGMessages,
    BindVerified,
    tg_messages_queue,
    bind_verify_queue,
)

def bind_verify(
        src: CommandSource,
        ctx: CommandContext
    ) -> None:
    if not isinstance(src, PlayerCommandSource):
        server: ServerInterface = src.get_server()
        src.reply(
            server.rtr("tgb.only_player_cmd")
        )
        return
    code: str = ctx["code"]
    from_player_name: str = src.player

    try:
        verify = bind_verify_queue.get_nowait()
    except Empty:
        src.reply("此验证码不属于任何验证")
        return
    
    try:
        if from_player_name != verify.player_name:
            src.reply("这不是你的验证")
            bind_verify_queue.put_nowait(verify)
            return
        if code != verify.code:
            src.reply("验证码错误")
            bind_verify_queue.put_nowait(verify)
            return
        if time.monotonic() >= verify.expired_at:
            src.reply("验证码已过期")
            if not verify.fut.done():
                verify.fut.set_result(
                    BindVerified(
                        user_id=verify.user_id,
                        player_name=from_player_name,
                        verified=False
                    )
                )
            return
        
        src.reply("验证成功！")
        if not verify.fut.done():
            verify.fut.set_result(
                BindVerified(
                    user_id=verify.user_id,
                    player_name=from_player_name,
                    verified=True
                )
            )
    finally:
        bind_verify_queue.task_done()

def reply_to_telegram_message_error(
        server: ServerInterface,
        player_name: str,
        f: Future[str]
    ) -> None:
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

def register_bind(parent_cmd: Literal) -> Literal:
    parent_cmd.then(
        Literal("bind").then(
            GreedyText("code")
                .runs(bind_verify)
        )
    )
    return parent_cmd

def register_commands(server: PluginServerInterface) -> None:
    root = register_reply(Literal("!!tgb"))
    root = register_bind(root) # 传上一个root，否则会覆盖

    server.register_command(root)

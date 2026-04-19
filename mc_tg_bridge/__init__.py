import threading

from mcdreforged import PluginServerInterface, Info, RText, RAction

from mc_tg_bridge.config import load_config, Config
from mc_tg_bridge.bot import TGBot
from mc_tg_bridge.util.dispatcher import StopSignal, mc_messages_queue, send_message_to_telegram
from mc_tg_bridge.command import register_commands

config: Config | None = None

bot: TGBot | None = None
bot_thread: threading.Thread | None = None
bot_thread_name = "tgb_telegram_bot"

mc_queue_thread: threading.Thread | None = None
mc_queue_thread_name = "tgb_minecraft_queue"

def get_config(server: PluginServerInterface) -> Config:
    """
    获取配置
    """
    if config is None:
        on_unload(server)
        raise ValueError("Config not loaded correctly")
    return config

def stop_mc_queue_thread() -> None:
    """
    停止 Telegram 消息消费队列
    """
    if mc_queue_thread is not None and mc_queue_thread.is_alive():
        mc_messages_queue.put_nowait(StopSignal())
        mc_queue_thread.join(timeout=10)

def has_old_thread_alive() -> bool:
    """
    是否有残留线程
    """
    for t in threading.enumerate():
        if t.name == bot_thread_name and t.is_alive():
            return True
        if t.name == mc_queue_thread_name and t.is_alive():
            return True
    return False

def message_send_text(server: PluginServerInterface) -> None:
    """
    Telegram 消息消费队列
    """
    if config is None:
        server.logger.critical("Config not loaded correctly")
        return
    server.logger.info(server.rtr("tgb.mc_queue_start"))
    while True:
        try:
            msg = mc_messages_queue.get()
            if isinstance(msg, StopSignal):
                mc_messages_queue.task_done()
                break
        except Exception:
            server.logger.exception(server.rtr("tgb.mc_queue_error"))
            continue

        try:
            server.say(
                RText(
                    config.to_mc_message_format.format(
                        player=(
                            msg.player_name
                            if msg.player_name is not None
                            else msg.fullname
                        ),
                        text=msg.text
                    )
                ).h(
                    f"UserID: {msg.userid}\n"
                    f"UserName: @{msg.username}\n"
                    f"FullName: {msg.fullname}\n"
                    f"FromChat: {msg.fromchat}"
                ).c(
                    RAction.suggest_command,
                    f"!!tgb reply {msg.fromchat} {msg.frommessageid} "
                )
            )
        except Exception:
            server.logger.exception(server.rtr("tgb.mc_queue_error"))
            continue
        finally:
            mc_messages_queue.task_done()
    server.logger.info(server.rtr("tgb.mc_queue_stop"))

def start_mc_queue_worker(server: PluginServerInterface) -> None:
    """
    启动 Telegram 消息消费队列
    """
    global mc_queue_thread
    if mc_queue_thread is not None and mc_queue_thread.is_alive():
        return
    if bot_thread is not None and bot_thread.is_alive():
        mc_queue_thread = threading.Thread(
            target=message_send_text,
            args=(server,),
            name=mc_queue_thread_name
        )
        mc_queue_thread.start()
    else:
        server.logger.warning(server.rtr("tgb.bot_not_running"))

def on_load(server: PluginServerInterface, prev) -> None:
    """
    插件加载
    """
    global config
    config = load_config(server)
    register_commands(server)
    if not config.plugin_status:
        server.logger.error(server.rtr("tgb.not_available"))
        return

    if has_old_thread_alive():
        server.logger.error(server.rtr("tgb.threading_running"))
        return

    global bot, bot_thread
    bot = TGBot(
        serverinterface=server,
        token=config.telegram.bot_token,
        admin_id=config.telegram.admin_id,
        chat_ids=config.telegram.chat_ids,
        message_format=config.to_tg_message_format
    )

    bot_thread = threading.Thread(
        target=bot.run,
        name=bot_thread_name
    )
    bot_thread.start()

    if config.mc_to_tg_send_events:
        server.register_event_listener("PlayerDeathEvent", on_player_death)
        server.register_event_listener("PlayerAdvancementEvent", on_player_advancement)

    if prev is not None:
        start_mc_queue_worker(server)

def on_server_startup(server: PluginServerInterface) -> None:
    """
    服务器已启动
    """
    config = get_config(server)
    start_mc_queue_worker(server)
    send_message_to_telegram(
        player=None,
        text=config.server_started_message
    )

def on_player_joined(
        server: PluginServerInterface,
        player: str,
        info: Info
    ) -> None:
    """
    玩家加入
    """
    config = get_config(server)
    if bot_thread is not None and bot_thread.is_alive():
        send_message_to_telegram(
            player=None,
            text=config.joined_message.format(
                player=player
            )
        )
    else:
        stop_mc_queue_thread()
        server.logger.warning(server.rtr("tgb.bot_not_running"))

def on_user_info(server: PluginServerInterface, info: Info) -> None:
    """
    玩家消息
    """
    if info.player is None or info.content is None:
        return
    if info.content.startswith("!!"):
        return
    if bot_thread is not None and bot_thread.is_alive():
        send_message_to_telegram(
            player=info.player,
            text=info.content
        )
    else:
        stop_mc_queue_thread()
        server.logger.warning(server.rtr("tgb.bot_not_running"))

def on_player_death(
        server: PluginServerInterface,
        player: str,
        event: str,
        content
    ) -> None:
    """
    玩家死亡
    """
    for i in content:
        send_message_to_telegram(
            player=None,
            text=i.raw
        )

def on_player_advancement(
        server: PluginServerInterface,
        player: str,
        event: str,
        content
    ) -> None:
    """
    玩家获得成就
    """
    for i in content:
        send_message_to_telegram(
            player=None,
            text=i.raw
        )

def on_player_left(
        server: PluginServerInterface,
        player: str,
    ) -> None:
    """
    玩家离开
    """
    config = get_config(server)
    if bot_thread is not None and bot_thread.is_alive():
        send_message_to_telegram(
            player=None,
            text=config.left_message.format(
                player=player
            )
        )
    else:
        stop_mc_queue_thread()
        server.logger.warning(server.rtr("tgb.bot_not_running"))

def on_server_stop(
        server: PluginServerInterface,
        server_return_code: int
    ) -> None:
    """
    服务器停止
    """
    config = get_config(server)
    send_message_to_telegram(
        player=None,
        text=config.server_stopped_message.format(
            code=server_return_code
        )
    )

    global mc_queue_thread
    if mc_queue_thread is not None:
        mc_messages_queue.put_nowait(StopSignal())
        mc_queue_thread.join(timeout=10)
        if mc_queue_thread.is_alive():
            server.logger.warning(
                server.rtr(
                    "tgb.still_running",
                    threadname=mc_queue_thread_name
                )
            )

    mc_queue_thread = None

def on_unload(server: PluginServerInterface) -> None:
    """
    插件卸载
    """
    global bot, bot_thread
    if bot is not None:
        bot.stop()

    if bot_thread is not None:
        bot_thread.join(timeout=10)
        if bot_thread.is_alive():
            server.logger.warning(
                server.rtr(
                    "tgb.still_running",
                    threadname=bot_thread_name
                )
            )

    bot = None
    bot_thread = None

    server.logger.info(server.rtr("tgb.unload"))
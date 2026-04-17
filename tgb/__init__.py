import threading

from mcdreforged import PluginServerInterface, Info, RText

from tgb.config import load_config
from tgb.bot import TGBot
from tgb.util.dispatcher import StopSignal, mc_messages_queue, send_message_to_telegram


bot: TGBot | None = None
bot_thread: threading.Thread | None = None
bot_thread_name = "tgb_telegram_bot"

mc_queue_thread: threading.Thread | None = None
mc_queue_thread_name = "tgb_minecraft_queue"

def has_old_thread_alive() -> bool:
    """
    是否有残留进程
    """
    for t in threading.enumerate():
        if t.name == bot_thread_name and t.is_alive():
            return True
        if t.name == mc_queue_thread_name and t.is_alive():
            return True
    return False

def message_send_text(server: PluginServerInterface) -> None:
    config = load_config(server)
    server.logger.info(server.tr("tgb.mc_queue_start"))
    while True:
        try:
            msg = mc_messages_queue.get()
            if isinstance(msg, StopSignal):
                mc_messages_queue.task_done()
                break
        except Exception:
            server.logger.exception(server.tr("tgb.mc_queue_error"))
            continue

        try:
            server.say(
                RText(
                    config.to_mc_message_format.format(
                        player=msg.fullname,
                        text=msg.text
                    )
                ).h(
                    f"UserID: {msg.userid}\n"
                    f"UserName: {msg.username}\n"
                    f"FullName: {msg.fullname}\n"
                    f"FromChat: {msg.fromchat}"
                )
            )
        except Exception:
            server.logger.exception(server.tr("tgb.mc_queue_error"))
            continue
        finally:
            mc_messages_queue.task_done()
    server.logger.info(server.tr("tgb.mc_queue_stop"))

def on_server_startup(server: PluginServerInterface) -> None:
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
        server.logger.warning(server.tr("tgb.bot_not_running"))

def on_load(server: PluginServerInterface, prev) -> None:
    config = load_config(server)
    if not config.plugin_status:
        server.logger.error(server.tr("tgb.not_available"))
        return

    if has_old_thread_alive():
        server.logger.error(server.tr("tgb.threading_running"))
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
    if prev is not None:
        on_server_startup(server)

def on_unload(server: PluginServerInterface) -> None:
    global bot, bot_thread, mc_queue_thread
    if bot is not None:
        bot.stop()

    if bot_thread is not None:
        bot_thread.join(timeout=60)
        if bot_thread.is_alive():
            server.logger.warning(
                server.tr(
                    "tgb.still_running",
                    threadname=bot_thread_name
                )
            )

    bot = None
    bot_thread = None

    if mc_queue_thread is not None:
        mc_messages_queue.put_nowait(StopSignal())
        mc_queue_thread.join(timeout=30)
        if mc_queue_thread.is_alive():
            server.logger.warning(
                server.tr(
                    "tgb.still_running",
                    threadname=mc_queue_thread_name
                )
            )

    mc_queue_thread = None
    server.logger.info(server.tr("tgb.unload"))

def on_user_info(server: PluginServerInterface, info: Info) -> None:
    if info.player is None or info.content is None:
        return
    if bot_thread is not None and bot_thread.is_alive():
        send_message_to_telegram(
            player=info.player,
            text=info.content
        )
    else:
        server.logger.warning(server.tr("tgb.bot_not_running"))
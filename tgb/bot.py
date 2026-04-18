from typing import Any, Literal
from pathlib import Path
import asyncio
import json
import os

from mcdreforged import PluginServerInterface
from telegram import User, Update
from telegram.ext import Application, CallbackContext, ExtBot, JobQueue
from telegram.ext import CommandHandler, MessageHandler, AIORateLimiter, filters
from telegram.constants import BOT_API_VERSION

from tgb.util import BoolStr
from tgb.util.dispatcher import StopSignal, tg_messages_queue, send_message_to_minecraft

BotData = dict[Any, Any]
ChatData = dict[Any, Any]
UserData = dict[Any, Any]
Context = CallbackContext[ExtBot[None], UserData, ChatData, BotData]
App = Application[ExtBot[None], Context, UserData, ChatData, BotData, JobQueue[Context]]

Status = Literal["nostarted", "starting", "running", "stopping", "stopped"]

class TGBot_init:
    def __init__(
        self,
        serverinterface: PluginServerInterface,
        token: str,
        admin_id: int,
        chat_ids: list[int],
        message_format: str,
    ) -> None:
        self.mcserver: PluginServerInterface = serverinterface
        self.telegram_token: str = token
        self.admin_id: int = admin_id
        self.chat_ids_list: list[int] = chat_ids
        self.chat_ids_set: set[int] = set(chat_ids)
        self.message_format: str = message_format
        self.status: Status = "nostarted"
        self.bot: App = self._init_bot()

        self.bind_players: dict[str, str] = {}
        self.bind_path = Path(
            serverinterface.get_data_folder(),
            "bind.json"
        )
        self.bind_cache_path = Path(
            serverinterface.get_data_folder(),
            "bind.json.cache"
        )
    
    async def set_command(self, app: App) -> None:
        await app.bot.set_my_commands([
            ("start", "激活机器人"),
            ("bind", "绑定到玩家"),
            ("status", "查看服务器状态"),
            ("stop", "停止服务器"),
            ("restart", "重启服务器"),
            ("exec", "向服务器发送命令"),
            ("exec_mcdr", "发送MCDR命令")
        ])

    async def startup(self, app: App) -> None:
        try:
            me: User = await app.bot.get_me()
            self.mcserver.logger.info(
                self.mcserver.tr(
                    "tgb.started",
                    name=me.full_name,
                    id=me.id
                )
            )
            self.mcserver.logger.info(
                self.mcserver.tr(
                    "tgb.version",
                    version=BOT_API_VERSION
                )
            )
            await self.set_command(app)
            self.status = "running"
            self.send_msg_task = asyncio.create_task(
                self.send_messages(),
                name="send_messages_task"
            )
            if self.bind_path.exists():
                self.bind_players = json.loads(
                    self.bind_path.read_text(
                        encoding="utf-8"
                    )
                )
        except Exception as e:
            self.mcserver.logger.exception(
                self.mcserver.tr("tgb.start_failed")
            )
            raise RuntimeError("Bot Startup failed") from e
    
    async def shutdown(self, app: App) -> None:
        tg_messages_queue.put_nowait(StopSignal())
        try:
            await self.send_msg_task
        except Exception as e:
            self.mcserver.logger.debug(f"{e}")
            pass
        self.mcserver.logger.info(
            self.mcserver.tr(
                "tgb.shutdown"
            )
        )

    async def on_error(self, app: object, context: Context) -> None:
        self.mcserver.logger.error(f"ERROR: {context.error}")

    async def send_messages(self) -> None:
        self.mcserver.logger.info(
            self.mcserver.tr("tgb.tg_queue_start")
        )
        while True:
            try:
                msg = await asyncio.to_thread(
                    tg_messages_queue.get
                )
                if isinstance(msg, StopSignal):
                    tg_messages_queue.task_done()
                    break
            except Exception:
                self.mcserver.logger.exception(
                    self.mcserver.tr("tgb.tg_queue_error")
                )
                continue

            try:
                if msg.reply_chat_id is not None and msg.reply_to_message_id is not None:
                    await self.bot.bot.send_message(
                        chat_id=msg.reply_chat_id,
                        text=msg.text,
                        reply_to_message_id=msg.reply_to_message_id,
                    )
                    continue

                tasks = [
                    self.bot.bot.send_message(
                        chat_id=chat_id,
                        text=self.message_format.format(
                            player=msg.player,
                            text=msg.text
                        )
                    )
                    for chat_id in self.chat_ids_list
                ]
                results = await asyncio.gather(
                    *tasks,
                    return_exceptions=True
                )
                for chat_id, result in zip(self.chat_ids_list, results):
                    if isinstance(result, BaseException):
                        self.mcserver.logger.error(
                            self.mcserver.tr(
                                "tgb.tg_queue_send_error",
                                chat_id=chat_id,
                                error=f"{result!r}"
                            )
                        )
            except Exception:
                self.mcserver.logger.exception(
                    self.mcserver.tr("tgb.tg_queue_error")
                )
                continue
            finally:
                tg_messages_queue.task_done()
        self.mcserver.logger.info(
            self.mcserver.tr("tgb.tg_queue_stop")
        )

    
    def is_allowed_chat(self, update: Update) -> bool:
        if not update.effective_chat:
            return False
        if update.effective_chat.id not in self.chat_ids_set:
            return False
        return True

    def _init_bot(self) -> App:
        app = Application.builder()
        app.token(self.telegram_token)
        app.post_init(self.startup)
        app.post_shutdown(self.shutdown)
        app.rate_limiter(AIORateLimiter(max_retries=3))
        return app.build()

class TGBot_command(TGBot_init):
    def __init__(
            self,
            serverinterface: PluginServerInterface,
            token: str,
            admin_id: int,
            chat_ids: list[int],
            message_format: str,
        ) -> None:
        super().__init__(
            serverinterface=serverinterface,
            token=token,
            admin_id=admin_id,
            chat_ids=chat_ids,
            message_format=message_format
        )
        self.files_lock = asyncio.Lock()
    
    async def start_handler(self, update: Update, context: Context) -> None:
        if not update.message:
            return
        await update.message.reply_text("你好，我正在休眠\nZzz……")
    
    async def status_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        ServerRunning: bool = self.mcserver.is_server_running()
        ServerRconRunning: bool = self.mcserver.is_rcon_running()
        ServerProgramPID: int | None = self.mcserver.get_server_pid()
    
    async def bind_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        if not (user := update.effective_user):
            return
        if not (args := context.args):
            await update.message.reply_text(
                "Usage: /bind [Player Name]"
            )
            return
        if len(args) > 1:
            await update.message.reply_text(
                "Player name has no spaces"
            )
            return
        
        userid = str(user.id)
        player_name: str = args[0]

        async with self.files_lock:
            self.bind_players[userid] = player_name
            with self.bind_cache_path.open("w", encoding="utf-8") as f:
                json.dump(
                    self.bind_players,
                    f,
                    indent=2
                )
                f.flush()
                os.fsync(f.fileno())

            os.replace(self.bind_cache_path, self.bind_path)
        
        await update.message.reply_text(
            f"已绑定为 {player_name}"
        )

    async def messages_handler(self, update: Update, context: Context) -> None:
        if (chat := update.effective_chat) is None:
            return
        if chat.id not in self.chat_ids_set:
            return
        if (message := update.message) is None:
            return
        if message.text is None:
            return
        if (user := update.effective_user) is None:
            return
        player_name = self.bind_players.get(str(user.id))
        send_message_to_minecraft(
            player_name=player_name,
            userid=user.id,
            username=user.username,
            fullname=user.full_name,
            fromchat=chat.id,
            text=message.text,
        )

class TGBot(TGBot_command):
    def __init__(
            self,
            *,
            serverinterface: PluginServerInterface,
            token: str,
            admin_id: int,
            chat_ids: list[int],
            message_format: str,
        ) -> None:
        super().__init__(
            serverinterface=serverinterface,
            token=token,
            admin_id=admin_id,
            chat_ids=chat_ids,
            message_format=message_format
        )
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()

    def register_handlers(self) -> None:
        self.bot.add_error_handler(self.on_error)
        self.bot.add_handler(CommandHandler("bind", self.bind_handler))
        self.bot.add_handler(
            MessageHandler(
                filters=~filters.COMMAND,
                callback=self.messages_handler
            )
        )

    def run(self) -> None:
        self.status = "starting"
        self.register_handlers()
        asyncio.set_event_loop(self.loop)
        try:
            self.bot.run_polling(
                stop_signals=None,
                close_loop=False
            )
        finally:
            if (
                not self.loop.is_running()
                and not self.loop.is_closed()
                ):
                self.loop.close()
            self.status = "stopped"

    def stop(self) -> None:
        self.status = "stopping"
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.bot.stop_running)
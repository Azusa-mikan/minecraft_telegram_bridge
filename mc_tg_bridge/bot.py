from typing import Any, Literal
from functools import partial
from pathlib import Path
import asyncio
import json
import os
from concurrent.futures import Future
import time
import secrets
import queue
import psutil

from mcdreforged import PluginServerInterface
from telegram import User, Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackContext, ExtBot, JobQueue
from telegram.ext import CommandHandler, MessageHandler, AIORateLimiter, filters
from telegram.ext import CallbackQueryHandler
from telegram.constants import BOT_API_VERSION, ParseMode
from telegram.error import TelegramError

from mc_tg_bridge.util import BoolStr, TelegramCommandSource
from mc_tg_bridge.util.dispatcher import (
    StopSignal,
    BindVerified,
    BindVerify,
    tg_messages_queue,
    mc_messages_queue,
    bind_verify_queue,
    send_message_to_minecraft
)

import minecraft_data_api as api # type: ignore

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
        
        self.files_lock = asyncio.Lock()
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._mc_cpu_last_pid: int | None = None
        self._mc_cpu_last_wall: float | None = None
        self._mc_cpu_last_total: float | None = None
    
    async def set_command(self, app: App) -> bool:
        return await app.bot.set_my_commands([
            ("start", "激活机器人"),
            ("status", "查看服务器状态"),
            ("bind", "绑定玩家"),
            ("stop", "停止服务器"),
            ("restart", "重启服务器"),
            ("start_server", "启动服务器"),
            ("exec", "向服务器发送命令（带 !! 前缀则执行 MCDR 命令）"),
            ("list", "获取服务器玩家列表")
        ])

    async def startup(self, app: App) -> None:
        try:
            me: User = await app.bot.get_me()
            self.username = me.username
            self.mcserver.logger.info(
                self.mcserver.rtr(
                    "tgb.started",
                    name=me.full_name,
                    id=me.id
                )
            )
            self.mcserver.logger.info(
                self.mcserver.rtr(
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
                self.mcserver.rtr("tgb.start_failed")
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
            self.mcserver.rtr(
                "tgb.shutdown"
            )
        )

    async def on_error(self, app: object, context: Context) -> None:
        self.mcserver.logger.error(f"ERROR: {context.error}")

    async def send_messages(self) -> None:
        self.mcserver.logger.info(
            self.mcserver.rtr("tgb.tg_queue_start")
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
                    self.mcserver.rtr("tgb.tg_queue_error")
                )
                continue

            try:
                if msg.from_chat_id is not None and msg.from_message_id is not None:
                    await self.bot.bot.send_message(
                        chat_id=msg.from_chat_id,
                        reply_to_message_id=msg.from_message_id,
                        text=self.message_format.format(
                            player=msg.player,
                            text=msg.text
                        ),
                    )
                    continue

                tasks = [
                    self.bot.bot.send_message(
                        chat_id=chat_id,
                        text=(self.message_format.format(
                            player=msg.player,
                            text=msg.text
                        ) if msg.player is not None else msg.text)
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
                            self.mcserver.rtr(
                                "tgb.tg_queue_send_error",
                                chat_id=chat_id,
                                error=f"{result!r}"
                            )
                        )
            except TelegramError as e:
                if (
                    msg.error_message is not None
                    and not msg.error_message.done()
                    ):
                    msg.error_message.set_result(
                        str(e)
                    )
                self.mcserver.logger.warning(
                    f"{self.mcserver.rtr("tgb.tg_queue_error")}: "
                    f"{e}"
                )
                continue
            except Exception as e:
                self.mcserver.logger.exception(
                    self.mcserver.rtr("tgb.tg_queue_error")
                )
                continue
            finally:
                tg_messages_queue.task_done()
        self.mcserver.logger.info(
            self.mcserver.rtr("tgb.tg_queue_stop")
        )

    def is_allowed_chat(self, update: Update) -> bool:
        if not update.effective_chat:
            return False
        if update.effective_chat.id not in self.chat_ids_set:
            return False
        return True

    @staticmethod
    def inlinekeyboard(action: str, text: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(text, callback_data=f"action:{action}")],
            [InlineKeyboardButton("取消", callback_data="action:cancel")],
        ])

    def _sample_mc_cpu_percent(self, pid: int) -> float | None:
        try:
            mc_pid = psutil.Process(pid)
            cpu_times = mc_pid.cpu_times()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        total = cpu_times.user + cpu_times.system
        now = time.monotonic()

        if (
            self._mc_cpu_last_pid != pid
            or self._mc_cpu_last_wall is None
            or self._mc_cpu_last_total is None
        ):
            self._mc_cpu_last_pid = pid
            self._mc_cpu_last_wall = now
            self._mc_cpu_last_total = total
            return None

        dt_wall = now - self._mc_cpu_last_wall
        dt_total = total - self._mc_cpu_last_total

        self._mc_cpu_last_wall = now
        self._mc_cpu_last_total = total

        if dt_wall <= 0:
            return 0.0

        return max(0.0, (dt_total / dt_wall) * 100.0)

    async def _bind_callback_async(
            self,
            bot: ExtBot[None],
            fut: Future[BindVerified]
        ) -> None:
        result: BindVerified = fut.result()
        if result.verified:
            async with self.files_lock:
                self.bind_players[result.user_id] = result.player_name
                with self.bind_cache_path.open("w", encoding="utf-8") as f:
                    json.dump(
                        self.bind_players,
                        f,
                        indent=2
                    )
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(self.bind_cache_path, self.bind_path)
            
            await bot.send_message(
                chat_id=result.user_id,
                text=f"已绑定为 {result.player_name}"
            )
        else:
            await bot.send_message(
                chat_id=result.user_id,
                text=f"绑定失败: 超时"
            )

    def bind_callback(
            self,
            bot: ExtBot[None],
            fut: Future[BindVerified]
        ) -> None:
        asyncio.run_coroutine_threadsafe(
            self._bind_callback_async(bot, fut),
            self.loop
        )

    async def inlinekeyboard_callback(self, update: Update, context: Context):
        if (query := update.callback_query) is None:
            return
        if (chat := update.effective_chat) is None:
            return
        if query.from_user.id != self.admin_id:
            await query.answer("你没有权限进行此操作", show_alert=True)
            return
        if chat.id not in self.chat_ids_set:
            return

        operation: str = query.data or ""

        match operation:
            case "action:cancel":
                await query.answer()
                await query.edit_message_text(
                    text="已取消操作",
                    reply_markup=None
                )
                return
            case "action:stop_server":
                await query.answer()
                self.mcserver.stop()
                await query.edit_message_text(
                    text="服务器正在关闭",
                    reply_markup=None
                )
                return
            case "action:restart_server":
                await query.answer()
                self.mcserver.restart()
                await query.edit_message_text(
                    text="服务器正在重启",
                    reply_markup=None
                )
                return
            case "action:start_server":
                await query.answer()
                self.mcserver.start()
                await query.edit_message_text(
                    text="服务器正在启动",
                    reply_markup=None
                )
                return
            case _:
                await query.answer("未知操作", show_alert=True)
                return

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
    
    async def start_handler(self, update: Update, context: Context) -> None:
        if not update.message:
            return
        if not (user := update.effective_user):
            return
        if not (args := context.args):
            await update.message.reply_text("你好，我正在休眠\nZzz……")
            return
        
        arg: str = "".join(args)
        userid = str(user.id)

        if arg.startswith("bind_"):
            got = False
            try:
                verify = bind_verify_queue.get_nowait()
                got = True
                if time.monotonic() >= verify.expired_at:
                    if not verify.fut.done():
                        verify.fut.set_result(
                            BindVerified(
                                user_id=verify.user_id,
                                player_name=verify.player_name,
                                verified=False
                            )
                        )
                else:
                    bind_verify_queue.put_nowait(verify)
            except queue.Empty:
                pass
            finally:
                if got:
                    bind_verify_queue.task_done()

            if userid in self.bind_players:
                await update.message.reply_text(
                    text="你已经绑定过，不要重复绑定"
                )
                return

            rest: str = arg[len("bind_"):]

            try:
                from_userid, player_name = rest.split("_", 1)
            except ValueError:
                await update.message.reply_text(
                    text="格式无效"
                )
                return

            if userid != from_userid:
                await update.message.reply_text("这不是你的验证链接")
                return

            code: str = f"{secrets.randbelow(1_000_000):06d}"
            fut = Future()
            expired_at = time.monotonic() + 60

            try:
                bind_verify_queue.put_nowait(
                    BindVerify(
                        userid,
                        player_name,
                        code,
                        False,
                        expired_at,
                        fut
                    )
                )
            except queue.Full:
                await context.bot.send_message(
                    chat_id=userid,
                    text="当前有其它验证进行中，请稍后再试"
                    )
                return
            fut.add_done_callback(partial(self.bind_callback, context.bot))
            await context.bot.send_message(
                chat_id=userid,
                text=f"请进入服务器发送 `!!tgb bind {code}` 即可完成验证",
                parse_mode=ParseMode.MARKDOWN_V2
            )
    
    async def status_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        server_program_pid: int | None = self.mcserver.get_server_pid()

        if server_program_pid is not None:
            server_status = self.mcserver.get_server_information()
            server_running: bool = self.mcserver.is_server_running()
            server_rcon_running: bool = self.mcserver.is_rcon_running()
            tg_queue_count: int = tg_messages_queue.qsize()
            mc_queue_count: int = mc_messages_queue.qsize()
            bind_queue_count = bind_verify_queue.qsize()
            mc_pid = psutil.Process(server_program_pid)
            mc_usage_cpu = self._sample_mc_cpu_percent(server_program_pid)
            mc_usage_cpu_text = "采样中..." if mc_usage_cpu is None else f"{mc_usage_cpu:.2f}%"
            mc_usege_mem_mb: float = mc_pid.memory_info().rss / 1024 / 1024
            mc_threads: int = mc_pid.num_threads()
            mc_io = mc_pid.io_counters()
            mc_read_mb: float = mc_io.read_bytes / 1024 / 1024
            mc_write_mb: float = mc_io.write_bytes / 1024 / 1024

            await update.message.reply_text(
                (
                    f"服务器状态:\n"
                    f"服务端版本: {server_status.version}\n"
                    f"运行状态: {BoolStr[server_running]}\n"
                    f"服务端CPU占用: {mc_usage_cpu_text}\n"
                    f"服务端内存占用: {mc_usege_mem_mb:.2f} MB\n"
                    f"服务端线程占用: {mc_threads}\n"
                    f"IO累计读写(MB): {mc_read_mb:.1f}/{mc_write_mb:.1f}\n\n"
                    f"RCON状态: {BoolStr[server_rcon_running]}\n"
                    f"服务器进程PID: {server_program_pid}\n"
                    f"未处理队列数量(TG/MC): {tg_queue_count}/{mc_queue_count}\n"
                    f"验证绑定处理队列: {bind_queue_count}/{bind_verify_queue.maxsize}"
                )
            )
        else:
            await update.message.reply_text(
                "服务器未运行"
            )
    
    async def bind_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        if not (user := update.effective_user):
            return
        if not (args := context.args):
            await update.message.reply_text(
                "用法: /bind [玩家名]"
            )
            return
        if len(args) > 1:
            await update.message.reply_text(
                "玩家名不应该有空格"
            )
            return
        
        userid = str(user.id)
        player_name: str = args[0]
        
        if userid in self.bind_players:
            await update.message.reply_text(
                text="你已经绑定过，不要重复绑定"
            )
            return

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"验证你是 {player_name}",
                url=f"https://t.me/{self.username}?start=bind_{userid}_{player_name}"
            )]
        ])
        await update.message.reply_text(
            text="点击下方按钮验证",
            reply_markup=reply_markup
        )

    async def stop_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        await update.message.reply_text(
            text="确定要停止服务器？",
            reply_markup=self.inlinekeyboard("stop_server", "确认")
        )

    async def restart_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        await update.message.reply_text(
            text="确定要重启服务器？",
            reply_markup=self.inlinekeyboard("restart_server", "确认")
        )
    
    async def start_server_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        if self.mcserver.is_server_running():
            await update.message.reply_text(
                    text="服务器已经在运行了"
                )
            return
        await update.message.reply_text(
            text="确定要启动服务器？",
            reply_markup=self.inlinekeyboard("start_server", "确认")
        )

    async def exec_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        if not self.mcserver.is_server_running():
            await update.message.reply_text(
                    text="服务器未在运行"
                )
            return
        if (user := update.effective_user) is None:
            return
        if not (args := context.args):
            await update.message.reply_text(
                    text=(
                        "用法: /exec [Minecraft 命令]\n"
                        "或以 !! 为前缀来执行 MCDR 命令"
                    )
                )
            return
        
        command: str = " ".join(args)

        if command.startswith("!!"):
            mcdr_command: str = command[2:]
            self.mcserver.execute_command(
                command=mcdr_command,
                source=TelegramCommandSource(
                    mcdr_server=self.mcserver,
                    messsage_context=update.message,
                    admin_id=self.admin_id
                )
            )
            await update.message.reply_text(
                text="命令已执行"
            )
            return
        
        if user.id != self.admin_id:
            await update.message.reply_text(
                text="你没有执行此命令的权限"
            )
            return
        
        if self.mcserver.is_rcon_running():
            result = self.mcserver.rcon_query(command)
            await update.message.reply_text(
                text=f"命令执行结果:\n{result}"
            )
            return

        self.mcserver.execute(command)
        await update.message.reply_text(
            text="命令已执行"
        )

    async def list_handler(self, update: Update, context: Context) -> None:
        if not self.is_allowed_chat(update):
            return
        if not update.message:
            return
        player_list_raw: tuple[int, int, list[str]] | None = api.get_server_player_list()

        if player_list_raw is None:
            await update.message.reply_text(
                f"查询玩家列表失败"
            )
            return
        
        player_count, player_count_max, player_list = player_list_raw
        player_lists: str = "\n".join(player_list)
        
        await update.message.reply_text(
            f"游玩人数(当前/最大): {player_count}/{player_count_max}\n"
            f"玩家列表:\n{player_lists}"
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
            userid=user.id,
            player_name=player_name,
            username=user.username,
            fullname=user.full_name,
            fromchat=chat.id,
            frommessageid=message.id,
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

    def register_handlers(self) -> None:
        self.bot.add_error_handler(self.on_error)
        self.bot.add_handler(CommandHandler("start", self.start_handler))
        self.bot.add_handler(CommandHandler("status", self.status_handler))
        self.bot.add_handler(CommandHandler("bind", self.bind_handler))
        self.bot.add_handler(CommandHandler("stop", self.stop_handler, filters.User(self.admin_id)))
        self.bot.add_handler(CommandHandler("restart", self.restart_handler, filters.User(self.admin_id)))
        self.bot.add_handler(CommandHandler("start_server", self.start_server_handler, filters.User(self.admin_id)))
        self.bot.add_handler(CommandHandler("exec", self.exec_handler))
        self.bot.add_handler(CommandHandler("list", self.list_handler))
        self.bot.add_handler(
            MessageHandler(
                filters=~filters.COMMAND,
                callback=self.messages_handler
            )
        )
        self.bot.add_handler(CallbackQueryHandler(self.inlinekeyboard_callback))

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
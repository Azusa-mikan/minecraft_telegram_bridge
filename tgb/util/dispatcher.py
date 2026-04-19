from dataclasses import dataclass
from queue import Queue
from concurrent.futures import Future

@dataclass(frozen=True, slots=True)
class StopSignal:
    pass

@dataclass(slots=True)
class MCTOTGMessages:
    player: str | None
    text: str
    from_chat_id: int | None = None
    from_message_id: int | None = None
    error_message: Future[str] | None = None

@dataclass(slots=True)
class TGTOMCMessages:
    userid: int
    player_name: str | None
    username: str | None
    fullname: str
    fromchat: int
    frommessageid: int
    text: str

@dataclass(slots=True)
class BindVerified:
    user_id: str
    player_name: str
    verified: bool

@dataclass(slots=True)
class BindVerify:
    user_id: str
    player_name: str
    code: str
    verified: bool
    expired_at: float
    fut: Future[BindVerified]

tg_messages_queue: Queue[StopSignal | MCTOTGMessages] = Queue()
mc_messages_queue: Queue[StopSignal | TGTOMCMessages] = Queue()
bind_verify_queue: Queue[BindVerify] = Queue(maxsize=1)

def send_message_to_telegram(
        *,
        player: str | None,
        text: str,
        from_chat_id: int | None = None,
        from_message_id: int | None = None,
    ) -> None:
    tg_messages_queue.put_nowait(
        MCTOTGMessages(
            player,
            text,
            from_chat_id,
            from_message_id,
        )
    )

def send_message_to_minecraft(
        *,
        userid: int,
        player_name: str | None,
        username: str | None,
        fullname: str,
        fromchat: int,
        frommessageid: int,
        text: str,
    ) -> None:
    mc_messages_queue.put_nowait(
        TGTOMCMessages(
            userid,
            player_name,
            username,
            fullname,
            fromchat,
            frommessageid,
            text
        )
    )
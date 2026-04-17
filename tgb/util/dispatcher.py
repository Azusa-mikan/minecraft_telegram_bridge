from dataclasses import dataclass
from queue import Queue

@dataclass(frozen=True)
class StopSignal:
    pass

@dataclass
class MCTOTGMessages:
    player: str
    text: str
    reply_chat_id: int | None = None
    reply_to_message_id: int | None = None

@dataclass
class TGTOMCMessages:
    userid: int
    username: str | None
    fullname: str
    fromchat: int
    text: str

tg_messages_queue: Queue[StopSignal | MCTOTGMessages] = Queue()
mc_messages_queue: Queue[StopSignal | TGTOMCMessages] = Queue()

def send_message_to_telegram(
        player: str,
        text: str
    ) -> None:
    tg_messages_queue.put_nowait(
        MCTOTGMessages(
            player,
            text
        )
    )

def send_message_to_minecraft(
        userid: int,
        username: str | None,
        fullname: str,
        fromchat: int,
        text: str,
    ) -> None:
    mc_messages_queue.put_nowait(
        TGTOMCMessages(
            userid,
            username,
            fullname,
            fromchat,
            text
        )
    )
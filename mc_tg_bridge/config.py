from pathlib import Path
import pkgutil

from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from pydantic import BaseModel, ValidationError
from mcdreforged import PluginServerInterface

yaml = YAML(typ="rt")
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.preserve_quotes = True

config_raw = pkgutil.get_data("mc_tg_bridge", "resources/config.yaml")

class ResourceBundleError(Exception):
    def __init__(self, file_path: Path | str | None = None) -> None:
        self.file = file_path


class TelegramConfig(BaseModel):
    bot_token: str
    admin_id: int
    chat_ids: list[int]

class Config(BaseModel):
    plugin_status: bool
    to_tg_message_format: str
    to_mc_message_format: str
    joined_message: str
    left_message: str
    server_started_message: str
    server_stopped_message: str
    mc_to_tg_send_events: bool
    telegram: TelegramConfig


def load_config(
        server: PluginServerInterface
    ):
    config_path = Path(server.get_data_folder(), "config.yaml")
    try:
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            if config_raw is not None:
                config_path.write_bytes(config_raw)
            else:
                raise ResourceBundleError(config_path)
        raw_config = yaml.load(
            config_path.read_text(encoding="utf-8")
        )
        return Config.model_validate(raw_config)
    except (ParserError, ValidationError) as e:
        raise ResourceBundleError(config_path) from e

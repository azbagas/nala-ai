"""Config loader (PRD §6.2 "Config Loader").

Loads `config.yaml` + `persona.yaml`, expands ${VAR} / ${VAR:-default} from the
environment, validates everything with pydantic, and supports persona reload.
Secrets are read only from environment variables (PRD §11.3).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent.parent

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_hhmm(value: str) -> str:
    if not _HHMM_PATTERN.match(value):
        raise ValueError(f"expected HH:MM (24h), got {value!r}")
    return value


HHMM = Annotated[str, AfterValidator(_validate_hhmm)]


def expand_env(obj: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in strings."""
    if isinstance(obj, str):
        def _sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            value = os.environ.get(name)
            if value is None or value == "":
                if default is not None:
                    return default
                raise ConfigError(f"Environment variable {name!r} is not set")
            return value

        return _ENV_PATTERN.sub(_sub, obj)
    if isinstance(obj, dict):
        return {k: expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env(v) for v in obj]
    return obj


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Top level of {path} must be a mapping")
    return data


def _resolve_dir(env_name: str, default: str) -> Path:
    raw = os.environ.get(env_name) or default
    p = Path(raw)
    return p if p.is_absolute() else (BASE_DIR / p)


# ---------------------------------------------------------------------------
# config.yaml schema
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppSection(_Strict):
    timezone: str = "Asia/Jakarta"
    language: str = "id"
    owner_telegram_id: int | None = None
    log_level: str = "INFO"

    @field_validator("owner_telegram_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class LLMSection(_Strict):
    provider: str = "gemini"
    chat_model: str
    utility_model: str
    temperature_chat: float = Field(0.9, ge=0, le=2)
    temperature_utility: float = Field(0.2, ge=0, le=2)
    max_output_tokens_chat: int = Field(300, gt=0)
    history_window_messages: int = Field(24, gt=0)
    request_timeout_seconds: float = Field(30, gt=0)
    max_retries: int = Field(3, ge=0)


class TypingDelay(_Strict):
    min_seconds: float = Field(1.0, ge=0)
    max_seconds: float = Field(6.0, ge=0)
    chars_per_second: float = Field(25, gt=0)

    @model_validator(mode="after")
    def _order(self) -> "TypingDelay":
        if self.min_seconds > self.max_seconds:
            raise ValueError("typing_delay.min_seconds must be <= max_seconds")
        return self


class ConversationSection(_Strict):
    debounce_seconds: float = Field(4, ge=0)
    max_bubbles: int = Field(3, ge=1)
    max_bubble_chars: int = Field(280, gt=0)
    typing_delay: TypingDelay = TypingDelay()


class TimeRange(_Strict):
    start: HHMM
    end: HHMM


class Window(TimeRange):
    name: str
    chance: float = Field(ge=0, le=1)


class Bonus(_Strict):
    chance: float = Field(0.25, ge=0, le=1)
    max_per_day: int = Field(1, ge=0)
    awake_start: HHMM = "09:00"
    awake_end: HHMM = "21:30"


class ProactiveSection(_Strict):
    daily_cap: int = Field(4, ge=0)
    quiet_hours: TimeRange
    windows: list[Window]
    bonus: Bonus = Bonus()
    skip_if_user_active_within_minutes: int = Field(20, ge=0)
    backoff_after_ignored: int = Field(2, ge=0)
    stop_after_ignored: int = Field(4, ge=0)
    message_type_weights: dict[str, float]

    @field_validator("windows")
    @classmethod
    def _unique_names(cls, v: list[Window]) -> list[Window]:
        names = [w.name for w in v]
        if len(names) != len(set(names)):
            raise ValueError("proactive window names must be unique")
        return v


class MemorySection(_Strict):
    nightly_job_time: HHMM = "23:30"
    daily_life_job_time: HHMM = "05:30"
    thread_expire_days: int = Field(7, ge=1)
    rolling_summary_every_days: int = Field(7, ge=1)


class StorageSection(_Strict):
    db_path: str = "companion.db"
    backup_dir: str = "backups"
    log_dir: str = "logs"


class BackupSection(_Strict):
    daily_time: HHMM = "03:00"
    keep_days: int = Field(14, ge=1)


class SafetySection(_Strict):
    # Local help resources shown when the user expresses serious distress (PRD §11.1).
    crisis_resources: list[str] = []


class AppConfig(_Strict):
    app: AppSection
    llm: LLMSection
    conversation: ConversationSection = ConversationSection()
    proactive: ProactiveSection
    memory: MemorySection = MemorySection()
    storage: StorageSection = StorageSection()
    backup: BackupSection = BackupSection()
    safety: SafetySection = SafetySection()


# ---------------------------------------------------------------------------
# persona.yaml schema (extra keys allowed so the owner can add fields freely)
# ---------------------------------------------------------------------------

class Pronouns(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    self_: str = Field("aku", alias="self")
    you: str = "kamu"


class SpeakingStyle(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    register_: str = Field("casual", alias="register")
    emoji_level: str = "medium"
    slang: list[str] = []


class Persona(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    age: int | None = None
    user_nickname: str = "kamu"
    pronouns: Pronouns = Pronouns()
    backstory: str = ""
    traits: list[str] = []
    interests: list[str] = []
    dislikes: list[str] = []
    speaking_style: SpeakingStyle = SpeakingStyle()
    boundaries: list[str] = []
    life: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Secrets & aggregate settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str
    gemini_api_key: str

    def __repr__(self) -> str:  # never leak secrets into logs
        return "Secrets(telegram_bot_token=***, gemini_api_key=***)"


@dataclass
class Settings:
    config: AppConfig
    persona: Persona
    secrets: Secrets = field(repr=False)
    config_dir: Path
    prompts_dir: Path
    data_dir: Path

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.app.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.config.storage.db_path

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / self.config.storage.backup_dir

    @property
    def log_dir(self) -> Path:
        return self.data_dir / self.config.storage.log_dir

    @property
    def persona_path(self) -> Path:
        return self.config_dir / "persona.yaml"

    def reload_persona(self) -> Persona:
        """Re-read persona.yaml (FR-2.3). Keeps the old persona if the new one is invalid."""
        self.persona = load_persona(self.persona_path)
        return self.persona


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def load_secrets() -> Secrets:
    missing = [n for n in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY") if not os.environ.get(n)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
    return Secrets(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"].strip(),
        gemini_api_key=os.environ["GEMINI_API_KEY"].strip(),
    )


def load_app_config(path: Path) -> AppConfig:
    raw = expand_env(_read_yaml(path))
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError
        raise ConfigError(f"Invalid config in {path}:\n{exc}") from exc


def load_persona(path: Path) -> Persona:
    raw = _read_yaml(path)
    try:
        return Persona.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Invalid persona in {path}:\n{exc}") from exc


def load_settings(env_file: Path | None = None) -> Settings:
    """Load .env, config.yaml, persona.yaml and secrets into a Settings object."""
    load_dotenv(env_file or BASE_DIR / ".env", override=False)

    config_dir = _resolve_dir("CONFIG_DIR", "config")
    prompts_dir = _resolve_dir("PROMPTS_DIR", "prompts")
    data_dir = _resolve_dir("DATA_DIR", "data")

    config = load_app_config(config_dir / "config.yaml")
    persona = load_persona(config_dir / "persona.yaml")
    secrets = load_secrets()

    settings = Settings(
        config=config,
        persona=persona,
        secrets=secrets,
        config_dir=config_dir,
        prompts_dir=prompts_dir,
        data_dir=data_dir,
    )
    for d in (settings.data_dir, settings.backup_dir, settings.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    return settings

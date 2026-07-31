"""Runtime configuration.

Defaults are the conservative ones: headed browser, persistent profile, slow
human-ish pacing, and dry-run **off** only when explicitly asked. Everything can
be overridden by environment variable so a CI run and a real run share code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass
class HumanizeConfig:
    """Knobs for the bot-detection-avoidance layer (see `agent/humanize.py`)."""

    enabled: bool = True

    #: Mean/stdev (seconds) of the pause inserted before each meaningful action.
    action_delay_mean: float = 0.9
    action_delay_stdev: float = 0.35

    #: Per-character typing delay range (seconds).
    type_delay_min: float = 0.04
    type_delay_max: float = 0.18

    #: Pause between two consecutive line items, and between two orders.
    inter_item_pause: tuple[float, float] = (2.5, 6.0)
    inter_order_pause: tuple[float, float] = (8.0, 20.0)

    #: Stop after this many returns in one session; a human does not place 200
    #: returns back to back, and volume is the loudest bot signal there is.
    max_returns_per_session: int = 25

    #: Move the mouse to an element before clicking it.
    mouse_move_before_click: bool = True


@dataclass
class BrowserConfig:
    headless: bool = field(default_factory=lambda: _env_bool("AGENT_HEADLESS", False))

    #: Persistent profile directory. Reusing it keeps the Flipkart session alive
    #: across runs, which means one OTP instead of one per run — both friendlier
    #: to the user and far less bot-like.
    user_data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("AGENT_PROFILE_DIR", REPO_ROOT / ".browser-profile")
        )
    )

    locale: str = "en-IN"
    timezone_id: str = "Asia/Kolkata"
    viewport: tuple[int, int] = (1440, 900)
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )
    default_timeout_ms: int = 30_000

    #: Set by the test harness to point the Flipkart adapter at the mock site.
    base_url_override: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentConfig:
    workbook: Path = field(
        default_factory=lambda: Path(os.getenv("AGENT_WORKBOOK", REPO_ROOT / "data" / "return_tasks.xlsx"))
    )
    sheet_name: str = os.getenv("AGENT_SHEET", "Returns")

    #: When true the agent walks the entire flow but stops short of the final
    #: irreversible confirm click. Used for rehearsals against a live account.
    dry_run: bool = field(default_factory=lambda: _env_bool("AGENT_DRY_RUN", False))

    #: Login phone. Supply it at runtime with `--phone` or AGENT_LOGIN_PHONE; the
    #: placeholder default exists so nothing real is ever stored in the repo.
    login_phone: str = os.getenv("AGENT_LOGIN_PHONE", "9000000001")

    #: How long to wait for a human to type the OTP, in seconds.
    otp_timeout_s: float = _env_float("AGENT_OTP_TIMEOUT", 180.0)

    #: Default return reason chosen in the platform UI.
    return_reason: str = os.getenv("AGENT_RETURN_REASON", "Item is not as described")
    refund_mode: str = os.getenv("AGENT_REFUND_MODE", "Original payment method")

    max_attempts_per_item: int = 2

    browser: BrowserConfig = field(default_factory=BrowserConfig)
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)

    log_dir: Path = field(default_factory=lambda: REPO_ROOT / "logs")
    screenshot_on_failure: bool = True

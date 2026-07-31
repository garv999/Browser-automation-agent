"""Browser agent for automated multi-item returns."""

from .config import AgentConfig, BrowserConfig, HumanizeConfig
from .models import (
    FlowModel,
    LineItem,
    Platform,
    ReturnOutcome,
    ReturnStatus,
    ReturnTask,
    TaskStatus,
)
from .runner import ReturnsAgent, RunReport

__version__ = "1.0.0"

__all__ = [
    "AgentConfig",
    "BrowserConfig",
    "FlowModel",
    "HumanizeConfig",
    "LineItem",
    "Platform",
    "ReturnOutcome",
    "ReturnStatus",
    "ReturnTask",
    "ReturnsAgent",
    "RunReport",
    "TaskStatus",
]

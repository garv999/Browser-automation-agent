"""Shared test fixtures.

The end-to-end tests drive a real Chromium against a real HTTP server. Delays
are shrunk rather than switched off, so the humanised click/type paths — the
ones that actually run in production — are the paths under test.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from agent.config import AgentConfig, HumanizeConfig
from agent.models import Platform
from agent.runner import ReturnsAgent
from mock_site.server import MOCK_OTP, STATE, create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerThread(threading.Thread):
    def __init__(self, app, port: int):
        super().__init__(daemon=True)
        self.server = make_server("127.0.0.1", port, app, threaded=True)

    def run(self) -> None:
        self.server.serve_forever()

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture(scope="session")
def mock_server():
    """One mock storefront for the whole session."""
    port = _free_port()
    thread = ServerThread(create_app(), port)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    thread.stop()


@pytest.fixture(autouse=True)
def reset_mock_state():
    STATE.reset()
    yield
    STATE.reset()


@pytest.fixture
def workbook(tmp_path) -> Path:
    """A freshly seeded workbook matching the mock storefront's fixtures."""
    from scripts.seed_workbook import rows_from_fixtures, write

    path = tmp_path / "return_tasks.xlsx"
    write(rows_from_fixtures(), path)
    return path


@pytest.fixture
def workbook_for(tmp_path):
    """Seed a workbook containing only the named orders.

    Keeping each end-to-end test to one order makes a failure point at a single
    flow instead of at 'something in the suite'.
    """
    from scripts.seed_workbook import rows_from_fixtures, write

    counter = {"n": 0}

    def _make(*order_ids: str) -> Path:
        rows = [r for r in rows_from_fixtures() if not order_ids or r["order_id"] in order_ids]
        assert rows, f"no fixture rows for {order_ids}"
        counter["n"] += 1
        path = tmp_path / f"workbook_{counter['n']}.xlsx"
        write(rows, path)
        return path

    return _make


@pytest.fixture
def fast_humanize() -> HumanizeConfig:
    """Real humanisation, compressed timings — the code paths still execute."""
    return HumanizeConfig(
        enabled=True,
        action_delay_mean=0.01,
        action_delay_stdev=0.004,
        type_delay_min=0.0,
        type_delay_max=0.002,
        inter_item_pause=(0.0, 0.01),
        inter_order_pause=(0.0, 0.01),
        max_returns_per_session=25,
        mouse_move_before_click=True,
    )


@pytest.fixture
def make_config(mock_server, fast_humanize, tmp_path):
    counter = {"n": 0}

    def _make(workbook: Path, **overrides) -> AgentConfig:
        counter["n"] += 1
        cfg = AgentConfig()
        cfg.workbook = workbook
        # A copy per run: a test that tightens the session cap must not leak
        # that setting into the next run in the same test.
        cfg.humanize = replace(fast_humanize)
        cfg.log_dir = tmp_path / "logs"
        cfg.browser.headless = True
        # A fresh profile per run: reusing one across tests would carry a login
        # session between them and hide an authentication regression.
        cfg.browser.user_data_dir = tmp_path / f"profile_{counter['n']}"
        cfg.browser.base_url_override = {
            Platform.FLIPKART.value: f"{mock_server}/flipkart",
            Platform.AMAZON.value: f"{mock_server}/amazon",
        }
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    return _make


@pytest.fixture
def run_agent(make_config):
    """Run the agent against a workbook and hand back the report plus the log."""

    def _run(workbook: Path, _mutate=None, **overrides):
        config = make_config(workbook, **overrides)
        if _mutate:
            # For nested settings that `setattr` on the config cannot reach.
            _mutate(config)
        lines: list[str] = []
        agent = ReturnsAgent(
            config,
            logger=lines.append,
            otp_provider=lambda phone: MOCK_OTP,
        )
        report = agent.run()
        return report, lines, config

    return _run


@pytest.fixture
def rows_by_sku():
    """Read a workbook back keyed by SKU — how every assertion inspects results."""
    from agent.excel_io import read_tasks

    def _read(workbook: Path) -> dict:
        return {t.sku: t for t in read_tasks(workbook)}

    return _read

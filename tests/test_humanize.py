"""Bot-detection avoidance.

Two things are worth asserting here. The stealth script must actually cover the
known fingerprint probes, and the session pacer must actually stop — a volume
cap that can be exceeded is the one bug in this module that gets an account
blocked.
"""

from __future__ import annotations

import random
import statistics

from agent.config import HumanizeConfig
from agent.humanize import Humanizer, SessionPacer, stealth_script


def test_stealth_script_covers_the_standard_probes():
    js = stealth_script()
    for probe in ["navigator.webdriver", "window.chrome", "plugins", "languages", "permissions", "getParameter"]:
        assert probe in js, f"stealth script does not address {probe}"


def test_delays_are_right_skewed_not_uniform():
    """Human pauses are mostly short with an occasional long one. A uniform
    distribution is itself a detectable signature."""
    human = Humanizer(HumanizeConfig(action_delay_mean=1.0, action_delay_stdev=0.4), rng=random.Random(7))
    samples = [human._log_normal_delay(1.0, 0.4) for _ in range(4000)]

    assert statistics.median(samples) < statistics.mean(samples)
    assert max(samples) > statistics.mean(samples) * 2
    assert min(samples) > 0


def test_delays_track_the_configured_mean():
    human = Humanizer(rng=random.Random(11))
    samples = [human._log_normal_delay(0.9, 0.35) for _ in range(6000)]
    assert 0.8 < statistics.mean(samples) < 1.0


def test_disabled_humanizer_does_not_sleep():
    human = Humanizer(HumanizeConfig(enabled=False))
    assert human.pause() == 0.0
    assert human.pause_between((5.0, 10.0)) == 0.0


def test_session_cap_is_enforced():
    config = HumanizeConfig(max_returns_per_session=3, inter_item_pause=(0, 0), inter_order_pause=(0, 0))
    pacer = SessionPacer(config)

    for _ in range(3):
        assert not pacer.exhausted
        pacer.record_return()

    assert pacer.exhausted


def test_pause_is_longer_between_orders_than_between_items():
    config = HumanizeConfig(inter_item_pause=(0.0, 0.001), inter_order_pause=(0.02, 0.03))
    pacer = SessionPacer(config)

    assert pacer.before_task("OD1") == 0.0          # first task, no pause
    same_order = pacer.before_task("OD1")
    new_order = pacer.before_task("OD2")

    assert same_order <= 0.001
    assert new_order >= 0.02

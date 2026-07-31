"""Bot-detection avoidance.

Anti-bot systems on retail sites score a session on three broad signals. This
module addresses all three, because defeating only one is worthless:

1. **Environment fingerprint** — headless Chrome leaks `navigator.webdriver`,
   an empty plugin array, a missing `window.chrome`, a mismatched UA/platform,
   and a WebGL vendor of "Google SwiftShader". `stealth_script()` patches these
   before any page script runs.
2. **Behavioural timing** — bots click instantly, type at a constant rate, and
   never overshoot a target. Real pointer traces are jittery and log-normal in
   their pauses. `pause()`, `type_like_human()` and `click()` reproduce that.
3. **Volume and rhythm** — the loudest signal of all. A human does not place 40
   returns in four minutes. `SessionPacer` caps returns per session and inserts
   growing pauses between items and orders.

None of this defeats a determined detector, and it is not meant to: the account
is the user's own and the actions are ones they are entitled to take. The goal
is to look like what this actually is — a person working through their returns.
"""

from __future__ import annotations

import math
import random
import time
from typing import Optional

from .config import HumanizeConfig

# Patched into every page before site scripts run. Each block removes one of the
# standard automation tells.
STEALTH_JS = """
// 1. navigator.webdriver — set by every CDP-driven browser, checked by everyone.
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. window.chrome — absent in headless, present in every real Chrome.
if (!window.chrome) {
  window.chrome = { runtime: {}, app: { isInstalled: false } };
}

// 3. Plugins and mimeTypes — headless reports an empty list, which no real
//    browser does. Length is what most checks actually read.
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5].map(i => ({ name: `Plugin ${i}`, filename: `p${i}.dll` })),
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-GB', 'en-US'] });

// 4. Permissions API — headless resolves notifications to 'denied' while
//    Notification.permission says 'default'; the mismatch is a known probe.
const _query = window.navigator.permissions && window.navigator.permissions.query;
if (_query) {
  window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _query(params);
}

// 5. WebGL vendor/renderer — headless returns SwiftShader, a dead giveaway.
const _getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (parameter) {
  if (parameter === 37445) return 'Intel Inc.';              // UNMASKED_VENDOR_WEBGL
  if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
  return _getParameter.apply(this, [parameter]);
};

// 6. Hardware profile consistent with the advertised macOS user agent.
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
"""


def stealth_script() -> str:
    return STEALTH_JS


class Humanizer:
    """Timing and input behaviour for one browser session."""

    def __init__(self, config: Optional[HumanizeConfig] = None, rng: Optional[random.Random] = None):
        self.config = config or HumanizeConfig()
        self.rng = rng or random.Random()

    # -- timing -----------------------------------------------------------

    def _log_normal_delay(self, mean: float, stdev: float) -> float:
        """Human inter-action gaps are right-skewed: mostly quick, occasionally
        long. A log-normal reproduces that far better than a uniform range."""
        if mean <= 0:
            return 0.0
        sigma = math.sqrt(math.log(1 + (stdev / mean) ** 2))
        mu = math.log(mean) - sigma**2 / 2
        return max(0.05, self.rng.lognormvariate(mu, sigma))

    def pause(self, scale: float = 1.0) -> float:
        """Pause as a person would before the next deliberate action."""
        if not self.config.enabled:
            return 0.0
        delay = self._log_normal_delay(
            self.config.action_delay_mean * scale, self.config.action_delay_stdev * scale
        )
        time.sleep(delay)
        return delay

    def pause_between(self, span: tuple[float, float]) -> float:
        if not self.config.enabled:
            return 0.0
        delay = self.rng.uniform(*span)
        time.sleep(delay)
        return delay

    # -- input ------------------------------------------------------------

    def type_like_human(self, locator, text: str) -> None:
        """Type character by character with a variable cadence, and a longer
        beat after separators — where a real typist actually hesitates."""
        if not self.config.enabled:
            locator.fill(text)
            return

        locator.click()
        self.pause(0.3)
        for char in text:
            locator.type(char, delay=0)
            delay = self.rng.uniform(self.config.type_delay_min, self.config.type_delay_max)
            if char in " -_@.":
                delay *= 1.8
            time.sleep(delay)

    def click(self, page, locator) -> None:
        """Move the pointer toward the element, then click slightly off-centre.

        Bots click the exact centre of the bounding box every time; humans do
        not, and the distribution of click offsets is a cheap detector.
        """
        if not self.config.enabled:
            locator.click()
            return

        locator.scroll_into_view_if_needed()
        self.pause(0.4)

        if self.config.mouse_move_before_click:
            try:
                box = locator.bounding_box()
            except Exception:
                box = None
            if box:
                target_x = box["x"] + box["width"] * self.rng.uniform(0.3, 0.7)
                target_y = box["y"] + box["height"] * self.rng.uniform(0.3, 0.7)
                # A couple of intermediate points: pointer paths are not straight
                # jumps, and `steps` alone still yields a perfect line.
                page.mouse.move(
                    target_x + self.rng.uniform(-60, 60),
                    target_y + self.rng.uniform(-40, 40),
                    steps=self.rng.randint(4, 9),
                )
                page.mouse.move(target_x, target_y, steps=self.rng.randint(6, 14))
                self.pause(0.25)
                page.mouse.click(target_x, target_y)
                return

        locator.click()

    def idle_scroll(self, page) -> None:
        """A short read-the-page scroll. Sessions with zero scrolling on a long
        product page look synthetic."""
        if not self.config.enabled:
            return
        for _ in range(self.rng.randint(1, 3)):
            page.mouse.wheel(0, self.rng.randint(180, 520))
            time.sleep(self.rng.uniform(0.25, 0.9))


class SessionPacer:
    """Caps how much work one session does, and paces it.

    Volume is the signal that gets accounts flagged, so this is the part of the
    module that matters most. When the cap is hit the runner stops cleanly and
    leaves the remaining tasks Pending for the next run.
    """

    def __init__(self, config: Optional[HumanizeConfig] = None, humanizer: Optional[Humanizer] = None):
        self.config = config or HumanizeConfig()
        self.humanizer = humanizer or Humanizer(self.config)
        self.returns_placed = 0
        self._last_order: Optional[str] = None

    @property
    def exhausted(self) -> bool:
        return self.returns_placed >= self.config.max_returns_per_session

    def record_return(self) -> None:
        self.returns_placed += 1

    def before_task(self, order_id: str) -> float:
        """Pause before starting a line item; longer when moving to a new order."""
        if self._last_order is None:
            self._last_order = order_id
            return 0.0

        span = (
            self.config.inter_order_pause
            if order_id != self._last_order
            else self.config.inter_item_pause
        )
        self._last_order = order_id
        return self.humanizer.pause_between(span)

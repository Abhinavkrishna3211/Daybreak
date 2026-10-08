"""Daylight gate: decides when the box may open.

Pure Python, no hardware imports, so it can be unit-tested on a laptop.
Feed it one Sample per second; read `state` and `progress()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from statistics import median


class State(str, Enum):
    IDLE = "idle"
    RINGING = "ringing"
    DWELL = "dwell"
    UNLOCKED = "unlocked"


# Ceilings the light sensor cannot physically cross. The LTR390 at gain 1 and 18-bit resolution
# tops out at 262143 counts, which is 0.6 * 262143 = 157k lux, and direct sun is about 120k. Real
# sunlight puts UV counts at a few percent of the visible ones, never half.
#
# These are here because of how the part fails, not for tidiness. An absent or unresponsive
# LTR390 answers an I2C read with all ones, and 0xFFFFFFFF arrives as 4.29e9 - so an unplugged
# sensor reads brighter than the sun and its UV ratio comes out at exactly 1.0. Both of those sail
# through a check that only has a lower bound, which would run the gate to its brightest tier and
# open the box in a dark room. "Too bright to be real" has to fail the gate, not pass it.
LUX_MAX = 200_000.0
UV_RATIO_MAX = 0.5


@dataclass
class Sample:
    lux: float
    uvs: float          # raw UV counts (LTR390 UVS mode)
    als: float          # raw visible counts (LTR390 ALS mode)
    steps: int          # cumulative step count since boot
    flicker: float = -1.0  # 0..1 ratio of 100 Hz energy to DC; -1 = sensor not fitted

    @property
    def uv_ratio(self) -> float:
        return self.uvs / self.als if self.als > 0 else 0.0

    @property
    def plausible(self) -> bool:
        """False when the light sensor is missing, or its read came back as nonsense.

        A dark room is plausible - lux 0 is a real reading, and it fails the daylight check on
        its own merits. This is only about values no working sensor can produce.
        """
        return 0.0 <= self.lux <= LUX_MAX and 0.0 <= self.uv_ratio <= UV_RATIO_MAX


@dataclass
class GateConfig:
    steps_required: int = 40
    lux_min: float = 1000.0
    uv_ratio_min: float = 0.02      # CALIBRATE on day 1 (docs/06-test-protocol.md §A)
    flicker_max: float = 0.10
    grace_s: int = 90               # short cloud / doorway dips are forgiven
    tier_window_s: int = 60         # valid seconds used to pick the tier
    # (min median lux, required seconds) — highest first.
    # Defaults follow Huberman's Sleep Toolkit (~10 / ~20 / 30-60 min); adjustable.
    tiers: list[tuple[float, int]] = field(default_factory=lambda: [
        (20000.0, 10 * 60),   # clear
        (5000.0, 20 * 60),    # cloudy
        (0.0, 30 * 60),       # overcast
    ])

    @classmethod
    def demo(cls) -> "GateConfig":
        """Short timings for filming and bench tests."""
        return cls(steps_required=10, grace_s=10, tier_window_s=5,
                   tiers=[(20000.0, 30), (5000.0, 45), (0.0, 60)])


class Gate:
    def __init__(self, cfg: GateConfig | None = None):
        self.cfg = cfg or GateConfig()
        self.reset()

    # ---- lifecycle -------------------------------------------------------
    def reset(self) -> None:
        self.state = State.IDLE
        self.steps_at_alarm = 0
        self.valid_s = 0
        self.gap_s = 0
        self.tier_lux: list[float] = []
        self.required_s: int | None = None
        self.unlock_reason: str | None = None

    def start_alarm(self, current_steps: int) -> None:
        self.reset()
        self.state = State.RINGING
        self.steps_at_alarm = current_steps

    def safety_open(self, reason: str) -> None:
        """Override button, max-lock timer or watchdog."""
        self.state = State.UNLOCKED
        self.unlock_reason = f"safety:{reason}"

    # ---- core ------------------------------------------------------------
    def is_daylight(self, s: Sample) -> bool:
        c = self.cfg
        if not s.plausible:
            return False        # a missing or faulty sensor is not daylight, however bright
        if s.lux < c.lux_min or s.uv_ratio < c.uv_ratio_min:
            return False
        if s.flicker >= 0 and s.flicker > c.flicker_max:
            return False
        return True

    def _pick_tier(self) -> int:
        m = median(self.tier_lux)
        for min_lux, seconds in self.cfg.tiers:
            if m >= min_lux:
                return seconds
        return self.cfg.tiers[-1][1]

    def update(self, s: Sample) -> State:
        """Call once per second."""
        if self.state in (State.IDLE, State.UNLOCKED):
            return self.state

        walked = (s.steps - self.steps_at_alarm) >= self.cfg.steps_required
        if self.state == State.RINGING:
            if walked and self.is_daylight(s):
                self.state = State.DWELL
            else:
                return self.state

        # DWELL
        if self.is_daylight(s):
            self.gap_s = 0
            self.valid_s += 1
            if self.required_s is None:
                self.tier_lux.append(s.lux)
                if len(self.tier_lux) >= self.cfg.tier_window_s:
                    self.required_s = self._pick_tier()
        else:
            self.gap_s += 1
            if self.gap_s > self.cfg.grace_s:
                # left the daylight for too long: start over
                self.valid_s = 0
                self.tier_lux.clear()
                self.required_s = None
                self.gap_s = 0

        if self.required_s is not None and self.valid_s >= self.required_s:
            self.state = State.UNLOCKED
            self.unlock_reason = "daylight"
        return self.state

    def progress(self) -> dict:
        return {
            "state": self.state.value,
            "valid_s": self.valid_s,
            "required_s": self.required_s,
            "gap_s": self.gap_s,
            "reason": self.unlock_reason,
        }

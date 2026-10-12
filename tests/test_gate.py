from gate import Gate, GateConfig, Sample, State

SUN = dict(lux=40000, uvs=2000, als=40000)        # ratio 0.05
SHADE = dict(lux=8000, uvs=400, als=8000)         # ratio 0.05
TORCH = dict(lux=9000, uvs=5, als=9000)           # bright, no UV
ROOM = dict(lux=300, uvs=1, als=300)


def run(gate, light, seconds, steps, flicker=-1.0):
    for _ in range(seconds):
        gate.update(Sample(steps=steps, flicker=flicker, **light))
    return gate.state


def demo_gate():
    g = Gate(GateConfig.demo())
    g.start_alarm(current_steps=100)
    return g


def test_honest_sun_unlocks_on_clear_tier():
    g = demo_gate()
    assert run(g, ROOM, 5, steps=100) == State.RINGING
    assert run(g, SUN, 29, steps=120) == State.DWELL
    assert run(g, SUN, 1, steps=120) == State.UNLOCKED
    assert g.unlock_reason == "daylight"


def test_shade_needs_longer_cloudy_tier():
    g = demo_gate()
    run(g, SHADE, 44, steps=120)
    assert g.state == State.DWELL and g.required_s == 45
    run(g, SHADE, 1, steps=120)
    assert g.state == State.UNLOCKED


def test_torch_never_counts():
    g = demo_gate()
    assert run(g, TORCH, 600, steps=500) == State.RINGING


def test_no_walk_no_dwell():
    g = demo_gate()
    assert run(g, SUN, 300, steps=105) == State.RINGING  # only 5 steps


def test_flicker_rejects_lamp_even_with_uv():
    g = demo_gate()
    assert run(g, SUN, 120, steps=120, flicker=0.5) == State.RINGING


def test_short_dip_forgiven_long_dip_resets():
    g = demo_gate()
    run(g, SUN, 20, steps=120)
    run(g, ROOM, 8, steps=120)          # within 10 s grace
    assert g.valid_s == 20
    run(g, ROOM, 11, steps=120)         # exceeds grace
    assert g.valid_s == 0 and g.state == State.DWELL


def test_an_unplugged_light_sensor_never_counts_as_daylight():
    """The reading this actually produced on the board, before the gate had an upper bound.

    An absent LTR390 answers every I2C read with all ones, so lux arrives as 4.29e9 and - since
    the UV channel reads the same way - uv_ratio comes out at exactly 1.0. Both clear the minimums
    by a wide margin, so the gate ran to its brightest tier and opened the box in a dark room.
    """
    dead = dict(lux=4294967296.0, uvs=4294967296.0, als=4294967296.0)
    assert not Sample(steps=0, **dead).plausible
    g = demo_gate()
    assert run(g, dead, 600, steps=200) == State.RINGING
    assert g.valid_s == 0

    # and the sketch's own "not fitted" sentinel is just as unconvincing
    assert run(demo_gate(), dict(lux=-1.0, uvs=-1.0, als=-1.0), 600, steps=200) == State.RINGING


def test_real_daylight_still_passes_the_plausibility_bounds():
    """The ceilings have to sit above anything real: direct sun is about 120k lux."""
    assert Sample(steps=0, lux=120000, uvs=6000, als=120000).plausible
    assert Sample(steps=0, lux=0, uvs=0, als=0).plausible      # a dark room is a real reading


def test_late_daylight_after_window_requires_walking_again():
    """Racking up steps, then waiting out the 120 s door window indoors, must not let a much
    later daylight reading count - that's the "walk now, fake daylight hours later" cheat the
    door window exists to close."""
    g = demo_gate()
    run(g, ROOM, 150, steps=120)                      # walks early, then sits past the window indoors
    assert g.state == State.RINGING and g.verified_steps == 0
    assert run(g, SUN, 5, steps=120) == State.RINGING  # no new steps - still short
    assert run(g, SUN, 5, steps=140) == State.DWELL    # walks again, daylight is prompt this time


def test_safety_open():
    g = demo_gate()
    g.safety_open("override")
    assert g.state == State.UNLOCKED and g.unlock_reason == "safety:override"

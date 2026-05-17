import pytest

from treasure_scanner.utils.throttle import Throttle


def test_initial_interval_is_base():
    t = Throttle(base_interval=3.0)
    assert t.current == 3.0
    assert t.consecutive_failures == 0


def test_failure_below_threshold_does_not_bump():
    t = Throttle(base_interval=3.0)
    t.record_failure()
    t.record_failure()
    assert t.current == 3.0
    assert t.consecutive_failures == 2


def test_three_failures_double_interval():
    t = Throttle(base_interval=3.0)
    for _ in range(3):
        t.record_failure()
    assert t.current == 6.0


def test_repeated_failures_keep_doubling_until_max():
    t = Throttle(base_interval=2.0, max_interval=20.0)
    for _ in range(20):
        t.record_failure()
    assert t.current == 20.0


def test_success_decays_back_toward_base():
    t = Throttle(base_interval=2.0)
    for _ in range(3):
        t.record_failure()
    assert t.current == 4.0
    t.record_success()
    # 4.0 * 0.9 = 3.6
    assert t.current == pytest.approx(3.6)
    assert t.consecutive_failures == 0


def test_success_does_not_dip_below_base():
    t = Throttle(base_interval=3.0)
    t.record_success()
    assert t.current == 3.0


def test_record_response_dispatches():
    t = Throttle(base_interval=2.0)
    t.record_response(200)
    assert t.consecutive_failures == 0
    t.record_response(429)
    t.record_response(429)
    t.record_response(503)
    assert t.current == 4.0

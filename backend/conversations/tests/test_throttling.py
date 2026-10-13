import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from conversations.tests.test_conversation_turn import (
    TURN_URL,
    ai,
    device_session,
    pair_robot,
    turn_body,
    world,
)
from devices.tests.helpers import setup_parent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def exhaust(client, robot, child, limit, **extra):
    """Send `limit` allowed turns, then one more; return the last response."""
    for _ in range(limit):
        ok = client.post(
            TURN_URL, turn_body(robot, child, **extra), format="json"
        )
        assert ok.status_code == 200, ok.content
    return client.post(TURN_URL, turn_body(robot, child, **extra), format="json")


# --- boundary & response shape ---------------------------------------------


def test_within_window_allows_then_next_request_is_throttled(world, ai):
    last = exhaust(world.client, world.robot, world.child, 30)
    assert last.status_code == 429, last.content


def test_throttled_response_is_429_with_retry_after_and_default_body(world, ai):
    throttled = exhaust(world.client, world.robot, world.child, 30)
    assert throttled.status_code == 429
    # DRF's default detail ("Request was throttled.") plus its usual
    # appended wait when the throttle can compute one.
    assert throttled.json()["detail"].startswith("Request was throttled.")
    retry_after = int(throttled["Retry-After"])
    assert 1 <= retry_after <= 60


def test_throttled_request_never_calls_the_ai_client(world, ai):
    with override_settings(CONVERSATION_TURN_RATE_LIMIT="2/min"):
        throttled = exhaust(world.client, world.robot, world.child, 2)
    assert throttled.status_code == 429
    assert len(ai.calls) == 2


# --- bucket independence ----------------------------------------------------


def test_session_and_device_buckets_are_independent(world, ai):
    robot, token = device_session(world.client)
    with override_settings(CONVERSATION_TURN_RATE_LIMIT="2/min"):
        session_last = exhaust(world.client, world.robot, world.child, 2)
        assert session_last.status_code == 429

        device_ok = APIClient().post(
            TURN_URL,
            turn_body(robot, world.child),
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
    assert device_ok.status_code == 200, device_ok.content


def test_different_robots_use_independent_device_buckets(world, ai):
    robot_a, token_a = device_session(world.client)
    robot_b, token_b = device_session(world.client)
    with override_settings(CONVERSATION_TURN_RATE_LIMIT="2/min"):
        for _ in range(2):
            ok = APIClient().post(
                TURN_URL,
                turn_body(robot_a, world.child),
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token_a}",
            )
            assert ok.status_code == 200, ok.content
        throttled_a = APIClient().post(
            TURN_URL,
            turn_body(robot_a, world.child),
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_a}",
        )
        robot_b_ok = APIClient().post(
            TURN_URL,
            turn_body(robot_b, world.child),
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token_b}",
        )
    assert throttled_a.status_code == 429
    assert robot_b_ok.status_code == 200, robot_b_ok.content


# --- anonymous & disabled modes ---------------------------------------------


def test_anonymous_requests_stay_401_and_do_not_consume_a_bucket(world, ai):
    with override_settings(CONVERSATION_TURN_RATE_LIMIT="2/min"):
        for _ in range(3):
            anon = APIClient().post(
                TURN_URL, turn_body(world.robot, world.child), format="json"
            )
            assert anon.status_code == 401
        authenticated = exhaust(world.client, world.robot, world.child, 2)
    assert authenticated.status_code == 429
    assert len(ai.calls) == 2  # the anonymous 401s never reached the AI


def test_empty_configuration_disables_throttling(world, ai):
    with override_settings(CONVERSATION_TURN_RATE_LIMIT=""):
        last = exhaust(world.client, world.robot, world.child, 35)
    assert last.status_code == 200, last.content
    assert len(ai.calls) == 36


# --- ownership semantics -----------------------------------------------------


def test_ownership_404_semantics_hold_under_throttling(world, ai):
    other_client = APIClient()
    other_parent = setup_parent(other_client, "other-parent@example.com")
    other_robot = pair_robot(other_client)
    foreign_body = turn_body(other_robot, world.child)
    with override_settings(CONVERSATION_TURN_RATE_LIMIT="5/min"):
        # Throttle runs before ownership: the foreign 404 consumes one
        # slot of the caller's own bucket, exactly like an owned turn.
        foreign = world.client.post(TURN_URL, foreign_body, format="json")
        assert foreign.status_code == 404

        own_ok = world.client.post(
            TURN_URL, turn_body(world.robot, world.child), format="json"
        )
        assert own_ok.status_code == 200, own_ok.content

        for _ in range(3):
            fill = world.client.post(
                TURN_URL, turn_body(world.robot, world.child), format="json"
            )
            assert fill.status_code == 200, fill.content

        # Window now full: the throttle answers first - identically for
        # owned and foreign robot ids (no existence oracle).
        exhausted_foreign = world.client.post(TURN_URL, foreign_body, format="json")
        exhausted_own = world.client.post(
            TURN_URL, turn_body(world.robot, world.child), format="json"
        )
    assert exhausted_foreign.status_code == 429
    assert exhausted_own.status_code == 429
    assert exhausted_foreign.json() == exhausted_own.json()

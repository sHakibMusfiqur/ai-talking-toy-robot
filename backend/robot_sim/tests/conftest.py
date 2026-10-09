"""Shared fixtures for the robot simulator tests.

``broker``/``bridge`` are re-exported from the mqtt_bridge test package so
the simulator's integration test runs against the same live Mosquitto
broker and the same production bridge subscriber as the rest of the suite.
"""

import pytest

from mqtt_bridge.tests.conftest import broker, bridge  # noqa: F401

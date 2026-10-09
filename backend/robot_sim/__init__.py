"""Software-in-the-loop robot simulator.

Drives the real Web Controller -> Django -> MQTT -> device-response path
against a live Mosquitto broker without any ESP32 hardware, using the
same frozen contracts as the firmware (registration/challenge-token auth,
Section 11 topics, semantic-action whitelist). Development tool only: it
never talks to an AI provider and fabricates no telemetry.
"""

__version__ = "0.1.0"

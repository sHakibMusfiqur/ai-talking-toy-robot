import copy
import hashlib
import json
import re
import threading
import time

IDEMPOTENCY_TTL_SECONDS = 600
MAX_KEY_LENGTH = 255
KEY_PATTERN = re.compile(r"\A[A-Za-z0-9._:-]{1,255}\Z")

KEY_INVALID_MESSAGE = (
    "Invalid Idempotency-Key header: expected 1-255 characters "
    "from the set [A-Za-z0-9._:-]."
)


class IdempotencyConflict(Exception):
    """Same robot/key reused with a different payload -> HTTP 409."""


def validate_idempotency_key(raw):
    """Return an error message, or ``None`` when absent or valid."""
    if raw is None:
        return None
    if not KEY_PATTERN.fullmatch(raw):
        return KEY_INVALID_MESSAGE
    return None


def make_storage_key(robot_id, key):
    """Per-robot scoped registry key; never logs or exposes ``key``."""
    return hashlib.sha256(f"{robot_id}\x00{key}".encode("utf-8")).hexdigest()


def make_request_fingerprint(*, sidecar, audio_bytes):
    """SHA-256 over the canonical semantic payload (no raw audio)."""
    payload = {
        "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
        "child_profile_id": str(sidecar["child_profile_id"]),
        "conversation_id": (
            str(sidecar["conversation_id"])
            if sidecar.get("conversation_id") is not None
            else None
        ),
        "language_hint": sidecar.get("language_hint"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Entry:
    __slots__ = ("lock", "result", "fingerprint", "expires_at")

    def __init__(self):
        self.lock = threading.Lock()
        self.result = None
        self.fingerprint = None
        self.expires_at = 0.0


_registry = {}
_registry_guard = threading.Lock()


def _now():
    return time.monotonic()


def _sweep_locked(now):
    """Prune unusable entries. Caller holds ``_registry_guard``; an entry
    whose key lock is held is never swept (it is executing or queued)."""
    dead = [
        storage_key
        for storage_key, entry in _registry.items()
        if not entry.lock.locked()
        and (entry.result is None or now >= entry.expires_at)
    ]
    for storage_key in dead:
        del _registry[storage_key]


def execute_with_idempotency(storage_key, fingerprint, workflow):
    """Run ``workflow`` under the key's lock, or replay its record.

    Returns ``(result, replayed)``. Raises ``IdempotencyConflict`` for a
    same-key/different-fingerprint request (leaving the stored record
    untouched); any workflow exception propagates with no record left
    behind. The first successful execution is never marked as a replay.
    """
    while True:
        with _registry_guard:
            _sweep_locked(_now())
            entry = _registry.get(storage_key)
            if entry is None:
                entry = _Entry()
                _registry[storage_key] = entry
        with entry.lock:
            with _registry_guard:
                current = _registry.get(storage_key)
            if current is not entry:
                # Swept while we waited; retry with a fresh entry.
                continue
            now = _now()
            if entry.result is not None and now < entry.expires_at:
                if entry.fingerprint == fingerprint:
                    return copy.deepcopy(entry.result), True
                raise IdempotencyConflict(
                    "Idempotency-Key was already used with a different "
                    "request payload."
                )
            try:
                result = workflow()
            except BaseException:
                with _registry_guard:
                    if _registry.get(storage_key) is entry:
                        entry.result = None
                        entry.fingerprint = None
                raise
            with _registry_guard:
                if _registry.get(storage_key) is entry:
                    entry.result = copy.deepcopy(result)
                    entry.fingerprint = fingerprint
                    entry.expires_at = _now() + IDEMPOTENCY_TTL_SECONDS
            return result, False

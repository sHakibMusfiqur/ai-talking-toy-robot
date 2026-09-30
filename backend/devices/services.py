import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import timedelta

from bson import ObjectId
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from django.conf import settings
from django.utils import timezone
from django_mongodb_backend import transaction

from analytics.services import record_audit

from .exceptions import (
    DeviceAlreadyRegistered,
    InvalidDeviceCredentials,
    InvalidDeviceId,
    InvalidPairingCode,
    InvalidPublicKey,
)
from .models import DeviceChallenge, DeviceCredential, Robot, RobotEvent, RobotState
from .tokens import issue_device_token

logger = logging.getLogger(__name__)

# No vowels/ambiguous glyphs (0/O, 1/I/L) — codes are transcribed by humans.
PAIRING_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

PUBLIC_KEY_BEGIN = "-----BEGIN PUBLIC KEY-----"
PUBLIC_KEY_END = "-----END PUBLIC KEY-----"

AUTH_FAILED_CHALLENGE = "device.auth_failed.challenge"
AUTH_FAILED_SIGNATURE = "device.auth_failed.signature"
TOKEN_ISSUED = "device.token_issued"


def generate_pairing_code():
    length = settings.DEVICE_PAIRING_CODE_LENGTH
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(length))


def sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_pairing_code(code):
    return sha256_hex(code)


def register_device(*, device_id, public_key, firmware_version="", ip=None):
    """Device self-registration (Flow 4): creates Robot + DeviceCredential and
    issues a short-lived, single-use pairing code. The raw code is returned
    exactly once to the registering device and never persisted raw."""
    device_id = _normalize_device_id(device_id)
    public_key = _validate_public_key(public_key)

    if Robot.objects.filter(device_id=device_id).exists():
        raise DeviceAlreadyRegistered()

    code = generate_pairing_code()
    now = timezone.now()
    expires_at = now + timedelta(seconds=settings.DEVICE_PAIRING_CODE_TTL_SECONDS)

    with transaction.atomic():
        robot = Robot.objects.create(
            device_id=device_id,
            pairing_status=Robot.PAIRING_UNPAIRED,
            pairing_code_hash=hash_pairing_code(code),
            pairing_code_expires_at=expires_at,
            firmware_version=firmware_version,
        )
        DeviceCredential.objects.create(robot=robot, public_key=public_key)
        record_audit(
            actor=None,
            action="robot.registered",
            target=str(robot.pk),
            ip=ip,
        )
    return robot, code


def pair_robot(*, pairing_code, parent, nickname=None, ip=None):
    """Explicit per-robot pairing: the raw code selects exactly one robot and
    the ownership link is established server-side from the authenticated
    session — never from client-supplied owner ids. Single-use: the code hash
    is consumed atomically, so a code can never be replayed or reassigned."""
    if not isinstance(pairing_code, str) or not pairing_code.strip():
        raise InvalidPairingCode()
    code_hash = hash_pairing_code(pairing_code.strip().upper())
    now = timezone.now()

    with transaction.atomic():
        robot = (
            Robot.objects.filter(
                pairing_code_hash=code_hash,
                pairing_status=Robot.PAIRING_UNPAIRED,
                pairing_code_expires_at__gt=now,
            )
            .order_by("pk")
            .first()
        )
        if robot is None:
            raise InvalidPairingCode()

        updated = Robot.objects.filter(
            pk=robot.pk,
            pairing_code_hash=code_hash,
            pairing_status=Robot.PAIRING_UNPAIRED,
            pairing_code_expires_at__gt=now,
        ).update(
            parent=parent,
            pairing_status=Robot.PAIRING_PAIRED,
            paired_at=now,
            pairing_code_hash="",
            pairing_code_expires_at=None,
            **({"nickname": nickname} if nickname else {}),
        )
        if updated != 1:
            raise InvalidPairingCode()

        record_audit(
            actor=parent.user,
            action="robot.paired",
            target=str(robot.pk),
            ip=ip,
        )
        robot.refresh_from_db()
    return robot


def create_device_challenge(*, device_id, ip=None):
    """Phase 7 (AR-10): issue a short-lived random server nonce to a
    registered device. The raw challenge is returned exactly once and only
    its SHA-256 hash is persisted. Issuing a new challenge atomically
    invalidates the device's previous unconsumed challenge (one live
    challenge per robot, bounded rows). Unknown or malformed device ids
    raise the uniform 401 — a device_id alone never confirms existence."""
    device_id = _normalize_device_id(device_id)
    robot = Robot.objects.filter(device_id=device_id).first()
    if robot is None:
        raise InvalidDeviceCredentials()

    now = timezone.now()
    ttl = settings.DEVICE_CHALLENGE_TTL_SECONDS
    with transaction.atomic():
        DeviceChallenge.objects.filter(
            robot=robot, consumed_at__isnull=True
        ).update(consumed_at=now)
        DeviceChallenge.objects.filter(robot=robot, expires_at__lt=now).delete()
        raw_challenge = secrets.token_hex(32)
        challenge = DeviceChallenge.objects.create(
            robot=robot,
            challenge_hash=sha256_hex(raw_challenge),
            expires_at=now + timedelta(seconds=ttl),
        )
    return str(challenge.pk), raw_challenge, ttl


def exchange_device_token(*, device_id, challenge_id, challenge, signature, ip=None):
    """Phase 7 signed-challenge exchange (AR-10): verifies the device
    proves possession of the private key by signing the nonce, consumes
    the challenge single-use, and issues a short-lived device JWT.

    Every fault path raises the same uniform 401 (unknown device, unknown
    or malformed challenge, expired or replayed challenge, bad signature)
    so responses reveal nothing about stored state. Failures for a known
    device are audited without recording any challenge/signature material;
    unknown device ids are not audited (anti-probing, Phase 19 rate
    limiting covers enumeration attempts)."""
    device_id = _normalize_device_id(device_id)
    now = timezone.now()
    robot = Robot.objects.filter(device_id=device_id).first()
    if robot is None:
        raise InvalidDeviceCredentials()

    valid_id = isinstance(challenge_id, str) and ObjectId.is_valid(challenge_id)
    dc = (
        DeviceChallenge.objects.filter(pk=ObjectId(challenge_id), robot=robot).first()
        if valid_id
        else None
    )
    challenge_ok = (
        dc is not None
        and dc.consumed_at is None
        and dc.expires_at > now
        and isinstance(challenge, str)
        and hmac.compare_digest(dc.challenge_hash, sha256_hex(challenge))
    )
    if not challenge_ok:
        _audit_auth_failure(AUTH_FAILED_CHALLENGE, robot, ip)
        raise InvalidDeviceCredentials()

    if not _verify_device_signature(
        robot.credential.public_key, challenge, signature
    ):
        _audit_auth_failure(AUTH_FAILED_SIGNATURE, robot, ip)
        raise InvalidDeviceCredentials()

    with transaction.atomic():
        consumed = DeviceChallenge.objects.filter(
            pk=dc.pk, consumed_at__isnull=True, expires_at__gt=now
        ).update(consumed_at=now)
        if consumed != 1:
            _audit_auth_failure(AUTH_FAILED_CHALLENGE, robot, ip)
            raise InvalidDeviceCredentials()
        token, ttl = issue_device_token(robot)
        DeviceCredential.objects.filter(robot=robot).update(
            token_hash=sha256_hex(token),
            token_expires_at=now + timedelta(seconds=ttl),
        )
        record_audit(
            actor=None,
            action=TOKEN_ISSUED,
            target=str(robot.pk),
            ip=ip,
        )

    # Phase 8: broker provisioning happens OUTSIDE the transaction (network
    # I/O must not hold a Mongo transaction open) and is best-effort — a
    # broker outage never fails token exchange; the device retries later.
    _provision_mqtt_client(device_id, token)
    return token, ttl


def _provision_mqtt_client(device_id, token):
    """Section 26 token-derived MQTT credentials: the device's broker password
    is the JWT just issued to it (username = device_id). Failure is logged
    and swallowed — provisioning is an availability concern, never an
    authentication concern."""
    try:
        from mqtt_bridge.provisioning import ensure_device_client

        ensure_device_client(device_id, token)
    except Exception:
        logger.warning(
            "mqtt provisioning skipped for device",
            extra={"device_id": device_id},
            exc_info=True,
        )


def update_status(*, robot, online=None, state=None, battery_percent=None, rssi=None):
    """Single write path for device status (Section 12.2): used by REST and
    by MQTT ingestion alike. Partial-update semantics — None means "not
    reported in this payload, keep previous value". Every status update
    refreshes last_seen."""
    now = timezone.now()
    with transaction.atomic():
        state_obj, _ = RobotState.objects.get_or_create(robot=robot)
        changed = False
        for field, value in (
            ("online", online),
            ("state", state),
            ("battery_percent", battery_percent),
            ("rssi", rssi),
        ):
            if value is not None:
                setattr(state_obj, field, value)
                changed = True
        if changed:
            state_obj.save()
        robot.last_seen = now
        robot.save(update_fields=["last_seen"])
    return state_obj


def record_heartbeat(*, robot):
    """Heartbeat = proof of life: robot is online now (Section 11.2)."""
    now = timezone.now()
    with transaction.atomic():
        state_obj, _ = RobotState.objects.get_or_create(robot=robot)
        if not state_obj.online:
            state_obj.online = True
            state_obj.save(update_fields=["online"])
        robot.last_seen = now
        robot.save(update_fields=["last_seen"])
    return state_obj


def record_robot_event(*, robot, event_type, payload=None):
    """Lifecycle/error/safety events from the robot (Section 11.1). The
    payload is already validated at the transport boundary; this guard is
    the service-level contract for any other caller."""
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type is required")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    now = timezone.now()
    with transaction.atomic():
        event = RobotEvent.objects.create(
            robot=robot, event_type=event_type, payload=payload
        )
        robot.last_seen = now
        robot.save(update_fields=["last_seen"])
    return event


def _audit_auth_failure(action, robot, ip):
    record_audit(actor=None, action=action, target=str(robot.pk), ip=ip)


def _verify_device_signature(public_key_pem, challenge, signature):
    """Verify an asymmetric signature over the raw challenge string.
    EC keys accept DER-encoded ECDSA-SHA256 (OpenSSL/Python) and raw
    64-byte r||s (ESP32 mbedTLS) signatures; RSA keys accept
    PKCS#1 v1.5 SHA-256. Any parse or verification error is a plain
    False — never an exception surfacing to the client."""
    if not isinstance(signature, str) or not signature:
        return False
    try:
        sig = base64.b64decode(signature, validate=True)
        key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, binascii.Error, TypeError):
        return False
    challenge_bytes = challenge.encode("utf-8")

    if isinstance(key, ec.EllipticCurvePublicKey):
        try:
            key.verify(sig, challenge_bytes, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError):
            pass
        if len(sig) == 64:
            r = int.from_bytes(sig[:32], "big")
            s = int.from_bytes(sig[32:], "big")
            try:
                key.verify(
                    encode_dss_signature(r, s),
                    challenge_bytes,
                    ec.ECDSA(hashes.SHA256()),
                )
                return True
            except (InvalidSignature, ValueError):
                return False
        return False

    if isinstance(key, rsa.RSAPublicKey):
        try:
            key.verify(
                sig, challenge_bytes, padding.PKCS1v15(), hashes.SHA256()
            )
            return True
        except (InvalidSignature, ValueError):
            return False

    return False


def _normalize_device_id(device_id):
    if not isinstance(device_id, str) or not device_id.strip():
        raise InvalidDeviceId()
    try:
        return str(uuid.UUID(device_id.strip()))
    except (ValueError, AttributeError, TypeError):
        raise InvalidDeviceId()


def _validate_public_key(public_key):
    if not isinstance(public_key, str):
        raise InvalidPublicKey()
    key = public_key.strip()
    if not (
        key.startswith(PUBLIC_KEY_BEGIN)
        and key.endswith(PUBLIC_KEY_END)
        and len(key) > len(PUBLIC_KEY_BEGIN) + len(PUBLIC_KEY_END)
    ):
        raise InvalidPublicKey()
    return key

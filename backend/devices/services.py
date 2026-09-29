import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django_mongodb_backend import transaction

from analytics.services import record_audit

from .exceptions import (
    DeviceAlreadyRegistered,
    InvalidDeviceId,
    InvalidPairingCode,
    InvalidPublicKey,
)
from .models import DeviceCredential, Robot

# No vowels/ambiguous glyphs (0/O, 1/I/L) — codes are transcribed by humans.
PAIRING_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

PUBLIC_KEY_BEGIN = "-----BEGIN PUBLIC KEY-----"
PUBLIC_KEY_END = "-----END PUBLIC KEY-----"


def generate_pairing_code():
    length = settings.DEVICE_PAIRING_CODE_LENGTH
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(length))


def hash_pairing_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


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

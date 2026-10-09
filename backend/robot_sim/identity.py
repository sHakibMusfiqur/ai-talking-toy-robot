"""Persistent simulator identity: a locally generated EC P-256 keypair and
UUID device id, stored in a gitignored state file.

Rules (mirrored from the firmware identity module):
- the private key is generated locally, never transmitted, never logged,
  and never included in reprs or error messages
- the state file is 0600 where the OS supports it and lives under
  ``sim_state/`` (gitignored via the repo's .gitignore)
- the device id must be a UUID (the backend register endpoint requires it)
"""

import base64
import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

STATE_FILENAME = "identity.json"


class IdentityError(RuntimeError):
    """Local identity state is missing/corrupt; the message never contains
    key material."""


@dataclass(frozen=True)
class Identity:
    device_id: str
    private_key: ec.EllipticCurvePrivateKey = field(repr=False)
    created: bool = False

    @property
    def public_key_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )

    def sign_challenge(self, challenge: str) -> str:
        """ECDSA-SHA256 over the raw challenge, DER encoded and base64 -
        the exact signature format the backend's token endpoint accepts."""
        der = self.private_key.sign(
            challenge.encode("utf-8"), ec.ECDSA(hashes.SHA256())
        )
        return base64.b64encode(der).decode("ascii")


def state_path(state_dir) -> Path:
    return Path(state_dir) / STATE_FILENAME


def load_or_create_identity(state_dir) -> Identity:
    path = state_path(state_dir)
    if path.exists():
        return _load(path)
    return _create(path)


def _load(path: Path) -> Identity:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        device_id = raw["device_id"]
        pem = raw["private_key_pem"]
        uuid.UUID(device_id)
        private_key = serialization.load_pem_private_key(
            pem.encode("ascii"), password=None
        )
    except Exception as exc:  # noqa: BLE001 - normalize, never echo content
        raise IdentityError(
            f"simulator identity file {path} is unreadable or corrupt "
            f"({type(exc).__name__}); delete the file to generate a new "
            "robot identity"
        ) from None
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise IdentityError(
            f"simulator identity file {path} does not hold an EC key; "
            "delete the file to generate a new robot identity"
        )
    return Identity(device_id=device_id, private_key=private_key, created=False)


def _create(path: Path) -> Identity:
    device_id = str(uuid.uuid4())
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = (
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"device_id": device_id, "private_key_pem": pem}, indent=2
    )
    _atomic_write_private(path, payload)
    return Identity(device_id=device_id, private_key=private_key, created=True)


def _atomic_write_private(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    if os.name == "nt":
        tmp.write_text(content, encoding="utf-8")
    else:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(str(path), stat.S_IRUSR | stat.S_IWUSR)


def file_mode(path) -> int:
    """Mode bits of the state file (0600 on POSIX); best-effort on Windows."""
    return stat.S_IMODE(os.stat(path).st_mode)

"""Unit tests for the local simulator identity (key handling, no network)."""

import base64
import json
import os
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from robot_sim import identity as identity_module


def test_creates_identity_and_persists_state(tmp_path):
    ident = identity_module.load_or_create_identity(tmp_path)
    assert ident.created is True
    uuid.UUID(ident.device_id)  # backend register requires a UUID
    path = identity_module.state_path(tmp_path)
    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert set(stored) == {"device_id", "private_key_pem"}
    assert stored["device_id"] == ident.device_id


def test_reload_returns_same_identity_not_created(tmp_path):
    first = identity_module.load_or_create_identity(tmp_path)
    second = identity_module.load_or_create_identity(tmp_path)
    assert second.created is False
    assert second.device_id == first.device_id
    assert second.public_key_pem == first.public_key_pem


def test_signature_verifies_against_stored_public_key(tmp_path):
    ident = identity_module.load_or_create_identity(tmp_path)
    challenge = "a" * 64
    signature = ident.sign_challenge(challenge)
    der = base64.b64decode(signature)
    public = serialization.load_pem_public_key(ident.public_key_pem.encode("ascii"))
    public.verify(der, challenge.encode("utf-8"), ec.ECDSA(hashes.SHA256()))


def test_repr_never_contains_key_material(tmp_path):
    ident = identity_module.load_or_create_identity(tmp_path)
    text = repr(ident)
    assert "PRIVATE" not in text
    assert "BEGIN" not in text
    assert "private_key" not in text  # hidden by field(repr=False)


def test_corrupt_state_file_raises_without_leaking_content(tmp_path):
    path = identity_module.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{ definitely not json", encoding="utf-8")
    with pytest.raises(identity_module.IdentityError) as excinfo:
        identity_module.load_or_create_identity(tmp_path)
    message = str(excinfo.value)
    assert "delete the file" in message
    assert "PRIVATE" not in message and "BEGIN" not in message


def test_non_uuid_device_id_rejected(tmp_path):
    path = identity_module.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    path.write_text(
        json.dumps({"device_id": "not-a-uuid", "private_key_pem": pem}),
        encoding="utf-8",
    )
    with pytest.raises(identity_module.IdentityError):
        identity_module.load_or_create_identity(tmp_path)


def test_non_ec_key_rejected(tmp_path):
    path = identity_module.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "device_id": str(uuid.uuid4()),
                "private_key_pem": "-----BEGIN PRIVATE KEY-----\ngarbage\n",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(identity_module.IdentityError):
        identity_module.load_or_create_identity(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_state_file_mode_is_0600(tmp_path):
    identity_module.load_or_create_identity(tmp_path)
    mode = identity_module.file_mode(identity_module.state_path(tmp_path))
    assert mode == 0o600

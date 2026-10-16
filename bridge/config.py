"""Portable state. No upstream API key is written to disk."""
import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from .providers import DEFAULT_CONNECTION, normalize_connection, valid_model

ROUTE = "claude-sonnet-4-5"
DEFAULT_MODEL = ""


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def display_name(model, connection=None):
    connection = connection or DEFAULT_CONNECTION
    label = model.upper().replace("-SOL", " SOL") if model.startswith("gpt-") else model
    return label + " (" + connection["name"] + ")"


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        token_path = self.root / "admin.json"
        if not token_path.exists():
            atomic_json(token_path, {"token": secrets.token_urlsafe(32)})
        self.admin = json.loads(token_path.read_text())["token"]
        self.path = self.root / "profile.json"
        self.profile = None
        self.load_error = False
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text())
                if not valid_model(value["model"]) or not re.fullmatch(r"[0-9a-f]{64}", value["key_sha256"]):
                    raise ValueError("Invalid profile")
                if value.get("schema") != 2:
                    raise ValueError("Unknown schema")
                value["connection"] = normalize_connection(value["connection"])
                self.profile = value
            except (ValueError, KeyError, TypeError):
                self.load_error = True

    def save(self, key, model, verified_at, connection=None):
        if not valid_model(model):
            raise ValueError("Invalid model")
        profile = {"schema": 2, "model": model, "connection": normalize_connection(connection or DEFAULT_CONNECTION), "key_sha256": digest(key), "verified_at": verified_at}
        atomic_json(self.path, profile)
        self.profile = profile
        self.load_error = False

    def accepts(self, key):
        return bool(self.profile and key and secrets.compare_digest(digest(key), self.profile["key_sha256"]))


def desktop_config(port, model, connection=None):
    """An importable template; the user supplies the key inside Claude."""
    return {
        "inferenceProvider": "gateway",
        "inferenceCredentialKind": "static",
        "inferenceGatewayBaseUrl": f"http://127.0.0.1:{port}",
        "inferenceGatewayAuthScheme": "bearer",
        "modelDiscoveryEnabled": True,
        "inferenceModels": [{"name": ROUTE, "labelOverride": display_name(model, connection), "supports1m": False}],
        "modelPrefer1mContext": False,
        "inferenceModelPricingEnabled": False,
    }

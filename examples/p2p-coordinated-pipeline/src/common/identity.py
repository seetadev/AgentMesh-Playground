"""Persistent Ed25519 identity management for AgentMesh agents."""

import json
import secrets
from pathlib import Path

from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.crypto.keys import KeyPair

# Keys are stored relative to the example root, not src/
KEYS_DIR = Path(__file__).resolve().parent.parent.parent / "keys"


def load_or_create_identity(name: str) -> KeyPair:
    """
    Load a persistent Ed25519 key pair from disk, or generate a new one.

    Keys are stored in keys/<name>.json so agents keep the same PeerID
    across restarts — critical for DHT routing and peer discovery.
    """
    KEYS_DIR.mkdir(exist_ok=True)
    key_file = KEYS_DIR / f"{name}.json"

    if key_file.exists():
        data = json.loads(key_file.read_text())
        return create_new_key_pair(bytes.fromhex(data["seed"]))

    seed = secrets.token_bytes(32)
    key_pair = create_new_key_pair(seed)
    key_file.write_text(json.dumps({"seed": seed.hex()}))
    return key_pair

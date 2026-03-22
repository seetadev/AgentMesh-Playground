import json
from pathlib import Path

from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.crypto.keys import KeyPair


KEYS_DIR = Path(__file__).resolve().parent.parent.parent / "keys"


def load_or_create_identity(name: str) -> KeyPair:
    """Load a persistent identity from disk, or create and save a new one."""
    KEYS_DIR.mkdir(exist_ok=True)
    key_file = KEYS_DIR / f"{name}.json"

    if key_file.exists():
        data = json.loads(key_file.read_text())
        seed = bytes.fromhex(data["seed"])
        return create_new_key_pair(seed)

    import secrets
    seed = secrets.token_bytes(32)
    key_pair = create_new_key_pair(seed)
    key_file.write_text(json.dumps({"seed": seed.hex()}))
    print(f"Created new identity for '{name}', saved to {key_file}")
    return key_pair

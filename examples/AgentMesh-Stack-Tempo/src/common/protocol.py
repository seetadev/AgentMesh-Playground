import json
import struct

from libp2p.custom_types import TProtocol
from libp2p.network.stream.net_stream import INetStream

SIGNAL_PROTOCOL_ID = TProtocol("/aoin/signal/v1")
CHAT_PROTOCOL_ID = TProtocol("/aoin/chat/v1")
MAX_READ_LEN = 2**32 - 1

# Tempo Testnet (Moderato)
TEMPO_CHAIN_ID = 42431
TEMPO_RPC_URL = "https://rpc.moderato.tempo.xyz"
TEMPO_CURRENCY = "0x20c0000000000000000000000000000000000000"  # pathUSD

SIGNAL_PRICE = "0.05"  # USD per signal

# DHT content key for provider discovery
DHT_PROVIDER_KEY = "aoin-signal-v1"


async def send_msg(stream: INetStream, data: dict) -> None:
    """Send a length-prefixed JSON message over a libp2p stream."""
    payload = json.dumps(data).encode()
    header = struct.pack(">I", len(payload))
    await stream.write(header + payload)


async def recv_msg(stream: INetStream) -> dict:
    """Read a length-prefixed JSON message from a libp2p stream."""
    header = await stream.read(4)
    if len(header) < 4:
        raise ConnectionError("Stream closed before message header")
    length = struct.unpack(">I", header[:4])[0]
    payload = await stream.read(length)
    return json.loads(payload.decode())

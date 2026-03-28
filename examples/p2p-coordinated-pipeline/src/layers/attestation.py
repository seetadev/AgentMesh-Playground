"""
Layer 6: Attestation Backends

Pluggable backends for recording execution-step integrity proofs.
Three implementations are provided, selected at runtime via env vars:

  LocalHashBackend      — in-memory records, zero external dependencies (default)
  RPCAttestationBackend — JSON-RPC POST to any compatible endpoint (stdlib only)
  FilecoinFEVMBackend   — FVM actor stub; inline comments show the web3.py calls
                          needed to make it live

Factory:
  build_attestation_backend() reads env vars and returns the best available
  backend in this priority order:
    1. FEVM_RPC_URL + FEVM_CONTRACT + FEVM_PRIVATE_KEY → FilecoinFEVMBackend
    2. ATTESTATION_RPC_URL                             → RPCAttestationBackend
    3. (default)                                       → LocalHashBackend
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data record
# ---------------------------------------------------------------------------

@dataclass
class AttestationRecord:
    """An immutable record of one step's integrity proof."""
    step_id: str
    protocol_id: str
    worker_name: str
    output_hash: str        # SHA-256[:16] of the step's output_data
    timestamp: float        # Unix epoch (seconds)
    backend: str            # Which backend stored this record
    tx_id: Optional[str] = None  # CID / transaction hash for on-chain records


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AttestationBackend(ABC):
    """
    Interface for attestation backends.

    Each backend must implement `attest()`, which stores or submits a
    cryptographic proof that a specific step produced a specific output.
    """

    @abstractmethod
    async def attest(
        self,
        step_id: str,
        protocol_id: str,
        worker_name: str,
        output_hash: str,
    ) -> AttestationRecord:
        """Record an integrity proof for a completed step."""
        ...


# ---------------------------------------------------------------------------
# Backend 1: LocalHashBackend (default, no external deps)
# ---------------------------------------------------------------------------

class LocalHashBackend(AttestationBackend):
    """
    In-memory attestation — stores records in a Python dict.

    Suitable for development and testing. Records are lost when the process
    exits.  Guaranteed to succeed without any external services.
    """

    def __init__(self) -> None:
        self._records: dict[str, AttestationRecord] = {}

    async def attest(
        self,
        step_id: str,
        protocol_id: str,
        worker_name: str,
        output_hash: str,
    ) -> AttestationRecord:
        record = AttestationRecord(
            step_id=step_id,
            protocol_id=protocol_id,
            worker_name=worker_name,
            output_hash=output_hash,
            timestamp=time.time(),
            backend="local",
        )
        self._records[step_id] = record
        log.debug(
            f"[Attestation] Local: step={step_id}  hash={output_hash}  "
            f"worker={worker_name}"
        )
        return record

    def get(self, step_id: str) -> Optional[AttestationRecord]:
        return self._records.get(step_id)


# ---------------------------------------------------------------------------
# Backend 2: RPCAttestationBackend (stdlib urllib, no extra deps)
# ---------------------------------------------------------------------------

class RPCAttestationBackend(AttestationBackend):
    """
    JSON-RPC attestation — POSTs a proof to any compatible HTTP endpoint.

    On transport error the backend logs a warning and falls back to local
    storage so the pipeline never stalls due to an unavailable endpoint.

    Configure via env var:
        ATTESTATION_RPC_URL=http://my-attestation-server:8080/attest
    """

    def __init__(self, rpc_url: str) -> None:
        self._rpc_url = rpc_url
        self._fallback = LocalHashBackend()

    async def attest(
        self,
        step_id: str,
        protocol_id: str,
        worker_name: str,
        output_hash: str,
    ) -> AttestationRecord:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "attest",
                "params": {
                    "step_id": step_id,
                    "protocol_id": protocol_id,
                    "worker_name": worker_name,
                    "output_hash": output_hash,
                    "timestamp": time.time(),
                },
                "id": 1,
            }
        ).encode()

        tx_id: Optional[str] = None
        try:
            req = urllib.request.Request(
                self._rpc_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                tx_id = body.get("result", {}).get("tx_id")
            log.debug(
                f"[Attestation] RPC: step={step_id}  hash={output_hash}  tx={tx_id}"
            )
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            log.warning(
                f"[Attestation] RPC call to {self._rpc_url} failed ({exc}); "
                "falling back to local storage"
            )
            return await self._fallback.attest(
                step_id, protocol_id, worker_name, output_hash
            )

        return AttestationRecord(
            step_id=step_id,
            protocol_id=protocol_id,
            worker_name=worker_name,
            output_hash=output_hash,
            timestamp=time.time(),
            backend="rpc",
            tx_id=tx_id,
        )


# ---------------------------------------------------------------------------
# Backend 3: FilecoinFEVMBackend (stub with web3.py extension points)
# ---------------------------------------------------------------------------

class FilecoinFEVMBackend(AttestationBackend):
    """
    Filecoin FVM attestation — submits proofs to an on-chain FVM actor.

    This implementation is a *stub* that shows exactly where to insert
    web3.py calls to make it live.  The stub falls back to LocalHashBackend
    when web3 is not installed or the RPC is unreachable.

    Configure via env vars:
        FEVM_RPC_URL      — Filecoin JSON-RPC endpoint
                            e.g. https://api.calibration.node.glif.io/rpc/v1
        FEVM_CONTRACT     — Deployed FVM actor/contract address (0x...)
        FEVM_PRIVATE_KEY  — Hex-encoded private key of the signing account

    To activate the live path:
        pip install web3
    Then uncomment the web3 blocks in _submit_on_chain() below.
    """

    _ABI = [
        {
            "inputs": [
                {"internalType": "string", "name": "stepId",     "type": "string"},
                {"internalType": "string", "name": "protocolId", "type": "string"},
                {"internalType": "string", "name": "outputHash", "type": "string"},
            ],
            "name": "attest",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]

    def __init__(self, rpc_url: str, contract_address: str, private_key: str) -> None:
        self._rpc_url = rpc_url
        self._contract_address = contract_address
        self._private_key = private_key
        self._fallback = LocalHashBackend()

    def _submit_on_chain(
        self,
        step_id: str,
        protocol_id: str,
        output_hash: str,
    ) -> Optional[str]:
        """
        Submit an attestation transaction to the FVM actor.

        Returns the transaction hash (CID) on success, None on failure.

        --- To activate: uncomment the block below and pip install web3 ---

        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        account = w3.eth.account.from_key(self._private_key)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(self._contract_address),
            abi=self._ABI,
        )
        tx = contract.functions.attest(step_id, protocol_id, output_hash).build_transaction(
            {
                "from":     account.address,
                "nonce":    w3.eth.get_transaction_count(account.address),
                "gas":      200_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return receipt.transactionHash.hex()
        """
        return None  # stub — remove when activating the web3 block above

    async def attest(
        self,
        step_id: str,
        protocol_id: str,
        worker_name: str,
        output_hash: str,
    ) -> AttestationRecord:
        tx_id: Optional[str] = None
        backend_name = "fevm"
        try:
            tx_id = self._submit_on_chain(step_id, protocol_id, output_hash)
            if tx_id:
                log.info(
                    f"[Attestation] FEVM: step={step_id}  hash={output_hash}  tx={tx_id}"
                )
            else:
                # stub path — falls through to local record with backend="fevm-stub"
                backend_name = "fevm-stub"
                log.debug(
                    f"[Attestation] FEVM stub (web3 not active): step={step_id}  "
                    f"hash={output_hash}"
                )
        except Exception as exc:
            log.warning(
                f"[Attestation] FEVM submission failed ({exc}); "
                "falling back to local storage"
            )
            return await self._fallback.attest(
                step_id, protocol_id, worker_name, output_hash
            )

        return AttestationRecord(
            step_id=step_id,
            protocol_id=protocol_id,
            worker_name=worker_name,
            output_hash=output_hash,
            timestamp=time.time(),
            backend=backend_name,
            tx_id=tx_id,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_attestation_backend() -> AttestationBackend:
    """
    Return the best available attestation backend based on env vars.

    Priority:
      1. FEVM_RPC_URL + FEVM_CONTRACT + FEVM_PRIVATE_KEY → FilecoinFEVMBackend
      2. ATTESTATION_RPC_URL                             → RPCAttestationBackend
      3. (default)                                       → LocalHashBackend
    """
    fevm_url = os.environ.get("FEVM_RPC_URL", "").strip()
    fevm_contract = os.environ.get("FEVM_CONTRACT", "").strip()
    fevm_key = os.environ.get("FEVM_PRIVATE_KEY", "").strip()
    if fevm_url and fevm_contract and fevm_key:
        log.info("[Attestation] Using FilecoinFEVMBackend")
        return FilecoinFEVMBackend(fevm_url, fevm_contract, fevm_key)

    rpc_url = os.environ.get("ATTESTATION_RPC_URL", "").strip()
    if rpc_url:
        log.info(f"[Attestation] Using RPCAttestationBackend → {rpc_url}")
        return RPCAttestationBackend(rpc_url)

    log.debug("[Attestation] Using LocalHashBackend (default)")
    return LocalHashBackend()

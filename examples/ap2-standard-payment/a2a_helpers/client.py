"""A2A HTTP client for inter-agent communication."""
import logging
from typing import Any, Optional

import httpx

from a2a_helpers.types import AgentCard, JsonRpcRequest

logger = logging.getLogger(__name__)


class A2AClient:
    """Client for communicating with A2A agents over HTTP."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def get_agent_card(self) -> dict[str, Any]:
        """Fetch the AgentCard from /.well-known/agent.json."""
        client = self._get_client()
        resp = await client.get(f"{self.base_url}/.well-known/agent.json")
        resp.raise_for_status()
        return resp.json()

    async def send_message(
        self,
        message: dict[str, Any],
        request_id: int | str = 1,
    ) -> dict[str, Any]:
        """Send an A2A message via JSON-RPC message/send."""
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {"message": message},
        }
        client = self._get_client()
        resp = await client.post(
            f"{self.base_url}/a2a",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_task(self, task_id: str, request_id: int | str = 1) -> dict[str, Any]:
        """Get task status via JSON-RPC tasks/get."""
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tasks/get",
            "params": {"taskId": task_id},
        }
        client = self._get_client()
        resp = await client.post(
            f"{self.base_url}/a2a",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_task(self, task_id: str, request_id: int | str = 1) -> dict[str, Any]:
        """Cancel a task via JSON-RPC tasks/cancel."""
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tasks/cancel",
            "params": {"taskId": task_id},
        }
        client = self._get_client()
        resp = await client.post(
            f"{self.base_url}/a2a",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

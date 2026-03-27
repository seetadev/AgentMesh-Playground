"""Lightweight A2A HTTP server using Starlette."""
import json
import logging
from typing import Any, Callable, Awaitable, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

# Type for JSON-RPC method handlers
# Handler receives (params: dict) and returns (result: Any)
MethodHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class A2AServer:
    """Lightweight A2A JSON-RPC server."""

    def __init__(self, agent_card: dict[str, Any], name: str = "A2AAgent"):
        self.agent_card = agent_card
        self.name = name
        self._handlers: dict[str, MethodHandler] = {}
        self._app: Optional[Starlette] = None

    def register_method(self, method_name: str, handler: MethodHandler):
        """Register a JSON-RPC method handler."""
        self._handlers[method_name] = handler

    async def _handle_agent_card(self, request: Request) -> JSONResponse:
        """Serve AgentCard at /.well-known/agent.json."""
        return JSONResponse(self.agent_card)

    async def _handle_jsonrpc(self, request: Request) -> JSONResponse:
        """Handle JSON-RPC 2.0 requests at /a2a."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id")

        handler = self._handlers.get(method)
        if handler is None:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })

        try:
            result = await handler(params)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            })
        except Exception as e:
            logger.exception(f"[{self.name}] Error handling {method}")
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(e)},
            })

    def build_app(self) -> Starlette:
        """Build the Starlette ASGI application."""
        self._app = Starlette(
            routes=[
                Route("/.well-known/agent.json", self._handle_agent_card, methods=["GET"]),
                Route("/a2a", self._handle_jsonrpc, methods=["POST"]),
            ],
        )
        return self._app

    def run(self, host: str = "0.0.0.0", port: int = 8000, log_level: str = "warning"):
        """Run the server with uvicorn."""
        app = self.build_app()
        logger.info(f"[{self.name}] Starting A2A server on {host}:{port}")
        uvicorn.run(app, host=host, port=port, log_level=log_level)

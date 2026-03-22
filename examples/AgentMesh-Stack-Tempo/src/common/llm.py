"""OpenRouter LLM client for free chat queries."""

import os

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "")

SYSTEM_PROMPT = (
    "You are AOIN, a financial markets AI assistant operating on a decentralized "
    "peer-to-peer network. You are knowledgeable about equities, options, forex, "
    "crypto, technical analysis, fundamental analysis, and market microstructure. "
    "Give concise, actionable answers. When discussing trades or positions, always "
    "mention relevant risks. You can answer general questions too, but your expertise "
    "is in financial markets."
)


async def chat(user_message: str) -> str:
    """Send a chat message to OpenRouter and return the response."""
    if not OPENROUTER_API_KEY:
        return "Error: OPENROUTER_API_KEY not configured on this agent."
    if not OPENROUTER_MODEL:
        return "Error: OPENROUTER_MODEL not configured on this agent."

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

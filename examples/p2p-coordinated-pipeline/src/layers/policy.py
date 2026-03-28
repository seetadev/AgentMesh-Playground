"""
Layer 2: Policy Extraction

Translates a natural-language task description into a structured PolicySet.
Policies define the constraints under which agents operate — budget limits,
latency SLAs, output format requirements, and quality tiers.

This is the bridge between "user intent" and "machine-readable coordination rules."

Two extraction paths:
  - LLM path (ANTHROPIC_API_KEY set + anthropic package installed):
      Uses Claude (claude-haiku-4-5-20251001) to parse the task into structured
      capabilities and constraints via a JSON-output prompt.
  - Rule-based path (default / fallback):
      Keyword matching. No external dependencies. Works for demos.
"""

import json
import logging
import os

from ..common.messages import Policy, PolicySet, WorkerCapability

log = logging.getLogger(__name__)

try:
    from anthropic import Anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


# Ordered list of (keyword_fragment, WorkerCapability) — order matters for pipeline stages
_CAPABILITY_KEYWORDS: list[tuple[str, WorkerCapability]] = [
    # Data Validation (always first in a pipeline)
    ("validat",    WorkerCapability.DATA_VALIDATION),
    ("clean",      WorkerCapability.DATA_VALIDATION),
    ("schema",     WorkerCapability.DATA_VALIDATION),
    ("check",      WorkerCapability.DATA_VALIDATION),
    # Data Transformation (before analytics)
    ("transform",  WorkerCapability.DATA_TRANSFORMATION),
    ("convert",    WorkerCapability.DATA_TRANSFORMATION),
    ("normaliz",   WorkerCapability.DATA_TRANSFORMATION),
    ("restructur", WorkerCapability.DATA_TRANSFORMATION),
    # Analytics
    ("analyt",     WorkerCapability.ANALYTICS),
    ("analyz",     WorkerCapability.ANALYTICS),
    ("statistic",  WorkerCapability.ANALYTICS),
    ("comput",     WorkerCapability.ANALYTICS),
    ("aggregat",   WorkerCapability.ANALYTICS),
    ("calculat",   WorkerCapability.ANALYTICS),
    ("measur",     WorkerCapability.ANALYTICS),
    # Report Generation (always last)
    ("report",     WorkerCapability.REPORT_GENERATION),
    ("summary",    WorkerCapability.REPORT_GENERATION),
    ("summariz",   WorkerCapability.REPORT_GENERATION),
    ("document",   WorkerCapability.REPORT_GENERATION),
    ("generat",    WorkerCapability.REPORT_GENERATION),
]

# Quality-tier keywords
_PREMIUM_KEYWORDS = {"premium", "high quality", "accurate", "precise", "thorough"}
_URGENT_KEYWORDS  = {"urgent", "fast", "asap", "immediately", "quickly"}

_LLM_SYSTEM_PROMPT = """\
You are a task-decomposition assistant for a multi-agent data-processing system.
Given a natural-language task description, extract a structured JSON object.

Available capabilities (use only these exact strings):
  "data_validation", "data_transformation", "analytics", "report_generation"

Return ONLY valid JSON with this exact schema:
{
  "capabilities": ["<cap1>", "<cap2>", ...],
  "budget": <float>,
  "latency_ms": <int>,
  "quality": "standard" | "premium",
  "urgent": true | false
}

Rules:
- Include only the capabilities genuinely needed by the task (order matters: validation → transformation → analytics → report).
- If no capability is clearly needed, default to ["analytics"].
- budget and latency_ms are the values passed in; do not change them unless the task text implies a different constraint.
- urgent = true if the task contains urgency words (fast, quickly, asap, immediately, urgent).
- quality = "premium" if the task contains quality words (precise, thorough, accurate, high quality, premium).
"""


class PolicyExtractor:
    """
    Extracts a PolicySet from a free-text task description.

    Each call to extract() returns a PolicySet containing:
      - required_capabilities: which worker types are needed
      - policies: list of named constraints (budget, latency, format, quality)

    Example:
        extractor = PolicyExtractor()
        policy = extractor.extract(
            "Validate and analyze a dataset, then generate a report",
            budget=0.10,
        )
        # policy.required_capabilities == [DATA_VALIDATION, ANALYTICS, REPORT_GENERATION]
        # policy.get("max_budget_usd") == 0.10
    """

    def extract(
        self,
        task: str,
        budget: float = 0.10,
        max_latency_ms: int = 5000,
    ) -> PolicySet:
        """Extract a PolicySet from a natural-language task description."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            if not _ANTHROPIC_AVAILABLE:
                log.warning(
                    "[PolicyExtractor] ANTHROPIC_API_KEY is set but the 'anthropic' "
                    "package is not installed. Falling back to rule-based extraction. "
                    "Run: pip install anthropic"
                )
            else:
                try:
                    return self._extract_llm(task, budget, max_latency_ms, api_key)
                except Exception as exc:
                    log.warning(
                        f"[PolicyExtractor] LLM extraction failed ({exc}), "
                        "falling back to rule-based"
                    )
        return self._extract_rule_based(task, budget, max_latency_ms)

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------

    def _extract_llm(
        self,
        task: str,
        budget: float,
        max_latency_ms: int,
        api_key: str,
    ) -> PolicySet:
        """Use Claude to extract structured policies from the task description."""
        client = Anthropic(api_key=api_key)
        user_msg = (
            f"Task: {task}\n"
            f"Budget hint: {budget} USD\n"
            f"Latency hint: {max_latency_ms} ms"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        log.info(f"[PolicyExtractor] LLM extraction: {raw}")
        parsed = json.loads(raw)

        # Validate capabilities against enum; skip unknown values
        caps: list[WorkerCapability] = []
        seen: set[WorkerCapability] = set()
        for cap_str in parsed.get("capabilities", []):
            try:
                cap = WorkerCapability(cap_str)
                if cap not in seen:
                    caps.append(cap)
                    seen.add(cap)
            except ValueError:
                log.warning(f"[PolicyExtractor] LLM returned unknown capability: {cap_str!r}")
        if not caps:
            caps = [WorkerCapability.ANALYTICS]

        effective_budget = float(parsed.get("budget", budget))
        effective_latency = int(parsed.get("latency_ms", max_latency_ms))
        if parsed.get("urgent"):
            effective_latency = min(effective_latency, 1000)
        quality = "premium" if parsed.get("quality") == "premium" else "standard"

        return PolicySet(
            task_description=task,
            required_capabilities=caps,
            policies=[
                Policy(key="max_budget_usd", value=effective_budget,  negotiable=True),
                Policy(key="max_latency_ms", value=effective_latency, negotiable=True),
                Policy(key="output_format",  value="json",            negotiable=False),
                Policy(key="quality_tier",   value=quality,           negotiable=True),
            ],
        )

    # ------------------------------------------------------------------
    # Rule-based path (default / fallback)
    # ------------------------------------------------------------------

    def _extract_rule_based(
        self,
        task: str,
        budget: float,
        max_latency_ms: int,
    ) -> PolicySet:
        """Keyword-based extraction. No external dependencies."""
        capabilities = self._detect_capabilities(task)
        policies = self._build_policies(task, budget, max_latency_ms)
        return PolicySet(
            task_description=task,
            required_capabilities=capabilities,
            policies=policies,
        )

    def _detect_capabilities(self, text: str) -> list[WorkerCapability]:
        """Detect required capabilities using keyword matching (preserves order)."""
        lower = text.lower()
        seen: set[WorkerCapability] = set()
        caps: list[WorkerCapability] = []
        for keyword, cap in _CAPABILITY_KEYWORDS:
            if keyword in lower and cap not in seen:
                caps.append(cap)
                seen.add(cap)
        # Default to analytics if nothing detected
        return caps or [WorkerCapability.ANALYTICS]

    def _build_policies(
        self, text: str, budget: float, max_latency_ms: int
    ) -> list[Policy]:
        """Build constraint policies from task context and budget/latency params."""
        lower = text.lower()

        # Urgency → tighten latency SLA
        if any(w in lower for w in _URGENT_KEYWORDS):
            max_latency_ms = min(max_latency_ms, 1000)

        # Quality keywords → upgrade quality tier
        quality = "standard"
        if any(w in lower for w in _PREMIUM_KEYWORDS):
            quality = "premium"

        return [
            Policy(key="max_budget_usd", value=budget,         negotiable=True),
            Policy(key="max_latency_ms", value=max_latency_ms, negotiable=True),
            Policy(key="output_format",  value="json",         negotiable=False),
            Policy(key="quality_tier",   value=quality,        negotiable=True),
        ]

"""
Optional AI layer — a Claude "second opinion" on the scan.

This is OFF unless an Anthropic API key is provided (ANTHROPIC_API_KEY). When
present, we hand Claude the real numbers the scanner computed for the top few
candidates and ask it to pick the single best short-term trade and explain why.

IMPORTANT: Claude only ever sees the data WE fetched from Alpaca — it does not
invent prices. If the AI call fails for any reason, the app silently falls back
to the rule-based pick, so this can never break scanning.

Note on credentials: an Anthropic API key (console.anthropic.com) is separate
from a Claude Pro subscription. Pro is the chat product; the API is billed on
its own. A scan costs a fraction of a cent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from strategy import SymbolScore

# Current, most capable Claude model. Change here if you prefer another.
DEFAULT_MODEL = "claude-opus-4-8"

# JSON shape we ask Claude to return, enforced via structured outputs.
_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "recommendation": {"type": "string", "enum": ["GO", "CAUTION", "NO-GO"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string"},
    },
    "required": ["symbol", "recommendation", "confidence", "rationale"],
    "additionalProperties": False,
}


@dataclass
class AIResult:
    symbol: str
    recommendation: str  # GO / CAUTION / NO-GO
    confidence: str      # low / medium / high
    rationale: str


def _build_prompt(candidates: List[SymbolScore]) -> str:
    lines = [
        "You are a disciplined short-term trading assistant. Below are stock "
        "candidates that already PASSED a momentum + volume filter (price above "
        "the N-day moving average AND above-average volume today). Using ONLY "
        "the numbers provided — do not assume any other data — pick the single "
        "best candidate for a short-term momentum swing trade.",
        "",
        "Candidates:",
    ]
    for c in candidates:
        lines.append(
            f"- {c.symbol}: price ${c.last_price:.2f}, "
            f"{c.momentum_strength * 100:.1f}% above its moving average, "
            f"volume {c.volume_ratio:.2f}x average, composite score {c.score:.4f}"
        )
    lines += [
        "",
        "Prefer strong momentum backed by genuinely heavy volume; be wary of a "
        "big move on only slightly-above-average volume. Give one clear pick, a "
        "GO/CAUTION/NO-GO recommendation, your confidence, and a one- or "
        "two-sentence rationale a beginner can follow. This is educational, not "
        "financial advice.",
    ]
    return "\n".join(lines)


def ai_choose(
    candidates: List[SymbolScore],
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> Optional[AIResult]:
    """
    Ask Claude to pick the best candidate. Returns an AIResult, or None if the
    call fails or the SDK isn't available (caller then falls back to the
    rule-based pick).
    """
    if not candidates or not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": _build_prompt(candidates)}],
        )
        # With output_config.format the first text block is guaranteed valid JSON.
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
        return AIResult(
            symbol=data["symbol"],
            recommendation=data["recommendation"],
            confidence=data["confidence"],
            rationale=data["rationale"],
        )
    except Exception:
        # Any failure (bad key, network, older SDK, refusal) -> fall back cleanly.
        return None

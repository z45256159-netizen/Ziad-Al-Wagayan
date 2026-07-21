"""
Optional AI layer — a FREE "second opinion" on the scan.

This is OFF unless you provide a free API key. It uses providers that expose the
standard OpenAI-compatible chat API, so no paid service and no extra Python
packages are required (calls go out via the standard library).

Get a FREE key (no credit card):
  * Groq        -> https://console.groq.com   (fast; set GROQ_API_KEY)        [default]
  * OpenRouter  -> https://openrouter.ai       (free models; OPENROUTER_API_KEY)

When enabled, the app hands the AI the real numbers the scanner computed for the
top candidates and asks it to pick the single best short-term trade and explain
why. The AI only ever sees data WE fetched from Alpaca — it never invents prices.
If the call fails for any reason, the app silently falls back to the rule-based
pick, so this can never break scanning.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from strategy import SymbolScore

# Free, OpenAI-compatible providers. `model` is a sensible free default per
# provider — override with the AI_MODEL secret if you like.
PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
    },
}
DEFAULT_PROVIDER = "groq"


@dataclass
class AIResult:
    symbol: str
    recommendation: str  # GO / CAUTION / NO-GO
    confidence: str      # low / medium / high
    rationale: str


def _build_messages(candidates: List[SymbolScore]) -> list:
    lines = ["Candidates:"]
    for c in candidates:
        lines.append(
            f"- {c.symbol}: price ${c.last_price:.2f}, "
            f"{c.momentum_strength * 100:.1f}% above its moving average, "
            f"volume {c.volume_ratio:.2f}x average, composite score {c.score:.4f}"
        )
    data = "\n".join(lines)
    system = (
        "You are a disciplined short-term trading assistant. You will be given "
        "stock candidates that already PASSED a momentum + volume filter (price "
        "above the moving average AND above-average volume today). Using ONLY the "
        "numbers provided, pick the single best candidate for a short-term "
        "momentum swing trade. Prefer strong momentum backed by genuinely heavy "
        "volume; be wary of a big move on only slightly-above-average volume. "
        "This is educational, not financial advice.\n\n"
        "Respond with ONLY a JSON object (no prose, no code fences) with exactly "
        "these keys:\n"
        '  "symbol": one of the candidate tickers,\n'
        '  "recommendation": one of "GO", "CAUTION", "NO-GO",\n'
        '  "confidence": one of "low", "medium", "high",\n'
        '  "rationale": one or two sentences a beginner can follow.'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": data},
    ]


def _extract_json(text: str) -> Optional[dict]:
    """Parse the first JSON object out of the model's reply, tolerantly."""
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None


def ai_choose(
    candidates: List[SymbolScore],
    api_key: str,
    provider: str = DEFAULT_PROVIDER,
    model: Optional[str] = None,
) -> Optional[AIResult]:
    """
    Ask a free LLM to pick the best candidate. Returns an AIResult, or None if
    the call fails (caller then falls back to the rule-based pick).
    """
    if not candidates or not api_key:
        return None

    cfg = PROVIDERS.get(provider)
    if cfg is None:
        return None

    payload = {
        "model": model or cfg["model"],
        "messages": _build_messages(candidates),
        "temperature": 0.2,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }

    request = urllib.request.Request(
        cfg["url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        data = _extract_json(content)
        if not data:
            return None
        return AIResult(
            symbol=str(data["symbol"]).upper(),
            recommendation=str(data.get("recommendation", "CAUTION")).upper(),
            confidence=str(data.get("confidence", "medium")).lower(),
            rationale=str(data.get("rationale", "")).strip(),
        )
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        # Bad key, network issue, rate limit, unexpected shape -> clean fallback.
        return None

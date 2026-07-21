"""
Optional AI layer — a FREE "second opinion" powered by Groq.

This is OFF unless you provide a free Groq API key. It calls Groq's chat API
directly over the standard library (no paid service, no extra Python packages).

Get a FREE Groq key (no credit card): https://console.groq.com  ->  API Keys

When enabled, the app hands Groq the real numbers the scanner computed for the
top candidates and asks it to pick the single best short-term trade and explain
why. Groq only ever sees data WE fetched from Alpaca — it never invents prices.
If the call fails, the app falls back to the rule-based pick, so this can never
break scanning.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from strategy import SymbolScore

# Groq endpoint + a solid free model. Override the model with the AI_MODEL secret.
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


@dataclass
class AIResult:
    symbol: str
    recommendation: str  # GO / CAUTION / NO-GO
    confidence: str      # low / medium / high
    rationale: str


def _post(api_key: str, payload: dict, timeout: int = 30) -> Optional[dict]:
    """POST to Groq and return the parsed JSON body, or None on any failure."""
    request = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None


def verify_key(api_key: str, model: str = GROQ_MODEL) -> bool:
    """Cheap check that a Groq key actually works."""
    if not api_key:
        return False
    body = _post(api_key, {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        "max_tokens": 5,
    }, timeout=15)
    try:
        return bool(body["choices"][0]["message"]["content"])
    except (TypeError, KeyError, IndexError):
        return False


def _build_messages(candidates: List[SymbolScore]) -> list:
    lines = ["Candidates (all already passed a trend + RSI + MACD + volume filter):"]
    for c in candidates:
        lines.append(
            f"- {c.symbol}: price ${c.last_price:.2f}, "
            f"{c.momentum_strength * 100:.1f}% above its 20-day average, "
            f"RSI {c.rsi:.0f}, MACD histogram {c.macd_hist:+.2f}, "
            f"volume {c.volume_ratio:.2f}x average, composite score {c.score:.4f}"
        )
    data = "\n".join(lines)
    system = (
        "You are a disciplined short-term trading assistant. You will be given "
        "stock candidates that already PASSED a multi-indicator filter (uptrend "
        "via moving-average crossover, RSI momentum, bullish MACD, and "
        "above-average volume). Using ONLY the numbers provided, pick the single "
        "best candidate for a short-term momentum swing trade. Favor strong, "
        "volume-backed momentum with RSI that shows strength without being "
        "overbought (very high RSI is a caution). This is educational, not "
        "financial advice.\n\n"
        "Respond with ONLY a JSON object (no prose, no code fences) with exactly "
        "these keys:\n"
        '  "symbol": one of the candidate tickers,\n'
        '  "recommendation": one of "GO", "CAUTION", "NO-GO",\n'
        '  "confidence": one of "low", "medium", "high",\n'
        '  "rationale": 2-3 sentences a beginner can follow — say WHY this stock '
        "won over the others, referencing its trend, RSI, MACD and volume, and "
        "what would make you cautious."
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
    model: Optional[str] = None,
) -> Optional[AIResult]:
    """
    Ask Groq to pick the best candidate. Returns an AIResult, or None if the
    call fails (caller then falls back to the rule-based pick).
    """
    if not candidates or not api_key:
        return None

    body = _post(api_key, {
        "model": model or GROQ_MODEL,
        "messages": _build_messages(candidates),
        "temperature": 0.3,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    })
    if body is None:
        return None
    try:
        content = body["choices"][0]["message"]["content"]
    except (TypeError, KeyError, IndexError):
        return None

    data = _extract_json(content)
    if not data:
        return None
    return AIResult(
        symbol=str(data["symbol"]).upper(),
        recommendation=str(data.get("recommendation", "CAUTION")).upper(),
        confidence=str(data.get("confidence", "medium")).lower(),
        rationale=str(data.get("rationale", "")).strip(),
    )

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

import base64
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
# Vision-capable free Groq model (override with the AI_VISION_MODEL secret if the
# default is ever retired). This is the model that actually LOOKS at the chart.
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


@dataclass
class AIResult:
    symbol: str
    recommendation: str  # GO / CAUTION / NO-GO
    confidence: str      # low / medium / high
    rationale: str
    pattern: str = ""    # chart pattern the AI identified


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


@dataclass
class VisionResult:
    pattern: str
    direction: str       # long / short / none
    recommendation: str  # GO / CAUTION / NO-GO
    confidence: str      # low / medium / high
    rationale: str


_VISION_PROMPT = (
    "You are a technical analyst. This is a daily candlestick chart of a stock. "
    "Look at it and identify the single clearest chart pattern actually visible — "
    "one of: head and shoulders, inverse head and shoulders, double top, double "
    "bottom, triple top, triple bottom, ascending triangle, descending triangle, "
    "symmetrical triangle, bull flag, bear flag, breakout, breakdown, uptrend, "
    "downtrend, or none. Be honest: if there is no clear pattern, say \"none\" — "
    "do NOT invent one. Then give a short trader's read.\n\n"
    "Respond with ONLY a JSON object (no prose, no code fences):\n"
    '{"pattern": "...", "direction": "long|short|none", '
    '"recommendation": "GO|CAUTION|NO-GO", "confidence": "low|medium|high", '
    '"rationale": "one or two sentences on what you see and why"}'
)


def vision_pattern(image_png: bytes, api_key: str,
                   model: Optional[str] = None) -> Optional[VisionResult]:
    """
    Send the chart IMAGE to a Groq vision model and get back the pattern it sees.
    Returns None on any failure (caller falls back to rule-based detection).
    """
    if not image_png or not api_key:
        return None
    b64 = base64.b64encode(image_png).decode("ascii")
    payload = {
        "model": model or VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "temperature": 0.2,
        "max_tokens": 500,
    }
    body = _post(api_key, payload, timeout=45)
    if body is None:
        return None
    try:
        content = body["choices"][0]["message"]["content"]
    except (TypeError, KeyError, IndexError):
        return None
    data = _extract_json(content)
    if not data:
        return None
    return VisionResult(
        pattern=str(data.get("pattern", "")).strip(),
        direction=str(data.get("direction", "none")).strip().lower(),
        recommendation=str(data.get("recommendation", "CAUTION")).strip().upper(),
        confidence=str(data.get("confidence", "medium")).strip().lower(),
        rationale=str(data.get("rationale", "")).strip(),
    )


def _build_messages(candidates: List[SymbolScore]) -> list:
    lines = ["Candidates (a list of movers — each with its indicators and a "
             "detected chart pattern):"]
    for c in candidates:
        lines.append(
            f"- {c.symbol}: price ${c.last_price:.2f}, pattern: "
            f"{c.pattern or 'n/a'}, {c.momentum_strength * 100:.1f}% vs 20-day "
            f"avg, RSI {c.rsi:.0f}, MACD {c.macd_hist:+.2f}, "
            f"vol {c.volume_ratio:.2f}x, support ${c.support:.2f}, "
            f"resistance ${c.resistance:.2f}"
        )
    data = "\n".join(lines)
    system = (
        "You are a sharp short-term / day-trading assistant. You are given a list "
        "of moving stocks, each with indicators, support/resistance, and a "
        "roughly-detected chart pattern. Go through them and pick the ONE with "
        "the cleanest tradeable setup right now — a clear pattern (breakout, "
        "double/triple bottom, bull flag, etc.) backed by momentum and volume, "
        "with room to a sensible target before resistance. Confirm or correct the "
        "detected pattern using the numbers. This is educational, not financial "
        "advice.\n\n"
        "Respond with ONLY a JSON object (no prose, no code fences) with exactly "
        "these keys:\n"
        '  "symbol": one of the candidate tickers,\n'
        '  "pattern": the chart pattern / setup you see (e.g. "Double bottom", '
        '"Bull flag", "Breakout"),\n'
        '  "recommendation": one of "GO", "CAUTION", "NO-GO",\n'
        '  "confidence": one of "low", "medium", "high",\n'
        '  "rationale": 2-3 sentences a beginner can follow — why this setup, and '
        "what would invalidate it."
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
        pattern=str(data.get("pattern", "")).strip(),
    )

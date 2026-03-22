"""Alpha Vantage API client and signal generation."""

import os
from datetime import datetime, timedelta

import httpx

AV_BASE_URL = "https://www.alphavantage.co/query"
AV_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")


async def _av_request(params: dict) -> dict:
    """Make a request to Alpha Vantage."""
    if not AV_API_KEY:
        raise ValueError("ALPHA_VANTAGE_API_KEY not configured")
    params["apikey"] = AV_API_KEY
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(AV_BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        if "Error Message" in data:
            raise ValueError(f"Alpha Vantage error: {data['Error Message']}")
        if "Note" in data:
            raise ValueError(f"Alpha Vantage rate limit: {data['Note']}")
        return data


async def get_quote(symbol: str) -> dict:
    """Get real-time quote for a symbol."""
    data = await _av_request({
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
    })
    quote = data.get("Global Quote", {})
    if not quote:
        raise ValueError(f"No quote data for {symbol}")
    return {
        "symbol": quote.get("01. symbol", symbol),
        "price": float(quote.get("05. price", 0)),
        "change": float(quote.get("09. change", 0)),
        "change_pct": float(quote.get("10. change percent", "0%").rstrip("%")),
        "volume": int(quote.get("06. volume", 0)),
        "prev_close": float(quote.get("08. previous close", 0)),
        "open": float(quote.get("02. open", 0)),
        "high": float(quote.get("03. high", 0)),
        "low": float(quote.get("04. low", 0)),
    }


async def get_daily(symbol: str, days: int = 30) -> list[dict]:
    """Get daily price history. Returns most recent `days` entries."""
    data = await _av_request({
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "compact",
    })
    series = data.get("Time Series (Daily)", {})
    if not series:
        raise ValueError(f"No daily data for {symbol}")

    entries = []
    for date_str in sorted(series.keys(), reverse=True)[:days]:
        bar = series[date_str]
        entries.append({
            "date": date_str,
            "open": float(bar["1. open"]),
            "high": float(bar["2. high"]),
            "low": float(bar["3. low"]),
            "close": float(bar["4. close"]),
            "volume": int(bar["5. volume"]),
        })
    return entries


async def get_rsi(symbol: str, period: int = 14) -> list[dict]:
    """Get RSI indicator values."""
    data = await _av_request({
        "function": "RSI",
        "symbol": symbol,
        "interval": "daily",
        "time_period": str(period),
        "series_type": "close",
    })
    series = data.get("Technical Analysis: RSI", {})
    if not series:
        raise ValueError(f"No RSI data for {symbol}")

    entries = []
    for date_str in sorted(series.keys(), reverse=True)[:30]:
        entries.append({
            "date": date_str,
            "rsi": float(series[date_str]["RSI"]),
        })
    return entries


async def get_macd(symbol: str) -> list[dict]:
    """Get MACD indicator values."""
    data = await _av_request({
        "function": "MACD",
        "symbol": symbol,
        "interval": "daily",
        "series_type": "close",
    })
    series = data.get("Technical Analysis: MACD", {})
    if not series:
        raise ValueError(f"No MACD data for {symbol}")

    entries = []
    for date_str in sorted(series.keys(), reverse=True)[:30]:
        vals = series[date_str]
        entries.append({
            "date": date_str,
            "macd": float(vals["MACD"]),
            "signal": float(vals["MACD_Signal"]),
            "histogram": float(vals["MACD_Hist"]),
        })
    return entries


async def get_sma(symbol: str, period: int = 20) -> list[dict]:
    """Get SMA indicator values."""
    data = await _av_request({
        "function": "SMA",
        "symbol": symbol,
        "interval": "daily",
        "time_period": str(period),
        "series_type": "close",
    })
    series = data.get("Technical Analysis: SMA", {})
    if not series:
        raise ValueError(f"No SMA data for {symbol}")

    entries = []
    for date_str in sorted(series.keys(), reverse=True)[:30]:
        entries.append({
            "date": date_str,
            "sma": float(series[date_str]["SMA"]),
        })
    return entries


def generate_signal(quote: dict, rsi: list[dict], macd: list[dict], sma: list[dict]) -> dict:
    """Generate a Long/Short/Neutral signal from market data.

    Uses a weighted scoring system:
    - RSI: oversold (<30) = bullish, overbought (>70) = bearish
    - MACD: histogram positive & rising = bullish, negative & falling = bearish
    - Price vs SMA: above = bullish, below = bearish
    - Daily change momentum
    """
    score = 0
    reasons = []

    # RSI analysis (weight: 30)
    current_rsi = rsi[0]["rsi"] if rsi else 50
    if current_rsi < 30:
        score += 30
        reasons.append(f"RSI oversold ({current_rsi:.1f})")
    elif current_rsi < 40:
        score += 15
        reasons.append(f"RSI low ({current_rsi:.1f})")
    elif current_rsi > 70:
        score -= 30
        reasons.append(f"RSI overbought ({current_rsi:.1f})")
    elif current_rsi > 60:
        score -= 15
        reasons.append(f"RSI high ({current_rsi:.1f})")
    else:
        reasons.append(f"RSI neutral ({current_rsi:.1f})")

    # MACD analysis (weight: 30)
    if macd and len(macd) >= 2:
        hist_now = macd[0]["histogram"]
        hist_prev = macd[1]["histogram"]
        macd_rising = hist_now > hist_prev

        if hist_now > 0 and macd_rising:
            score += 30
            reasons.append("MACD bullish & rising")
        elif hist_now > 0:
            score += 10
            reasons.append("MACD positive but fading")
        elif hist_now < 0 and not macd_rising:
            score -= 30
            reasons.append("MACD bearish & falling")
        elif hist_now < 0:
            score -= 10
            reasons.append("MACD negative but recovering")

    # Price vs SMA (weight: 25)
    price = quote["price"]
    current_sma = sma[0]["sma"] if sma else price
    sma_diff_pct = ((price - current_sma) / current_sma) * 100 if current_sma else 0

    if sma_diff_pct > 2:
        score += 25
        reasons.append(f"Price {sma_diff_pct:.1f}% above SMA20")
    elif sma_diff_pct > 0:
        score += 10
        reasons.append(f"Price slightly above SMA20")
    elif sma_diff_pct < -2:
        score -= 25
        reasons.append(f"Price {abs(sma_diff_pct):.1f}% below SMA20")
    elif sma_diff_pct < 0:
        score -= 10
        reasons.append(f"Price slightly below SMA20")

    # Momentum from daily change (weight: 15)
    change_pct = quote["change_pct"]
    if change_pct > 2:
        score += 15
        reasons.append(f"Strong daily gain ({change_pct:+.1f}%)")
    elif change_pct > 0.5:
        score += 8
        reasons.append(f"Positive momentum ({change_pct:+.1f}%)")
    elif change_pct < -2:
        score -= 15
        reasons.append(f"Strong daily loss ({change_pct:+.1f}%)")
    elif change_pct < -0.5:
        score -= 8
        reasons.append(f"Negative momentum ({change_pct:+.1f}%)")

    # Determine direction and confidence
    # Score range: -100 to +100
    if score > 15:
        direction = "Long"
    elif score < -15:
        direction = "Short"
    else:
        direction = "Neutral"

    confidence = min(95, max(20, 50 + abs(score)))

    # Expiry recommendation based on signal strength
    if abs(score) > 50:
        expiry_days = 7
    elif abs(score) > 25:
        expiry_days = 14
    else:
        expiry_days = 30

    expiry = (datetime.now() + timedelta(days=expiry_days)).strftime("%Y-%m-%d")

    return {
        "direction": direction,
        "confidence": confidence,
        "expiry": expiry,
        "reasons": reasons,
        "indicators": {
            "rsi": round(current_rsi, 1),
            "macd_histogram": round(macd[0]["histogram"], 4) if macd else None,
            "sma20": round(current_sma, 2) if sma else None,
            "price": quote["price"],
            "change_pct": quote["change_pct"],
        },
    }

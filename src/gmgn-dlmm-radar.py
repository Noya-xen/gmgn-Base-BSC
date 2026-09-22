#!/usr/bin/env python3
"""Scan selected GMGN chains and send radar and volume boards to Telegram."""

import json
import os
import argparse
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from html import escape as html_escape
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def configure_console_encoding():
    """Keep emoji/symbols in local reports printable on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


configure_console_encoding()


def load_private_env():
    env_path = Path.home() / ".config/gmgn-bsc-base-radar/telegram.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_private_env()
TOKEN = os.environ.get("TG_BOT_TOKEN", "")
# This radar intentionally has its own destination, separate from any trade group.
CHAT_ID = os.environ.get("TG_RADAR_GROUP_CHAT_ID", "")
SIGNAL_THREAD_ID = os.environ.get("TG_SIGNAL_THREAD_ID", "").strip()
# Keep the explicit TG_SEND_WATCH_THREAD_ID name supported because it is
# intuitive in the env file. TG_WATCH_THREAD_ID is accepted as a shorter
# alias for future configurations.
WATCH_THREAD_ID = os.environ.get(
    "TG_SEND_WATCH_THREAD_ID",
    os.environ.get("TG_WATCH_THREAD_ID", ""),
).strip()
VOLUME_THREAD_ID = os.environ.get("TG_VOLUME_THREAD_ID", "").strip()
SEND_WATCH = os.environ.get("TG_SEND_WATCH", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
RADAR_TIMEZONE = os.environ.get("RADAR_TIMEZONE", "UTC")
RADAR_LOCATION = os.environ.get("RADAR_LOCATION", RADAR_TIMEZONE)
# GMGN currently exposes the same market/token command family for these
# chains. Keep the list in one place so operators can select any subset via
# RADAR_CHAINS / VOLUME_CHAINS without changing the screening flow.
SUPPORTED_CHAINS = (
    "sol", "bsc", "base", "eth", "arbitrum", "hyperevm",
    "robinhood", "arc", "stable",
)
DEFAULT_CHAINS = ("bsc", "base")
LIMIT = 100

# The existing systemd/Hermes schedule starts a fresh run every five minutes.
# Signal/Watch keep priority; volume scanning is allowed to use only the
# remaining part of that five-minute slot and resumes from saved state.
RADAR_INTERVAL_SECONDS = int(os.environ.get("RADAR_INTERVAL_SECONDS", "300"))
VOLUME_SAFETY_SECONDS = int(os.environ.get("VOLUME_SAFETY_SECONDS", "10"))
VOLUME_START_DELAY_SECONDS = int(
    os.environ.get("VOLUME_START_DELAY_SECONDS", "60")
)
SIGNAL_ANALYSIS_LIMIT = max(
    1, int(os.environ.get("SIGNAL_ANALYSIS_LIMIT", "10"))
)
VOLUME_SCAN_LIMIT = int(os.environ.get("VOLUME_SCAN_LIMIT", "20"))
VOLUME_SPIKE_MIN_15M = float(os.environ.get("VOLUME_SPIKE_MIN_15M", "500000"))
VOLUME_SPIKE_MIN_1H = float(os.environ.get("VOLUME_SPIKE_MIN_1H", "1000000"))
VOLUME_ALERT_COOLDOWN_SECONDS = int(
    os.environ.get("VOLUME_ALERT_COOLDOWN_SECONDS", "900")
)
VOLUME_STATE_PATH = Path(
    os.environ.get(
        "VOLUME_STATE_FILE",
        str(Path.home() / ".config/gmgn-bsc-base-radar/volume-state.json"),
    )
)

# These thresholds classify the same 1h scan into different use cases. They
# are intentionally kept separate from the GMGN candidate gates above so the
# full Signal board does not lose active names.
WATCH_MIN_SCORE = 55
LP_MIN_SCORE = 60
LP_MIN_LIQUIDITY = 25_000
LP_MIN_VL = 0.10
LP_MAX_VL = 0.80
LP_MIN_FLOW = 0.60
LP_MAX_FLOW = 1.40
LP_MAX_SWAP_SPEED = 1.80

CHAIN_LABELS = {
    "sol": "SOL",
    "bsc": "BSC",
    "base": "BASE",
    "eth": "ETH",
    "arbitrum": "ARBITRUM",
    "hyperevm": "HYPEREVM",
    "robinhood": "ROBINHOOD",
    "arc": "ARC",
    "stable": "STABLE",
}


def chain_title(chain):
    return CHAIN_LABELS.get(chain, chain.upper())


def parse_chains(value):
    """Parse one or more distinct supported chains for one radar cycle."""
    if isinstance(value, str):
        requested = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    else:
        requested = tuple(str(part).strip().lower() for part in value if str(part).strip())
    if not requested:
        raise ValueError(
            "RADAR_CHAINS must contain at least one chain: "
            + ",".join(SUPPORTED_CHAINS)
        )
    if len(set(requested)) != len(requested):
        raise ValueError("RADAR_CHAINS must not contain duplicate chains")
    unsupported = [chain for chain in requested if chain not in SUPPORTED_CHAINS]
    if unsupported:
        raise ValueError(
            f"Unsupported chain(s): {', '.join(unsupported)}. "
            f"Choose from: {', '.join(SUPPORTED_CHAINS)}"
        )
    return requested


RADAR_CHAINS = parse_chains(
    os.environ.get("RADAR_CHAINS", ",".join(DEFAULT_CHAINS))
)
VOLUME_CHAINS = parse_chains(
    os.environ.get("VOLUME_CHAINS", ",".join(RADAR_CHAINS))
)
# Backward-compatible alias for integrations that imported CHAINS.
CHAINS = RADAR_CHAINS


def trend_command(chain):
    """Return the GMGN Trending command for one EVM chain.

    BSC and Base use the same EVM-style gates as the former Robinhood board.
    In particular, no Solana ``min-gas-fee`` gate is applied because GMGN gas
    values are not comparable across chains.
    """
    if chain not in SUPPORTED_CHAINS:
        raise ValueError(f"Unsupported radar chain: {chain}")
    return (
        f"gmgn-cli market trending --chain {chain} --interval 1h --limit {LIMIT} "
        "--order-by volume --direction desc "
        "--min-liquidity 2500 --min-holder-count 200 --min-created 30m "
        "--min-smart-degen-count 2 --min-swaps 500 --min-marketcap 100000"
    )


# Keep named commands available for operators/scripts that used the old layout.
BSC_CMD = trend_command("bsc")
BASE_CMD = trend_command("base")
ARC_CMD = trend_command("arc")
SOL_CMD = trend_command("sol")
ETH_CMD = trend_command("eth")
ARBITRUM_CMD = trend_command("arbitrum")
HYPEREVM_CMD = trend_command("hyperevm")
ROBINHOOD_CMD = trend_command("robinhood")
STABLE_CMD = trend_command("stable")


def run(cmd, timeout=60):
    try:
        out = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        ).stdout
        return json.loads(out)
    except Exception:
        return {}


def gather(cmd=BSC_CMD, timeout=60):
    tr = run(cmd, timeout=timeout)
    if isinstance(tr, dict):
        rank = tr.get("data", {}).get("rank", [])
        if isinstance(rank, list):
            return rank
    return []


def safe_for_dlmm(t):
    """Reject rows that GMGN explicitly marks as wash trading."""
    return t.get("is_wash_trading") is not True


def token_price_data(t):
    """Fetch one exact token snapshot for swap/volume acceleration metrics."""
    address = t.get("address")
    chain = t.get("chain")
    if not address or not chain:
        return None
    cmd = [
        "gmgn-cli", "token", "info", "--chain", chain,
        "--address", address, "--raw",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
        data = json.loads(out).get("price", {})
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def token_price_map(hits):
    """Fetch exact snapshots for the full eligible universe with bounded concurrency."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(token_price_data, hits))
    return {
        (t.get("chain"), t.get("address")): snapshot
        for t, snapshot in zip(hits, snapshots)
        if t.get("chain") and t.get("address") and snapshot
    }


def flow_5m(t, price_data=None):
    """Return volume FLOW plus five-minute swap acceleration."""
    address = t.get("address")
    chain = t.get("chain")
    vol_1h = float(t.get("volume") or 0)
    if not address or not chain or vol_1h <= 0:
        return None, "-", 0, 0, None
    now = int(time.time())
    cmd = [
        "gmgn-cli", "market", "kline", "--chain", chain,
        "--address", address, "--resolution", "1m",
        "--from", str(now - 480), "--to", str(now), "--raw",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
        candles = json.loads(out).get("list", [])[-5:]
        if not candles:
            return None, "-", 0, 0, None
        vol_5m = sum(float(c.get("volume") or 0) for c in candles)
        ratio = (vol_5m * 12) / vol_1h
        open_5m = float(candles[0].get("open") or 0)
        close_5m = float(candles[-1].get("close") or 0)
        price_change_5m = ((close_5m / open_5m) - 1) if open_5m > 0 else 0

        # Reuse the full-universe snapshot when available.
        price_data = price_data or token_price_data(t) or {}
        buy_vol_5m = float(price_data.get("buy_volume_5m") or 0)
        sell_vol_5m = float(price_data.get("sell_volume_5m") or 0)
        swaps_5m = int(float(price_data.get("swaps_5m") or 0))
        swaps_1h = float(price_data.get("swaps_1h") or t.get("swaps") or 0)
        swap_speed = (swaps_5m * 12 / swaps_1h) if swaps_1h > 0 else None

        # Require price and directional volume to agree. A 5% margin prevents
        # tiny buy/sell differences from being mislabeled directional.
        if price_change_5m > 0.01 and buy_vol_5m > sell_vol_5m * 1.05:
            direction = "📈"
        elif price_change_5m < -0.01 and sell_vol_5m > buy_vol_5m * 1.05:
            direction = "📉"
        else:
            direction = "🔄"

        if ratio > 1.20:
            icon = "🔥"
        elif ratio >= 0.80:
            icon = "🟢"
        elif ratio >= 0.50:
            icon = "🟡"
        else:
            icon = "🧊"
        return ratio, f"{icon}{direction}{ratio:.1f}", int(swaps_1h), swaps_5m, swap_speed
    except Exception:
        return None, "-", 0, 0, None


def volume_spike_data(t, chain, timeout=20):
    """Read four 15m candles and return current 15m/rolling 1h volume."""
    address = t.get("address")
    if not address:
        return None
    now = int(time.time())
    cmd = [
        "gmgn-cli", "market", "kline", "--chain", chain,
        "--address", address, "--resolution", "15m",
        "--from", str(now - 3600), "--to", str(now), "--raw",
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max(1, timeout)
        ).stdout
        raw = json.loads(out)
        candles = raw.get("list", []) if isinstance(raw, dict) else []
        candles = [c for c in candles if isinstance(c, dict)]
        candles.sort(key=lambda c: float(c.get("time") or 0))
        candles = candles[-4:]
        if len(candles) < 4:
            return None

        volumes = [float(c.get("volume") or 0) for c in candles]
        volume_15m = volumes[-1]
        volume_1h = sum(volumes)
        average_15m = volume_1h / 4 if volume_1h > 0 else 0
        spike_ratio = volume_15m / average_15m if average_15m > 0 else 0

        first_open = float(candles[0].get("open") or 0)
        last_close = float(candles[-1].get("close") or 0)
        previous_close = float(candles[-2].get("close") or 0)
        change_1h = ((last_close / first_open) - 1) * 100 if first_open > 0 else 0
        change_15m = (
            ((last_close / previous_close) - 1) * 100
            if previous_close > 0 else 0
        )
        return {
            "volume_15m": volume_15m,
            "volume_1h": volume_1h,
            "spike_ratio": spike_ratio,
            "change_15m": change_15m,
            "change_1h": change_1h,
        }
    except Exception:
        return None


def volume_threshold_status(data):
    """Return independent 15m/1h status; either passing window alerts."""
    pass_15m = data["volume_15m"] >= VOLUME_SPIKE_MIN_15M
    pass_1h = data["volume_1h"] >= VOLUME_SPIKE_MIN_1H
    if not pass_15m and not pass_1h:
        return None
    if pass_15m and pass_1h:
        status = "both"
    elif pass_15m:
        status = "15m"
    else:
        status = "1h"
    return {
        "pass_15m": pass_15m,
        "pass_1h": pass_1h,
        "status": status,
    }


def money(v):
    v = float(v or 0)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}k"
    return f"{v:.0f}"


def ca_markup(t):
    """Make only the contract address copyable in Telegram."""
    ca = " ".join(str(t.get("address") or "-").split())
    return f"<code>{html_escape(ca)}</code>"


def chart_markup(t):
    """Return a direct GMGN K-line link for the token and chain."""
    chain = str(t.get("chain") or "").strip()
    address = " ".join(str(t.get("address") or "").split())
    url = f"https://www.gmgn.cc/kline/{chain}/{urllib.parse.quote(address, safe='')}"
    return f'<a href="{html_escape(url, quote=True)}">📊 Chart GMGN</a>'


def analyze_token(t, price_data=None):
    """Build one shared metric record for Signal, Watch, and LP reports."""
    price_data = price_data or {}
    vol = float(t.get("volume") or 0)
    liq = float(t.get("liquidity") or 0)
    vl = vol / liq if liq > 0 else 0
    ratio, flow_label, swaps_1h, swaps_5m, swap_speed = flow_5m(t, price_data)
    flow = float(ratio or 0)
    current_price = float(price_data.get("price") or t.get("price") or 0)
    price_5m = float(price_data.get("price_5m") or 0)
    change_5m = ((current_price / price_5m) - 1) * 100 if price_5m > 0 else 0
    buy_vol = float(price_data.get("buy_volume_5m") or 0)
    sell_vol = float(price_data.get("sell_volume_5m") or 0)
    total_directional = buy_vol + sell_vol
    buy_share = buy_vol / total_directional if total_directional > 0 else 0.5
    if "📈" in flow_label:
        direction = "bullish"
    elif "📉" in flow_label:
        direction = "bearish"
    else:
        direction = "mixed"
    return {
        "token": t,
        "flow_label": flow_label,
        "flow": flow,
        "vl": vl,
        "liquidity": liq,
        "swaps_1h": swaps_1h,
        "swaps_5m": swaps_5m,
        "swap_speed": float(swap_speed or 0),
        "change_5m": change_5m,
        "buy_share": buy_share,
        "direction": direction,
    }


def watch_score(m):
    """Score momentum worth monitoring, without calling it an entry."""
    score = 0
    if m["vl"] >= 1.0:
        score += 25
    elif m["vl"] >= 0.50:
        score += 18
    elif m["vl"] >= 0.25:
        score += 10
    if m["flow"] >= 1.20:
        score += 25
    elif m["flow"] >= 0.80:
        score += 18
    elif m["flow"] >= 0.50:
        score += 8
    if m["swap_speed"] >= 1.50:
        score += 20
    elif m["swap_speed"] >= 1.00:
        score += 14
    elif m["swap_speed"] >= 0.70:
        score += 6
    if m["direction"] == "bullish":
        score += 25
    elif m["direction"] == "mixed":
        score += 8
    if m["liquidity"] >= 50_000:
        score += 5
    return min(score, 100)


def lp_score(m):
    """Score fee activity while penalizing unstable momentum and sell pressure."""
    score = 0
    if m["liquidity"] >= 100_000:
        score += 25
    elif m["liquidity"] >= 50_000:
        score += 18
    elif m["liquidity"] >= LP_MIN_LIQUIDITY:
        score += 10
    if 0.20 <= m["vl"] <= 0.60:
        score += 25
    elif LP_MIN_VL <= m["vl"] <= LP_MAX_VL:
        score += 15
    if 0.75 <= m["flow"] <= 1.25:
        score += 20
    elif LP_MIN_FLOW <= m["flow"] <= LP_MAX_FLOW:
        score += 12
    if 0.80 <= m["swap_speed"] <= 1.40:
        score += 15
    elif 0.60 <= m["swap_speed"] <= LP_MAX_SWAP_SPEED:
        score += 8
    if m["direction"] == "mixed":
        score += 5
    elif m["direction"] == "bullish":
        score += 10
    else:
        score -= 25
    if 0.35 <= m["buy_share"] <= 0.65:
        score += 10
    return max(0, min(score, 100))


def build_reports():
    from datetime import datetime, timezone

    hits_by_chain = {
        chain: [t for t in gather(trend_command(chain)) if safe_for_dlmm(t)]
        for chain in RADAR_CHAINS
    }

    def rank_key(t):
        vol = float(t.get("volume") or 0)
        liq = float(t.get("liquidity") or 0)
        return (vol / liq if liq > 0 else 0, vol)

    for hits in hits_by_chain.values():
        hits.sort(key=rank_key, reverse=True)
    # The Signal board displays at most ten names per chain. Analyze only that
    # top slice so token-info and K-line requests do not consume the API
    # budget on names that will never be displayed.
    analysis_hits_by_chain = {
        chain: hits_by_chain[chain][:SIGNAL_ANALYSIS_LIMIT]
        for chain in RADAR_CHAINS
    }
    all_hits = [
        t for chain in RADAR_CHAINS for t in analysis_hits_by_chain[chain]
    ]
    price_by_address = token_price_map(all_hits)

    def snapshot_for(t):
        return price_by_address.get((t.get("chain"), t.get("address")))

    metrics = {
        id(t): analyze_token(t, snapshot_for(t))
        for t in all_hits
    }

    def metric(t):
        return metrics[id(t)]

    try:
        local_tz = ZoneInfo(RADAR_TIMEZONE)
    except ZoneInfoNotFoundError:
        local_tz = timezone.utc
    local_time = datetime.now(local_tz).strftime("%H:%M")

    def label(t):
        return html_escape((t.get("symbol") or "?")[:14])

    def chain_title(chain):
        return {
            "sol": "SOL",
            "bsc": "BSC",
            "base": "BASE",
            "eth": "ETH",
            "arbitrum": "ARBITRUM",
            "hyperevm": "HYPEREVM",
            "robinhood": "ROBINHOOD",
            "arc": "ARC",
            "stable": "STABLE",
        }.get(chain, chain.upper())

    def signal_report():
        lines = [f"GMGN V/L — {local_time} {RADAR_LOCATION}", ""]

        def add_section(title, hits):
            lines.extend([title, "SYM      V/L  S1H  S5M  S×    MC    FLOW", "-" * 49])
            if not hits:
                lines.append("none")
            for t in hits[:SIGNAL_ANALYSIS_LIMIT]:
                m = metric(t)
                row = (
                    f"{(t.get('symbol') or '?')[:7]:<7} {m['vl']:>4.1f} "
                    f"{m['swaps_1h']:>4} {m['swaps_5m']:>4} {m['swap_speed']:>4.1f} "
                    f"{money(t.get('market_cap')):>5} {m['flow_label']}"
                )
                ca = " ".join(str(t.get("address") or "-").split())
                lines.extend([row, "CA:", ca])

        for chain in RADAR_CHAINS:
            add_section(chain_title(chain), hits_by_chain[chain])

        lines.extend([
            "",
            "V/L",
            "1h volume / liquidity.",
            "Higher = faster potential fee velocity.",
            "",
            "S×",
            "(5m swaps × 12) / rolling 1h swaps.",
            "≥1.3 accelerating   ≥2.0 explosive",
            "",
            "FLOW",
            "🔥 hot   🟢 active   🟡 cooling   🧊 cold",
            "📈 bullish  📉 bearish  🔄 mixed/chop",
            "",
            "RULE",
            "MAX HOLD 1 HOUR.",
            "Get in, get out, then rotate to next pool.",
        ])
        return f"<pre>{html_escape(chr(10).join(lines))}</pre>"

    def watch_report():
        lines = [f"<b>👀 WATCH — kandidat pantauan</b> · {local_time} {html_escape(RADAR_LOCATION)}", ""]
        for chain in RADAR_CHAINS:
            hits = analysis_hits_by_chain[chain]
            rows = []
            for t in hits:
                m = metric(t)
                score = watch_score(m)
                if score >= WATCH_MIN_SCORE and m["direction"] != "bearish":
                    rows.append((score, t, m))
            rows.sort(key=lambda row: row[0], reverse=True)
            lines.append(f"<b>{chain_title(chain)}</b>")
            if not rows:
                lines.append("none")
            for score, t, m in rows[:8]:
                lines.append(
                    f"<b>{label(t)}</b> · WATCH {score}/100 · V/L {m['vl']:.1f} · "
                    f"S× {m['swap_speed']:.1f} · MC {money(t.get('market_cap'))} · "
                    f"{html_escape(m['flow_label'])}"
                )
                lines.append(f"CA: {ca_markup(t)} · {chart_markup(t)}")
                lines.append("")
        lines.extend([
            "<i>Watch menandai aktivitas yang layak dipantau; belum otomatis berarti aman dibeli.</i>",
        ])
        return "\n".join(lines)

    def lp_report():
        lines = [f"<b>💧 LP — fee capture watchlist</b> · {local_time} {html_escape(RADAR_LOCATION)}", ""]
        for chain in RADAR_CHAINS:
            hits = analysis_hits_by_chain[chain]
            rows = []
            for t in hits:
                m = metric(t)
                score = lp_score(m)
                if (
                    score >= LP_MIN_SCORE
                    and m["liquidity"] >= LP_MIN_LIQUIDITY
                    and LP_MIN_VL <= m["vl"] <= LP_MAX_VL
                    and LP_MIN_FLOW <= m["flow"] <= LP_MAX_FLOW
                    and m["swap_speed"] <= LP_MAX_SWAP_SPEED
                    and m["direction"] != "bearish"
                ):
                    rows.append((score, t, m))
            rows.sort(key=lambda row: row[0], reverse=True)
            lines.append(f"<b>{chain_title(chain)}</b>")
            if not rows:
                lines.append("none")
            for score, t, m in rows[:8]:
                lines.append(
                    f"<b>{label(t)}</b> · LP {score}/100 · Liq ${money(m['liquidity'])} · "
                    f"V/L {m['vl']:.1f} · MC {money(t.get('market_cap'))} · "
                    f"FLOW {html_escape(m['flow_label'])} · S× {m['swap_speed']:.1f}"
                )
                lines.append(f"CA: {ca_markup(t)} · {chart_markup(t)}")
                lines.append("")
        lines.extend([
            "<b>LP rule</b>",
            "Likuiditas besar + aktivitas stabil + sell pressure tidak dominan.",
            "Signal ini belum menghitung APR fee, impermanent loss, atau fee tier DEX.",
            "Selalu cek pool dan fee tier sebelum menaruh dana.",
        ])
        return "\n".join(lines)

    return {
        "signal": signal_report(),
        "watch": watch_report(),
        "lp": lp_report(),
        "radar_candidates": hits_by_chain,
    }


def build_volume_candidates(radar_candidates, deadline):
    """Discover volume candidates only after the SIGNAL cooldown."""
    candidates = {}
    for chain in VOLUME_CHAINS:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            candidates[chain] = []
            continue

        # Reuse the SIGNAL universe when both jobs scan the same chain. This
        # avoids a duplicate trending request in the same five-minute cycle.
        if chain in radar_candidates:
            hits = list(radar_candidates[chain])
        else:
            hits = [
                t for t in gather(
                    trend_command(chain), timeout=min(60, max(1, int(remaining)))
                )
                if safe_for_dlmm(t)
            ]

        hits.sort(
            key=lambda t: (
                float(t.get("volume") or 0)
                / float(t.get("liquidity") or 1)
                if float(t.get("liquidity") or 0) > 0 else 0,
                float(t.get("volume") or 0),
            ),
            reverse=True,
        )
        candidates[chain] = (
            hits[:VOLUME_SCAN_LIMIT]
            if VOLUME_SCAN_LIMIT > 0 else hits
        )
    return candidates


def load_volume_state():
    """Load resumable volume cursor and alert cooldown state."""
    default = {"next_chain": 0, "token_indices": {}, "last_alerts": {}}
    try:
        state = json.loads(VOLUME_STATE_PATH.read_text())
        if not isinstance(state, dict):
            return default
        state.setdefault("next_chain", 0)
        state.setdefault("token_indices", {})
        state.setdefault("last_alerts", {})
        return state
    except Exception:
        return default


def save_volume_state(state):
    """Persist volume cursor atomically so the next cycle can resume."""
    try:
        VOLUME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = VOLUME_STATE_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, indent=2) + "\n")
        temp_path.replace(VOLUME_STATE_PATH)
    except Exception as exc:
        print(f"volume-state=FAIL {exc}", file=sys.stderr)


def scan_volume_spikes(candidates, deadline, chains=None):
    """Scan candidates round-robin until the next radar slot is due."""
    chains = tuple(chains or VOLUME_CHAINS)
    if not chains:
        return [], 0, False
    state = load_volume_state()
    if not any(candidates.get(chain) for chain in chains):
        return [], 0, False

    try:
        next_chain = int(state.get("next_chain", 0)) % len(chains)
    except (TypeError, ValueError):
        next_chain = 0
    token_indices = state.get("token_indices", {})
    last_alerts = state.get("last_alerts", {})
    now = int(time.time())
    active_alerts = {}
    for key, value in last_alerts.items():
        sent_at = value.get("sent_at") if isinstance(value, dict) else value
        if (
            isinstance(sent_at, (int, float))
            and now - sent_at < VOLUME_ALERT_COOLDOWN_SECONDS
        ):
            active_alerts[key] = value
    last_alerts = active_alerts

    matches = []
    scanned = 0
    empty_chains = 0
    preempted = False

    try:
        while time.monotonic() < deadline:
            chain = chains[next_chain]
            hits = candidates.get(chain, [])
            if not hits:
                empty_chains += 1
                next_chain = (next_chain + 1) % len(chains)
                if empty_chains >= len(chains):
                    break
                continue

            empty_chains = 0
            try:
                token_index = int(token_indices.get(chain, 0)) % len(hits)
            except (TypeError, ValueError):
                token_index = 0
            token = hits[token_index]
            token_indices[chain] = (token_index + 1) % len(hits)
            next_chain = (next_chain + 1) % len(chains)

            remaining = deadline - time.monotonic()
            if remaining <= 1:
                preempted = True
                break
            data = volume_spike_data(
                token,
                chain,
                timeout=min(20, max(1, int(remaining))),
            )
            scanned += 1
            if not data:
                continue
            status = volume_threshold_status(data)
            if not status:
                continue

            address = str(token.get("address") or "").strip()
            alert_key = f"{chain}:{address}"
            previous = last_alerts.get(alert_key)
            previous_status = (
                previous.get("status")
                if isinstance(previous, dict) else None
            )
            previous_sent_at = (
                previous.get("sent_at")
                if isinstance(previous, dict) else previous
            )
            same_status_in_cooldown = (
                previous_status == status["status"]
                and isinstance(previous_sent_at, (int, float))
                and now - previous_sent_at < VOLUME_ALERT_COOLDOWN_SECONDS
            )
            if not address or same_status_in_cooldown:
                continue
            last_alerts[alert_key] = {
                "sent_at": now,
                "status": status["status"],
            }
            matches.append({
                "chain": chain,
                "token": token,
                "data": data,
                **status,
            })
    finally:
        state["next_chain"] = next_chain
        state["token_indices"] = token_indices
        state["last_alerts"] = last_alerts
        save_volume_state(state)

    if time.monotonic() >= deadline:
        preempted = True
    return matches, scanned, preempted


def build_volume_report(matches, chains=None):
    """Build a Watch-style board containing only volume spike matches."""
    from datetime import datetime, timezone

    try:
        local_tz = ZoneInfo(RADAR_TIMEZONE)
    except ZoneInfoNotFoundError:
        local_tz = timezone.utc
    local_time = datetime.now(local_tz).strftime("%H:%M")
    chains = tuple(chains or VOLUME_CHAINS)
    by_chain = {chain: [] for chain in chains}
    for match in matches:
        by_chain.setdefault(match["chain"], []).append(match)

    lines = [
        f"<b>👀 VOLUME WATCH</b> · {local_time} {html_escape(RADAR_LOCATION)}",
        "",
    ]
    for chain in chains:
        rows = by_chain.get(chain, [])
        if not rows:
            continue
        for match in rows:
            token = match["token"]
            data = match["data"]
            if match["status"] == "both":
                status_label = "🔥 BOTH PASS"
            elif match["status"] == "15m":
                status_label = "⚡ 15M PASS"
            else:
                status_label = "🕐 1H PASS"
            v15_status = "✅" if match["pass_15m"] else "⚠️"
            v1h_status = "✅" if match["pass_1h"] else "⚠️"
            symbol = html_escape(str(token.get("symbol") or "?")[:20])
            lines.extend([
                f"<b>{symbol}</b> · {status_label} · {chain_title(chain)}",
                f"15M: {v15_status} {money(data['volume_15m'])} / "
                f"min {money(VOLUME_SPIKE_MIN_15M)}",
                f"1H: {v1h_status} {money(data['volume_1h'])} / "
                f"min {money(VOLUME_SPIKE_MIN_1H)}",
                f"SPIKE: {data['spike_ratio']:.2f}x · "
                f"Δ15M: {data['change_15m']:+.1f}%",
                f"MC: ${money(token.get('market_cap'))}",
                f"CA: {ca_markup(token)}",
                "",
            ])

    lines.extend([
        "",
        "<b>RULE</b>",
        "Alert jika V15 ATAU V1H memenuhi minimum.",
        "SPIKE = V15 / (V1H / 4), sebagai informasi tambahan.",
        "",
        "Volume besar bukan jaminan aman untuk LP.",
        "Cek liquidity, buy/sell pressure, dan wash trading sebelum masuk.",
    ])
    return "\n".join(lines)


def get_chat_id():
    # The radar must never guess a destination because another bot/group may
    # be using the same Telegram credentials. Configure the dedicated group
    # explicitly through TG_RADAR_GROUP_CHAT_ID.
    return CHAT_ID


def split_message(text, limit=3800):
    """Split at line boundaries below Telegram's 4096-character limit."""
    def split_lines(value):
        chunks = []
        current = []
        current_size = 0
        for line in value.splitlines():
            line_size = len(line) + 1
            if current and current_size + line_size > limit:
                chunks.append("\n".join(current))
                current = []
                current_size = 0
            current.append(line)
            current_size += line_size
        if current:
            chunks.append("\n".join(current))
        return chunks or [""]

    # Keep Telegram HTML entities valid when the compact Signal preformatted
    # report needs to be split into more than one message.
    if text.startswith("<pre>") and text.endswith("</pre>"):
        return [f"<pre>{chunk}</pre>" for chunk in split_lines(text[5:-6])]
    return split_lines(text)


def send(text, chat_id, thread_id=""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail}") from exc


def send_report(report, chat_id, thread_id=""):
    """Send one HTML report, split safely for Telegram's size limit."""
    parts = split_message(report)
    for part in parts:
        result = send(part, chat_id, thread_id)
        if not result.get("ok"):
            raise RuntimeError(str(result))
    return len(parts)


def send_signal_report(report, chat_id, thread_id=""):
    """Backward-compatible SIGNAL sender."""
    return send_report(report, chat_id, thread_id)


def send_watch_report(report, chat_id, thread_id=""):
    """Send the WATCH board when TG_SEND_WATCH is enabled."""
    if not SEND_WATCH:
        return 0
    return send_report(report, chat_id, thread_id)


def print_credit():
    """Print the project credit banner for terminal runs."""
    print("  *==========================================*")
    print("    > Built by: Noya-xen (Github)")
    print("    > Follow me on X : @xinomixo")
    print("  *==========================================*\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="GMGN radar with independent radar and volume chain lists"
    )
    parser.add_argument(
        "--chains",
        help="Radar/SIGNAL chains (overrides RADAR_CHAINS)",
    )
    parser.add_argument(
        "--volume-chains",
        help="Volume SPIKE chains (overrides VOLUME_CHAINS)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    cycle_started = time.monotonic()
    args = parse_args()
    if args.chains:
        RADAR_CHAINS = parse_chains(args.chains)
        CHAINS = RADAR_CHAINS
        if not args.volume_chains and "VOLUME_CHAINS" not in os.environ:
            VOLUME_CHAINS = RADAR_CHAINS
    if args.volume_chains:
        VOLUME_CHAINS = parse_chains(args.volume_chains)
    print_credit()
    reports = build_reports()
    cid = get_chat_id()
    if not cid:
        # Fallback: print radar output locally when Telegram is not configured.
        print(f"\n--- SIGNAL ---\n{reports['signal']}")
        if SEND_WATCH:
            print(f"\n--- WATCH ---\n{reports['watch']}")
        print("[TG_RADAR_GROUP_CHAT_ID is not configured]", file=sys.stderr)
    else:
        try:
            parts_sent = send_signal_report(reports["signal"], cid, SIGNAL_THREAD_ID)
            print(f"signal=sent({parts_sent} msg)")
            if SEND_WATCH:
                watch_parts_sent = send_watch_report(reports["watch"], cid, WATCH_THREAD_ID)
                print(f"watch=sent({watch_parts_sent} msg)")
            else:
                print("watch=disabled")
        except Exception as exc:
            print(f"telegram=FAIL {exc}")

    volume_deadline = cycle_started + max(
        0, RADAR_INTERVAL_SECONDS - VOLUME_SAFETY_SECONDS
    )
    remaining_before_cooldown = volume_deadline - time.monotonic()
    volume_candidates = {}
    matches = []
    scanned = 0
    preempted = False
    radar_candidates = reports["radar_candidates"]
    volume_can_reuse_radar = all(
        chain in radar_candidates for chain in VOLUME_CHAINS
    )
    if (
        volume_can_reuse_radar
        and not any(radar_candidates.get(chain) for chain in VOLUME_CHAINS)
    ):
        print("volume=skipped(no volume candidates)", file=sys.stderr)
    elif remaining_before_cooldown <= VOLUME_START_DELAY_SECONDS:
        print(
            "volume=skipped(no time after SIGNAL cooldown)",
            file=sys.stderr,
        )
    else:
        print(
            f"gmgn-cooldown=sleep({VOLUME_START_DELAY_SECONDS}s)"
        )
        time.sleep(max(0, VOLUME_START_DELAY_SECONDS))
        volume_candidates = build_volume_candidates(
            radar_candidates, volume_deadline
        )
        if not any(volume_candidates.get(chain) for chain in VOLUME_CHAINS):
            print("volume=skipped(no volume candidates)", file=sys.stderr)
        else:
            matches, scanned, preempted = scan_volume_spikes(
                volume_candidates, volume_deadline, VOLUME_CHAINS
            )
    print(
        f"volume=scanned({scanned}) matches({len(matches)}) "
        f"preempted({str(preempted).lower()})"
    )
    if matches:
        volume_report = build_volume_report(matches, VOLUME_CHAINS)
        if cid and VOLUME_THREAD_ID:
            try:
                parts_sent = send_report(volume_report, cid, VOLUME_THREAD_ID)
                print(f"volume=sent({parts_sent} msg)")
            except Exception as exc:
                print(f"volume=FAIL {exc}")
        elif cid:
            print("volume=SKIP TG_VOLUME_THREAD_ID is not configured", file=sys.stderr)
            print(f"\n--- VOLUME SPIKE ---\n{volume_report}")
        else:
            print(f"\n--- VOLUME SPIKE ---\n{volume_report}")

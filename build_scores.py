#!/usr/bin/env python3
"""
build_scores.py
----------------
Erzeugt fortlaufend Signale für das BTC-Dashboard und pflegt:
  - signals.csv            (im Repo-Root)
  - reports/YYYY-MM.csv    (Monats-CSV im Repo, für direkte RAW-Nutzung)
  - latest.json            (zuletzt berechnete Scores + UTC)

Eigenschaften:
  - Robuste CoinGecko-Calls mit Retries/Timeout
  - ISO-8601 UTC Timestamps ("YYYY-MM-DDTHH:MM:SSZ")
  - Strikte CSV-Sanitisierung (Header, Spaltenanzahl, Typen)
  - Konfigurierbar via ENV:
      W_ETF      (default 0.6)
      W_STABLES  (default 0.3)
      W_STRESS   (default 0.1)
      PAIR       (default "BTC/CHF")
      ACTION     (default "hold")
      NOTE       (default "coingecko-auto")
      VS_CCY     (default "usd")
      STABLE_IDS (comma list; default "tether,usd-coin,dai")

Voraussetzungen:
  - Python 3.x
  - pip install requests
Läuft ideal im GitHub Action Workflow.
"""

from __future__ import annotations
import os
import csv
import json
import time
import math
import statistics as stats
import datetime as dt
from typing import Dict, Any, List, Tuple, Optional

import requests

# ---------------------
# Konfiguration & Const
# ---------------------
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
TIMEOUT_SEC = 20
RETRIES = 3
SLEEP_BETWEEN_RETRIES = 2.0  # Sekunden

HEADER = [
    "timestamp","pair","action","leverage","confidence","note",
    "Score_ETF","Score_Stables","Score_Stress","Score_Gewichtet"
]

# ---------------------
# ENV Parameter
# ---------------------
W_ETF     = float(os.getenv("W_ETF",     "0.6"))
W_STABLES = float(os.getenv("W_STABLES", "0.3"))
W_STRESS  = float(os.getenv("W_STRESS",  "0.1"))

PAIR   = os.getenv("PAIR",   "BTC/CHF")
ACTION = os.getenv("ACTION", "hold")
NOTE   = os.getenv("NOTE",   "coingecko-auto")

VS_CCY = os.getenv("VS_CCY", "usd").lower()
STABLE_IDS = [s.strip() for s in os.getenv("STABLE_IDS", "tether,usd-coin,dai").split(",") if s.strip()]

# ---------------------
# Hilfsfunktionen
# ---------------------
def utc_now_iso() -> str:
    """UTC Zeit als ISO-8601 (Z)"""
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def to_fixed(x: float, n: int = 6) -> str:
    return f"{float(x):.{n}f}"

def norm01(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))

def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """HTTP GET mit Retries/Timeout"""
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT_SEC)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
            time.sleep(SLEEP_BETWEEN_RETRIES)
    raise RuntimeError(f"GET failed after {RETRIES} attempts: {url} params={params} err={last_exc}")

def is_timestamp(x: str) -> bool:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt.datetime.strptime(x, fmt)
            return True
        except Exception:
            pass
    return False

def is_int(x: str) -> bool:
    try:
        int(x); return True
    except Exception:
        return False

def is_float(x: str) -> bool:
    try:
        float(x); return True
    except Exception:
        return False

def load_and_clean_csv(path: str) -> List[List[str]]:
    """
    CSV robust lesen; nur gültige Datenzeilen (10 Spalten, Types ok) zurückgeben.
    Header wird ignoriert (wir schreiben später neu).
    """
    rows: List[List[str]] = []
    if not os.path.exists(path):
        return rows
    with open(path, newline='') as f:
        r = csv.reader(f)
        first = True
        for raw in r:
            if first:
                first = False
                continue
            if len(raw) != 10:
                continue
            ts, pair, action, lev, conf, note, s_etf, s_stb, s_str, s_w = raw
            if not is_timestamp(ts):      continue
            if not is_int(lev):           continue
            if not is_float(conf):        continue
            if not is_float(s_etf):       continue
            if not is_float(s_stb):       continue
            if not is_float(s_str):       continue
            if not is_float(s_w):         continue
            rows.append([
                ts, pair, action, lev, conf, note, s_etf, s_stb, s_str, s_w
            ])
    return rows

def write_csv(path: str, rows: List[List[str]]) -> None:
    """Header + rows schreiben (überschreibt Datei)."""
    with open(path, "w", newline='') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

# ---------------------
# Datenbeschaffung
# ---------------------
def fetch_btc_market(days: int = 30, vs: str = VS_CCY) -> Dict[str, Any]:
    """
    Liefert Preis- & Volumen-Listen (täglich).
    Format:
      {
        'prices': [[ts_ms, value], ...],
        'market_caps': [[ts_ms, value], ...],
        'total_volumes': [[ts_ms, value], ...]
      }
    """
    url = f"{COINGECKO_BASE}/coins/bitcoin/market_chart"
    return get_json(url, params={"vs_currency": vs, "days": days, "interval": "daily"})

def fetch_stable_caps(ids: List[str] = STABLE_IDS) -> Tuple[float, float]:
    """
    Liefert (sum_market_cap, sum_market_cap_change_24h) für die angegebenen Stablecoins.
    Fällt bei fehlenden Feldern unkritisch auf 0.0 zurück.
    """
    if not ids:
        return 0.0, 0.0
    url = f"{COINGECKO_BASE}/coins/markets"
    j = get_json(url, params={
        "vs_currency": "usd",  # Stable-Mcap sinnvoll in USD
        "ids": ",".join(ids),
        "price_change_percentage": "24h"
    })
    total_cap = 0.0
    total_delta = 0.0
    for row in j:
        total_cap   += float(row.get("market_cap", 0.0) or 0.0)
        # nicht jedes Asset liefert market_cap_change_24h → weich behandeln
        total_delta += float(row.get("market_cap_change_24h", 0.0) or 0.0)
    return total_cap, total_delta

# ---------------------
# Score-Berechnung
# ---------------------
def compute_scores() -> Dict[str, float]:
    # BTC Momentum + Volumen-Impuls (30 Tage)
    market = fetch_btc_market(days=30, vs=VS_CCY)
    closes = [p[1] for p in market.get("prices", [])]
    vols   = [v[1] for v in market.get("total_volumes", [])]
    if len(closes) < 15 or len(vols) < 15:
        raise RuntimeError("Not enough BTC data from CoinGecko")

    sma_prev = stats.fmean(closes[-15:-1])
    sma_now  = stats.fmean(closes[-14:])
    mom = (sma_now - sma_prev) / max(1e-9, sma_prev)

    mean_v = stats.fmean(vols[-14:])
    std_v  = stats.pstdev(vols[-14:]) or 1.0
    z = (vols[-1] - mean_v) / std_v

    imp = 0.5 * mom + 0.5 * (z/2.0)
    score_etf = norm01(imp, -0.03, 0.03)

    # Stables: Veränderung der Gesamt-MarketCap (24h) relativ zur Summe
    cap, delta = fetch_stable_caps(STABLE_IDS)
    rel = (delta / max(1e-9, cap)) if cap > 0 else 0.0
    # Risikoappetit: fallende Dominanz (rel negativ) -> höherer Score
    score_stables = norm01(-rel, -0.01, 0.01)

    # Stress: Volatilität (StdAbw der Tagesrenditen)
    rets = [(closes[i] - closes[i-1]) / max(1e-9, closes[i-1]) for i in range(1, len(closes))]
    vol = abs(stats.pstdev(rets[-14:]) or 0.0)
    score_stress = norm01(vol, 0.005, 0.03)

    # Gewichtet
    score_weighted = max(0.0, min(1.0, W_ETF*score_etf + W_STABLES*score_stables + W_STRESS*score_stress))

    return {
        "Score_ETF":       round(score_etf, 6),
        "Score_Stables":   round(score_stables, 6),
        "Score_Stress":    round(score_stress, 6),
        "Score_Gewichtet": round(score_weighted, 6),
    }

# ---------------------
# Persistenz
# ---------------------
def build_new_row(scores: Dict[str, float]) -> List[str]:
    return [
        utc_now_iso(),
        PAIR,                # pair
        ACTION,              # action
        "0",                 # leverage
        to_fixed(scores["Score_Gewichtet"]),
        NOTE,                # note
        to_fixed(scores["Score_ETF"]),
        to_fixed(scores["Score_Stables"]),
        to_fixed(scores["Score_Stress"]),
        to_fixed(scores["Score_Gewichtet"]),
    ]

def write_signals_csv(path: str, new_row: List[str]) -> None:
    """signals.csv sanitisieren und neue Zeile anhängen (dann vollständig überschreiben)."""
    rows = load_and_clean_csv(path)
    rows.append(new_row)
    write_csv(path, rows)

def write_month_csv(new_row: List[str]) -> str:
    """reports/YYYY-MM.csv pflegen und Pfad zurückgeben."""
    ym = dt.datetime.utcnow().strftime("%Y-%m")
    reports_dir = "reports"
    ensure_dir(reports_dir)
    month_path = os.path.join(reports_dir, f"{ym}.csv")

    month_rows = load_and_clean_csv(month_path)
    # Dedupe auf (timestamp, pair, action) – zur Sicherheit
    seen: set[Tuple[str,str,str]] = {(r[0], r[1], r[2]) for r in month_rows}
    key = (new_row[0], new_row[1], new_row[2])
    if key not in seen:
        month_rows.append(new_row)
    write_csv(month_path, month_rows)
    return month_path

def write_latest_json(scores: Dict[str, float]) -> None:
    data = {"utc": utc_now_iso(), "scores": scores}
    with open("latest.json", "w") as f:
        json.dump(data, f, indent=2)

# ---------------------
# Main
# ---------------------
def main() -> None:
    scores = compute_scores()
    row = build_new_row(scores)

    write_signals_csv("signals.csv", row)
    month_path = write_month_csv(row)
    write_latest_json(scores)

    print("OK", {"utc": utc_now_iso(), "row": row, "month_csv": month_path})

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # harter Fail → Action schlägt fehl (sichtbar im Log)
        print("ERROR:", repr(e))
        raise

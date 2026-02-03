
#!/usr/bin/env python3
import os, sys, json, time, math, datetime as dt
from typing import Dict, Any

import requests

COINGECKO_BASE = 'https://api.coingecko.com/api/v3'

# -------- Helpers --------
def get_json(url: str, params: Dict[str, Any] | None = None):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def norm01(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))

# -------- Data fetch --------
def fetch_btc_market(days: int = 30, vs='usd'):
    # Daily prices for normalization
    url = f"{COINGECKO_BASE}/coins/bitcoin/market_chart"
    j = get_json(url, params={'vs_currency': vs, 'days': days, 'interval': 'daily'})
    # returns lists: prices, market_caps, total_volumes (timestamps, value)
    return j

def fetch_stable_caps(ids=('tether','usd-coin','dai')):
    # Simple current data with 24h change
    ids_str = ','.join(ids)
    url = f"{COINGECKO_BASE}/coins/markets"
    j = get_json(url, params={'vs_currency':'usd','ids':ids_str,'price_change_percentage':'24h'})
    # Build dict of market caps and 24h change in supply proxy via market cap change
    caps = {row['id']: float(row.get('market_cap',0.0)) for row in j}
    # coin gecko gives price change; for a rough proxy, we can use market_cap_change_24h if present
    # else compare to previous via /coins/{id}?localization=false & market_data=true, but keep it light
    # If not available, we approximate stables delta by summing market_cap_change_24h from row if exists
    deltas = {}
    for row in j:
        mc_change = row.get('market_cap_change_24h')
        deltas[row['id']] = float(mc_change) if mc_change is not None else 0.0
    total_cap = sum(caps.values())
    total_delta = sum(deltas.values())
    return total_cap, total_delta

# -------- Score builder --------
def compute_scores():
    # BTC momentum/vol impulse as ETF proxy
    market = fetch_btc_market(days=30)
    closes = [p[1] for p in market['prices']]
    vols = [v[1] for v in market['total_volumes']]
    if len(closes) < 15:
        raise RuntimeError('Not enough BTC data')
    # simple momentum: last SMA diff
    import statistics
    sma14_prev = statistics.fmean(closes[-15:-1])
    sma14_now  = statistics.fmean(closes[-14:])
    mom = (sma14_now - sma14_prev) / max(1e-9, sma14_prev)
    # volume impulse z-score (last vs mean/std)
    mean_v = statistics.fmean(vols[-14:])
    std_v  = statistics.pstdev(vols[-14:]) or 1.0
    z = (vols[-1] - mean_v)/std_v
    imp = 0.5*mom + 0.5*(z/2.0)
    score_etf = norm01(imp, -0.03, 0.03)

    # Stablecoin dominance proxy: total cap delta 24h normalized by cap
    cap, delta = fetch_stable_caps()
    rel = (delta / max(1e-9, cap))
    # invert: falling dominance => positive risk appetite
    score_stables = norm01(-rel, -0.01, 0.01)

    # Stress: ATR-like using daily returns std
    rets = []
    for i in range(1,len(closes)):
        rets.append((closes[i]-closes[i-1])/max(1e-9, closes[i-1]))
    import statistics as stats
    vol = abs(stats.pstdev(rets[-14:]) or 0.0)
    score_stress = max(0.0, min(1.0, norm01(vol, 0.005, 0.03)))

    # Weighted
    w_etf = float(os.getenv('W_ETF','0.6'))
    w_st  = float(os.getenv('W_STABLES','0.3'))
    w_sr  = float(os.getenv('W_STRESS','0.1'))
    score_weighted = max(0.0, min(1.0, w_etf*score_etf + w_st*score_stables + w_sr*score_stress))

    return {
        'Score_ETF': round(score_etf, 6),
        'Score_Stables': round(score_stables, 6),
        'Score_Stress': round(score_stress, 6),
        'Score_Gewichtet': round(score_weighted, 6),
    }


def append_csv(path='signals.csv', row: Dict[str,Any] | None = None):
    import csv, os
    exists = os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['timestamp','pair','action','leverage','confidence','note','Score_ETF','Score_Stables','Score_Stress','Score_Gewichtet'])
        w.writerow([
            dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'BTC/CHF','hold',0,
            row['Score_Gewichtet'],
            'coingecko-auto',
            row['Score_ETF'], row['Score_Stables'], row['Score_Stress'], row['Score_Gewichtet']
        ])


def main():
    scores = compute_scores()
    append_csv('signals.csv', scores)
    # also dump latest.json for debugging
    with open('latest.json','w') as f:
        json.dump({'utc': dt.datetime.utcnow().isoformat()+'Z', 'scores': scores}, f, indent=2)
    print('OK', scores)

if __name__ == '__main__':
    main()

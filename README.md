
# BTC Dashboard – Online Scores (No TradingView)

Dieses Repository erzeugt **Score_ETF, Score_Stables, Score_Stress, Score_Gewichtet** automatisch aus **CoinGecko**-Daten (kostenlos) und schreibt sie alle 30 Minuten in `signals.csv`. Die CSV kann dein Dashboard per `signals_http.url` direkt einlesen.

## So verwendest du es

1. Neues GitHub‑Repo erstellen (öffentlich).
2. Die Dateien aus diesem Paket committen:
   - `build_scores.py`
   - `.github/workflows/scores.yml`
3. (Optional) GitHub Pages aktivieren, wenn du eine hübsche HTML‑Ansicht willst. Für das Dashboard genügt der **RAW‑Link** zu `signals.csv`.
4. Warten bis der Workflow läuft (max. 1–2 Minuten). Danach findest du `signals.csv` im Repo.
5. **RAW‑URL** der CSV kopieren und in deinem Dashboard (`config/app.yaml`) eintragen:

```yaml
signals_http:
  url: "https://raw.githubusercontent.com/<USER>/<REPO>/main/signals.csv"
  format: "csv"
  timeout: 5.0
```

## Was genau berechnet wird
- **Score_ETF (Proxy):** BTC‑Momentum + Volumen‑Impuls (30‑Tage), aus CoinGecko (`/coins/bitcoin/market_chart`).
- **Score_Stables:** Summierte Mcap‑Änderung (24h) von USDT + USDC + DAI relativ zur Stables‑Mcap (Risikoappetit ~fallende Dominanz → höherer Score).
- **Score_Stress:** Volatilitäts‑Proxy auf Tagesrenditen (StdAbw der letzten 14), auf 0..1 normalisiert.
- **Score_Gewichtet:** `0.6*ETF + 0.3*Stables + 0.1*Stress` (via Env‑Variablen änderbar).

## Hinweise
- CoinGecko API ist **kostenlos**; Web‑Doku: https://www.coingecko.com/en/api  
- Das Workflow‑Intervall stellst du in `.github/workflows/scores.yml` (Cron) ein.
- `latest.json` wird zusätzlich geschrieben, um die letzten Werte leicht zu prüfen.

## Haftungsausschluss
Nur zu Demonstrationszwecken. Keine Finanzberatung.

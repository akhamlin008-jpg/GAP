"""
scanner_app.py  -- Streamlit UI wrapping the scanner engine.
============================================================
Same engine, same signals, same Alpaca/IEX data as scanner_engine.py.
The UI only changes how you READ and TRIGGER scans -- it does NOT change
scan speed. The bottleneck is the Alpaca round-trips; the browser adds ~ms.

WHY CACHING MATTERS HERE:
  Streamlit reruns the whole file on every click. Without caching, switching
  signals would re-download the universe and re-scan every time. The
  @st.cache_data decorators below stop that: the universe is cached for the
  session, and each scan result is cached by its inputs so flipping back to a
  signal you already ran is instant.

RUN IT:
  pip install streamlit requests
  set APCA_API_KEY_ID / HZGzaoE2o53uvhjPG46Ac6nWPQoNzK65zXkpTKB2BcCV as environment variables first
  then:  streamlit run scanner_app.py
  (a browser tab opens automatically)

Not run live here; syntax-checked only. Free IEX data still underrepresents
thin pre-market micro-caps -- same caveat as the engine.
"""

import os
import json
import time
import requests
import streamlit as st

# ---------------- CONFIG (secrets from env only) ----------------
PKVYMUIHXDS2AUY3S6VAI56LCR    = os.environ.get("APCA_API_KEY_ID")
HZGzaoE2o53uvhjPG46Ac6nWPQoNzK65zXkpTKB2BcCV = os.environ.get("HZGzaoE2o53uvhjPG46Ac6nWPQoNzK65zXkpTKB2BcCV")
DATA_FEED   = "iex"
UNIVERSE_CACHE = "sec_universe.json"
SEC_USER_AGENT = "market-scanner akhamlin008@gmail.com"
BATCH = 200


# ---------------- ENGINE (unchanged logic) ----------------
@st.cache_data(ttl=86400)            # cache the universe for a day
def build_universe():
    if os.path.exists(UNIVERSE_CACHE):
        if (time.time() - os.path.getmtime(UNIVERSE_CACHE)) / 86400.0 < 7:
            with open(UNIVERSE_CACHE) as f:
                return json.load(f)
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    r.raise_for_status()
    tickers = sorted({e["ticker"].strip().upper().replace(".", "-")
                      for e in r.json().values() if e.get("ticker")})
    with open(UNIVERSE_CACHE, "w") as f:
        json.dump(tickers, f)
    return tickers


def snapshot_batch(symbols):
    url = "https://data.alpaca.markets/v2/stocks/snapshots"
    headers = {"APCA-API-KEY-ID": PKVYMUIHXDS2AUY3S6VAI56LCR, "APCA-API-SECRET-KEY": APCA_SECRET}
    params = {"symbols": ",".join(symbols), "feed": DATA_FEED}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            return {}
        return r.json().get("snapshots", r.json())
    except Exception:
        return {}


# ---------------- SIGNALS ----------------
def gap_up(snap):
    p = (snap.get("latestTrade") or {}).get("p")
    pc = (snap.get("prevDailyBar") or {}).get("c")
    return (p - pc) / pc * 100.0 if p and pc else None

def momentum_open(snap):
    p = (snap.get("latestTrade") or {}).get("p")
    o = (snap.get("dailyBar") or {}).get("o")
    return (p - o) / o * 100.0 if p and o else None

def rel_volume(snap):
    t = (snap.get("dailyBar") or {}).get("v")
    pv = (snap.get("prevDailyBar") or {}).get("v")
    return (t / pv) * 100.0 if t and pv else None

SIGNALS = {
    "Gap Up (vs prior close)": ("gap_up", gap_up, "GAP %"),
    "Momentum (vs today open)": ("momentum_open", momentum_open, "MOVE %"),
    "Rel Volume (today vs yest, crude)": ("rel_volume", rel_volume, "RVOL x100"),
}


# ---------------- SCAN (cached by inputs) ----------------
@st.cache_data(ttl=60, show_spinner=False)
def run_scan(signal_key, min_price, max_price, min_vol, top_n, _stamp):
    """_stamp lets the user force a fresh scan; otherwise reuses cache for 60s."""
    _, fn, _ = SIGNALS[signal_key]
    universe = build_universe()
    rows = []
    progress = st.progress(0.0, text="Scanning...")
    for i in range(0, len(universe), BATCH):
        snaps = snapshot_batch(universe[i:i + BATCH])
        for sym, snap in snaps.items():
            if not isinstance(snap, dict):
                continue
            price = (snap.get("latestTrade") or {}).get("p")
            vol = (snap.get("dailyBar") or {}).get("v")
            if not price or not (min_price <= price <= max_price):
                continue
            if vol is not None and vol < min_vol:
                continue
            val = fn(snap)
            if val is None:
                continue
            rows.append({"Symbol": sym, "Value": round(val, 1),
                         "Price": round(price, 2), "Volume": vol})
        progress.progress(min(i + BATCH, len(universe)) / len(universe),
                          text=f"Scanning... {len(rows)} hits")
        time.sleep(0.4)
    progress.empty()
    rows.sort(key=lambda x: x["Value"], reverse=True)
    return rows[:top_n]


# ---------------- UI ----------------
st.set_page_config(page_title="Market Scanner", layout="wide")
st.title("Market Scanner")

if not PKVYMUIHXDS2AUY3S6VAI56LCR or not APCA_SECRET:
    st.error("Set APCA_API_KEY_ID and HZGzaoE2o53uvhjPG46Ac6nWPQoNzK65zXkpTKB2BcCV as environment "
             "variables, then restart: streamlit run scanner_app.py")
    st.stop()

with st.sidebar:
    st.header("Settings")
    signal_key = st.selectbox("Signal", list(SIGNALS.keys()))
    min_price = st.number_input("Min price", value=0.50, step=0.10)
    max_price = st.number_input("Max price", value=50.0, step=1.0)
    min_vol   = st.number_input("Min today volume", value=50_000, step=10_000)
    top_n     = st.slider("Show top N", 10, 100, 40)
    go = st.button("Run scan", type="primary")

st.caption("Free IEX data: pre-market and thin micro-caps are underrepresented. "
           "Validate against a chart before trading.")

if go:
    stamp = time.time()  # forces a fresh scan each button press
    results = run_scan(signal_key, min_price, max_price, min_vol, top_n, stamp)
    if not results:
        st.warning("No hits. Loosen filters or run during market hours.")
    else:
        _, _, val_header = SIGNALS[signal_key]
        st.subheader(f"{signal_key} — {len(results)} results")
        # rename Value column to the signal-specific header
        for r in results:
            r[val_header] = r.pop("Value")
        st.dataframe(results, use_container_width=True, hide_index=True)
else:
    st.info("Set your filters in the sidebar and click Run scan.")

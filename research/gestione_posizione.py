#!/usr/bin/env python3
"""One-off study (12/11/2026): does active management of a triggered setup change the mechanical
result of the published rules?

    python3 research/gestione_posizione.py        (needs network, about one minute)

Same entries as the rule replay (plan at 08:00 Rome on each of the last ~250 sessions); only the exit
changes. Variants: stop moved to break-even after +1R / +1.5R / +2R, profit taken at 1R / 1.5R / 2R
(or at T1 if nearer), stop trailed 1R / 1.5R behind the best price, no target at all (stop or session
close). Stop and target inside the same 5-minute bar count as a stop; a moved stop is active from the
following bar. Results are compared trade by trade with the base rule (paired difference +/- standard error).
"""
import math
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from xau import data, engine  # noqa: E402

t0 = time.time()
N = 250
h1 = data.duka_candles(interval="1HOUR", bars=12000)
m5 = data.duka_candles(interval="5MIN", bars=(N + 12) * 280, max_pages=40)
print("data", len(h1), len(m5), m5["time"].iloc[0], m5["time"].iloc[-1], f"{time.time()-t0:.0f}s", flush=True)

def simulate(s, fut, i0, entry_px, mode, k=1.0):
    d, stop0 = s["dir"], s["stop"]
    risk = abs(stop0 - entry_px)
    t1 = s["targets"][0]["price"]
    stop_cur, best = stop0, entry_px
    is_limit = s["type"] != "break"
    H, L, C = fut["high"].values, fut["low"].values, fut["close"].values
    tgt = t1
    if mode == "tp":            # take profit at k R (or T1 if nearer)
        alt = entry_px + d * k * risk
        tgt = alt if abs(alt - entry_px) < abs(t1 - entry_px) else t1
    mfe = 0.0
    for i in range(i0, len(fut)):
        entry_bar = is_limit and i == i0
        hit_stop = H[i] >= stop_cur if d < 0 else L[i] <= stop_cur
        if hit_stop:
            return d * (stop_cur - entry_px) / risk, mfe
        if mode != "hold" and not entry_bar:
            if (L[i] <= tgt if d < 0 else H[i] >= tgt):
                return d * (tgt - entry_px) / risk, max(mfe, d * (tgt - entry_px) / risk)
        if not entry_bar:
            best = min(best, L[i]) if d < 0 else max(best, H[i])
            mfe = d * (best - entry_px) / risk
            if mode == "be" and mfe >= k:
                stop_cur = entry_px
            elif mode == "trail" and mfe >= k:
                cand = best - d * k * risk
                stop_cur = min(stop_cur, cand) if d < 0 else max(stop_cur, cand)
    return d * (C[-1] - entry_px) / risk, mfe

VARS = [("base", "base", 0), ("be1.0", "be", 1.0), ("be1.5", "be", 1.5), ("be2.0", "be", 2.0),
        ("tp1.0", "tp", 1.0), ("tp1.5", "tp", 1.5), ("tp2.0", "tp", 2.0), ("trail1.0", "trail", 1.0),
        ("trail1.5", "trail", 1.5), ("hold", "hold", 0)]
rows, sessions = [], []
td = engine.tday_of(m5["time"])
days = sorted(pd.unique(td))
first_ok = m5["time"].iloc[0] + pd.Timedelta(days=4)
now = pd.Timestamp.now(tz="UTC")
for day in days:
    day = pd.Timestamp(day)
    b = engine.session_bounds(day)
    if b["open"] < first_ok or b["close"] > now or day.day_of_week >= 5:
        continue
    asof = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=8, tz="Europe/Rome").tz_convert("UTC")
    deadline = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=21, tz="Europe/Rome").tz_convert("UTC")
    sess = m5[(m5["time"] >= b["open"]) & (m5["time"] < b["close"])]
    if len(sess) < 200:
        continue
    try:
        A = engine.analyze(h1, m5, asof, with_stats=False)
    except Exception:
        continue
    sessions.append(day)
    fut = m5[(m5["time"] >= asof) & (m5["time"] < b["close"])].reset_index(drop=True)
    for s in A["scenarios"]:
        r = engine.evaluate_setup(s, m5, asof, deadline, b["close"])
        if r["entry_px"] is None:
            continue
        i0 = int(fut.index[fut["time"] == r["entry_time"]][0])
        rec = {"day": day, "role": s["role"], "type": s["type"], "ref": float(r["R"]), "status": r["status"]}
        for name, mode, k in VARS:
            R, mfe = simulate(s, fut, i0, r["entry_px"], "base" if mode == "base" else mode, k)
            rec[name] = R
            if name == "hold":
                rec["mfe_hold"] = mfe
            if name == "base":
                rec["mfe"] = mfe
        rows.append(rec)
df = pd.DataFrame(rows)
df = df[df["day"].isin(sessions[-N:])].reset_index(drop=True)          # same window as the rule replay
print(f"sessions {len(sessions[-N:])} ({sessions[-N]:%d/%m/%Y} - {sessions[-1]:%d/%m/%Y}), with trades {df['day'].nunique()}, "
      f"trades {len(df)}  [{time.time()-t0:.0f}s]")
print("baseline check: max |sim-ref| =", float((df["base"] - df["ref"]).abs().max()))

def line(g, name):
    x = g[name]; n = len(x)
    diff = g[name] - g["base"]
    return (f"{name:9s} n={n:3d} mean={x.mean():+.3f} se={x.std(ddof=1)/math.sqrt(n):.3f} win={100*(x>0).mean():4.0f}% "
            f"loss={100*(x<0).mean():4.0f}%  diff_vs_base={diff.mean():+.3f} ± {diff.std(ddof=1)/math.sqrt(n):.3f}")
for label, g in (("ALL", df), ("pull-back", df[df["role"] == "con il trend"]), ("breakout", df[df["role"] == "con il trend · rottura"]),
                 ("range", df[df["role"] == "range"])):
    print(f"\n== {label}")
    if len(g) < 5:
        continue
    for name, _, _ in VARS:
        print(line(g, name))
# stability: first half vs second half for the main variants
h = len(df) // 2
for name in ("be1.0", "tp1.0", "trail1.0"):
    a, b_ = df.iloc[:h], df.iloc[h:]
    print(f"\nhalves {name}: first diff={ (a[name]-a['base']).mean():+.3f}  second diff={(b_[name]-b_['base']).mean():+.3f}")
# situation like today's: trades that at some point were >= +2R in favour without reaching T1 yet
g = df[(df["mfe"] >= 2.0) & (df["status"] != "target 1")]
print("\ntrades with MFE>=2R that did not reach T1:", len(g), "mean base R", g["base"].mean() if len(g) else None)
g2 = df[df["mfe"] >= 2.0]
print("trades with MFE>=2R (any):", len(g2), "status counts", g2["status"].value_counts().to_dict())
print("DONE", f"{time.time()-t0:.0f}s")

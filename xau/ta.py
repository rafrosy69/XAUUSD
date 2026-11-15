"""Technical indicators (pure pandas/numpy, Wilder-style smoothing where standard)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/n)."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ag, al = rma(up, n), rma(dn, n)
    rs = ag / al.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(al != 0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]),
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = rma(true_range(df), n)
    pdi = 100 * rma(plus_dm, n) / tr
    mdi = 100 * rma(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx.fillna(0.0), n)


def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[dict]:
    """Confirmed fractal pivots: a high (low) that is the extreme of `left` bars before and
    `right` bars after it. Only pivots with `right` completed bars after them are returned,
    so the function never looks ahead of the data it is given."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    t = df["time"].to_numpy()
    out = []
    n = len(df)
    for i in range(left, n - right):
        wh = h[i - left:i + right + 1]
        wl = l[i - left:i + right + 1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1:
            out.append({"i": i, "time": pd.Timestamp(t[i]).tz_localize("UTC") if pd.Timestamp(t[i]).tzinfo is None else pd.Timestamp(t[i]),
                        "price": float(h[i]), "type": "H"})
        if l[i] == wl.min() and (wl == l[i]).sum() == 1:
            out.append({"i": i, "time": pd.Timestamp(t[i]).tz_localize("UTC") if pd.Timestamp(t[i]).tzinfo is None else pd.Timestamp(t[i]),
                        "price": float(l[i]), "type": "L"})
    return out

"""Dependency-free SVG charts (inline in the HTML report, crisp at any zoom)."""
from __future__ import annotations

import html
import math

import pandas as pd

ROME = "Europe/Rome"
COL = {"bg": "#0f141b", "grid": "#1d2530", "axis": "#7d8a9a", "text": "#c5d0dc", "muted": "#66727f",
       "up": "#2ebd85", "down": "#f6465d", "gold": "#e3b341", "blue": "#58a6ff", "violet": "#b392f0",
       "orange": "#f0883e", "white": "#e6edf3"}
GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"


def fnum(x, d=1, sign=False) -> str:
    """Italian number format (decimal comma, typographic minus)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n.d."
    s = f"{abs(x):,.{d}f}".replace(",", " ").replace(".", ",").replace(" ", "")
    if x < 0 and float(f"{abs(x):.{d}f}") != 0:
        return "\u2212" + s
    return ("+" + s) if sign and float(f"{abs(x):.{d}f}") != 0 else s


def nice_step(span: float, target: int = 7) -> float:
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _spread(items: list[dict], min_gap: float, lo: float, hi: float) -> None:
    """Greedy vertical de-overlap of labels (in place): sets item['ly']."""
    items.sort(key=lambda d: d["y"])
    prev = None
    for it in items:
        y = max(it["y"], lo)
        if prev is not None and y - prev < min_gap:
            y = prev + min_gap
        it["ly"] = y
        prev = y
    over = (items[-1]["ly"] - hi) if items else 0
    if over > 0:                      # push the whole stack up from the bottom
        nxt = None
        for it in reversed(items):
            y = min(it["ly"], hi)
            if nxt is not None and nxt - y < min_gap:
                y = nxt - min_gap
            it["ly"] = y
            nxt = y


def candle_chart(df: pd.DataFrame, *, kind: str = "intraday", width: int = 1180, height: int = 440,
                 future: float = 0.0, overlays=(), hlines=(), bands=(), sessions=(), price=None,
                 y_include=(), tick_hours: int = 3, uid: str = "c", legend=(), markers=()) -> str:
    """Candlestick chart.
    overlays : [(label, values aligned to df, colour)]
    hlines   : [{price, label, color, dash}]      labelled on the right, inside the projection area
    bands    : [{lo, hi, color, label, from_future}]
    sessions : [(start_ts, end_ts, label, colour)] background shading (intraday only)
    markers  : [{time, dir, label, tip, color}]   entry arrows on the bar that contains `time`
    """
    n = len(df)
    nf = int(round(n * future))
    ml, mr, mt, mb = 8, 62, 22, 26
    pw, ph = width - ml - mr, height - mt - mb
    step = pw / (n + nf)
    cw = max(1.0, min(step * 0.66, 11))
    lo = float(df["low"].min())
    hi = float(df["high"].max())
    for v in y_include:
        if v is not None and math.isfinite(v):
            lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    X = lambda i: ml + (i + 0.5) * step
    Y = lambda p: mt + (hi - p) / (hi - lo) * ph
    tl = df["time"].dt.tz_convert(ROME)
    o: list[str] = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" '
             f'role="img" style="{FONT}">')
    o.append(f'<defs><clipPath id="clip-{uid}"><rect x="{ml}" y="{mt}" width="{pw}" height="{ph}"/></clipPath></defs>')
    o.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="{COL["bg"]}" rx="4"/>')
    # --- session shading
    tv = df["time"]
    for (s, e, label, colour) in sessions:
        idx = [i for i in range(n) if s <= tv.iloc[i] < e]
        if not idx:
            continue
        x0, x1 = X(idx[0]) - step / 2, X(idx[-1]) + step / 2
        o.append(f'<rect x="{x0:.1f}" y="{mt}" width="{x1 - x0:.1f}" height="{ph}" fill="{colour}" opacity="0.055"/>')
        if x1 - x0 > 44:
            o.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{mt + 12}" font-size="10" fill="{colour}" opacity="0.85" '
                     f'text-anchor="middle">{_esc(label)}</text>')
    # --- horizontal grid + price axis
    st = nice_step(hi - lo, 7)
    p = math.ceil(lo / st) * st
    while p <= hi:
        y = Y(p)
        o.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="{COL["grid"]}" stroke-width="1"/>')
        o.append(f'<text x="{ml + pw + 6}" y="{y + 3.5:.1f}" font-size="10.5" fill="{COL["axis"]}">{fnum(p, 0)}</text>')
        p += st
    # --- time axis (two passes: day / month labels have priority over minor ticks)
    ticks = []
    for i in range(n):
        t = tl.iloc[i]
        if kind == "daily":
            if i == 0 or t.month != tl.iloc[i - 1].month:
                ticks.append((i, f"{MESI[t.month - 1]} {str(t.year)[2:]}", True, i > 0))
            elif t.day_of_week == 0 and n <= 140:
                ticks.append((i, f"{t.day}", False, False))
        else:
            prev = tl.iloc[i - 1] if i else None
            if prev is None or t.date() != prev.date():
                ticks.append((i, f"{GIORNI[t.day_of_week]} {t.day:02d}", True, prev is not None))
            elif tick_hours and t.minute == 0 and t.hour % tick_hours == 0:
                ticks.append((i, f"{t.hour:02d}:00", False, False))
    placed: list[float] = []
    for strong_pass in (True, False):
        for (i, lab, strong, line) in ticks:
            if strong != strong_pass:
                continue
            x = X(i) - (step / 2 if line else 0)
            if line:
                o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{mt}" y2="{mt + ph}" stroke="#2b3644" stroke-width="1" stroke-dasharray="3 3"/>')
            xl = min(max(x, ml + 18), ml + pw - 16)
            need = (46 if kind != "daily" else 30) if not strong else 44
            if any(abs(xl - q) < need for q in placed):
                continue
            o.append(f'<text x="{xl:.1f}" y="{height - 8}" font-size="10.5" fill="{COL["text"] if strong else COL["axis"]}" '
                     f'text-anchor="middle" font-weight="{600 if strong else 400}">{lab}</text>')
            placed.append(xl)
    # --- zone bands
    xf = X(n) - step / 2 + 4                    # start of the projection area
    labels: list[dict] = []
    for b in bands:
        y0, y1 = Y(b["hi"]), Y(b["lo"])
        hgt = max(y1 - y0, 2.0)
        x0 = xf if b.get("from_future") else ml
        o.append(f'<g clip-path="url(#clip-{uid})">')
        if not b.get("from_future"):
            o.append(f'<rect x="{ml}" y="{y0:.1f}" width="{pw}" height="{hgt:.1f}" fill="{b["color"]}" opacity="0.07"/>')
        o.append(f'<rect x="{xf:.1f}" y="{y0:.1f}" width="{ml + pw - xf:.1f}" height="{hgt:.1f}" fill="{b["color"]}" opacity="0.22"/>')
        o.append('</g>')
        if b.get("label"):
            labels.append({"y": (y0 + y1) / 2, "text": b["label"], "color": b["color"], "col": 0, "bold": True})
    # --- horizontal reference lines
    for h in hlines:
        if not (lo <= h["price"] <= hi):
            continue
        y = Y(h["price"])
        fut_only = h.get("from_future")
        x0 = xf if fut_only else ml
        o.append(f'<line x1="{x0:.1f}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="{h["color"]}" '
                 f'stroke-width="{h.get("w", 1)}" stroke-dasharray="{h.get("dash", "4 3")}" opacity="{h.get("op", 0.75)}"/>')
        if h.get("label"):
            labels.append({"y": y, "text": h["label"], "color": h["color"], "col": h.get("col", 1), "bold": False})
    # --- candles
    o.append(f'<g clip-path="url(#clip-{uid})">')
    for i in range(n):
        r = df.iloc[i]
        up = r["close"] >= r["open"]
        c = COL["up"] if up else COL["down"]
        x = X(i)
        yo, yc, yh, yl = Y(r["open"]), Y(r["close"]), Y(r["high"]), Y(r["low"])
        top, bh = min(yo, yc), max(abs(yc - yo), 1.0)
        t = tl.iloc[i]
        ts = f"{GIORNI[t.day_of_week]} {t.day:02d}/{t.month:02d}" + ("" if kind == "daily" else f" {t.hour:02d}:{t.minute:02d}")
        tip = f"{ts}  A {fnum(r['open'])}  Max {fnum(r['high'])}  Min {fnum(r['low'])}  C {fnum(r['close'])}"
        o.append(f'<g><title>{tip}</title><line x1="{x:.1f}" x2="{x:.1f}" y1="{yh:.1f}" y2="{yl:.1f}" stroke="{c}" stroke-width="1"/>'
                 f'<rect x="{x - cw / 2:.1f}" y="{top:.1f}" width="{cw:.1f}" height="{bh:.1f}" fill="{c}"/></g>')
    # --- overlays (moving averages)
    for (label, vals, colour) in overlays:
        pts = [f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals) if v is not None and math.isfinite(v)]
        if len(pts) > 1:
            o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{colour}" stroke-width="1.4" opacity="0.9"/>')
    o.append('</g>')
    # --- entry markers: arrow above the bar for a short, below the bar for a long
    for m in markers:
        idx = [i for i in range(n) if tv.iloc[i] <= m["time"]]
        if not idx or n < 2 or m["time"] >= tv.iloc[-1] + (tv.iloc[-1] - tv.iloc[-2]):
            continue
        i = idx[-1]
        x, c = X(i), m.get("color", COL["violet"])
        if m["dir"] < 0:
            y = Y(float(df["high"].iloc[i])) - 4
            pts, ty = f"{x:.1f},{y:.1f} {x - 5:.1f},{y - 9:.1f} {x + 5:.1f},{y - 9:.1f}", y - 12.5
        else:
            y = Y(float(df["low"].iloc[i])) + 4
            pts, ty = f"{x:.1f},{y:.1f} {x - 5:.1f},{y + 9:.1f} {x + 5:.1f},{y + 9:.1f}", y + 20.5
        o.append(f'<g><title>{_esc(m.get("tip", ""))}</title><polygon points="{pts}" fill="{c}" stroke="{COL["bg"]}" stroke-width="1"/>'
                 f'<text x="{x:.1f}" y="{ty:.1f}" font-size="10.5" font-weight="700" fill="{c}" text-anchor="middle" '
                 f'paint-order="stroke" stroke="{COL["bg"]}" stroke-width="3">{_esc(m.get("label", ""))}</text></g>')
    # --- last price line (below the label plates)
    if price is not None and lo <= price <= hi:
        y = Y(price)
        o.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="{COL["gold"]}" stroke-width="1" stroke-dasharray="2 2" opacity="0.9"/>')
    # --- labels in the projection area (two columns), de-overlapped, on an opaque plate
    for col in (0, 1):
        grp = [l for l in labels if l["col"] == col]
        if not grp:
            continue
        _spread(grp, 13.0, mt + 9, mt + ph - 5)
        for l in grp:
            w = 5.75 * len(l["text"]) + 7
            if col == 0:
                xx, anchor, rx = xf + 6, "start", xf + 2.5
            else:
                xx, anchor, rx = ml + pw - 5, "end", ml + pw - 1.5 - w
            o.append(f'<rect x="{rx:.1f}" y="{l["ly"] - 6.2:.1f}" width="{w:.1f}" height="12.4" rx="2.5" fill="{COL["bg"]}" opacity="0.9"/>')
            o.append(f'<text x="{xx:.1f}" y="{l["ly"] + 3.6:.1f}" font-size="10.5" fill="{l["color"]}" text-anchor="{anchor}" '
                     f'font-weight="{700 if l["bold"] else 500}">{_esc(l["text"])}</text>')
    # --- last price tag
    if price is not None and lo <= price <= hi:
        y = Y(price)
        o.append(f'<rect x="{ml + pw + 1}" y="{y - 8.5:.1f}" width="{mr - 2}" height="17" rx="3" fill="{COL["gold"]}"/>')
        o.append(f'<text x="{ml + pw + mr / 2:.1f}" y="{y + 4:.1f}" font-size="11" font-weight="700" fill="#161a1f" text-anchor="middle">{fnum(price)}</text>')
    # --- legend
    lx = ml + 8
    for (label, colour) in legend:
        o.append(f'<line x1="{lx}" x2="{lx + 16}" y1="{mt + ph - 10}" y2="{mt + ph - 10}" stroke="{colour}" stroke-width="2"/>')
        o.append(f'<text x="{lx + 21}" y="{mt + ph - 6.5}" font-size="10.5" fill="{COL["text"]}" paint-order="stroke" stroke="{COL["bg"]}" stroke-width="3">{_esc(label)}</text>')
        lx += 30 + 6.3 * len(label)
    o.append('</svg>')
    return "".join(o)


def hourly_chart(profile: list[dict], *, cur_hour: int | None, sess_of_hour: dict, events=(), width: int = 1180,
                 height: int = 250) -> str:
    """Average hourly range by hour of day (Rome time) with today's events on top."""
    ml, mr, mt, mb = 34, 10, 46, 30
    pw, ph = width - ml - mr, height - mt - mb
    mx = max(p["mean"] for p in profile) * 1.12 or 1
    bw = pw / 24
    colours = {"asia": COL["blue"], "london": COL["gold"], "ny": COL["violet"], "closed": COL["muted"]}
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" role="img" style="{FONT}">']
    st = nice_step(mx, 4)
    v = 0.0
    while v <= mx:
        y = mt + ph - v / mx * ph
        o.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="{COL["grid"]}"/>')
        o.append(f'<text x="{ml - 6}" y="{y + 3.5:.1f}" font-size="10.5" fill="{COL["axis"]}" text-anchor="end">{fnum(v, 0)}</text>')
        v += st
    for p in profile:
        h = p["hour"]
        x = ml + h * bw
        bh = p["mean"] / mx * ph
        c = colours[sess_of_hour.get(h, "closed")]
        cur = cur_hour is not None and h == cur_hour
        outline = 'stroke="#e6edf3" stroke-width="1.5"' if cur else ""
        o.append(f'<g><title>{h:02d}:00–{(h + 1) % 24:02d}:00  range medio {fnum(p["mean"])} $ (mediana {fnum(p["median"])} $)</title>'
                 f'<rect x="{x + 3:.1f}" y="{mt + ph - bh:.1f}" width="{bw - 6:.1f}" height="{max(bh, 0.5):.1f}" rx="2" fill="{c}" '
                 f'opacity="{1 if cur else 0.62}" {outline}/></g>')
        if p["mean"] > 0:
            o.append(f'<text x="{x + bw / 2:.1f}" y="{mt + ph - bh - 4:.1f}" font-size="9.5" fill="{COL["text"]}" text-anchor="middle">{fnum(p["mean"], 0)}</text>')
        o.append(f'<text x="{x + bw / 2:.1f}" y="{height - 12}" font-size="10.5" fill="{COL["white"] if cur else COL["axis"]}" '
                 f'text-anchor="middle" font-weight="{700 if cur else 400}">{h:02d}</text>')
    if cur_hour is not None:
        x = ml + cur_hour * bw + bw / 2
        o.append(f'<text x="{x:.1f}" y="{height - 1}" font-size="9" fill="{COL["white"]}" text-anchor="middle">adesso</text>')
    # events: marker + label rows (staggered)
    rows_end = [-1e9, -1e9]
    for ev in sorted(events, key=lambda e: e["hour_f"]):
        x = ml + ev["hour_f"] * bw
        c = COL["down"] if ev["rel"] >= 3 else (COL["orange"] if ev["rel"] == 2 else COL["muted"])
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{mt - 4}" y2="{mt + ph}" stroke="{c}" stroke-width="1" stroke-dasharray="2 3" opacity="0.8"/>')
        if ev.get("label"):
            w = 6.0 * len(ev["label"]) + 8
            row = 0 if x - w / 2 > rows_end[0] + 4 else (1 if x - w / 2 > rows_end[1] + 4 else None)
            if row is not None:
                yy = 12 + row * 14
                xa = min(max(x, ml + w / 2), ml + pw - w / 2)
                o.append(f'<text x="{xa:.1f}" y="{yy}" font-size="10.5" fill="{c}" text-anchor="middle" font-weight="600">{_esc(ev["label"])}</text>')
                rows_end[row] = xa + w / 2
    o.append(f'<text x="{ml}" y="{mt - 30}" font-size="0" fill="none">.</text>')
    o.append('</svg>')
    return "".join(o)


def gauge(score: float, width: int = 260, height: int = 34) -> str:
    """Bias gauge from -1 (bearish) to +1 (bullish)."""
    x = 10 + (score + 1) / 2 * (width - 20)
    c = COL["down"] if score <= -0.25 else (COL["up"] if score >= 0.25 else COL["gold"])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="gauge" role="img" style="{FONT}">'
            f'<defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="{COL["down"]}"/>'
            f'<stop offset="0.5" stop-color="#3a4452"/><stop offset="1" stop-color="{COL["up"]}"/></linearGradient></defs>'
            f'<rect x="10" y="10" width="{width - 20}" height="6" rx="3" fill="url(#gg)" opacity="0.85"/>'
            f'<line x1="{width / 2}" x2="{width / 2}" y1="6" y2="20" stroke="#8b98a8" stroke-width="1"/>'
            f'<circle cx="{x:.1f}" cy="13" r="6.5" fill="{c}" stroke="#0f141b" stroke-width="2"/>'
            f'<text x="10" y="31" font-size="9.5" fill="{COL["axis"]}">ribassista</text>'
            f'<text x="{width - 10}" y="31" font-size="9.5" fill="{COL["axis"]}" text-anchor="end">rialzista</text></svg>')

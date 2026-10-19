#!/usr/bin/env python3
"""Decompose a recorded touch session into a composite PNG and a text summary.

Input: the JSON that index.html posts to server.py (see README).
Output: <base>.png (dwell heatmap, stroke paths, continuity timeline, legends)
        <base>.txt (per-stroke numbers and plain-language labels)

Only dependency: Pillow.
"""
import datetime
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# ---- tunables ---------------------------------------------------------------
PANEL_W = 460           # rendered width of each screen-shaped panel, px
SLOW_PXS, FAST_PXS = 150.0, 500.0     # speed labels, CSS px per second
SPEED_SCALE_PXS = 750.0  # colour ramp top; fixed so images from different sessions compare
TAP_MAX_MS, TAP_MAX_TRAVEL = 200.0, 14.0
HOLD_MAX_TRAVEL = 24.0
HEAT_SIGMA_FRAC = 0.035  # heatmap blur radius as a fraction of screen width
MIN_W, MAX_W = 2.5, 15.0  # stroke width range (contact-area proxy), px

HEAT = [(0.00, (10, 8, 24)), (0.25, (85, 22, 115)), (0.50, (205, 50, 60)),
        (0.75, (252, 165, 35)), (1.00, (255, 250, 205))]
SPEED = [(0.0, (165, 25, 50)), (0.5, (250, 178, 48)), (1.0, (135, 218, 255))]
BG = (18, 18, 22)
PANEL_BG = (6, 6, 8)
INK = (225, 225, 230)
DIM = (140, 140, 150)
LANE_COLORS = [(140, 210, 255), (255, 170, 110), (170, 255, 150), (255, 140, 220), (240, 240, 120)]


# ---- helpers ----------------------------------------------------------------
def lerp(a, b, t):
    return a + (b - a) * t


def cmap(stops, t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(lerp(c0[i], c1[i], u))) for i in range(3))
    return stops[-1][1]


def font(size):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
                 "/system/fonts/Roboto-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def speed_label(v):
    return "slow" if v < SLOW_PXS else "moderate" if v < FAST_PXS else "fast"


def region(x, y, w, h):
    col = ["left", "center", "right"][min(2, int(3 * x / max(w, 1)))]
    row = ["upper", "middle", "lower"][min(2, int(3 * y / max(h, 1)))]
    return f"{row}-{col}" if not (row == "middle" and col == "center") else "center"


def compass(dx, dy):
    if dx == 0 and dy == 0:
        return "nowhere"
    a = math.degrees(math.atan2(dy, dx))  # screen coords: +y is down
    names = ["rightward", "down-right", "downward", "down-left", "leftward", "up-left", "upward", "up-right"]
    return names[int(((a + 22.5) % 360) // 45)]


# ---- stroke extraction and features -----------------------------------------
def build_strokes(events):
    open_, strokes = {}, []
    for e in events:
        k, pid = e.get("k"), e.get("id", 0)
        if k == "d":
            if pid in open_:
                strokes.append(open_.pop(pid))
            open_[pid] = {"pid": pid, "raw": [e]}
        elif k == "m":
            if pid in open_:
                open_[pid]["raw"].append(e)
        elif k in ("u", "c"):
            if pid in open_:
                s = open_.pop(pid)
                s["raw"].append(e)
                strokes.append(s)
    strokes.extend(open_.values())
    strokes.sort(key=lambda s: s["raw"][0]["t"])
    return strokes


def analyse_stroke(s, W, H):
    pts = []
    for e in s["raw"]:
        if pts and e["t"] <= pts[-1]["t"]:
            continue  # drop duplicate / out-of-order timestamps
        area = max(0.0, float(e.get("w", 0)) * float(e.get("h", 0)))
        pts.append({"x": float(e["x"]), "y": float(e["y"]), "t": float(e["t"]) / 1000.0, "area": area})
    if not pts:
        pts = [{"x": 0.0, "y": 0.0, "t": 0.0, "area": 0.0}]
    n = len(pts)
    # segment speeds, then per-sample speed with light smoothing
    seg = []
    for a, b in zip(pts, pts[1:]):
        dt = b["t"] - a["t"]
        d = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        seg.append(d / dt if dt > 0 else 0.0)
    raw = [seg[0] if seg else 0.0] + [(seg[i - 1] + seg[i]) / 2 for i in range(1, len(seg))] + ([seg[-1]] if seg else [])
    raw = raw[:n] if len(raw) >= n else raw + [0.0] * (n - len(raw))
    for i, p in enumerate(pts):
        lo, hi = max(0, i - 2), min(n, i + 3)
        p["v"] = sum(raw[lo:hi]) / (hi - lo)
        p["dt"] = (pts[i + 1]["t"] - p["t"]) if i + 1 < n else 0.0

    path = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(pts, pts[1:]))
    x0, y0, x1, y1 = pts[0]["x"], pts[0]["y"], pts[-1]["x"], pts[-1]["y"]
    net = math.hypot(x1 - x0, y1 - y0)
    dur = pts[-1]["t"] - pts[0]["t"]
    straight = net / path if path > 0 else 1.0

    # turning, reversals along the dominant axis, enclosed area; on a lightly decimated path
    turn, reversals, circ = 0.0, 0, 0.0
    dec = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p["x"] - dec[-1]["x"], p["y"] - dec[-1]["y"]) >= 6:
            dec.append(p)
    axis = "x"
    if len(dec) >= 3:
        xs = [p["x"] for p in dec]; ys = [p["y"] for p in dec]
        axis = "x" if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else "y"
        prev_sign, prev_ang = 0, None
        for a, b in zip(dec, dec[1:]):
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            ang = math.atan2(dy, dx)
            if prev_ang is not None:
                d = (ang - prev_ang + math.pi) % (2 * math.pi) - math.pi
                turn += d
            prev_ang = ang
            step = dx if axis == "x" else dy
            sign = 1 if step > 0 else -1 if step < 0 else 0
            if sign and prev_sign and sign != prev_sign:
                reversals += 1
            if sign:
                prev_sign = sign
        area2 = sum(a["x"] * b["y"] - b["x"] * a["y"] for a, b in zip(dec, dec[1:] + dec[:1]))
        circ = 4 * math.pi * abs(area2) / 2 / (path * path) if path > 0 else 0.0  # 1.0 for a perfect circle

    areas = [p["area"] for p in pts if p["area"] > 0]
    moving = [p["v"] for p in pts if p["dt"] > 0] or [0.0]
    mean_v = (sum(p["v"] * p["dt"] for p in pts) / dur) if dur > 0 else 0.0
    peak_v = max(moving)
    direction = compass(x1 - x0, y1 - y0)

    if dur * 1000 <= TAP_MAX_MS and path <= TAP_MAX_TRAVEL:
        kind = "tap"
    elif path <= HOLD_MAX_TRAVEL:
        kind = "hold"
    elif abs(turn) >= math.radians(300) and circ >= 0.15 and straight < 0.5:
        kind = "circular"
    elif reversals >= 2 and straight < 0.6:
        kind = "back-and-forth"
        direction = "side-to-side" if axis == "x" else "up-and-down"
    elif straight >= 0.85:
        kind = "straight drag"
    else:
        kind = "curved drag"

    s.update({
        "pts": pts, "dur": dur, "path": path, "net": net, "straight": straight,
        "turn_deg": math.degrees(turn), "reversals": reversals, "kind": kind,
        "mean_v": mean_v, "peak_v": peak_v,
        "area_mean": (sum(areas) / len(areas)) if areas else 0.0,
        "area_min": min(areas) if areas else 0.0, "area_max": max(areas) if areas else 0.0,
        "start_region": region(x0, y0, W, H), "end_region": region(x1, y1, W, H),
        "direction": direction, "circ": circ,
        "t0": pts[0]["t"], "t1": pts[-1]["t"],
    })
    return s


def assign_lanes(strokes):
    """Greedy lane assignment: concurrent strokes get different lanes (~fingers)."""
    lane_free_at = []
    for s in strokes:
        for i, free in enumerate(lane_free_at):
            if s["t0"] >= free:
                s["lane"] = i
                lane_free_at[i] = s["t1"]
                break
        else:
            s["lane"] = len(lane_free_at)
            lane_free_at.append(s["t1"])
    return len(lane_free_at)


# ---- drawing ----------------------------------------------------------------
def draw_heatmap(strokes, W, H, pw, ph):
    cell = 4.0
    gw, gh = max(1, int(math.ceil(W / cell))), max(1, int(math.ceil(H / cell)))
    grid = [0.0] * (gw * gh)
    sigma = max(1.0, HEAT_SIGMA_FRAC * W / cell)
    r = int(math.ceil(sigma * 2.5))
    kern = {}
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            kern[(dx, dy)] = math.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma))
    total = 0.0
    for s in strokes:
        for p in s["pts"]:
            wgt = p["dt"]
            if wgt <= 0:
                continue
            total += wgt
            cx, cy = int(p["x"] / cell), int(p["y"] / cell)
            for (dx, dy), k in kern.items():
                gx, gy = cx + dx, cy + dy
                if 0 <= gx < gw and 0 <= gy < gh:
                    grid[gy * gw + gx] += wgt * k
    mx = max(grid) if grid else 0.0
    peak = None
    if mx > 0:
        i = grid.index(mx)
        peak = ((i % gw) * cell + cell / 2, (i // gw) * cell + cell / 2)
    img = Image.new("L", (gw, gh))
    img.putdata([int(255 * (v / mx) ** 0.5) if mx > 0 else 0 for v in grid])
    pal = []
    for i in range(256):
        pal.extend(cmap(HEAT, i / 255.0))
    img = img.resize((pw, ph), Image.BILINEAR)
    img.putpalette(pal)
    return img.convert("RGB"), peak


def width_for(area, amin, amax, varies):
    if not varies or amax <= 0:
        return (MIN_W + MAX_W) / 2 * 0.8
    u = (area - amin) / (amax - amin) if amax > amin else 0.5
    return lerp(MIN_W, MAX_W, max(0.0, min(1.0, u)))


def draw_paths(strokes, W, H, pw, ph, vmax, amin, amax, varies):
    img = Image.new("RGB", (pw, ph), PANEL_BG)
    d = ImageDraw.Draw(img)
    sc = pw / W
    f_small = font(13)
    placed = []
    for s in strokes:
        pts = s["pts"]
        P = [(p["x"] * sc, p["y"] * sc) for p in pts]
        if s["kind"] in ("tap", "hold"):
            x, y = P[0]
            col = cmap(SPEED, 0.0)
            rad = 9 if s["kind"] == "tap" else min(26, 9 + 4 * s["dur"])
            d.ellipse([x - rad, y - rad, x + rad, y + rad], outline=col, width=3 if s["kind"] == "tap" else 2)
            if s["kind"] == "hold":
                wd = width_for(s["area_mean"], amin, amax, varies)
                d.ellipse([x - wd / 2, y - wd / 2, x + wd / 2, y + wd / 2], fill=col)
        else:
            for i in range(len(P) - 1):
                col = cmap(SPEED, pts[i]["v"] / vmax if vmax > 0 else 0)
                wd = width_for(pts[i]["area"], amin, amax, varies)
                d.line([P[i], P[i + 1]], fill=col, width=max(1, int(round(wd))))
                r = wd / 2
                d.ellipse([P[i + 1][0] - r, P[i + 1][1] - r, P[i + 1][0] + r, P[i + 1][1] + r], fill=col)
            # direction chevrons every ~60 px of rendered path
            acc, nxt = 0.0, 45.0
            for i in range(len(P) - 1):
                (ax, ay), (bx, by) = P[i], P[i + 1]
                seg = math.hypot(bx - ax, by - ay)
                if seg == 0:
                    continue
                while acc + seg >= nxt:
                    u = (nxt - acc) / seg
                    x, y = lerp(ax, bx, u), lerp(ay, by, u)
                    ux, uy = (bx - ax) / seg, (by - ay) / seg
                    L = 7
                    d.line([(x - ux * L - uy * L * 0.8, y - uy * L + ux * L * 0.8), (x, y),
                            (x - ux * L + uy * L * 0.8, y - uy * L - ux * L * 0.8)], fill=(255, 255, 255), width=2)
                    nxt += 70.0
                acc += seg
            sx, sy = P[0]
            d.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], outline=(255, 255, 255), width=2)
            ex, ey = P[-1]
            d.ellipse([ex - 4, ey - 4, ex + 4, ey + 4], fill=(255, 255, 255))
        # stroke number
        lx, ly = P[0]
        lx, ly = min(pw - 16, max(2, lx + 8)), min(ph - 16, max(2, ly - 18))
        while any(abs(lx - px) < 16 and abs(ly - py) < 16 for px, py in placed):
            ly += 16
        placed.append((lx, ly))
        d.rectangle([lx - 2, ly - 1, lx + 14, ly + 14], fill=(0, 0, 0))
        d.text((lx, ly), str(s["n"]), fill=LANE_COLORS[s["lane"] % len(LANE_COLORS)], font=f_small)
    return img


def draw_timeline(d, x0, y0, width, strokes, T, lanes, vmax, amin, amax, varies):
    f_small = font(12)
    lane_h = 44
    # time axis
    axis_y = y0 + 4
    step = 1.0 if T <= 15 else 5.0 if T <= 90 else 15.0
    t = 0.0
    while t <= T + 1e-9:
        x = x0 + width * (t / T if T > 0 else 0)
        d.line([(x, axis_y), (x, axis_y + lanes * lane_h + 12)], fill=(45, 45, 52), width=1)
        d.text((x + 2, axis_y + lanes * lane_h + 12), f"{t:g}s", fill=DIM, font=f_small)
        t += step
    for ln in range(lanes):
        cy = axis_y + 10 + ln * lane_h + lane_h / 2
        d.line([(x0, cy), (x0 + width, cy)], fill=(40, 40, 46), width=1)
        d.text((x0 - 58, cy - 7), f"finger {ln + 1}", fill=LANE_COLORS[ln % len(LANE_COLORS)], font=f_small)
    for s in strokes:
        cy = axis_y + 10 + s["lane"] * lane_h + lane_h / 2
        pts = s["pts"]
        xa = x0 + width * (s["t0"] / T if T > 0 else 0)
        xb = x0 + width * (s["t1"] / T if T > 0 else 0)
        if s["kind"] == "tap":
            col = cmap(SPEED, 0.0)
            d.rectangle([xa - 1, cy - 14, max(xb, xa + 2), cy + 14], fill=col)
        else:
            for i in range(len(pts) - 1):
                if pts[i]["dt"] <= 0:
                    continue
                a = x0 + width * (pts[i]["t"] / T)
                b = x0 + width * (pts[i + 1]["t"] / T)
                col = cmap(SPEED, pts[i]["v"] / vmax if vmax > 0 else 0)
                hh = width_for(pts[i]["area"], amin, amax, varies) * 1.3
                d.rectangle([a, cy - hh, max(b, a + 1), cy + hh], fill=col)
        d.text((xa, cy - 30), str(s["n"]), fill=LANE_COLORS[s["lane"] % len(LANE_COLORS)], font=f_small)
    return 4 + 10 + lanes * lane_h + 30


def gradient_bar(d, x, y, w, h, stops):
    for i in range(w):
        d.line([(x + i, y), (x + i, y + h)], fill=cmap(stops, i / max(1, w - 1)))


# ---- summary ----------------------------------------------------------------
def firmness(area, amin, amax, varies):
    if not varies:
        return "n/a"
    u = (area - amin) / (amax - amin) if amax > amin else 0.5
    return "light" if u < 0.33 else "medium" if u < 0.66 else "firm"


def summary_text(meta, strokes, W, H, T, lanes, contact, varies, amin, amax, peak):
    out = []
    out.append(f"Touch session {meta.get('started', '?')}")
    out.append(f"Canvas {W}x{H} CSS px ({'portrait' if H >= W else 'landscape'}). "
               f"Duration {T:.1f} s. Max simultaneous fingers: {lanes}.")
    if T > 0:
        out.append(f"In contact {contact:.1f} s ({100 * contact / T:.0f}%), lifted {T - contact:.1f} s.")
    if varies:
        out.append(f"Contact-area sensor: VARIES (range {amin:.0f}-{amax:.0f} px^2), so stroke width and "
                   f"'firmness' below are meaningful (bigger area = fingertip pressed flatter = firmer).")
    else:
        out.append("Contact-area sensor: constant on this device, so width/firmness carry no information.")
    if peak:
        out.append(f"Dwell heatmap peak: ({peak[0]:.0f},{peak[1]:.0f}) = {region(peak[0], peak[1], W, H)}.")
    out.append("")
    out.append(f"Strokes: {len(strokes)}  (speed in CSS px/s; screen width = {W} px; "
               f"slow < {SLOW_PXS:.0f}, moderate < {FAST_PXS:.0f}, fast beyond)")
    for s in strokes:
        line = (f"{s['n']:2d}. {s['kind']:<14} finger {s['lane'] + 1}  "
                f"{s['t0']:6.2f}-{s['t1']:6.2f}s  dur {s['dur']:5.2f}s")
        if s["kind"] == "tap":
            line += f"  at ({s['pts'][0]['x']:.0f},{s['pts'][0]['y']:.0f}) {s['start_region']}"
        elif s["kind"] == "hold":
            line += (f"  at ({s['pts'][0]['x']:.0f},{s['pts'][0]['y']:.0f}) {s['start_region']}"
                     f"  firmness {firmness(s['area_mean'], amin, amax, varies)}")
        else:
            line += (f"  path {s['path']:.0f}px  mean {s['mean_v']:.0f}px/s ({speed_label(s['mean_v'])})"
                     f"  peak {s['peak_v']:.0f}px/s"
                     f"  {s['start_region']} -> {s['end_region']} ({s['direction']})")
            if s["kind"] == "back-and-forth":
                line += f"  {s['reversals']} reversals"
            if s["kind"] == "circular":
                line += f"  {abs(s['turn_deg']) / 360:.1f} turns"
            line += f"  firmness {firmness(s['area_mean'], amin, amax, varies)}"
        out.append(line)
    # overall characterisation
    drags = [s for s in strokes if s["kind"] not in ("tap", "hold")]
    out.append("")
    if drags:
        mv = sum(s["mean_v"] * s["dur"] for s in drags) / max(1e-9, sum(s["dur"] for s in drags))
        out.append(f"Overall tempo: {speed_label(mv)} ({mv:.0f} px/s, about {mv / max(W, 1):.2f} screen-widths/s).")
    kinds = {}
    for s in strokes:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    out.append("Mix: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + ".")
    if len(strokes) >= 2 and T > 0:
        gaps = [b["t0"] - a["t1"] for a, b in zip(strokes, strokes[1:]) if b["t0"] >= a["t1"]]
        if gaps:
            out.append(f"Rhythm: {len(gaps)} lifts, mean gap {sum(gaps) / len(gaps):.2f}s, longest {max(gaps):.2f}s; "
                       f"{'mostly continuous' if contact / T > 0.75 else 'intermittent' if contact / T > 0.4 else 'sparse'}.")
    return "\n".join(out) + "\n"


# ---- main entry --------------------------------------------------------------
def render_session(data, base):
    canvas = data.get("canvas") or {}
    W, H = float(canvas.get("w") or 400), float(canvas.get("h") or 800)
    events = data.get("events") or []
    strokes = [analyse_stroke(s, W, H) for s in build_strokes(events)]
    for i, s in enumerate(strokes, 1):
        s["n"] = i
    lanes = assign_lanes(strokes)
    T = max((s["t1"] for s in strokes), default=0.0)
    contact = sum(s["dur"] for s in strokes)
    areas = [p["area"] for s in strokes for p in s["pts"] if p["area"] > 0]
    amin, amax = (min(areas), max(areas)) if areas else (0.0, 0.0)
    varies = amax > 0 and (amax - amin) / amax > 0.15
    speeds = sorted(p["v"] for s in strokes for p in s["pts"] if p["dt"] > 0)
    vmax = speeds[int(0.95 * (len(speeds) - 1))] if speeds else 1.0
    vmax = max(vmax, SPEED_SCALE_PXS)

    sc = PANEL_W / W
    pw, ph = PANEL_W, int(round(H * sc))
    M = 20
    img_w = M + pw + M + pw + M
    header_h, title_h, legend_h = 64, 26, 70
    tl_h = 4 + 10 + max(1, lanes) * 44 + 30
    img_h = header_h + title_h + ph + M + legend_h + M + 24 + tl_h + M
    img = Image.new("RGB", (img_w, img_h), BG)
    d = ImageDraw.Draw(img)
    f_title, f_body, f_small = font(20), font(14), font(12)

    heat, peak = draw_heatmap(strokes, W, H, pw, ph)
    paths = draw_paths(strokes, W, H, pw, ph, vmax, amin, amax, varies)

    # header
    d.text((M, 12), "touch session", fill=INK, font=f_title)
    d.text((M, 38), f"{data.get('started', '')}   screen {W:.0f}x{H:.0f}px   {T:.1f}s   "
                    f"{len(strokes)} strokes   contact {100 * contact / T if T > 0 else 0:.0f}%   "
                    f"fingers {lanes}", fill=DIM, font=f_body)
    # panels
    y = header_h
    d.text((M, y + 4), "DWELL  (where time was spent; bright = lingered)", fill=INK, font=f_body)
    d.text((M + pw + M, y + 4), "PATHS  (colour = speed, width = contact area, > direction, o start, * end)",
           fill=INK, font=f_small)
    y += title_h
    img.paste(heat, (M, y))
    img.paste(paths, (M + pw + M, y))
    d.rectangle([M - 1, y - 1, M + pw, y + ph], outline=(60, 60, 70))
    d.rectangle([M + pw + M - 1, y - 1, M + pw + M + pw, y + ph], outline=(60, 60, 70))
    y += ph + M
    # legends
    gradient_bar(d, M, y, 220, 14, SPEED)
    d.text((M, y + 18), f"slow (0)", fill=DIM, font=f_small)
    d.text((M + 150, y + 18), f"fast ({vmax:.0f} px/s)", fill=DIM, font=f_small)
    gradient_bar(d, M + 260, y, 160, 14, HEAT)
    d.text((M + 260, y + 18), "little dwell", fill=DIM, font=f_small)
    d.text((M + 360, y + 18), "much dwell", fill=DIM, font=f_small)
    xw = M + pw + M
    if varies:
        d.line([(xw, y + 7), (xw + 60, y + 7)], fill=INK, width=int(MIN_W))
        d.text((xw + 66, y), "light contact", fill=DIM, font=f_small)
        d.line([(xw, y + 30), (xw + 60, y + 30)], fill=INK, width=int(MAX_W))
        d.text((xw + 66, y + 22), f"firm contact (area {amin:.0f}-{amax:.0f} px^2)", fill=DIM, font=f_small)
    else:
        d.text((xw, y), "contact area constant on this device:", fill=DIM, font=f_small)
        d.text((xw, y + 18), "stroke width carries no information", fill=DIM, font=f_small)
    d.text((xw, y + 40), "ring = tap, ring+dot = hold (ring size = hold length)", fill=DIM, font=f_small)
    y += legend_h + M
    # timeline
    d.text((M, y), "CONTINUITY  (bars = finger down, gaps = lifted; colour = speed, thickness = contact area)",
           fill=INK, font=f_body)
    y += 24
    draw_timeline(d, M + 60, y, img_w - 2 * M - 60, strokes, max(T, 0.001), max(1, lanes), vmax, amin, amax, varies)

    png = base + ".png"
    txt = base + ".txt"
    img.save(png)
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write(summary_text(data, strokes, W, H, T, lanes, contact, varies, amin, amax, peak))
    return png, txt


def main(argv):
    if not argv:
        print("usage: render.py session.json [more.json ...]", file=sys.stderr)
        return 2
    for path in argv:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        base = os.path.splitext(path)[0]
        png, txt = render_session(data, base)
        print(png)
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

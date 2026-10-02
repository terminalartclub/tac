from rich.text import Text
from rich.style import Style
from rich.color import Color
import math
import random
import bisect

# chlorine

N = 300
W = width
H = height * 2
TAU = math.pi * 2

NW = 7
WSEED = 5
LMIN = 17.0
LMAX = 23.0
MSPEED = (2, -1, 2, -2, 1, -3, 2)
GAIN = 0.75
EPS = 0.08
LO = 0.28
CMAX = 8.0
CPOW = 1.6
CALM = 0.42
F_CALM = 125

AMB = 0.12
LAMP = 1.6
LFALL = 15.0
HOT = 0.7
LENS = 0.6
TOPF = 0.25
TOPL = 55.0
DEEP = (32, 118, 175)
SHALLOW = (45, 168, 166)
LK = 240.0
LS = 0.25
BLEACH = 0.06

LANE = 0.48
LANE_W = 0.75
RAILA = 0.9
SPILL = 0.27
SP_UP = 0.25
SP_DN = 0.40
SP_FALL = 9.0
WET = 1.0
SPILL_RGB = (240, 160, 128)

POOL_M = 5.5
FLOAT_OD = 1.1
FLOAT_ID = 0.4
MOON = (-0.42, -0.42, 0.80)
TANW = 0.43
SHADOW = 0.85
SLIDE = 0.35
TILT = 0.30
ROCK = 2.2
VALVE = 1.0
VINYL = (205, 22, 30)
GLOSS = 60
SPEC = 230

px0, px1 = round(W * 0.125), round(W * 0.675)
py0, py1 = -round(H * 0.08), round(H * 0.79)
pw, ph = px1 - px0, py1 - py0
RAD = 3.0
lane_x = px0 + pw / 2
LANE_T0, LANE_T1 = py0 + 9.5, py1 - 9.5
lamp_x, lamp_y = px0 + 0.5, round(H * 0.60)


def sdf_box(x, y, x0, y0, x1, y1, r):
    hx, hy = (x1 - x0) / 2 - r, (y1 - y0) / 2 - r
    qx = abs(x - (x0 + x1) / 2) - hx
    qy = abs(y - (y0 + y1) / 2) - hy
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def cover(d, half):
    v = half + 0.5 - d
    return 0.0 if v <= 0 else (1.0 if v >= 1 else v)


def smooth(a, b, v):
    t = min(1.0, max(0.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


def lampfield(x, y):
    dl = math.hypot(x - lamp_x, (y - lamp_y) * 0.8)
    return AMB + LAMP / (1.0 + (dl / LFALL) ** 2) + HOT * math.exp(-dl * dl / 12.0)


rail_ys = (py1 - 19, py1 - 12)
tread_x = px1 - 3.5


def albedo(fx, fy):
    dx = abs(fx - lane_x)
    c = cover(dx, LANE_W) if LANE_T0 <= fy <= LANE_T1 else 0.0
    for ty in (LANE_T0, LANE_T1):
        c = max(c, cover(abs(fy - ty), 0.5) * cover(dx, 4.5))
    a = 1.0 - LANE * c
    if rail_ys[0] < fy < rail_ys[1]:
        a *= 1.0 - 0.55 * cover(abs(fx - tread_x), 0.5)
    if px1 - 5.5 <= fx <= px1 - 1.5:
        for ry in rail_ys:
            a += RAILA * cover(abs(fy - ry), 0.35)
    return a


_r = random.Random(WSEED)
_base = _r.uniform(0, TAU)
waves = []
for i in range(NW):
    ang = _base + TAU * i / NW + _r.uniform(-0.35, 0.35)
    lam = _r.uniform(LMIN, LMAX)
    eta = _r.uniform(0.35, 0.55) * 5.0 / NW
    k = TAU / lam
    waves.append((math.cos(ang) * k, math.sin(ang) * k, eta, 0.4 * 5.0 / NW, MSPEED[i % len(MSPEED)], _r.uniform(0, TAU)))

pool = []
for y in range(max(0, py0), min(H, py1)):
    for x in range(px0, px1):
        if sdf_box(x + 0.5, y + 0.5, px0, py0, px1, py1, RAD) < 0:
            pool.append((x, y))
npool = len(pool)


def warp(x, y):
    return 1.4 * math.sin(x * 0.11 + y * 0.05 + 1.0) + 1.1 * math.sin(y * 0.09 - x * 0.04 + 2.0)


def warp_grad(x, y):
    gx = 0.154 * math.cos(x * 0.11 + y * 0.05 + 1.0) - 0.044 * math.cos(y * 0.09 - x * 0.04 + 2.0)
    gy = 0.07 * math.cos(x * 0.11 + y * 0.05 + 1.0) + 0.099 * math.cos(y * 0.09 - x * 0.04 + 2.0)
    return gx, gy


wtab = []
for kx, ky, eta, dlt, m, ph0 in waves:
    SA, CA, WXX, WYY, WXY, WGX, WGY = [], [], [], [], [], [], []
    k2 = kx * kx + ky * ky
    k1 = math.sqrt(k2)
    for x, y in pool:
        gx, gy = warp_grad(x, y)
        ex, ey = kx + gx * 0.6, ky + gy * 0.6
        A = kx * x + ky * y + warp(x, y) * 0.6 + ph0
        SA.append(math.sin(A))
        CA.append(math.cos(A))
        WXX.append(-eta * ex * ex / k2 * GAIN)
        WYY.append(-eta * ey * ey / k2 * GAIN)
        WXY.append(-eta * ex * ey / k2 * GAIN)
        WGX.append(dlt * ex / k1)
        WGY.append(dlt * ey / k1)
    wtab.append((m, SA, CA, WXX, WYY, WXY, WGX, WGY))

base = []
for x, y in pool:
    d = smooth(0.30, 0.78, (y - py0) / ph)
    tint = tuple(s + (dp - s) * d for s, dp in zip(SHALLOW, DEEP))
    lf = lampfield(x + 0.5, y + 0.5)
    edge = -sdf_box(x + 0.5, y + 0.5, px0, py0, px1, py1, RAD)
    wall = (0.70 + 0.30 * min(1.0, edge / 3.0)) * (TOPF + (1 - TOPF) * smooth(-6.0, TOPL, y))
    base.append((tint[0] * lf * wall, tint[1] * lf * wall, tint[2] * lf * wall))

RING_C = (px0 + pw * 0.68, H * 0.17)
RING_F, RING_T, RING_V = 105, 185, 0.30
RING_A = 1.5
RING_K = TAU / 7.0
RING_W = 4.5
ring_px = [(i, x + 0.5 - RING_C[0], y + 0.5 - RING_C[1]) for i, (x, y) in enumerate(pool)]
ring_px = sorted(((math.hypot(dx, dy), i, dx, dy) for i, dx, dy in ring_px))
ring_r = [p[0] for p in ring_px]

PPM = pw / POOL_M
F_RO = FLOAT_OD * PPM / 2
F_RI = FLOAT_ID * PPM / 2
F_RC = (F_RO + F_RI) / 2
F_A = (F_RO - F_RI) / 2
F_C = (px0 + pw * 0.34, H * 0.30)
F_PATH = (3.5, 6.0)
_ml = math.sqrt(MOON[0] ** 2 + MOON[1] ** 2 + MOON[2] ** 2)
LM = (MOON[0] / _ml, MOON[1] / _ml, MOON[2] / _ml)
_hl = math.sqrt(LM[0] ** 2 + LM[1] ** 2 + (LM[2] + 1) ** 2)
HM = (LM[0] / _hl, LM[1] / _hl, (LM[2] + 1) / _hl)
_sl = math.hypot(LM[0], LM[1])
SH_DIR = (-LM[0] / _sl, -LM[1] / _sl)
idx = {p: i for i, p in enumerate(pool)}

DECK = (9, 10, 15)
bgR = [[0.0] * W for _ in range(H)]
bgG = [[0.0] * W for _ in range(H)]
bgB = [[0.0] * W for _ in range(H)]

win_y0, win_y1 = round(H * 0.74), round(H * 0.86)

prints = []
_fa = (px1 + 3.0, (rail_ys[0] + rail_ys[1]) / 2 + 1.0)
_fb = (W - 2.0, (win_y0 + win_y1) / 2 - 1.0)
_fl = math.hypot(_fb[0] - _fa[0], _fb[1] - _fa[1])
_ux, _uy = (_fb[0] - _fa[0]) / _fl, (_fb[1] - _fa[1]) / _fl
_ns = max(2, int(_fl / 5.2))
for _k in range(_ns + 1):
    _t = _k / _ns
    _side = 0.9 if _k % 2 else -0.9
    prints.append((_fa[0] + (_fb[0] - _fa[0]) * _t - _uy * _side, _fa[1] + (_fb[1] - _fa[1]) * _t + _ux * _side,
                   0.55 + 0.45 * (1 - _t)))


def wet(x, y):
    v = 0.0
    for fx_, fy_, k in prints:
        du, dv = (x - fx_) * _ux + (y - fy_) * _uy, -(x - fx_) * _uy + (y - fy_) * _ux
        d = (du / 1.3) ** 2 + (dv / 0.75) ** 2
        if d < 1.6:
            v = max(v, k * min(1.0, 1.6 - d))
    return v * WET


def spill(x, y):
    dist = W - x
    if dist < 0 or dist > 26:
        return 0.0
    y0, y1 = win_y0 - dist * SP_UP, win_y1 + dist * SP_DN
    soft = 0.6 + dist * 0.10
    c = smooth(-soft, soft, y - y0) * smooth(-soft, soft, y1 - y)
    fall = math.exp(-dist / SP_FALL) + 1.2 * math.exp(-dist / 1.2)
    return c * fall


for y in range(H):
    for x in range(W):
        sd = sdf_box(x + 0.5, y + 0.5, px0, py0, px1, py1, RAD)
        if sd < 0:
            continue
        r, g, b = DECK
        if x % 12 == 5 or y % 12 == 7:
            r, g, b = r - 2, g - 2, b - 2
        lf = lampfield(x + 0.5, y + 0.5)
        if sd < 2.0:
            k = 1.0 - sd / 2.0
            r = 30 + (18 + 22 * k) * lf
            g = 36 + (40 + 50 * k) * lf
            b = 42 + (40 + 52 * k) * lf
        else:
            glow = math.exp(-(sd - 2.0) / 5.0) * lf
            r += 6 * glow
            g += 26 * glow
            b += 30 * glow
            s = spill(x + 0.5, y + 0.5) * SPILL
            wv_ = wet(x + 0.5, y + 0.5)
            r += SPILL_RGB[0] * s * (1 + 1.4 * wv_) + 7 * wv_
            g += SPILL_RGB[1] * s * (1 + 1.4 * wv_) + 20 * wv_
            b += SPILL_RGB[2] * s * (1 + 1.4 * wv_) + 24 * wv_
        bgR[y][x], bgG[y][x], bgB[y][x] = r, g, b

for ry in rail_ys:
    for x in range(px1 - 5, px1 + 3):
        k = 1.0 if x < px1 else 0.6
        if x == px1 - 5:
            k = 0.45
        bgR[ry][x], bgG[ry][x], bgB[ry][x] = 120 * k, 175 * k, 180 * k


lens = []
for yy in range(lamp_y - 3, lamp_y + 4):
    for xx in range(px0 - 1, px0 + 3):
        e = ((xx + 0.5 - (px0 + 0.4)) / 1.3) ** 2 + ((yy + 0.5 - lamp_y) / 2.6) ** 2
        k = LENS * max(0.0, min(1.0, 1.6 - e * 1.2))
        if k > 0:
            lens.append(((xx, yy), k))


def tone(v):
    return int(255 * (1.0 - math.exp(-v / 190.0)))


_style = {}


def sty(t, b):
    key = (t, b)
    s = _style.get(key)
    if s is None:
        s = Style(color=Color.from_rgb(*t), bgcolor=Color.from_rgb(*b))
        _style[key] = s
    return s


for frame in range(N):
    canvas.clear()
    if len(_style) > 6000:
        _style.clear()
    hxx = [0.0] * npool
    hyy = [0.0] * npool
    hxy = [0.0] * npool
    gxs = [0.0] * npool
    gys = [0.0] * npool
    amp = 1.0 - CALM * (0.5 + 0.5 * math.cos(TAU * (frame - F_CALM) / N))
    for m, SA, CA, WXX, WYY, WXY, WGX, WGY in wtab:
        phs = TAU * m * frame / N
        cf, sf = math.cos(phs) * amp, math.sin(phs) * amp
        Sv = [sa * cf + ca * sf for sa, ca in zip(SA, CA)]
        Cv = [ca * cf - sa * sf for sa, ca in zip(SA, CA)]
        hxx = [h + w * s for h, w, s in zip(hxx, WXX, Sv)]
        hyy = [h + w * s for h, w, s in zip(hyy, WYY, Sv)]
        hxy = [h + w * s for h, w, s in zip(hxy, WXY, Sv)]
        gxs = [h + w * c for h, w, c in zip(gxs, WGX, Cv)]
        gys = [h + w * c for h, w, c in zip(gys, WGY, Cv)]

    tau = frame - RING_F
    if 0 <= tau < RING_T:
        R0 = RING_V * tau
        env = min(1.0, tau / 4.0) * (1.0 - tau / RING_T) ** 1.2 / math.sqrt(1.0 + R0 / 10.0)
        A = RING_A * env
        w2 = RING_W * RING_W
        lo_i = bisect.bisect_left(ring_r, max(0.5, R0 - 2.5 * RING_W))
        hi_i = bisect.bisect_right(ring_r, R0 + 2.5 * RING_W)
        for r, i, dx, dy in ring_px[lo_i:hi_i]:
            s = r - R0
            e = math.exp(-s * s / w2)
            cs, sn = math.cos(RING_K * s), math.sin(RING_K * s)
            g1 = A * e * (-2 * s / w2 * cs - RING_K * sn)
            g2 = A * e * ((4 * s * s / (w2 * w2) - 2 / w2 - RING_K * RING_K) * cs + 4 * RING_K * s / w2 * sn)
            ux, uy = dx / r, dy / r
            q = g1 / r
            hxx[i] += GAIN * (g2 * ux * ux + q * (1 - ux * ux))
            hyy[i] += GAIN * (g2 * uy * uy + q * (1 - uy * uy))
            hxy[i] += GAIN * (g2 - q) * ux * uy
            gxs[i] += 0.8 * g1 * ux
            gys[i] += 0.8 * g1 * uy

    fa = TAU * frame / N + 2.2
    fcx = F_C[0] + F_PATH[0] * math.cos(fa)
    fcy = F_C[1] + F_PATH[1] * math.sin(fa)
    sgx = sgy = 0.0
    nsamp = 0
    for ox, oy in ((0, 0), (F_RC, 0), (-F_RC, 0), (0, F_RC), (0, -F_RC)):
        j = idx.get((int(fcx + ox), int(fcy + oy)))
        if j is not None:
            sgx += gxs[j]
            sgy += gys[j]
            nsamp += 1
    if nsamp:
        sgx, sgy = sgx / nsamp, sgy / nsamp
    rgx = rgy = 0.0
    if 0 <= tau < RING_T:
        dxr, dyr = fcx - RING_C[0], fcy - RING_C[1]
        rr = math.hypot(dxr, dyr) + 1e-6
        s = rr - R0
        if abs(s) < 3 * RING_W:
            e = math.exp(-s * s / (RING_W * RING_W))
            g1 = A * e * (-2 * s / (RING_W * RING_W) * math.cos(RING_K * s) - RING_K * math.sin(RING_K * s))
            rgx, rgy = g1 * dxr / rr, g1 * dyr / rr
    fx = fcx + SLIDE * sgx + 0.6 * rgx
    fy = fcy + SLIDE * sgy + 0.6 * rgy
    tlx = TILT * sgx + ROCK * rgx
    tly = TILT * sgy + ROCK * rgy
    psi = TAU * frame / N + 0.4 + 0.25 * math.sin(TAU * 2 * frame / N)

    dpt = 1.0 + smooth(0.30, 0.78, (fy - py0) / ph)
    so = dpt * TANW * PPM
    scx, scy = fx + SH_DIR[0] * so, fy + SH_DIR[1] * so
    occ = {}
    reach = F_RO + 3.0
    for yy in range(int(scy - reach), int(scy + reach) + 1):
        for xx in range(int(scx - reach), int(scx + reach) + 1):
            j = idx.get((xx, yy))
            if j is None:
                continue
            rr = math.hypot(xx + 0.5 + gxs[j] - scx, yy + 0.5 + gys[j] - scy)
            pen = 0.9 + 0.25 * dpt
            o = smooth(-pen, pen, F_RO - rr) * smooth(-pen, pen, rr - F_RI)
            if o > 0.01:
                occ[j] = o

    R = [row[:] for row in bgR]
    G = [row[:] for row in bgG]
    B = [row[:] for row in bgB]
    for i in range(npool):
        x, y = pool[i]
        det = (1.0 + hxx[i]) * (1.0 + hyy[i]) - hxy[i] * hxy[i]
        inten = 1.0 / max(abs(det), EPS)
        o = occ.get(i)
        c = LO + (1 - LO) * min(inten, CMAX) ** CPOW * (1.0 - SHADOW * o if o else 1.0)
        f = albedo(x + gxs[i], y + gys[i]) * c
        br, bgc, bb = base[i]
        vr, vg, vb = br * f, bgc * f, bb * f
        lum = 0.25 * vr + 0.6 * vg + 0.15 * vb
        if lum > LK:
            s_ = (LK + (lum - LK) * LS) / lum
            vr, vg, vb = vr * s_, vg * s_, vb * s_
        wv = (vr + vg + vb) * BLEACH
        R[y][x] = tone(vr + wv)
        G[y][x] = tone(vg + wv)
        B[y][x] = tone(vb + wv)

    for (lx_, ly_), k in lens:
        R[ly_][lx_] += 255 * k
        G[ly_][lx_] += 255 * k
        B[ly_][lx_] += 245 * k
        R[ly_][lx_], G[ly_][lx_], B[ly_][lx_] = min(255, R[ly_][lx_]), min(255, G[ly_][lx_]), min(255, B[ly_][lx_])

    for ry in rail_ys:
        for x in range(px1 - 2, px1):
            R[ry][x] = 0.55 * R[ry][x] + 120
            G[ry][x] = 0.55 * G[ry][x] + 150
            B[ry][x] = 0.55 * B[ry][x] + 150

    nzt = 1.0 / math.sqrt(1.0 + tlx * tlx + tly * tly)
    vx_, vy_ = fx + F_RC * math.cos(psi), fy + F_RC * math.sin(psi)
    for yy in range(int(fy - F_RO) - 1, int(fy + F_RO) + 2):
        for xx in range(int(fx - F_RO) - 1, int(fx + F_RO) + 2):
            if not (0 <= yy < H and 0 <= xx < W):
                continue
            dx, dy = xx + 0.5 - fx, yy + 0.5 - fy
            r = math.hypot(dx, dy) + 1e-6
            cov = min(1.0, max(0.0, F_RO + 0.5 - r)) * min(1.0, max(0.0, r - F_RI + 0.5))
            if cov <= 0:
                continue
            s = max(-1.0, min(1.0, (r - F_RC) / F_A))
            nz = math.sqrt(1.0 - s * s)
            nx, ny = s * dx / r, s * dy / r
            nx, ny, nz = nx - tlx * nz, ny - tly * nz, nz
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            nx, ny, nz = nx / nl, ny / nl, nz / nl
            dif = max(0.0, nx * LM[0] + ny * LM[1] + nz * LM[2])
            spec = max(0.0, nx * HM[0] + ny * HM[1] + nz * HM[2]) ** GLOSS
            door = max(0.0, nx * 0.85 + ny * 0.25 + nz * 0.45) * math.exp(-(W - fx) / 30.0)
            rim = (1.0 - nz) ** 2
            pr, pg, pb = R[yy][xx], G[yy][xx], B[yy][xx]
            lit = 0.12 + 0.62 * dif + 0.25 * door
            fr = VINYL[0] * lit + 0.30 * rim * pr + SPEC * spec
            fg_ = VINYL[1] * lit + 0.30 * rim * pg + SPEC * spec
            fb_ = VINYL[2] * lit + 0.30 * rim * pb + SPEC * spec
            vd = math.hypot(xx + 0.5 - vx_, yy + 0.5 - vy_)
            if vd < 0.8:
                k = min(1.0, (0.8 - vd) * 2.5) * VALVE
                fr, fg_, fb_ = fr + (232 - fr) * k, fg_ + (226 - fg_) * k, fb_ + (216 - fb_) * k
            R[yy][xx] = pr * (1 - cov) + fr * cov
            G[yy][xx] = pg * (1 - cov) + fg_ * cov
            B[yy][xx] = pb * (1 - cov) + fb_ * cov

    for cy in range(height):
        line = Text()
        rt, gt, bt = R[2 * cy], G[2 * cy], B[2 * cy]
        rb, gb, bb2 = R[2 * cy + 1], G[2 * cy + 1], B[2 * cy + 1]
        prev, run = None, 0
        for x in range(W):
            st_ = sty((int(rt[x]), int(gt[x]), int(bt[x])), (int(rb[x]), int(gb[x]), int(bb2[x])))
            if st_ is prev:
                run += 1
            else:
                if run:
                    line.append("▀" * run, prev)
                prev, run = st_, 1
        if run:
            line.append("▀" * run, prev)
        canvas.write(line)

    await sleep(0.1)

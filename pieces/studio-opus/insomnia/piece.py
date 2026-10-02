from rich.text import Text, Span
from rich.style import Style
from rich.color import Color
import math
import random
from itertools import accumulate

# insomnia

N = 400
W = width
H = height * 2
TAU = math.pi * 2

RW = 3.4
RD = 3.9
RH = 2.5
FLOOR = 7.5
WXC = 1.5
WWD = 1.2
WZ0 = 0.85
WZ1 = 2.2
REV = 0.25
FRM = 0.07
MUL = 0.05
TRANSOM = 0.64
WX0, WX1 = WXC - WWD / 2, WXC + WWD / 2
XM = WXC
ZM = WZ0 + TRANSOM * (WZ1 - WZ0)
GY = RD + REV
TREE_Y = GY + 1.5

CAMX = 1.4
CAMY = 0.5
CAMZ = 0.78
PITCH = 36.0
HFOV = 58.0
VMIN = 80.0

LAMP_DX = 2.5
LAMP_D = 6.0
LAMP_H = 4.5
LAMP_R = 0.10
LAMP_I = 75.0
LAMP_C = (1.0, 0.56, 0.24)
LBOUNCE = 0.0006

SKY_I = 1.5
SKY_C = (0.30, 0.42, 0.85)
AMB = 0.028
AMB_C = (0.45, 0.50, 0.90)

CAR_A = 15.0
CAR_B = 12.5
CARS = ((22, CAR_A, 1, 3.0), (250, CAR_B, -1, 2.8))
CAR_ZH = 0.65
HEAD_I = 300.0
HEAD_C = (0.86, 0.92, 1.0)
HEAD_R = 0.08
TAIL_I = 11.0
TAIL_C = (1.0, 0.10, 0.05)
ENV0, ENV1, ENV2, ENV3 = -13.0, -7.0, -1.0, 3.0
TENV = (-2.0, 1.0, 3.0, 8.0)
BOUNCE = 0.00006
BLOOM = 0.35
BLOOM_L = 0.30

LEAF_L = 22.0
LEAF_H = 0.12
SWAYK = 0.045
GUSTS = ((125, 42.0, 1.0),)
FLUT = 0.35

EXPO = 0.50
ALB = (0.70, 0.70, 0.70, 0.70, 0.30, 0.80, 0.75, 0.75, 0.75, 0.75, 0.0)

COLS = ((WX0 + FRM, XM - MUL / 2), (XM + MUL / 2, WX1 - FRM))
ROWS = ((WZ0 + FRM, ZM - MUL / 2), (ZM + MUL / 2, WZ1 - FRM))
OPX = ((WX0, WX1),)
OPZ = ((WZ0, WZ1),)
LAMP = (WXC + LAMP_DX, GY + LAMP_D, LAMP_H - FLOOR)


def prof(w, b, spans):
    lo, hi = w - b, w + b
    acc = 0.0
    for a, c in spans:
        o = (c if c < hi else hi) - (a if a > lo else lo)
        if o > 0:
            acc += o
    return acc / (2 * b)


def smooth(a, b, v):
    t = min(1.0, max(0.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


cp_ = math.radians(PITCH)
fwd = (0.0, math.cos(cp_), math.sin(cp_))
upv = (0.0, -math.sin(cp_), math.cos(cp_))
KPX = max(math.tan(math.radians(HFOV / 2)) / (W / 2), math.tan(math.radians(VMIN / 2)) / (H / 2))
NORM = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
        (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0))


def cast(fx, fy):
    sx = (fx - W / 2) * KPX
    sy = (H / 2 - fy) * KPX
    dx, dy, dz = sx, fwd[1] + sy * upv[1], fwd[2] + sy * upv[2]
    best, face = 1e9, 0
    for t, f in (((RW - CAMX) / dx if dx > 1e-9 else (-CAMX / dx if dx < -1e-9 else 1e9), 1 if dx > 0 else 0),
                 ((RD - CAMY) / dy if dy > 1e-9 else (-CAMY / dy if dy < -1e-9 else 1e9), 3 if dy > 0 else 2),
                 ((RH - CAMZ) / dz if dz > 1e-9 else (-CAMZ / dz if dz < -1e-9 else 1e9), 5 if dz > 0 else 4)):
        if t < best:
            best, face = t, f
    x, z = CAMX + dx * best, CAMZ + dz * best
    if face == 3 and WX0 < x < WX1 and WZ0 < z < WZ1:
        best, face = (GY - CAMY) / dy, 10
        for t, f in (((WX0 - CAMX) / dx if dx < -1e-9 else 1e9, 6), ((WX1 - CAMX) / dx if dx > 1e-9 else 1e9, 7),
                     ((WZ0 - CAMZ) / dz if dz < -1e-9 else 1e9, 8), ((WZ1 - CAMZ) / dz if dz > 1e-9 else 1e9, 9)):
            if t < best:
                best, face = t, f
    dl = math.sqrt(dx * dx + dy * dy + dz * dz)
    return (CAMX + dx * best, CAMY + dy * best, CAMZ + dz * best, face, (dx, dy, dz), best * dl * KPX * 0.5)


NPX = W * H
SAMP = []
for py in range(H):
    for px in range(W):
        i = py * W + px
        sub = [cast(px + ox, py + oy) for ox, oy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))]
        if len({s[3] for s in sub}) == 1:
            SAMP.append((i, 1.0) + cast(px + 0.5, py + 0.5))
        else:
            for s in sub:
                SAMP.append((i, 0.25) + s[:5] + (s[5] * 0.5,))

TU0, TV0, TS = -1.5, -3.5, 0.035
TNU, TNV = 200, 220
tex = [[0.0] * TNU for _ in range(TNV)]
rng = random.Random(7)


def stamp(cu, cv, rad, op):
    i0, i1 = int((cu - rad - TU0) / TS), int((cu + rad - TU0) / TS) + 1
    j0, j1 = int((cv - rad - TV0) / TS), int((cv + rad - TV0) / TS) + 1
    for j in range(max(0, j0), min(TNV, j1)):
        v = TV0 + (j + 0.5) * TS
        row = tex[j]
        for i in range(max(0, i0), min(TNU, i1)):
            u = TU0 + (i + 0.5) * TS
            d = math.hypot(u - cu, v - cv) / rad
            if d < 1:
                a = op * min(1.0, (1 - d) * 2.5)
                row[i] = 1 - (1 - row[i]) * (1 - a)


THIN = [1, 0]


def leaves(u, v, n, spread):
    for _ in range(n):
        lu, lv, lr = u + rng.gauss(0, spread), v + rng.gauss(0, spread * 0.8), rng.uniform(0.04, 0.07)
        THIN[1] += 1
        if lu > B1U or THIN[1] % THIN[0] == 0:
            stamp(lu, lv, lr, 0.92)


def branch(u, v, ang, bend, length, thick, p, kids):
    steps = int(length / 0.03)
    for k in range(steps):
        ang += bend / steps + rng.uniform(-0.05, 0.05)
        u += math.cos(ang) * 0.03
        v += math.sin(ang) * 0.03
        th = thick * (1 - 0.7 * k / steps)
        stamp(u, v, th, 1.0)
        if kids and k > 6 and rng.random() < kids:
            branch(u, v, ang + rng.choice((-1, 1)) * rng.uniform(0.5, 0.9), rng.uniform(-0.4, 0.4),
                   length * rng.uniform(0.25, 0.4), th * 0.7, p, 0)
        if k > steps * 0.3 and rng.random() < p:
            leaves(u, v, rng.randint(3, 5), 0.07)
    leaves(u, v, 6, 0.08)


TRUNK = 3.3
B1THIN = 3
B1U = 2.3
for k in range(int(7.0 / 0.04)):
    v = -3.4 + k * 0.04
    stamp(TRUNK + 0.06 * math.sin(v * 1.3), v, 0.15, 1.0)
THIN[0] = B1THIN
branch(TRUNK - 0.1, -0.7, math.radians(168), 0.35, 3.4, 0.05, 0.05, 0.035)
THIN[0] = 1
branch(TRUNK - 0.1, 1.2, math.radians(158), 0.25, 2.3, 0.04, 0.12, 0.03)
branch(TRUNK - 0.1, 2.5, math.radians(172), -0.2, 1.7, 0.035, 0.08, 0.02)
branch(TRUNK - 0.1, -0.2, math.radians(150), 0.25, 1.5, 0.035, 0.16, 0.0)


def sample(u, v):
    fu = (u - TU0) / TS - 0.5
    fv = (v - TV0) / TS - 0.5
    i, j = int(fu // 1), int(fv // 1)
    if i < 0 or j < 0 or i >= TNU - 1 or j >= TNV - 1:
        return 0.0
    au, av = fu - i, fv - j
    r0, r1 = tex[j], tex[j + 1]
    return (r0[i] * (1 - au) + r0[i + 1] * au) * (1 - av) + (r1[i] * (1 - au) + r1[i + 1] * au) * av


def gust(frame):
    g = 0.0
    for c, w, a in GUSTS:
        d = (frame - c + N / 2) % N - N / 2
        if abs(d) < w:
            g += a * math.cos(math.pi * d / (2 * w)) ** 2
    return g


SW = [0.0] * 6


def set_sway(frame):
    a = TAU * frame / N
    g = gust(frame)
    amp = 1.5 * g
    SW[0], SW[1] = amp * math.sin(2 * a), amp * math.cos(2 * a)
    SW[2], SW[3] = amp * math.sin(5 * a + 1.1), amp * math.sin(3 * a + 0.3)
    SW[4], SW[5] = FLUT * g, 17 * a


def sway(u, v):
    k = TRUNK - 0.2 - u
    if k <= 0:
        return 0.0, 0.0
    k *= SWAYK
    f = SW[4] * math.sin(SW[5] + 9.0 * u + 7.0 * v)
    return (k * (SW[0] * math.cos(0.8 * v) + SW[1] * math.sin(0.8 * v) + 0.45 * SW[2] + f),
            k * (0.45 * SW[3] + 0.6 * f))


def aperture(x, y, z, lx, ly, lz, rad):
    s = (GY - y) / (ly - y)
    b = max(0.003, rad * s)
    T = prof(x + s * (lx - x), b, COLS) * prof(z + s * (lz - z), b, ROWS)
    if T > 0 and y < RD - 1e-6:
        s2 = (RD - y) / (ly - y)
        b2 = max(0.003, rad * s2)
        T *= prof(x + s2 * (lx - x), b2, OPX) * prof(z + s2 * (lz - z), b2, OPZ)
    return T


def blur(src, rad):
    k = 1.0 / (2 * rad + 1)
    lh = [(max(0, x - rad), min(W, x + rad + 1)) for x in range(W)]
    rows = []
    for y in range(H):
        run = list(accumulate(src[y * W:(y + 1) * W], initial=0.0))
        rows.append([(run[b] - run[a]) * k for a, b in lh])
    acc = [0.0] * W
    for y in range(min(rad, H)):
        acc = [p + q for p, q in zip(acc, rows[y])]
    res = []
    for y in range(H):
        if y + rad < H:
            acc = [p + q for p, q in zip(acc, rows[y + rad])]
        if y - rad - 1 >= 0:
            acc = [p - q for p, q in zip(acc, rows[y - rad - 1])]
        res.extend([v * k for v in acc])
    return res


WS = [(WX0 + (WX1 - WX0) * (a + 0.5) / 4, WZ0 + (WZ1 - WZ0) * (b + 0.5) / 4) for a in range(4) for b in range(4)]
WDA = (WX1 - WX0) * (WZ1 - WZ0) / 16
BR = [0.0] * NPX
BG_ = [0.0] * NPX
BB = [0.0] * NPX
OPEN = [0.0] * NPX
LST = [0.0] * NPX
GLASS = []
SURF = []
lamp_px = []
lsum, lcx, lcy = 0.0, 0.0, 0.0
lx, ly, lz = LAMP
for pix, w, x, y, z, f, d, fp in SAMP:
    if f == 10:
        GLASS.append((pix, w, x, z, d, fp))
        continue
    SURF.append((pix, w, x, y, z, f))
    nx, ny, nz = NORM[f]
    e = 0.0
    if f != 3:
        for wx, wz in WS:
            ddx, ddy, ddz = wx - x, GY - y, wz - z
            r2 = ddx * ddx + ddy * ddy + ddz * ddz
            r = math.sqrt(r2)
            cp = (nx * ddx + ny * ddy + nz * ddz) / r
            cw = ddy / r
            if cp > 0 and cw > 0:
                e += cp * cw / (math.pi * max(r2, 0.25)) * WDA
    al = ALB[f] * w
    BR[pix] += al * (AMB * AMB_C[0] + SKY_I * e * SKY_C[0])
    BG_[pix] += al * (AMB * AMB_C[1] + SKY_I * e * SKY_C[1])
    BB[pix] += al * (AMB * AMB_C[2] + SKY_I * e * SKY_C[2])
    OPEN[pix] += w * (e * 8 + 0.004)
    if f != 3:
        T = aperture(x, y, z, lx, ly, lz, LAMP_R)
        if T > 0:
            ddx, ddy, ddz = lx - x, ly - y, lz - z
            r2 = ddx * ddx + ddy * ddy + ddz * ddz
            cp = (nx * ddx + ny * ddy + nz * ddz) / math.sqrt(r2)
            if cp > 0:
                st = (TREE_Y - y) / (ly - y)
                e = LAMP_I * T * cp / r2 * al
                lamp_px.append((pix, e, x + st * (lx - x), z + st * (lz - z)))
                LST[pix] += e
                if f == 5:
                    lsum += e
                    lcx += e * x
                    lcy += e * y
if lsum > 0:
    lcx, lcy = lcx / lsum, lcy / lsum
    for pix, w, x, y, z, f in SURF:
        nx, ny, nz = NORM[f]
        ddx, ddy, ddz = lcx - x, lcy - y, RH - z
        r2 = ddx * ddx + ddy * ddy + ddz * ddz + 0.4
        r = math.sqrt(r2)
        cp = max(0.0, (nx * ddx + ny * ddy + nz * ddz) / r) if f != 5 else 0.15
        k = LBOUNCE * lsum * cp * (ddz / r) / r2 * ALB[f] * w
        BR[pix] += k * LAMP_C[0]
        BG_[pix] += k * LAMP_C[1]
        BB[pix] += k * LAMP_C[2]
lb = blur(LST, 3)
for i in range(NPX):
    BR[i] += BLOOM_L * lb[i] * LAMP_C[0]
    BG_[i] += BLOOM_L * lb[i] * LAMP_C[1]
    BB[i] += BLOOM_L * lb[i] * LAMP_C[2]


def build_cands(ly, lz, rad):
    out = []
    for pix, w, x, y, z, f in SURF:
        if f == 3:
            continue
        s = (GY - y) / (ly - y)
        tz = prof(z + s * (lz - z), max(0.003, rad * s), ROWS)
        if tz <= 0:
            continue
        if y < RD - 1e-6:
            s2 = (RD - y) / (ly - y)
            tz *= prof(z + s2 * (lz - z), max(0.003, rad * s2), OPZ)
            if tz <= 0:
                continue
        else:
            s2 = -1.0
        st = (TREE_Y - y) / (ly - y)
        n = NORM[f]
        out.append((pix, s, x * (1 - s), s2, x * (1 - s2), tz, st, x * (1 - st), z + st * (lz - z),
                    n[0], n[1] * (ly - y) + n[2] * (lz - z), x, (ly - y) ** 2 + (lz - z) ** 2, ALB[f] * w))
    return out


LIGHTS = []
for f0, depth, hd, v in CARS:
    cy = GY + depth
    zz = CAR_ZH - FLOOR
    lights = []
    for o in (-0.7, 0.7):
        lights.append((1.9 * hd, HEAD_I, HEAD_C, True, build_cands(cy + o, zz, HEAD_R), cy + o, zz))
    for o in (-0.65, 0.65):
        lights.append((-2.1 * hd, TAIL_I, TAIL_C, False, build_cands(cy + o, zz + 0.15, HEAD_R), cy + o, zz + 0.15))
    LIGHTS.append((f0, hd, v, lights))


TQ = 512
TM = TQ * 8 - 1
TONE = [int(255 * (1.0 - math.exp(-(i + 0.5) / TQ / EXPO))) for i in range(TM + 1)]
PLAIN = "\u2580" * W


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
    set_sway(frame)
    R = BR[:]
    G = BG_[:]
    B = BB[:]
    for i, e, u, v in lamp_px:
        du, dv = sway(u, v)
        e *= 1.0 - sample(u - du, v - dv)
        R[i] += e * LAMP_C[0]
        G[i] += e * LAMP_C[1]
        B[i] += e * LAMP_C[2]

    br = bg = bb = 0.0
    HL = None
    LIVE = []
    for f0, hd, v, lights in LIGHTS:
        tsec = ((frame - f0 + N // 2) % N - N // 2) / 10.0
        carx = WXC + hd * v * tsec
        if abs(carx - WXC) > 70:
            continue
        for off, li, lc, head, cands, lyy, lzz in lights:
            lxx = carx + off
            a = (lxx - WXC) * hd
            if head:
                env = smooth(ENV0, ENV1, a) * (1.0 - smooth(ENV2, ENV3, a))
            else:
                env = smooth(TENV[0], TENV[1], a) * (1.0 - smooth(TENV[2], TENV[3], a))
            if env <= 0:
                continue
            if HL is None:
                HL = [0.0] * NPX
            li = li * env
            LIVE.append((lxx, lyy, lzz, li * LEAF_H, lc))
            acc = 0.0
            for (i, s, ax, s2, ax2, tz, st, au, tv, nx, ndot, x, dyz2, al) in cands:
                wx = ax + s * lxx
                if wx < WX0 or wx > WX1:
                    continue
                T = tz * prof(wx, max(0.003, HEAD_R * s), COLS)
                if T <= 0:
                    continue
                if s2 > 0:
                    T *= prof(ax2 + s2 * lxx, max(0.003, HEAD_R * s2), OPX)
                    if T <= 0:
                        continue
                dlx = lxx - x
                r2 = dlx * dlx + dyz2
                cp = (nx * dlx + ndot) / math.sqrt(r2)
                if cp <= 0:
                    continue
                u = au + st * lxx
                du, dv = sway(u, tv)
                e = li * T * cp / r2 * al * (1.0 - sample(u - du, tv - dv))
                R[i] += e * lc[0]
                G[i] += e * lc[1]
                B[i] += e * lc[2]
                HL[i] += e * (lc[0] + lc[1] + lc[2]) / 3
                acc += e
            br += acc * lc[0]
            bg += acc * lc[1]
            bb += acc * lc[2]
    if HL is not None:
        tot = br + bg + bb
        cr, cg, cb = (br / tot, bg / tot, bb / tot) if tot > 0 else (0.33, 0.33, 0.33)
        hb = blur(HL, 3)
        kr, kg, kb = br * BOUNCE, bg * BOUNCE, bb * BOUNCE
        for i in range(NPX):
            a = OPEN[i]
            h = hb[i] * BLOOM * 3
            R[i] += kr * a + h * cr
            G[i] += kg * a + h * cg
            B[i] += kb * a + h * cb

    for pix, w, x, z, d, fp in GLASS:
        glass = prof(x, fp, COLS) * prof(z, fp, ROWS)
        dx, dy, dz = d
        tt = (TREE_Y - CAMY) / dy
        u, v = CAMX + dx * tt, CAMZ + dz * tt
        du, dv = sway(u, v)
        op = sample(u - du, v - dv)
        el = dz / math.sqrt(dx * dx + dy * dy)
        g = max(0.0, 1.0 - el * 2.4) ** 2
        sr, sg, sb = 0.026 + 0.12 * g, 0.034 + 0.075 * g, 0.095 + 0.045 * g
        d2 = (u - lx) ** 2 + (v - lz) ** 2 + (TREE_Y - ly) ** 2
        lit = LEAF_L / d2
        cr, cg, cb = 0.006 + lit * LAMP_C[0], 0.006 + lit * LAMP_C[1], 0.010 + lit * LAMP_C[2]
        for qx, qy, qz, qi, qc in LIVE:
            k = qi / ((u - qx) ** 2 + (v - qz) ** 2 + (TREE_Y - qy) ** 2)
            cr += k * qc[0]
            cg += k * qc[1]
            cb += k * qc[2]
        orr = sr * (1 - op) + op * cr
        og = sg * (1 - op) + op * cg
        ob = sb * (1 - op) + op * cb
        R[pix] += w * (orr * glass + 0.018 * (1 - glass))
        G[pix] += w * (og * glass + 0.020 * (1 - glass))
        B[pix] += w * (ob * glass + 0.030 * (1 - glass))

    TR = [TONE[min(TM, int(v * TQ))] for v in R]
    TG = [TONE[min(TM, int(v * TQ))] for v in G]
    TB = [TONE[min(TM, int(v * TQ))] for v in B]
    for cy in range(height):
        o1 = 2 * cy * W
        o2 = o1 + W
        spans = []
        prev, start = None, 0
        for x in range(W):
            a, b = o1 + x, o2 + x
            st_ = sty((TR[a], TG[a], TB[a]), (TR[b], TG[b], TB[b]))
            if st_ is not prev:
                if prev is not None:
                    spans.append(Span(start, x, prev))
                prev, start = st_, x
        spans.append(Span(start, W, prev))
        canvas.write(Text(PLAIN, spans=spans))

    await sleep(0.1)

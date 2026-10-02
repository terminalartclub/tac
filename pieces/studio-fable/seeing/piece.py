from rich.text import Text
from rich.style import Style
from rich.color import Color
import math
import random

# seeing

N = 300
DT = 0.1
W = width
H = height * 2
TAU = math.pi * 2

ARCSEC_W = 56.0
PPA = W / ARCSEC_W
DIST_AU = 9.0
KM_PER_AS = 1.496e8 * DIST_AU * 4.8481e-6
RE = 60268.0 / KM_PER_AS * PPA
RP = 54364.0 / KM_PER_AS * PPA
B_DEG = 20.0
DB_DEG = 3.5
PHASE_DEG = 5.0
ROT_DEG = 28.0
CX, CY = W * 0.47, H * 0.42
SWAY = 0.7
SS = 3

RING_GAIN = 1.9
EXPO = 0.68
MINN = 0.85
GAMMA = 1.5
SKY = (8, 8, 15)
HALO_K = 0.28
HALO_SIG = 7.0
HALO_TINT = (1.0, 0.92, 0.72)
MOON_K = 0.45
MOON_SIG = 0.38

SEEING_A = 1.6
SEEING_STEADY = 0.85
STEADY_K = 6.0
BLUR_LEVELS = (0.0, 0.5, 1.0, 1.6)
BLUR_BASE = 0.3
BLUR_SWING = 0.9
NWAVE = 6
CLOUD_DEPTH = 0.5
CLOUD_K = 6.0
CLOUD_PH = 0.62
CLOUD_BLUR = 0.5
CLOUD_HALO = 0.35
WSEED = 11

RINGS = (
    (1.235, 1.525, 0.10, (0.45, 0.41, 0.34)),
    (1.525, 1.64, 0.90, (0.98, 0.91, 0.74)),
    (1.64, 1.85, 1.80, (1.00, 0.93, 0.76)),
    (1.85, 1.95, 1.30, (0.95, 0.88, 0.72)),
    (1.95, 2.025, 0.05, (0.50, 0.46, 0.40)),
    (2.025, 2.12, 0.70, (0.86, 0.78, 0.62)),
    (2.12, 2.211, 0.50, (0.82, 0.74, 0.58)),
    (2.211, 2.217, 0.02, (0.80, 0.73, 0.57)),
    (2.217, 2.27, 0.50, (0.82, 0.74, 0.58)),
)
R_IN, R_OUT = 1.235, 2.27

BANDS = (
    (-90, (0.64, 0.60, 0.52)), (-60, (0.68, 0.63, 0.53)), (-45, (0.82, 0.70, 0.52)),
    (-30, (0.76, 0.62, 0.42)), (-22, (0.88, 0.76, 0.55)), (-12, (0.74, 0.59, 0.38)),
    (-5, (0.97, 0.85, 0.61)), (5, (0.97, 0.85, 0.61)), (12, (0.74, 0.59, 0.38)),
    (22, (0.88, 0.76, 0.55)), (30, (0.76, 0.62, 0.42)), (45, (0.84, 0.72, 0.54)),
    (60, (0.72, 0.66, 0.54)), (90, (0.64, 0.60, 0.52)),
)

MOONS = (
    (8.74, 95.0, 1.00),
    (4.89, 62.0, 0.63),
    (6.26, -105.0, 0.52),
    (3.95, 75.0, 0.16),
)

cB, sB = math.cos(math.radians(B_DEG)), math.sin(math.radians(B_DEG))
P = (0.0, cB, sB)
_d, _a = math.radians(DB_DEG), math.radians(PHASE_DEG)
S = (math.cos(_d) * math.sin(_a), math.sin(_d), math.cos(_d) * math.cos(_a))
SP = S[0] * P[0] + S[1] * P[1] + S[2] * P[2]
KQ = 1.0 / (RP * RP) - 1.0 / (RE * RE)
cR, sR = math.cos(math.radians(ROT_DEG)), math.sin(math.radians(ROT_DEG))


def ring_zone(r):
    for r0, r1, tau, alb in RINGS:
        if r0 <= r < r1:
            return tau, alb
    return 0.0, None


def band_color(lat):
    for i in range(1, len(BANDS)):
        if lat <= BANDS[i][0]:
            l0, c0 = BANDS[i - 1]
            l1, c1 = BANDS[i]
            t = (lat - l0) / (l1 - l0)
            return tuple(a + (b - a) * t for a, b in zip(c0, c1))
    return BANDS[-1][1]


def ell_hit(o, d):
    a0 = o[0] * P[0] + o[1] * P[1] + o[2] * P[2]
    a1 = d[0] * P[0] + d[1] * P[1] + d[2] * P[2]
    od = o[0] * d[0] + o[1] * d[1] + o[2] * d[2]
    oo = o[0] * o[0] + o[1] * o[1] + o[2] * o[2]
    dd = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
    A = KQ * a1 * a1 + dd / (RE * RE)
    Bq = 2 * KQ * a0 * a1 + 2 * od / (RE * RE)
    C = KQ * a0 * a0 + oo / (RE * RE) - 1.0
    disc = Bq * Bq - 4 * A * C
    if disc < 0:
        return None
    sq = math.sqrt(disc)
    return ((-Bq - sq) / (2 * A), (-Bq + sq) / (2 * A))


def shade(x, y):
    zr = -y * cB / sB
    rr = math.hypot(x, y / sB) / RE
    tau, ralb = ring_zone(rr)
    ring = None
    tv = 1.0
    if ralb is not None:
        mu, mu0 = sB, SP
        f = (1.0 - math.exp(-tau * (1.0 / mu + 1.0 / mu0))) * mu0 / (mu + mu0) * RING_GAIN
        p = (x, y, zr)
        h = ell_hit(p, S)
        if h is not None and h[1] > 0 and h[0] < h[1]:
            f *= 0.0
        ring = (ralb[0] * f, ralb[1] * f, ralb[2] * f)
        tv = math.exp(-tau / mu)
    g = ell_hit((x, y, 0.0), (0.0, 0.0, 1.0))
    globe = None
    zg = None
    if g is not None:
        zg = g[1]
        q = (x, y, zg)
        qp = q[0] * P[0] + q[1] * P[1] + q[2] * P[2]
        nx = 2 * (KQ * qp * P[0] + q[0] / (RE * RE))
        ny = 2 * (KQ * qp * P[1] + q[1] / (RE * RE))
        nz = 2 * (KQ * qp * P[2] + q[2] / (RE * RE))
        nl = math.sqrt(nx * nx + ny * ny + nz * nz)
        nx, ny, nz = nx / nl, ny / nl, nz / nl
        mu = max(0.02, nz)
        mu0 = max(0.0, nx * S[0] + ny * S[1] + nz * S[2])
        lat = math.degrees(math.asin(max(-1.0, min(1.0, qp / math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2)))))
        alb = band_color(lat)
        inten = (mu0 ** MINN) * (mu ** (MINN - 1.0)) if mu0 > 0 else 0.0
        if qp < 0 and SP > 0:
            t = -qp / SP
            hx, hy, hz = q[0] + t * S[0], q[1] + t * S[1], q[2] + t * S[2]
            rs = math.sqrt(hx * hx + hy * hy + hz * hz) / RE
            ts, _ = ring_zone(rs)
            if ts > 0:
                inten *= math.exp(-ts / SP)
        globe = (alb[0] * inten, alb[1] * inten, alb[2] * inten)
    if globe is None and ring is None:
        return (0.0, 0.0, 0.0)
    if globe is None:
        return ring
    if ring is None:
        return globe
    if zr > zg:
        return (ring[0] + tv * globe[0], ring[1] + tv * globe[1], ring[2] + tv * globe[2])
    return globe


def tone(v):
    return 255.0 * (min(1.0, max(0.0, v * EXPO)) ** (1.0 / GAMMA))


ext = R_OUT * RE + 3.0
BX0, BX1 = max(0, int(CX - ext - 6)), min(W, int(CX + ext + 7))
BY0, BY1 = max(0, int(CY - ext - 6)), min(H, int(CY + ext + 7))
BW, BH = BX1 - BX0, BY1 - BY0

clean = [[(0.0, 0.0, 0.0)] * BW for _ in range(BH)]
for py in range(BY0, BY1):
    for px in range(BX0, BX1):
        acc = [0.0, 0.0, 0.0]
        for sy in range(SS):
            for sx in range(SS):
                u = px + (sx + 0.5) / SS - CX
                v = -(py + (sy + 0.5) / SS - CY)
                x = u * cR + v * sR
                y = -u * sR + v * cR
                c = shade(x, y)
                acc[0] += c[0]
                acc[1] += c[1]
                acc[2] += c[2]
        clean[py - BY0][px - BX0] = (acc[0] / (SS * SS), acc[1] / (SS * SS), acc[2] / (SS * SS))

for a_re, phi_deg, rel in MOONS:
    phi = math.radians(phi_deg)
    r = a_re * RE
    x, y = r * math.cos(phi), r * math.sin(phi) * sB
    u = x * cR - y * sR
    v = x * sR + y * cR
    mx, my = CX + u, CY - v
    for py in range(int(my) - 2, int(my) + 3):
        for px in range(int(mx) - 2, int(mx) + 3):
            if BX0 <= px < BX1 and BY0 <= py < BY1:
                d2 = (px + 0.5 - mx) ** 2 + (py + 0.5 - my) ** 2
                g = MOON_K * rel * math.exp(-d2 / (2 * MOON_SIG * MOON_SIG))
                c = clean[py - BY0][px - BX0]
                clean[py - BY0][px - BX0] = (c[0] + g * 0.95, c[1] + g * 0.93, c[2] + g * 0.88)

baseR = [[tone(c[0]) for c in row] for row in clean]
baseG = [[tone(c[1]) for c in row] for row in clean]
baseB = [[tone(c[2]) for c in row] for row in clean]


def gauss_blur(img, sig):
    if sig <= 0:
        return [row[:] for row in img]
    rad = int(3 * sig + 0.999)
    k = [math.exp(-i * i / (2 * sig * sig)) for i in range(-rad, rad + 1)]
    ks = sum(k)
    k = [v / ks for v in k]
    h = len(img)
    w = len(img[0])
    tmp = [[0.0] * w for _ in range(h)]
    for yy in range(h):
        row = img[yy]
        out = tmp[yy]
        for xx in range(w):
            s = 0.0
            for i, kv in enumerate(k):
                xi = xx + i - rad
                if 0 <= xi < w:
                    s += row[xi] * kv
            out[xx] = s
    res = [[0.0] * w for _ in range(h)]
    for xx in range(w):
        for yy in range(h):
            s = 0.0
            for i, kv in enumerate(k):
                yi = yy + i - rad
                if 0 <= yi < h:
                    s += tmp[yi][xx] * kv
            res[yy][xx] = s
    return res


def flat(img):
    return [v for row in img for v in row]


levels = []
for sg in BLUR_LEVELS:
    levels.append((flat(gauss_blur(baseR, sg)), flat(gauss_blur(baseG, sg)), flat(gauss_blur(baseB, sg))))

def box_blur(img, w):
    h = len(img)
    wd = len(img[0])
    r = w // 2
    tmp = [[0.0] * wd for _ in range(h)]
    for yy in range(h):
        row = img[yy]
        out = tmp[yy]
        acc = sum(row[:r + 1])
        for xx in range(wd):
            out[xx] = acc / w
            if xx + r + 1 < wd:
                acc += row[xx + r + 1]
            if xx - r >= 0:
                acc -= row[xx - r]
    res = [[0.0] * wd for _ in range(h)]
    for xx in range(wd):
        acc = sum(tmp[yy][xx] for yy in range(r + 1))
        for yy in range(h):
            res[yy][xx] = acc / w
            if yy + r + 1 < h:
                acc += tmp[yy + r + 1][xx]
            if yy - r >= 0:
                acc -= tmp[yy - r][xx]
    return res


HPAD = int(3 * HALO_SIG + 1)
HX0, HX1 = max(0, BX0 - HPAD), min(W, BX1 + HPAD)
HY0, HY1 = max(0, BY0 - HPAD), min(H, BY1 + HPAD)
lum = [[0.0] * (HX1 - HX0) for _ in range(HY1 - HY0)]
for yy in range(BH):
    for xx in range(BW):
        lum[BY0 - HY0 + yy][BX0 - HX0 + xx] = 0.3 * baseR[yy][xx] + 0.55 * baseG[yy][xx] + 0.15 * baseB[yy][xx]
hw = int(round(math.sqrt(4 * HALO_SIG * HALO_SIG + 1)))
hw += 1 - hw % 2
halo_pad = [[max(0.0, v) for v in row] for row in box_blur(box_blur(box_blur(lum, hw), hw), hw)]
halo = [[halo_pad[BY0 - HY0 + yy][BX0 - HX0 + xx] for xx in range(BW)] for yy in range(BH)]

bgR = [[float(SKY[0])] * W for _ in range(H)]
bgG = [[float(SKY[1])] * W for _ in range(H)]
bgB = [[float(SKY[2])] * W for _ in range(H)]
for yy in range(HY1 - HY0):
    for xx in range(HX1 - HX0):
        hv = halo_pad[yy][xx] * HALO_K
        bgR[HY0 + yy][HX0 + xx] += hv * HALO_TINT[0]
        bgG[HY0 + yy][HX0 + xx] += hv * HALO_TINT[1]
        bgB[HY0 + yy][HX0 + xx] += hv * HALO_TINT[2]

wide = levels[-1]
active = []
for yy in range(BH):
    for xx in range(BW):
        i = yy * BW + xx
        if 0.3 * wide[0][i] + 0.55 * wide[1][i] + 0.15 * wide[2][i] > 0.4:
            if 4 <= xx < BW - 5 and 4 <= yy < BH - 5:
                active.append((xx, yy))
nact = len(active)
HA = [halo[yy][xx] for xx, yy in active]

_r = random.Random(WSEED)
waves = []
for i in range(NWAVE):
    ang = TAU * i / NWAVE + _r.uniform(-0.4, 0.4)
    lam = _r.uniform(12.0, 26.0)
    k = TAU / lam
    m = _r.choice((7, 9, 11, 13, 15, 17))
    amp = _r.uniform(0.6, 1.0) / math.sqrt(NWAVE)
    waves.append((math.cos(ang) * k, math.sin(ang) * k, amp, m, _r.uniform(0, TAU)))

wtab = []
for kx, ky, amp, m, ph0 in waves:
    SA = [math.sin(kx * (xx + BX0) + ky * (yy + BY0) + ph0) for xx, yy in active]
    CA = [math.cos(kx * (xx + BX0) + ky * (yy + BY0) + ph0) for xx, yy in active]
    ux, uy = kx / math.hypot(kx, ky), ky / math.hypot(kx, ky)
    wtab.append((m, SA, CA, amp * ux, amp * uy))

_style = {}


def sty(t, b):
    key = (t, b)
    s = _style.get(key)
    if s is None:
        s = Style(color=Color.from_rgb(*t), bgcolor=Color.from_rgb(*b))
        _style[key] = s
    return s


active_rows = set(((yy + BY0) // 2) for _, yy in active)
static_lines = {}
for cy in range(height):
    if cy in active_rows:
        continue
    line = Text()
    rt, gt, bt = bgR[2 * cy], bgG[2 * cy], bgB[2 * cy]
    rb, gb, bb2 = bgR[2 * cy + 1], bgG[2 * cy + 1], bgB[2 * cy + 1]
    prev, run = None, 0
    for x in range(W):
        st_ = sty((int(rt[x] + 0.5), int(gt[x] + 0.5), int(bt[x] + 0.5)), (int(rb[x] + 0.5), int(gb[x] + 0.5), int(bb2[x] + 0.5)))
        if st_ is prev:
            run += 1
        else:
            if run:
                line.append("▀" * run, prev)
            prev, run = st_, 1
    if run:
        line.append("▀" * run, prev)
    static_lines[cy] = line

for frame in range(N):
    canvas.clear()
    if len(_style) > 6000:
        _style.clear()
    th = TAU * frame / N
    steady = math.exp(-STEADY_K * (1.0 - math.cos(th)))
    weather = 0.72 + 0.28 * math.sin(2 * th + 1.1)
    cloud = math.exp(-CLOUD_K * (1.0 - math.cos(th - TAU * CLOUD_PH)))
    dim = 1.0 - CLOUD_DEPTH * cloud
    hk = CLOUD_HALO * cloud
    env = SEEING_A * weather * (1.0 - SEEING_STEADY * steady)
    bl = BLUR_BASE + BLUR_SWING * (0.5 + 0.5 * math.sin(3 * th + 0.4)) * (0.6 + 0.4 * math.sin(2 * th + 2.0))
    bl = bl * (1.0 - 0.92 * steady) + CLOUD_BLUR * cloud
    li = 0
    while li < len(BLUR_LEVELS) - 2 and BLUR_LEVELS[li + 1] < bl:
        li += 1
    wgt = (bl - BLUR_LEVELS[li]) / (BLUR_LEVELS[li + 1] - BLUR_LEVELS[li])
    wgt = min(1.0, max(0.0, wgt))
    A_, B_ = levels[li], levels[li + 1]
    imR = [(a + (b - a) * wgt) * dim for a, b in zip(A_[0], B_[0])]
    imG = [(a + (b - a) * wgt) * dim for a, b in zip(A_[1], B_[1])]
    imB = [(a + (b - a) * wgt) * dim for a, b in zip(A_[2], B_[2])]

    sx0 = SWAY * math.sin(th + 0.7)
    sy0 = SWAY * 0.6 * math.cos(th + 0.7)
    dx = [sx0] * nact
    dy = [sy0] * nact
    for m, SA, CA, ax, ay in wtab:
        cf, sf = math.cos(m * th), math.sin(m * th)
        Cv = [ca * cf - sa * sf for sa, ca in zip(SA, CA)]
        ax_, ay_ = ax * env, ay * env
        dx = [d + ax_ * c for d, c in zip(dx, Cv)]
        dy = [d + ay_ * c for d, c in zip(dy, Cv)]

    R = [row[:] for row in bgR]
    G = [row[:] for row in bgG]
    B = [row[:] for row in bgB]
    for j in range(nact):
        xx, yy = active[j]
        x = xx + dx[j]
        y = yy + dy[j]
        ix = int(x)
        iy = int(y)
        fx = x - ix
        fy = y - iy
        i00 = iy * BW + ix
        i10 = i00 + 1
        i01 = i00 + BW
        i11 = i01 + 1
        w00 = (1 - fx) * (1 - fy)
        w10 = fx * (1 - fy)
        w01 = (1 - fx) * fy
        w11 = fx * fy
        ry = R[yy + BY0]
        gy = G[yy + BY0]
        by = B[yy + BY0]
        px = xx + BX0
        hv = HA[j] * hk
        ry[px] += imR[i00] * w00 + imR[i10] * w10 + imR[i01] * w01 + imR[i11] * w11 + hv * HALO_TINT[0]
        gy[px] += imG[i00] * w00 + imG[i10] * w10 + imG[i01] * w01 + imG[i11] * w11 + hv * HALO_TINT[1]
        by[px] += imB[i00] * w00 + imB[i10] * w10 + imB[i01] * w01 + imB[i11] * w11 + hv * HALO_TINT[2]

    for cy in range(height):
        sl = static_lines.get(cy)
        if sl is not None:
            canvas.write(sl)
            continue
        line = Text()
        rt, gt, bt = R[2 * cy], G[2 * cy], B[2 * cy]
        rb, gb, bb2 = R[2 * cy + 1], G[2 * cy + 1], B[2 * cy + 1]
        prev, run = None, 0
        for x in range(W):
            st_ = sty((min(255, int(rt[x] + 0.5)), min(255, int(gt[x] + 0.5)), min(255, int(bt[x] + 0.5))),
                      (min(255, int(rb[x] + 0.5)), min(255, int(gb[x] + 0.5)), min(255, int(bb2[x] + 0.5))))
            if st_ is prev:
                run += 1
            else:
                if run:
                    line.append("▀" * run, prev)
                prev, run = st_, 1
        if run:
            line.append("▀" * run, prev)
        canvas.write(line)

    await sleep(DT)

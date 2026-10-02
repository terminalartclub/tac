# eastbound
from rich.text import Text
from rich.style import Style
from rich.color import Color
import math


N = 500
DT = 0.1
W = width
H = height * 2
D2R = math.pi / 180

R = 6371.0
RT = R + 60.0
HR, HM = 8.0, 1.2
LAMS = [420.0 + 40.0 * i for i in range(8)]
BR = [0.0331 * (440.0 / l) ** 4 for l in LAMS]
BM = 0.008
BMX = BM * 1.11
BM2, HM2 = 0.003, 5.5
BM2X = BM2 * 1.05
BO = [0.003 * math.exp(-((l - 600.0) / 75.0) ** 2) for l in LAMS]
GM = 0.8
OCC = 1.5

SUN_AZ = 250.0 * D2R
SUN_DEP = 2.9 * D2R
EDGE_AZ = 245.0 * D2R
HFOV = 40.0 * D2R
VFOV_MIN = 50.0 * D2R
HOR = 0.89
EYE = 0.03

EXPO = 420.0
TOES = (2.2, 2.9, 2.4)
SAT = 1.25
TSAT = 0.9
HL = 0.03
WB = (1.1, 1.0, 1.0)
SKY_STEPS = 18
GX = 4

ALT = 10.7
TC = 4.6
VAPP = 0.245 * TC
HEAD = 74.0 * D2R
REF_D, REF_AZ = 9.0, 203.0 * D2R
S_BACK = 53.0
LOOP = N * DT
T0 = 38.0
TAU_C = 1.5
SIG0 = 0.04
K_SH = 0.0012
SMIN = 0.42
FADE_IN = 0.8
DECAY = 2.5
WIND_V, WIND_AZ = 0.010, 153.0 * D2R
SINK = 0.0004
WOB = 0.25
PATCH = 0.45
GLINT = 0.35


def planck(l):
    x = 1.4388e7 / (l * 5778.0)
    return 1.0 / (l ** 5 * (math.exp(x) - 1.0))


def lobe(l, mu, s1, s2):
    s = s1 if l < mu else s2
    return math.exp(-0.5 * ((l - mu) / s) ** 2)


def cmf(l):
    x = 1.056 * lobe(l, 599.8, 37.9, 31.0) + 0.362 * lobe(l, 442.0, 16.0, 26.7) - 0.065 * lobe(l, 501.1, 20.4, 26.2)
    y = 0.821 * lobe(l, 568.8, 46.9, 40.5) + 0.286 * lobe(l, 530.9, 16.3, 31.1)
    z = 1.217 * lobe(l, 437.0, 11.8, 36.0) + 0.681 * lobe(l, 459.0, 26.0, 13.8)
    return x, y, z


MAT = [[0.0] * 8 for _ in range(3)]
for i, l in enumerate(LAMS):
    x, y, z = cmf(l)
    e = planck(l)
    MAT[0][i] = (3.2406 * x - 1.5372 * y - 0.4986 * z) * e
    MAT[1][i] = (-0.9689 * x + 1.8758 * y + 0.0415 * z) * e
    MAT[2][i] = (0.0557 * x - 0.2040 * y + 1.0570 * z) * e
for c in range(3):
    s = sum(MAT[c])
    MAT[c] = [v / s for v in MAT[c]]


def to_rgb(spec):
    return [sum(m * v for m, v in zip(MAT[c], spec)) for c in range(3)]


def chap(r, mu, hs):
    x = r / hs
    h = (r - R) / hs
    c = math.sqrt(math.pi * x / 2)
    if mu >= 0:
        return math.exp(-h) * c / ((c - 1) * mu + 1)
    rt = r * math.sqrt(max(0.0, 1 - mu * mu))
    ht = (rt - R) / hs
    xt = rt / hs
    full = 2 * math.exp(-ht) * math.sqrt(math.pi * xt / 2)
    cm = math.exp(-h) * c / ((c - 1) * (-mu) + 1)
    return max(0.0, full - cm)


def chord(r, mu, a):
    b = r * mu
    d = b * b - (r * r - a * a)
    if d <= 0:
        return 0.0
    sq = math.sqrt(d)
    t1 = -b + sq
    t0 = max(0.0, -b - sq)
    return max(0.0, t1 - t0)


def ozone_path(r, mu):
    return 0.5 * (chord(r, mu, R + 40.0) - chord(r, mu, R + 10.0))


def sun_tau(r, mu):
    if mu < 0 and r * math.sqrt(1 - mu * mu) < R + OCC:
        return None
    cr = chap(r, mu, HR) * HR
    cm = chap(r, mu, HM) * HM
    c2 = chap(r, mu, HM2) * HM2
    co = ozone_path(r, mu)
    return [BR[i] * cr + BMX * cm + BM2X * c2 + BO[i] * co for i in range(8)]


sun = (math.sin(SUN_AZ) * math.cos(SUN_DEP), math.cos(SUN_AZ) * math.cos(SUN_DEP), -math.sin(SUN_DEP))

FOC = min((W / 2) / math.tan(HFOV / 2), (H / 2) / math.tan(VFOV_MIN / 2))
CAM_AZ = EDGE_AZ - math.atan((W / 2) / FOC)
fh = (math.sin(CAM_AZ), math.cos(CAM_AZ), 0.0)
rh = (math.cos(CAM_AZ), -math.sin(CAM_AZ), 0.0)
PITCH = math.atan((HOR * H - H / 2) / FOC)
cp, sp = math.cos(PITCH), math.sin(PITCH)
fp = (fh[0] * cp, fh[1] * cp, sp)
up = (-fh[0] * sp, -fh[1] * sp, cp)


def ray(px, py):
    x = px - W / 2
    y = H / 2 - py
    d = [rh[k] * x + up[k] * y + fp[k] * FOC for k in range(3)]
    n = math.sqrt(sum(v * v for v in d))
    return [v / n for v in d]


def project(p):
    x = sum(a * b for a, b in zip(p, rh))
    y = sum(a * b for a, b in zip(p, up))
    z = sum(a * b for a, b in zip(p, fp))
    if z <= 0.01:
        return None
    return W / 2 + FOC * x / z, H / 2 - FOC * y / z, math.sqrt(x * x + y * y + z * z)


def phase_r(mu):
    return 3.0 / (16 * math.pi) * (1 + mu * mu)


def phase_m(mu, g):
    g2 = g * g
    return 3.0 / (8 * math.pi) * (1 - g2) * (1 + mu * mu) / ((2 + g2) * (1 + g2 - 2 * g * mu) ** 1.5)


R0 = R + EYE


def sky(d):
    vz = d[2]
    b = R0 * vz
    ct = R0 * R0 - RT * RT
    tmax = -b + math.sqrt(b * b - ct)
    ground = False
    if vz < 0:
        cg = R0 * R0 - R * R
        disc = b * b - cg
        if disc > 0:
            tg = -b - math.sqrt(disc)
            if tg > 0:
                tmax = tg
                ground = True
    mu = d[0] * sun[0] + d[1] * sun[1] + d[2] * sun[2]
    pr = phase_r(mu)
    pm = phase_m(mu, GM)
    osd = R0 * sun[2]
    vsd = mu
    tv = [0.0] * 8
    L = [0.0] * 8
    n = SKY_STEPS
    for i in range(n):
        t = tmax * ((i + 0.5) / n) ** 2
        dt = 2 * tmax * (i + 0.5) / (n * n)
        r = math.sqrt(R0 * R0 + 2 * b * t + t * t)
        h = r - R
        rr = math.exp(-h / HR)
        rm = math.exp(-h / HM)
        rm2 = math.exp(-h / HM2)
        ro = max(0.0, 1 - abs(h - 25.0) / 15.0)
        ext = [(BR[k] * rr + BMX * rm + BM2X * rm2 + BO[k] * ro) * dt for k in range(8)]
        mus = (osd + t * vsd) / r
        ts = sun_tau(r, mus)
        if ts is not None:
            for k in range(8):
                L[k] += (BR[k] * rr * pr + (BM * rm + BM2 * rm2) * pm) * dt * math.exp(-(ts[k] + tv[k] + 0.5 * ext[k]))
        for k in range(8):
            tv[k] += ext[k]
    return L, tv, ground


def tone3(c):
    l = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hl = HL * (l * EXPO) ** 2 / EXPO
    out = []
    for v, t, w in zip(c, TOES, WB):
        v = (max(0.0, l + (v - l) * SAT) + hl) * EXPO * w
        v = (1.0 - math.exp(-v)) ** t
        out.append(255.0 * v ** (1 / 2.2))
    return out


GROUND = (0.00004, 0.00005, 0.00008)
FLOOR = (9.0, 9.0, 16.0)

cols_g = list(range(0, W, GX)) + [W - 1]
skyrows = []
for py in range(H):
    vals = []
    for gx in cols_g:
        d = ray(gx + 0.5, py + 0.5)
        L, tv, ground = sky(d)
        rgb = to_rgb(L)
        if ground:
            trans = to_rgb([math.exp(-v) for v in tv])
            rgb = [rgb[c] + GROUND[c] * trans[c] for c in range(3)]
        vals.append(rgb)
    row = []
    for px in range(W):
        j = 0
        while j + 1 < len(cols_g) and cols_g[j + 1] <= px:
            j += 1
        if j + 1 < len(cols_g):
            a = (px - cols_g[j]) / (cols_g[j + 1] - cols_g[j])
        else:
            a = 0.0
            j = len(cols_g) - 2
            a = 1.0
        v0, v1 = vals[j], vals[min(j + 1, len(vals) - 1)]
        row.append([v0[c] * (1 - a) + v1[c] * a for c in range(3)])
    skyrows.append(row)

SKY = [[tone3(skyrows[y][x]) for x in range(W)] for y in range(H)]
for y in range(H):
    for x in range(W):
        SKY[y][x] = [max(a, b) for a, b in zip(SKY[y][x], FLOOR)]


def sun_rgb(p):
    r = math.sqrt(sum(v * v for v in p))
    acc = [0.0] * 8
    wsum = 0.0
    for k in range(-3, 4):
        off = k / 3.0 * 0.265 * D2R
        wgt = math.sqrt(max(0.0, 1 - (k / 3.5) ** 2))
        dep = SUN_DEP - off
        s = (math.sin(SUN_AZ) * math.cos(dep), math.cos(SUN_AZ) * math.cos(dep), -math.sin(dep))
        mu = (p[0] * s[0] + p[1] * s[1] + p[2] * s[2]) / r
        ts = sun_tau(r, mu)
        wsum += wgt
        if ts is None:
            continue
        for i in range(8):
            acc[i] += wgt * math.exp(-ts[i])
    return to_rgb([a / wsum for a in acc])


hd = (math.sin(HEAD), math.cos(HEAD), 0.0)
ref = (REF_D * math.sin(REF_AZ), REF_D * math.cos(REF_AZ))
S_LEN = VAPP * N * DT


def track(s):
    gx = ref[0] + (s - S_BACK) * hd[0]
    gy = ref[1] + (s - S_BACK) * hd[1]
    return gx, gy


def world(gx, gy, alt):
    n = math.sqrt(gx * gx + gy * gy + R * R)
    k = (R + alt) / n
    return (gx * k, gy * k, R * k)


def rel(p):
    return (p[0], p[1], p[2] - R0)


NL = 240
LIT = []
for i in range(NL + 1):
    s = S_LEN * i / NL
    gx, gy = track(s)
    p = world(gx, gy, ALT)
    LIT.append(sun_rgb(p))


def lit_at(s):
    f = max(0.0, min(NL - 1e-6, s / S_LEN * NL))
    i = int(f)
    a = f - i
    return [LIT[i][c] * (1 - a) + LIT[i + 1][c] * a for c in range(3)]


WIND = (WIND_V * math.sin(WIND_AZ), WIND_V * math.cos(WIND_AZ))
perp = (math.cos(HEAD), -math.sin(HEAD))
AMB = (0.00022, 0.00026, 0.00034)
CPH = 0.22
KB = 0.3
BLOOM = 0.12
TK = TSAT / SAT


SGRID = []
_s = 0.0
while _s < S_LEN:
    _gx, _gy = track(_s)
    _p = world(_gx, _gy, ALT)
    _q = project(rel(_p))
    if _q is None:
        _s += 0.3
        continue
    _ds = max(0.02, 0.35 * _q[2] / FOC)
    _lit = lit_at(_s)
    _dv = rel(_p)
    _mu = (_dv[0] * sun[0] + _dv[1] * sun[1] + _dv[2] * sun[2]) / _q[2]
    _ph = phase_m(_mu, 0.75) * 4 * math.pi * CPH
    _c = [_lit[c] * _ph + AMB[c] for c in range(3)]
    _cl = 0.2126 * _c[0] + 0.7152 * _c[1] + 0.0722 * _c[2]
    _c = tuple(_cl + (v - _cl) * TK for v in _c)
    SGRID.append((_s, _gx, _gy, _ds, _c, math.sin(_s * 0.31 + 1.3), math.sin(_s * 1.7 + 0.4) * math.sin(_s * 0.53 + 2.1)))
    _s += _ds


_style = {}


def sty(t, b):
    key = (t, b)
    s = _style.get(key)
    if s is None:
        s = Style(color=Color.from_rgb(*t), bgcolor=Color.from_rgb(*b))
        _style[key] = s
    return s


def splat(tau_px, emit, u, v, sig, amount, col):
    x0, x1 = int(u - 2.2 * sig), int(u + 2.2 * sig) + 1
    y0, y1 = int(v - 2.2 * sig), int(v + 2.2 * sig) + 1
    ws = []
    tot = 0.0
    i2 = 0.5 / (sig * sig)
    for yy in range(y0, y1 + 1):
        dy = (yy + 0.5 - v) ** 2
        for xx in range(x0, x1 + 1):
            w = math.exp(-((xx + 0.5 - u) ** 2 + dy) * i2)
            if w > 0.02:
                ws.append((xx, yy, w))
                tot += w
    if tot <= 0:
        return
    k = amount / tot
    for xx, yy, w in ws:
        if 0 <= xx < W and 0 <= yy < H:
            a = k * w
            key = yy * W + xx
            tau_px[key] = tau_px.get(key, 0.0) + a
            e = emit.get(key)
            if e is None:
                emit[key] = [col[0] * a, col[1] * a, col[2] * a]
            else:
                e[0] += col[0] * a
                e[1] += col[1] * a
                e[2] += col[2] * a


for frame in range(N):
    canvas.clear()
    if len(_style) > 20000:
        _style.clear()
    tl = (frame * DT + T0) % LOOP
    sp_ = VAPP * tl
    tau_px = {}
    emit = {}
    glow_px = {}
    gemit = {}
    for k, (s, gx0, gy0, ds, col, swob, spatch) in enumerate(SGRID):
        a0 = ((sp_ - s) / VAPP) % LOOP
        for age in (a0, a0 + LOOP):
            x = age / (2 * LOOP)
            if age <= 0 or x >= 1:
                continue
            fd = min(1.0, age / FADE_IN) ** 0.7 * (1 - x) ** DECAY * (1 + PATCH * x * spatch)
            if fd <= 0.003:
                continue
            ar = age * TC
            wob = WOB * x * swob
            gx = gx0 + WIND[0] * ar + perp[0] * wob
            gy = gy0 + WIND[1] * ar + perp[1] * wob
            kk = (R + ALT - SINK * ar) / math.sqrt(gx * gx + gy * gy + R * R)
            px_, py_, pz_ = gx * kk, gy * kk, R * kk - R0
            zc = px_ * fp[0] + py_ * fp[1] + pz_ * fp[2]
            if zc <= 0.01:
                continue
            u = W / 2 + FOC * (px_ * rh[0] + py_ * rh[1]) / zc
            v = H / 2 - FOC * (px_ * up[0] + py_ * up[1] + pz_ * up[2]) / zc
            if not (-4 < u < W + 4 and -4 < v < H + 4):
                continue
            kp = math.sqrt(px_ * px_ + py_ * py_ + pz_ * pz_) / FOC
            sh = K_SH * ar
            sig = max(SMIN, math.sqrt(SIG0 * SIG0 + sh * sh) / kp)
            amount = TAU_C * SIG0 * fd * ds / (kp * kp)
            splat(tau_px, emit, u, v, sig, amount, col)
            if BLOOM > 0 and age < LOOP and k % 4 == 0:
                splat(glow_px, gemit, u, v, max(1.4, 3 * sig), 4 * amount * BLOOM, col)

    img = [row[:] for row in SKY]
    lin_over = {}
    for key, tp in tau_px.items():
        yy, xx = divmod(key, W)
        al = 1 - math.exp(-tp)
        e = emit[key]
        sk = skyrows[yy][xx]
        lin_over[key] = [sk[c] * (1 - KB * al) + e[c] / tp * al for c in range(3)]

    for key, e in gemit.items():
        base = lin_over.get(key)
        if base is None:
            yy, xx = divmod(key, W)
            base = list(skyrows[yy][xx])
        lin_over[key] = [base[c] + e[c] for c in range(3)]

    gx, gy = track(sp_)
    q = project(rel(world(gx, gy, ALT)))
    if q is not None:
        u, v = q[0], q[1]
        lit = lit_at(sp_)
        pf = min(1.0, tl / 3.0)
        bl = 1.0 if frame % 10 == 0 else (0.35 if frame % 10 == 1 else 0.0)
        bl *= max(0.25, 1 - (lit[0] + lit[1]) / 0.04)
        add = [lit[c] * GLINT * pf for c in range(3)]
        add[0] += 0.006 * bl
        add[1] += 0.0004 * bl
        add[2] += 0.0003 * bl
        for yy in range(int(v) - 2, int(v) + 3):
            for xx in range(int(u) - 2, int(u) + 3):
                if 0 <= xx < W and 0 <= yy < H:
                    dd = (xx + 0.5 - u) ** 2 + (yy + 0.5 - v) ** 2
                    g = math.exp(-dd / 0.35) + 0.12 * math.exp(-dd / 2.0)
                    if g < 0.01:
                        continue
                    key = yy * W + xx
                    base = lin_over.get(key) or list(skyrows[yy][xx])
                    lin_over[key] = [base[c] + add[c] * g for c in range(3)]

    for key, lin in lin_over.items():
        yy, xx = divmod(key, W)
        img[yy][xx] = tone3(lin)

    for cy in range(height):
        line = Text()
        top, bot = img[2 * cy], img[2 * cy + 1]
        prev, run = None, 0
        for x in range(W):
            t = top[x]
            b = bot[x]
            st = sty((int(t[0]), int(t[1]), int(t[2])), (int(b[0]), int(b[1]), int(b[2])))
            if st is prev:
                run += 1
            else:
                if run:
                    line.append("▀" * run, prev)
                prev, run = st, 1
        if run:
            line.append("▀" * run, prev)
        canvas.write(line)

    await sleep(DT)

from rich.text import Text
from rich.style import Style
from rich.color import Color
import math
import random

# airshaft

N = 400
W = width
H = height * 2
TAU = math.pi * 2

XW, YD = 12.0, 18.0
GF, SH, NS = 3.6, 2.9, 14
HT = GF + NS * SH + 1.0
CX, CY, CZ = 2.6, 8.0, 1.6
TILTD = 12.0
FOCK = 0.72
PPY = 0.50
TILT = math.radians(TILTD)
FOC = FOCK * min(W, H / 1.25)

LP = (0.05, 9.2, 3.2)
IB = 20.0
RS = 1.6
LAMPC = (0.62, 0.82, 1.0)
SKYE = 0.5
SKYC = (0.95, 0.66, 0.50)
AMB = 0.006
XGAIN = 1.3
ALBC = (0.30, 0.32, 0.37)
ALBK = 0.75
ALB_AC = 0.36
EXPO = 2.6
BLEACH = 0.30
BG = (8, 8, 14)

VEIL_K = 0.028
VEILC = (0.90, 0.56, 0.30)
HALO = 0.38
RH = 5
HL = 1.7
SKYL = (0.008, 0.009, 0.02)
CLOUDL = (0.26, 0.16, 0.088)
CL_LO = 0.28
CL_HI = 0.80
CL_FLOOR = 0.0
CC1 = 4
NSTAR = 3
CC2 = 8
SKYHALO = 0.2
ZC = 600.0
WIND = 220.0

WLEN = (XW, YD, XW, YD)
BW = 2.0
KIT = (0.55, 1.45, 0.95, 2.15)
LIV = (0.30, 1.70, 0.90, 2.20)
BTH = (0.75, 1.25, 1.50, 2.15)
STR = (0.60, 1.40, 2.25, 3.25)
BAYS = (
    (KIT, LIV, BTH, STR, LIV, KIT),
    (LIV, KIT, BTH, LIV, KIT, LIV, BTH, KIT, LIV),
    (KIT, LIV, BTH, BTH, LIV, KIT),
    (LIV, BTH, KIT, LIV, KIT, LIV, BTH, KIT, LIV),
)
ROOF = (
    ((2.6, 5.8, 2.6), (7.2, 9.6, 1.4), (10.1, 10.24, 7.0), (9.4, 10.9, 6.4), (9.6, 10.7, 5.2)),
    ((7.4, 11.4, 3.4), (14.2, 14.34, 5.0), (15.0, 17.0, 1.8)),
    ((0.8, 3.4, 2.2), (6.0, 6.12, 4.0), (8.0, 11.0, 1.2)),
    ((3.6, 7.6, 2.4), (12.0, 15.4, 1.6), (9.3, 9.42, 6.0), (8.7, 10.0, 5.4)),
)
PIPES = ((2, 4), (3, 6), (3,), (1, 5, 8))
PIPESEL = ((), (), (), ())
PIPEU = tuple(tuple(k * 2.0 for k in PIPESEL[w]) for w in range(4))

TUNG = (1.0, 0.60, 0.26)
WARM = (1.0, 0.76, 0.50)
FLUO = (0.74, 1.0, 0.95)
TVC = (0.62, 0.80, 1.0)
TVL = 0.3
STAIRC = (1.0, 0.84, 0.64)
STAIRL = 0.45
LIT_SET = {
    (0, 0, 2): (0.75, TUNG, -1.0, 0.45),
    (0, 0, 5): (0.55, TUNG, 0.0, 0.0),
    (0, 0, 8): (0.70, TUNG, 1.0, 0.3),
    (0, 0, 12): (0.60, WARM, 0.0, 0.0),
    (2, 3, 11): (0.60, TUNG, 0.0, 0.0),
    (1, 5, 9): (0.50, TUNG, -1.0, 0.4),
}
LEDGEA = 1.0
LEDGEU = 0.25
CL_GAM = 0.7
PIPEA = 0.9
CAO = 0.0
TRIMW = 0.3
TRIMA = 1.4
LIT_T, LIT_W = 0.07, 0.03
STAIR_K = 3
STAIR_ON = ()
STAIR_TOP, STAIR_BOT = 5, 2
STAIR_F0, STAIR_DF, STAIR_HOLD = 290, 55, 100
KIT_KEY, KIT_F0, KIT_F1 = (0, 4, 2), 25, 150
TV_KEY = (0, 1, 6)
WLV = 1.0
ACP, ACW, ACH, ACD = 0.55, 0.75, 0.45, 0.55
BOXCUT = 0.6

WSEED = 7
rnd = random.Random(WSEED)
win = {}
boxes = {}
for w in range(4):
    for k, bay in enumerate(BAYS[w]):
        for s in range(NS):
            lit = None
            cur = (0.0, 0.0)
            if bay is STR:
                if s in STAIR_ON:
                    lit = tuple(c * 0.55 * WLV for c in FLUO)
            else:
                r = rnd.random()
                if r < LIT_T:
                    lv_ = rnd.uniform(0.35, 0.85) * WLV
                    lit = tuple(c * lv_ for c in TUNG)
                elif r < LIT_T + LIT_W:
                    lv_ = rnd.uniform(0.3, 0.6) * WLV
                    lit = tuple(c * lv_ for c in WARM)
                if lit and rnd.random() < 0.6:
                    side = rnd.choice((-1.0, 1.0))
                    cur = (side, rnd.uniform(0.25, 0.6))
            win[(w, k, s)] = [lit, cur]
            if bay is not STR and bay is not BTH and rnd.random() < ACP:
                mid = k * BW + (bay[0] + bay[1]) / 2 + rnd.uniform(-0.15, 0.15)
                zb = GF + s * SH + bay[2] - 0.62
                boxes[(w, k, s)] = (mid - ACW / 2, mid + ACW / 2, zb, zb + ACH, ACD)

for key in win:
    if BAYS[key[0]][key[1]] is not STR:
        win[key][0] = None
for key, (lv_, col_, cs_, cf_) in LIT_SET.items():
    win[key][0] = tuple(c * lv_ for c in col_)
    win[key][1] = (cs_, cf_)
DYN = [(KIT_KEY, TUNG, 0.7), (TV_KEY, TVC, TVL)]
for s in range(STAIR_TOP, STAIR_BOT - 1, -1):
    DYN.append(((0, STAIR_K, s), STAIRC, STAIRL))
for key, _, _ in DYN:
    win[key][0] = None
    win[key][1] = (0.0, 0.0)


def wall_point(w, u, z):
    if w == 0:
        return (u, YD, z), (0.0, -1.0, 0.0)
    if w == 1:
        return (XW, u, z), (-1.0, 0.0, 0.0)
    if w == 2:
        return (u, 0.0, z), (0.0, 1.0, 0.0)
    return (0.0, u, z), (1.0, 0.0, 0.0)


src = []
for (w, k, s), (lit, cur) in win.items():
    if lit:
        bu0, bu1, bz0, bz1 = BAYS[w][k]
        area = (bu1 - bu0) * (bz1 - bz0) * (1.0 - 0.6 * cur[1])
        p, n = wall_point(w, k * BW + (bu0 + bu1) / 2, GF + s * SH + (bz0 + bz1) / 2)
        src.append((w, p, n, lit[0] * area, lit[1] * area, lit[2] * area))

GU, GZ = 1.5, SH
grid = []
for w in range(4):
    nu = int(WLEN[w] / GU) + 2
    nz = int((HT - GF) / GZ) + 2
    gw = []
    for j in range(nz):
        z = GF + j * GZ
        row = []
        for m in range(nu):
            p, n = wall_point(w, m * GU, z)
            er = eg = eb = 0.0
            for sw, q, qn, ir, ig, ib in src:
                if sw == w:
                    continue
                vx, vy, vz = p[0] - q[0], p[1] - q[1], p[2] - q[2]
                d2 = vx * vx + vy * vy + vz * vz + 0.3
                ce = qn[0] * vx + qn[1] * vy + qn[2] * vz
                cr = -(n[0] * vx + n[1] * vy + n[2] * vz)
                if ce <= 0 or cr <= 0:
                    continue
                f = ce * cr / (d2 * d2)
                er += ir * f
                eg += ig * f
                eb += ib * f
            row.append((er * XGAIN, eg * XGAIN, eb * XGAIN))
        gw.append(row)
    grid.append(gw)


def cross(w, u, z):
    fu = min(max(u / GU, 0.0), len(grid[w][0]) - 1.001)
    fz = min(max((z - GF) / GZ, 0.0), len(grid[w]) - 1.001)
    m, j = int(fu), int(fz)
    au, az = fu - m, fz - j
    g0, g1 = grid[w][j], grid[w][j + 1]
    out = []
    for c in range(3):
        a0 = g0[m][c] + (g0[m + 1][c] - g0[m][c]) * au
        a1 = g1[m][c] + (g1[m + 1][c] - g1[m][c]) * au
        out.append(a0 + (a1 - a0) * az)
    return out


BPOS = (LP[0] + 1.2, LP[1], 0.0)


def lamp_e(p, n):
    vx, vy, vz = BPOS[0] - p[0], BPOS[1] - p[1], BPOS[2] - p[2]
    d = math.sqrt(vx * vx + vy * vy + vz * vz) + 1e-6
    cs = (n[0] * vx + n[1] * vy + n[2] * vz) / d
    em = -vz / d
    if cs <= 0 or em <= 0:
        return 0.0
    return IB * em * cs / (d * d + RS * RS)


def sky_e(z, wopp):
    dl = max(HT - z, 0.0)
    return SKYE * 0.5 * (1.0 - dl / math.sqrt(dl * dl + wopp * wopp))


def ov(a0, a1, b0, b1):
    lo = a0 if a0 > b0 else b0
    hi = a1 if a1 < b1 else b1
    return hi - lo if hi > lo else 0.0


def vplane(w, a, b, off):
    if w == 1 or w == 3:
        D = (XW - off - CX) if w == 1 else (off - CX)
        dy = s_ - b * c_
        dz = c_ + b * s_
        u = CY + D * dy / a
        z = CZ + D * dz / a
        hu = 0.5 / FOC * (abs(D * dy / (a * a)) + abs(D * c_ / a)) + 1e-4
        hz = 0.5 / FOC * (abs(D * dz / (a * a)) + abs(D * s_ / a)) + 1e-4
    else:
        D = (YD - off - CY) if w == 0 else (off - CY)
        dy = s_ - b * c_
        dz = c_ + b * s_
        u = CX + D * a / dy
        z = CZ + D * dz / dy
        hu = 0.5 / FOC * (abs(D / dy) + abs(D * a * c_ / (dy * dy))) + 1e-4
        hz = 0.5 / FOC * abs(D / (dy * dy)) + 1e-4
    return u, z, hu, hz


def hplane(w, a, b, zb):
    Hh = zb - CZ
    dz = c_ + b * s_
    x = CX + Hh * a / dz
    y = CY + Hh * (s_ - b * c_) / dz
    hx = 0.5 / FOC * (abs(Hh / dz) + abs(Hh * a * s_ / (dz * dz))) + 1e-4
    hy = 0.5 / FOC * abs(Hh / (dz * dz)) + 1e-4
    if w == 1:
        return XW - x, y, hx, hy
    if w == 3:
        return x, y, hx, hy
    if w == 0:
        return YD - y, x, hy, hx
    return y, x, hy, hx


def omega(z):
    D = max(HT - z, 0.0)
    return 4.0 * math.asin(min(1.0, XW * YD / math.sqrt((XW * XW + 4 * D * D) * (YD * YD + 4 * D * D))))


GZS = 0.25
Gt = [0.0]
_z = CZ
while _z < HT + GZS:
    Gt.append(Gt[-1] + GZS * (omega(_z) + omega(_z + GZS)) / (8 * math.pi))
    _z += GZS


def Gf(z):
    f = (z - CZ) / GZS
    j = int(f)
    if j >= len(Gt) - 1:
        return Gt[-1]
    return Gt[j] + (Gt[j + 1] - Gt[j]) * (f - j)


G0 = Gf(CZ)
s_, c_ = math.sin(TILT), math.cos(TILT)
vc = H * PPY
NP = W * H
statR = [0.0] * NP
statG = [0.0] * NP
statB = [0.0] * NP
skypix = []
dynpix = []
wacc = {}
dacc = {}

for py in range(H):
    b = (vc - (py + 0.5)) / FOC
    for px in range(W):
        i = py * W + px
        a = (px + 0.5 - W / 2) / FOC
        dx, dy, dz = a, s_ - b * c_, c_ + b * s_
        tx = (XW - CX) / dx if dx > 1e-9 else (-CX / dx if dx < -1e-9 else 1e9)
        ty = (YD - CY) / dy if dy > 1e-9 else (-CY / dy if dy < -1e-9 else 1e9)
        if tx < ty:
            w = 1 if dx > 0 else 3
            t = tx
            wopp = XW
            dperp = abs(dx)
            dalong = dy
        else:
            w = 0 if dy > 0 else 2
            t = ty
            wopp = YD
            dperp = abs(dy)
            dalong = dx
        u, z, hu, hz = vplane(w, a, b, 0.0)
        dist = t * math.sqrt(dx * dx + dy * dy + dz * dz)
        u0, u1, z0, z1 = u - hu, u + hu, z - hz, z + hz
        fa = 1.0 / ((u1 - u0) * (z1 - z0))
        above = ov(z0, z1, HT, 1e9) * (u1 - u0)
        roofc = 0.0
        for ru0, ru1, rh in ROOF[w]:
            roofc += ov(u0, u1, ru0, ru1) * ov(z0, z1, HT, HT + rh)
        sf = max(0.0, (above - roofc) * fa)
        if sf > 0:
            tc = (ZC - CZ) / dz
            gx_, gy_ = tc * dx, tc * dy
            skg = 0.85 + 0.3 * max(-1.0, min(1.0, (gy_ - gx_) / 260.0))
            skypix.append((i, CX + gx_, CY + gy_, sf * skg))
        zc = min(z, HT)
        p, n = wall_point(w, u, zc)
        e = lamp_e(p, n)
        es = sky_e(zc, wopp)
        xr, xg, xb = cross(w, u, zc)
        ao_ = 1.0 - CAO * math.exp(-min(u, WLEN[w] - u) / 0.6)
        er = (e * LAMPC[0] + es * SKYC[0] + xr + AMB) * ao_
        eg = (e * LAMPC[1] + es * SKYC[1] + xg + AMB) * ao_
        eb = (e * LAMPC[2] + es * SKYC[2] + xb + AMB) * ao_
        wr, wg, wb = ALBK * ALBC[0] * er, ALBK * ALBC[1] * eg, ALBK * ALBC[2] * eb
        r, g, bb = wr, wg, wb
        mydyn = []
        if z1 > GF - 0.3 and z0 < HT:
            s0 = max(0, int((z0 - GF) // SH) - 1)
            s1 = min(NS - 1, int((z1 - GF) // SH) + 1)
            for s in range(s0, s1 + 2):
                zs = GF + s * SH
                c = ov(z0, z1, zs - 0.15, zs + 0.15) * (u1 - u0) * fa
                if c > 0:
                    el = lamp_e((p[0], p[1], zs), (0.0, 0.0, -1.0))
                    r += c * (LEDGEA * (0.55 * er + LEDGEU * (el * LAMPC[0] + 0.6 * xr)) - wr)
                    g += c * (LEDGEA * (0.55 * eg + LEDGEU * (el * LAMPC[1] + 0.6 * xg)) - wg)
                    bb += c * (LEDGEA * (0.55 * eb + LEDGEU * (el * LAMPC[2] + 0.6 * xb)) - wb)
            c = (ov(u0, u1, -1.0, TRIMW) + ov(u0, u1, WLEN[w] - TRIMW, WLEN[w] + 1.0)) * (min(z1, HT) - max(z0, GF)) * fa
            if c > 0:
                r += c * (TRIMA * er - wr)
                g += c * (TRIMA * eg - wg)
                bb += c * (TRIMA * eb - wb)
            for pu in PIPEU[w]:
                c = ov(u0, u1, pu - 0.08, pu + 0.08) * (min(z1, HT) - max(z0, GF)) * fa
                if c > 0:
                    r += c * (PIPEA * er - wr)
                    g += c * (PIPEA * eg - wg)
                    bb += c * (PIPEA * eb - wb)
            bays = BAYS[w]
            k0 = max(0, int(u0 // BW))
            k1 = min(len(bays) - 1, int(u1 // BW))
            for s in range(s0, s1 + 1):
                zb = GF + s * SH
                for k in range(k0, k1 + 1):
                    bu0, bu1, bz0, bz1 = bays[k]
                    wu0, wu1 = k * BW + bu0, k * BW + bu1
                    fu = ov(u0, u1, wu0, wu1)
                    if fu <= 0:
                        continue
                    fz = ov(z0, z1, zb + bz0, zb + bz1)
                    if fz <= 0:
                        continue
                    lit, cur = win[(w, k, s)]
                    c = fu * fz * fa
                    cm = 0.0
                    if bu1 - bu0 > 0.8:
                        mm = (wu0 + wu1) / 2
                        cm += ov(u0, u1, mm - 0.035, mm + 0.035) * fz * fa
                    zt = zb + bz0 + 0.72 * (bz1 - bz0)
                    cm += fu * ov(z0, z1, zt - 0.035, zt + 0.035) * fa
                    cm = min(cm, c)
                    cg = c - cm
                    if lit:
                        gr, gg, gb = lit
                        acc = wacc.get((w, k, s))
                        if acc is None:
                            acc = wacc[(w, k, s)] = [0.0, 0.0, 0.0, dist, lit]
                        acc[0] += cg
                        acc[1] += cg * (px + 0.5)
                        acc[2] += cg * (py + 0.5)
                        if cur[0]:
                            cw_ = cur[1] * (wu1 - wu0)
                            cu0, cu1 = (wu0, wu0 + cw_) if cur[0] < 0 else (wu1 - cw_, wu1)
                            cc = min(cg, ov(u0, u1, cu0, cu1) * fz * fa)
                            r += cc * (gr * 0.30 - gr)
                            g += cc * (gg * 0.27 - gg)
                            bb += cc * (gb * 0.24 - gb)
                    else:
                        gr, gg, gb = 0.08 * er + 0.02 * es, 0.08 * eg + 0.025 * es, 0.10 * eb + 0.03 * es
                    r += cg * (gr - wr) + cm * (0.7 * wr - wr)
                    g += cg * (gg - wg) + cm * (0.7 * wg - wg)
                    bb += cg * (gb - wb) + cm * (0.7 * wb - wb)
                    for d_, dd in enumerate(DYN):
                        if dd[0] == (w, k, s):
                            mydyn.append((d_, cg))
                            acc = dacc.get(d_)
                            if acc is None:
                                acc = dacc[d_] = [0.0, 0.0, 0.0, dist]
                            acc[0] += cg
                            acc[1] += cg * (px + 0.5)
                            acc[2] += cg * (py + 0.5)
            slope = dz / dperp if dperp > 1e-6 else 1e9
            zlo = z0 - ACD * slope - 0.1
            sb0 = max(0, int((zlo - GF) // SH) - 1)
            ulo = min(u0, u0 - ACD * dalong / dperp if dperp > 1e-6 else u0) - 0.1
            uhi = max(u1, u1 - ACD * dalong / dperp if dperp > 1e-6 else u1) + 0.1
            kb0 = max(0, int(ulo // BW))
            kb1 = min(len(bays) - 1, int(uhi // BW))
            for s in (range(sb0, s1 + 1) if hz < BOXCUT else ()):
                for k in range(kb0, kb1 + 1):
                    bx = boxes.get((w, k, s))
                    if bx is None:
                        continue
                    ua, ub, za, zt_, dep = bx
                    dep_, ubb, hd, hub = hplane(w, a, b, za)
                    cb = ov(dep_ - hd, dep_ + hd, 0.0, dep) * ov(ubb - hub, ubb + hub, ua, ub) / (4 * hd * hub)
                    uf, zf, huf, hzf = vplane(w, a, b, dep)
                    cf = ov(uf - huf, uf + huf, ua, ub) * ov(zf - hzf, zf + hzf, za, zt_) / (4 * huf * hzf)
                    if cb + cf <= 0:
                        continue
                    cb = min(cb, 1.0)
                    cf = min(cf, 1.0 - cb)
                    pb = (p[0], p[1], za)
                    ebd = lamp_e(pb, (0.0, 0.0, -1.0))
                    xr2, xg2, xb2 = cross(w, (ua + ub) / 2, za)
                    ao = 0.45 + 0.55 * min(1.0, max(0.0, dep_ / dep)) ** 0.7
                    br_ = ALB_AC * ao * (ebd * LAMPC[0] + 0.55 * xr2 + AMB)
                    bg_ = ALB_AC * ao * (ebd * LAMPC[1] + 0.55 * xg2 + AMB)
                    bbb = ALB_AC * ao * (ebd * LAMPC[2] + 0.55 * xb2 + AMB * 1.2)
                    fr_, fg_, fb_ = ALB_AC * er * 0.85, ALB_AC * eg * 0.85, ALB_AC * eb * 0.85
                    keep = 1.0 - cb - cf
                    r = r * keep + cb * br_ + cf * fr_
                    g = g * keep + cb * bg_ + cf * fg_
                    bb = bb * keep + cb * bbb + cf * fb_
                    mydyn = [(d_, cg * keep) for d_, cg in mydyn]
        roofk = roofc * fa
        k_ = max(0.0, 1.0 - sf - roofk)
        statR[i] = r * k_ + roofk * 0.004
        statG[i] = g * k_ + roofk * 0.004
        statB[i] = bb * k_ + roofk * 0.006
        for d_, cg in mydyn:
            dynpix.append((i, d_, cg * k_))
        vl = VEIL_K * (Gf(min(z, HT)) - G0) * math.sqrt(dx * dx + dy * dy + dz * dz) / dz
        statR[i] += vl * VEILC[0]
        statG[i] += vl * VEILC[1]
        statB[i] += vl * VEILC[2]


def halo_pts(cx, cy, cov, dd):
    out = []
    rho = math.sqrt(cov / math.pi)
    amp = HALO * min(1.0, cov / 2.0) * (0.4 + 0.6 * min(dd, 40.0) / 40.0)
    rr = int(rho + RH) + 1
    for yy in range(int(cy) - rr, int(cy) + rr + 1):
        if yy < 0 or yy >= H:
            continue
        for xx in range(int(cx) - rr, int(cx) + rr + 1):
            if xx < 0 or xx >= W:
                continue
            r = max(0.0, math.hypot(xx + 0.5 - cx, yy + 0.5 - cy) - rho)
            if r <= RH:
                out.append((yy * W + xx, amp * math.exp(-r / HL) / (1.0 + r)))
    return out


for cov, sxs, sys_, dd, lit in wacc.values():
    if cov < 1e-3:
        continue
    for j, kk in halo_pts(sxs / cov, sys_ / cov, cov, dd):
        statR[j] += kk * lit[0]
        statG[j] += kk * lit[1]
        statB[j] += kk * lit[2]
_sx = [i % W for i, _, _, _ in skypix]
_sy = [i // W for i, _, _, _ in skypix]
if skypix:
    bx0, bx1, by0, by1 = min(_sx), max(_sx) + 1, min(_sy), max(_sy) + 1
    skymean = [SKYL[c] + (CLOUDL[c] - SKYL[c]) * (CL_FLOOR + (1 - CL_FLOOR) * 0.5) for c in range(3)]
    for yy in range(max(0, by0 - RH - 3), min(H, by1 + RH + 3)):
        for xx in range(max(0, bx0 - RH - 3), min(W, bx1 + RH + 3)):
            ddx = max(bx0 - (xx + 0.5), 0.0, (xx + 0.5) - bx1)
            ddy = max(by0 - (yy + 0.5), 0.0, (yy + 0.5) - by1)
            r = math.hypot(ddx, ddy)
            kk = SKYHALO * math.exp(-r / (HL * 1.6)) / (1.0 + 0.5 * r) * min(1.0, r * 2.0)
            j = yy * W + xx
            statR[j] += kk * skymean[0]
            statG[j] += kk * skymean[1]
            statB[j] += kk * skymean[2]
for d_, (cov, sxs, sys_, dd) in dacc.items():
    if cov < 1e-3:
        continue
    for j, kk in halo_pts(sxs / cov, sys_ / cov, cov, dd):
        dynpix.append((j, d_, kk))

VM = 64
_vr = random.Random(3)
VT = [[_vr.random() for _ in range(64)] for _ in range(VM)]


def vnoise(p, q, cells):
    S = WIND / cells
    fx, fy = p / S, q / S + 32.0
    ix, iy = math.floor(fx), math.floor(fy)
    tx, ty = fx - ix, fy - iy
    tx = tx * tx * (3 - 2 * tx)
    ty = ty * ty * (3 - 2 * ty)
    x0, x1 = ix % cells, (ix + 1) % cells
    r0, r1 = VT[iy % VM], VT[(iy + 1) % VM]
    a0 = r0[x0] + (r0[x1] - r0[x0]) * tx
    a1 = r1[x0] + (r1[x1] - r1[x0]) * tx
    return a0 + (a1 - a0) * ty


def cloud(x, y, f):
    p = x * 0.8 + y * 0.6 + WIND * f / N
    q = -x * 0.6 + y * 0.8
    v = 0.62 * vnoise(p, q * 0.8, CC1) + 0.38 * vnoise(p, q * 0.5, CC2)
    t = min(1.0, max(0.0, (v - CL_LO) / (CL_HI - CL_LO)))
    return CL_FLOOR + (1.0 - CL_FLOOR) * (t * t * (3 - 2 * t)) ** CL_GAM


_st = random.Random(5)
_full = [i for i, _, _, sf in skypix if sf >= 0.98]
stars = {i: _st.uniform(0.25, 0.6) for i in _st.sample(_full, min(NSTAR, len(_full)))}
SND = 4.5
DMIN = 0.3
TPEAK = 360
SZ = 0.022
SNOWA = 0.75
SNOWAMB = 0.03
EXPF = 0.6
ZS0, ZS1 = CZ + 0.6, CZ + 4.75
SPAN = ZS1 - ZS0
_sr = random.Random(19)
fx0, fx1 = 0.05, min(XW - 0.05, CX + 3.6)
fy0, fy1 = max(0.05, CY - 4.0), min(YD - 0.05, CY + 8.8)
NF = int((fx1 - fx0) * (fy1 - fy0) * SPAN * SND)
flakes = []
for _ in range(NF):
    mc = _sr.choice((4, 5, 6))
    flakes.append((_sr.uniform(fx0, fx1), _sr.uniform(fy0, fy1), _sr.uniform(0, SPAN),
                   mc * SPAN / N, _sr.uniform(0.10, 0.35), _sr.choice((2, 3, 4)),
                   _sr.uniform(0, TAU), _sr.uniform(0.7, 1.3), mc, [_sr.random() for _ in range(mc)]))


def dens(f):
    return DMIN + (1.0 - DMIN) * (0.5 + 0.5 * math.cos(TAU * (f - TPEAK) / N))
low_src = [(q, qn, ir, ig, ib) for (sw, q, qn, ir, ig, ib) in src if q[2] < ZS1 + 4.0]
Fv = (0.0, s_, c_)
Uv = (0.0, -c_, s_)


def proj(x, y, z):
    rx, ry, rz = x - CX, y - CY, z - CZ
    dep = ry * Fv[1] + rz * Fv[2]
    if dep < 0.12:
        return None
    return W / 2 + FOC * rx / dep, vc - FOC * (ry * Uv[1] + rz * Uv[2]) / dep, dep


def splat(R, G, B, x, y, c, cr, cg, cb):
    x -= 0.5
    y -= 0.5
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    for oy, wy in ((0, 1 - fy), (1, fy)):
        yy = iy + oy
        if yy < 0 or yy >= H:
            continue
        for ox, wx in ((0, 1 - fx), (1, fx)):
            xx = ix + ox
            if xx < 0 or xx >= W:
                continue
            k = c * wx * wy
            if k > 1.0:
                k = 1.0
            j = yy * W + xx
            R[j] += k * (cr - R[j])
            G[j] += k * (cg - G[j])
            B[j] += k * (cb - B[j])


def snow(R, G, B, frame):
    for x0, y0, z0, v, A, kk, ph, sz, mc, hs in flakes:
        z = ZS0 + (z0 - v * frame) % SPAN
        zp = ZS0 + (z0 - v * (frame - EXPF)) % SPAN
        if zp < z:
            continue
        cyc = math.floor((v * frame - z0) / SPAN)
        if hs[cyc % mc] > dens(((cyc * SPAN + z0) / v) % N):
            continue
        om = TAU * kk / N
        x = x0 + A * math.sin(om * frame + ph)
        y = y0 + A * math.cos(om * frame + ph * 1.3)
        p1 = proj(x, y, z)
        if p1 is None:
            continue
        sx, sy, dep = p1
        if sx < -4 or sx > W + 4 or sy < -4 or sy > H + 4:
            continue
        dpx = SZ * sz * FOC / dep
        cov = 0.785 * dpx * dpx
        vx, vy, vz = x - BPOS[0], y - BPOS[1], z
        d = math.sqrt(vx * vx + vy * vy + vz * vz)
        e = IB * (vz / d) / (d * d + RS * RS)
        er, eg, eb = e * LAMPC[0] + SNOWAMB, e * LAMPC[1] + SNOWAMB * 0.8, e * LAMPC[2] + SNOWAMB * 0.7
        for q, qn, ir, ig, ib in low_src:
            wx, wy, wz = x - q[0], y - q[1], z - q[2]
            d2 = wx * wx + wy * wy + wz * wz + 0.2
            ce = (qn[0] * wx + qn[1] * wy + qn[2] * wz) / math.sqrt(d2)
            if ce > 0:
                f = ce / d2
                er += ir * f
                eg += ig * f
                eb += ib * f
        cr, cg, cb = SNOWA * er, SNOWA * eg, SNOWA * eb
        p0 = proj(x0 + A * math.sin(om * (frame - EXPF) + ph), y0 + A * math.cos(om * (frame - EXPF) + ph * 1.3), zp)
        if p0 is None:
            p0 = p1
        L = math.hypot(sx - p0[0], sy - p0[1])
        n = 1 + int(L)
        c = cov / n
        for m in range(n):
            t = (m + 0.5) / n
            splat(R, G, B, p0[0] + (sx - p0[0]) * t, p0[1] + (sy - p0[1]) * t, c, cr, cg, cb)


def stair_level(s, frame):
    f_on = STAIR_F0 + (STAIR_TOP - s) * STAIR_DF
    dt = (frame - f_on) % N
    if dt >= STAIR_DF + STAIR_HOLD:
        return 0.0
    if dt == 0:
        return 0.55
    if dt == 1:
        return 0.08
    return 1.0


def tm(r, g, b):
    r *= EXPO
    g *= EXPO
    b *= EXPO
    L = 0.3 * r + 0.59 * g + 0.11 * b
    if L <= 1e-6:
        return BG
    Lm = 1.0 - math.exp(-L)
    k = Lm / L
    r, g, b = r * k, g * k, b * k
    mx = max(r, g, b)
    if mx > 1.0:
        t = (mx - 1.0) / (mx - Lm)
        r += (Lm - r) * t
        g += (Lm - g) * t
        b += (Lm - b) * t
    m = Lm * Lm * BLEACH
    r += (Lm * 1.04 - r) * m
    g += (Lm * 0.98 - g) * m
    b += (Lm * 0.88 - b) * m
    return (int(BG[0] + (255 - BG[0]) * min(1.0, r)), int(BG[1] + (255 - BG[1]) * min(1.0, g)),
            int(BG[2] + (255 - BG[2]) * min(1.0, b)))


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
    R = statR[:]
    G = statG[:]
    B = statB[:]
    for i, x, y, sf in skypix:
        cl = cloud(x, y, frame)
        st = stars.get(i, 0.0) * (1.0 - cl)
        R[i] += sf * (SKYL[0] + cl * (CLOUDL[0] - SKYL[0]) + st * 0.9)
        G[i] += sf * (SKYL[1] + cl * (CLOUDL[1] - SKYL[1]) + st * 0.95)
        B[i] += sf * (SKYL[2] + cl * (CLOUDL[2] - SKYL[2]) + st)
    on0 = 1.0 if KIT_F0 <= frame < KIT_F1 else 0.0
    tv = 0.62 + 0.22 * math.sin(TAU * 7 * frame / N) + 0.12 * math.sin(TAU * 19 * frame / N + 1.0)
    levels = [on0 * DYN[0][2], tv * DYN[1][2]]
    for d_ in range(2, len(DYN)):
        levels.append(stair_level(DYN[d_][0][2], frame) * DYN[d_][2])
    for i, d_, c in dynpix:
        col = DYN[d_][1]
        lv = levels[d_]
        R[i] += c * col[0] * lv
        G[i] += c * col[1] * lv
        B[i] += c * col[2] * lv
    snow(R, G, B, frame)
    px_ = [tm(R[j], G[j], B[j]) for j in range(NP)]
    for cy in range(height):
        line = Text()
        o1, o2 = 2 * cy * W, (2 * cy + 1) * W
        prev, run = None, 0
        for x in range(W):
            st_ = sty(px_[o1 + x], px_[o2 + x])
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

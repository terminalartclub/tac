from rich.text import Text
from rich.style import Style
from rich.color import Color
import math

# laps

N = 300
W = width
H = height * 2
TAU = math.pi * 2

R = 11.0
Z_TOP = 7.0
Z_BASE = -9.5
Z_WATER = 4.0
NW = 1.333
R0_G = 0.04
R0_W = 0.02

CAM = (0.0, -62.0, 13.0)
AIM = (0.0, 0.0, -1.5)
PX = max(0.364, 2 * 11.0 / (0.76 * W), 2 * 11.0 / (0.55 * H))
CY = 0.46

LDIR = (-0.55, -0.35, 0.75)
LCOL = (0.84, 0.90, 1.0)
AMB = (0.030, 0.034, 0.055)
AMB_T = (0.060, 0.066, 0.090)
ROOM = (0.010, 0.012, 0.020)
WIN = 45.0
WIN_R = 0.08
BG = (8, 8, 15)
EXPO = 3.3

SIG = (0.014, 0.007, 0.003)
SCAT = (0.040, 0.072, 0.120)
SCAT_L = 22.0

TABLE_A = (0.115, 0.106, 0.098)
WIN_W = 32.0
WIN_H = 53.0
WIN_C = (2.5, 1.6)
MULL = 3.0
MULL_U = None
MULL_V = -13.0
TABLE_Y1 = 14.0
FEATHER = 3.0
PEN_A, PEN_B = 6.0, 14.0
PEN_C, PEN_D = 14.0, 26.0
CAUS_MAX = 4.0
SHADOW = 0.55

FISH_L = 2.5
FISH_B = 0.95
FISH_C = 1.45
TAIL_L = 2.6
ORBIT_R = 5.5
ORBIT_Z = -2.0
LAPS = 2
F_HOVER = 150
HOVER_W = 26.0
TAIL_IDLE = 0.8
TAIL_K = 1.6
TAIL_AMP = 0.42
BODY_AMP = 0.10
FISH_TOP = (1.0, 0.30, 0.02)
FISH_BELLY = (1.0, 0.58, 0.20)
FISH_GAIN = 1.3
SPEC = 0.6
TAIL_COL = (1.0, 0.40, 0.06)
TAIL_GLOW = (0.30, 0.12, 0.03)
TAIL_ALPHA = 0.6
FORK = 0.55
FIN_X0, FIN_X1, FIN_H = -1.1, 0.7, 0.7
FILL = 0.38
RIM_K = 0.55
LIMB_K = 0.30
SUB = ((-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25))
NBIN = 16


def norm(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / l, v[1] / l, v[2] / l)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def add(a, b, t=1.0):
    return (a[0] + b[0] * t, a[1] + b[1] * t, a[2] + b[2] * t)


def refract(d, n, eta):
    c = -dot(d, n)
    k = 1.0 - eta * eta * (1.0 - c * c)
    if k < 0:
        return None
    s = eta * c - math.sqrt(k)
    return (eta * d[0] + s * n[0], eta * d[1] + s * n[1], eta * d[2] + s * n[2])


def reflect(d, n):
    k = 2.0 * dot(d, n)
    return (d[0] - k * n[0], d[1] - k * n[1], d[2] - k * n[2])


def fresnel(cosi, r0):
    m = 1.0 - cosi
    return r0 + (1.0 - r0) * m * m * m * m * m


L = norm(LDIR)
_dw = refract((-L[0], -L[1], -L[2]), (0.0, 0.0, 1.0), 1.0 / NW)
LW = (-_dw[0], -_dw[1], -_dw[2])


def env(d):
    ca = max(-1.0, min(1.0, dot(d, L)))
    ang = math.acos(ca)
    k = math.exp(-(ang / WIN_R) ** 2) * WIN + 0.18 * math.exp(-(ang / 0.55) ** 2)
    up = 0.5 + 0.5 * d[2]
    return (ROOM[0] * up + LCOL[0] * k, ROOM[1] * up + LCOL[1] * k, ROOM[2] * up + LCOL[2] * k)


def sphere_hits(o, d):
    b = dot(o, d)
    c = dot(o, o) - R * R
    disc = b * b - c
    if disc <= 0:
        return None
    s = math.sqrt(disc)
    return (-b - s, -b + s)


# ── caustic: forward-trace the window light through the bowl onto the table ──
MAP_R = 26.0
MAP_C = 0.4
MAP_N = int(2 * MAP_R / MAP_C)
imap = [[0.0] * MAP_N for _ in range(MAP_N)]

# the window, projected along the light onto the table: across = perpendicular to the light's
# horizontal travel, along = height / tan(elevation); feathered by the source's angular size
_lh = math.hypot(L[0], L[1])
ELEV = math.atan2(L[2], _lh)
TDIR = (-L[0] / _lh, -L[1] / _lh)
ACROSS = (TDIR[1], -TDIR[0])
WIN_HD = WIN_H / math.tan(ELEV)
FEATHER_V = FEATHER / math.sin(ELEV)


def smooth(a, b, v):
    t = min(1.0, max(0.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


def win_mask(x, y):
    dx, dy = x - WIN_C[0], y - WIN_C[1]
    u = dx * ACROSS[0] + dy * ACROSS[1]
    v = dx * TDIR[0] + dy * TDIR[1]
    m = smooth(-FEATHER / 2, FEATHER / 2, WIN_W / 2 - abs(u)) * smooth(-FEATHER_V / 2, FEATHER_V / 2, WIN_HD / 2 - abs(v))
    if MULL > 0:
        if MULL_U is not None:
            m *= 1.0 - smooth(-FEATHER * 0.3, FEATHER * 0.3, MULL / 2 - abs(u - MULL_U))
        if MULL_V is not None:
            m *= 1.0 - smooth(-FEATHER_V * 0.3, FEATHER_V * 0.3, MULL / (2 * math.tan(ELEV)) - abs(v - MULL_V))
    return m


def splat(x, y, v):
    fx = (x + MAP_R) / MAP_C - 0.5
    fy = (y + MAP_R) / MAP_C - 0.5
    ix, iy = int(math.floor(fx)), int(math.floor(fy))
    tx, ty = fx - ix, fy - iy
    for dy, wy in ((0, 1 - ty), (1, ty)):
        yy = iy + dy
        if 0 <= yy < MAP_N:
            row = imap[yy]
            for dx, wx in ((0, 1 - tx), (1, tx)):
                xx = ix + dx
                if 0 <= xx < MAP_N:
                    row[xx] += v * wx * wy


def light_trace(o, d):
    hits = sphere_hits(o, d)
    if hits is None:
        return None
    t0, t1 = hits
    p = add(o, d, t0)
    if p[2] < Z_BASE:
        return None
    w = 1.0
    if p[2] <= Z_TOP and p[2] <= Z_WATER:
        n = (p[0] / R, p[1] / R, p[2] / R)
        cosi = -dot(d, n)
        w *= 1.0 - fresnel(cosi, R0_G)
        d2 = refract(d, n, 1.0 / NW)
        if d2 is None:
            return None
        d = d2
        med = 1
    else:
        med = 0
    for _ in range(5):
        hits = sphere_hits(p, d)
        te = hits[1] if hits else 0.0
        tp = (Z_WATER - p[2]) / d[2] if abs(d[2]) > 1e-9 else -1.0
        tb = (Z_BASE - p[2]) / d[2] if d[2] < -1e-9 else -1.0
        ev, t = "exit", te
        if 1e-6 < tp < t:
            ev, t = "plane", tp
        if 1e-6 < tb < t:
            ev, t = "base", tb
        q = add(p, d, t)
        if ev == "base":
            return (q[0], q[1], w * 0.92)
        if ev == "plane":
            if med == 0:
                cosi = -d[2]
                w *= 1.0 - fresnel(cosi, R0_W)
                d = refract(d, (0.0, 0.0, 1.0), 1.0 / NW)
                med = 1
            else:
                d2 = refract(d, (0.0, 0.0, -1.0), NW)
                if d2 is None:
                    d = (d[0], d[1], -d[2])
                else:
                    w *= 1.0 - fresnel(d[2], R0_W)
                    d = d2
                    med = 0
            p = q
            continue
        n = (q[0] / R, q[1] / R, q[2] / R)
        if med == 1:
            nin = (-n[0], -n[1], -n[2])
            d2 = refract(d, nin, NW)
            if d2 is None:
                d = reflect(d, nin)
                p = q
                continue
            w *= 1.0 - fresnel(-dot(d, nin), R0_G)
            d = d2
        p = q
        if d[2] < -1e-9:
            t = (Z_BASE - p[2]) / d[2]
            q = add(p, d, t)
            return (q[0], q[1], w)
        return None
    return None


_e1 = norm(cross(L, (0.0, 0.0, 1.0)))
_e2 = cross(_e1, L)
_dl = (-L[0], -L[1], -L[2])
LS = 0.3
_ray_w = (LS * LS / L[2]) / (MAP_C * MAP_C)
_nr = int(R * 1.05 / LS)
for _iu in range(-_nr, _nr + 1):
    for _iv in range(-_nr, _nr + 1):
        u, v = _iu * LS, _iv * LS
        if u * u + v * v > (R * 1.02) ** 2:
            continue
        o = (L[0] * 40 + _e1[0] * u + _e2[0] * v, L[1] * 40 + _e1[1] * u + _e2[1] * v, L[2] * 40 + _e1[2] * u + _e2[2] * v)
        hits = sphere_hits(o, _dl)
        if hits is None:
            continue
        p = add(o, _dl, hits[0])
        if p[2] < Z_BASE:
            continue
        tt = (Z_BASE - o[2]) / _dl[2]
        qx, qy = o[0] + _dl[0] * tt, o[1] + _dl[1] * tt
        m0 = win_mask(qx, qy)
        if m0 <= 0.0:
            continue
        splat(qx, qy, -_ray_w * m0)
        res = light_trace(o, _dl)
        if res is not None:
            splat(res[0], res[1], _ray_w * m0 * res[2])

_K7 = (1 / 64, 6 / 64, 15 / 64, 20 / 64, 15 / 64, 6 / 64, 1 / 64)
_ys = [y for y in range(MAP_N) if any(imap[y])]
_xs = [x for x in range(MAP_N) if any(imap[y][x] for y in _ys)]
_y0, _y1 = max(0, min(_ys) - 14), min(MAP_N, max(_ys) + 15)
_x0, _x1 = max(0, min(_xs) - 14), min(MAP_N, max(_xs) + 15)


def blur7(src, stride):
    tmp = [[0.0] * MAP_N for _ in range(MAP_N)]
    out = [[0.0] * MAP_N for _ in range(MAP_N)]
    for y in range(_y0, _y1):
        row, o_ = src[y], tmp[y]
        for x in range(_x0, _x1):
            s = 0.0
            for k in range(7):
                s += row[min(MAP_N - 1, max(0, x + (k - 3) * stride))] * _K7[k]
            o_[x] = s
    for x in range(_x0, _x1):
        col = [tmp[y][x] for y in range(MAP_N)]
        for y in range(_y0, _y1):
            s = 0.0
            for k in range(7):
                s += col[min(MAP_N - 1, max(0, y + (k - 3) * stride))] * _K7[k]
            out[y][x] = s
    return out


# penumbra grows with distance from the base: three blur levels blended by radius
dev_a = blur7(imap, 1)
dev_b = blur7(dev_a, 2)
dev_c = blur7(dev_b, 4)


def _at(mp, x, y):
    fx = (x + MAP_R) / MAP_C - 0.5
    fy = (y + MAP_R) / MAP_C - 0.5
    ix, iy = min(MAP_N - 2, int(fx)), min(MAP_N - 2, int(fy))
    tx, ty = fx - ix, fy - iy
    r0, r1 = mp[iy], mp[iy + 1]
    return (r0[ix] * (1 - tx) + r0[ix + 1] * tx) * (1 - ty) + (r1[ix] * (1 - tx) + r1[ix + 1] * tx) * ty


def dev_at(x, y):
    if abs(x) >= MAP_R - 2 * MAP_C or abs(y) >= MAP_R - 2 * MAP_C:
        return 0.0
    r = math.hypot(x, y)
    wb = smooth(PEN_A, PEN_B, r)
    wc = smooth(PEN_C, PEN_D, r)
    a = _at(dev_a, x, y)
    if wb > 0:
        a += (_at(dev_b, x, y) - a) * wb
    if wc > 0:
        a += (_at(dev_c, x, y) - a) * wc
    return a


def table_lit(x, y, shade=1.0):
    return min(CAUS_MAX, max(0.0, win_mask(x, y) + dev_at(x, y))) * L[2] * shade


def table_rgb(lit):
    return (TABLE_A[0] * (AMB_T[0] + LCOL[0] * lit), TABLE_A[1] * (AMB_T[1] + LCOL[1] * lit), TABLE_A[2] * (AMB_T[2] + LCOL[2] * lit))


# ── camera: trace every pixel once; the bowl, table and glass never move ──
_f = norm((AIM[0] - CAM[0], AIM[1] - CAM[1], AIM[2] - CAM[2]))
_r = norm(cross(_f, (0.0, 0.0, 1.0)))
_u = cross(_r, _f)
_D = math.hypot(AIM[0] - CAM[0], AIM[1] - CAM[1], AIM[2] - CAM[2])
RIM_R = math.sqrt(R * R - Z_TOP * Z_TOP)
MEN_R = math.sqrt(R * R - Z_WATER * Z_WATER)
_NC = 96
_circ = [(math.cos(TAU * k / _NC), math.sin(TAU * k / _NC)) for k in range(_NC)]


def ring_cover(o, d, rr, zz, width_):
    if abs(d[2]) < 1e-9:
        return 0.0, 0.0
    t = (zz - o[2]) / d[2]
    if t <= 0:
        return 0.0, 0.0
    q = add(o, d, t)
    if abs(math.hypot(q[0], q[1]) - rr) > 2.5:
        return 0.0, 0.0

    def d2(ang):
        wx, wy, wz = rr * math.cos(ang) - o[0], rr * math.sin(ang) - o[1], zz - o[2]
        tc = wx * d[0] + wy * d[1] + wz * d[2]
        ex, ey, ez = wx - d[0] * tc, wy - d[1] * tc, wz - d[2] * tc
        return ex * ex + ey * ey + ez * ez

    best, bk = 9.0, 0
    for k, (cx, sx) in enumerate(_circ):
        wx, wy, wz = rr * cx - o[0], rr * sx - o[1], zz - o[2]
        tc = wx * d[0] + wy * d[1] + wz * d[2]
        ex, ey, ez = wx - d[0] * tc, wy - d[1] * tc, wz - d[2] * tc
        dd = ex * ex + ey * ey + ez * ez
        if dd < best:
            best, bk = dd, k
    da = TAU / _NC
    a0 = bk * da
    fm, f0, fp = d2(a0 - da), best, d2(a0 + da)
    den = fm - 2 * f0 + fp
    ang = a0 + (0.5 * da * (fm - fp) / den if abs(den) > 1e-12 else 0.0)
    best = min(best, d2(ang))
    return max(0.0, min(1.0, 1.0 - math.sqrt(best) / width_)), ang


def cam_trace(d):
    d0 = d
    acc = [0.0, 0.0, 0.0]
    mult = [1.0, 1.0, 1.0]
    segs = []
    landing = None
    o = CAM
    hits = sphere_hits(o, d)
    tt = (Z_BASE - o[2]) / d[2] if d[2] < -1e-9 else -1.0
    t0 = hits[0] if hits else -1.0
    p = add(o, d, t0) if hits else None
    bb = dot(o, d)
    dc = math.sqrt(max(0.0, dot(o, o) - bb * bb))
    limb = max(0.0, 1.0 - abs(dc - R) / 0.45)
    zt = o[2] - d[2] * bb
    if not (Z_BASE <= zt <= Z_TOP):
        limb = 0.0
    if hits and t0 > 0 and p[2] >= Z_BASE and (tt < 0 or t0 < tt):
        if p[2] <= Z_TOP:
            n = (p[0] / R, p[1] / R, p[2] / R)
            cosi = -dot(d, n)
            fr = fresnel(cosi, R0_G)
            e = env(reflect(d, n))
            acc[0] += fr * e[0]
            acc[1] += fr * e[1]
            acc[2] += fr * e[2]
            mult = [m * (1 - fr) for m in mult]
            if p[2] <= Z_WATER:
                d2 = refract(d, n, 1.0 / NW)
                d = d2
                med = 1
            else:
                med = 0
        else:
            med = 0
        for _ in range(6):
            hh = sphere_hits(p, d)
            te = hh[1] if hh else 0.0
            tp = (Z_WATER - p[2]) / d[2] if abs(d[2]) > 1e-9 else -1.0
            tb = (Z_BASE - p[2]) / d[2] if d[2] < -1e-9 else -1.0
            ev, t = "exit", te
            if 1e-6 < tp < t:
                ev, t = "plane", tp
            if 1e-6 < tb < t:
                ev, t = "base", tb
            q = add(p, d, t)
            if med == 1:
                segs.append((p, d, t, tuple(mult), tuple(acc)))
                zk = 0.55 + 0.45 * ((p[2] + q[2]) * 0.5 - Z_BASE) / (Z_WATER - Z_BASE)
                for i in range(3):
                    acc[i] += mult[i] * SCAT[i] * zk * (1.0 - math.exp(-t / SCAT_L))
                    mult[i] *= math.exp(-SIG[i] * t)
            if ev == "base":
                landing = (q[0], q[1], 0.92)
                break
            if ev == "plane":
                if med == 0:
                    cosi = -d[2]
                    fr = fresnel(cosi, R0_W)
                    e = env(reflect(d, (0.0, 0.0, 1.0)))
                    for i in range(3):
                        acc[i] += mult[i] * fr * e[i]
                        mult[i] *= 1 - fr
                    d = refract(d, (0.0, 0.0, 1.0), 1.0 / NW)
                    med = 1
                else:
                    d2 = refract(d, (0.0, 0.0, -1.0), NW)
                    if d2 is None:
                        d = (d[0], d[1], -d[2])
                    else:
                        fr = fresnel(d[2], R0_W)
                        mult = [m * (1 - fr) for m in mult]
                        d = d2
                        med = 0
                p = q
                continue
            n = (q[0] / R, q[1] / R, q[2] / R)
            if med == 1:
                nin = (-n[0], -n[1], -n[2])
                d2 = refract(d, nin, NW)
                if d2 is None:
                    d = reflect(d, nin)
                    p = q
                    continue
                fr = fresnel(-dot(d, nin), R0_G)
                mult = [m * (1 - fr) for m in mult]
                d = d2
            p = q
            if d[2] < -1e-9:
                t2 = (Z_BASE - p[2]) / d[2]
                q2 = add(p, d, t2)
                landing = (q2[0], q2[1], 1.0)
            else:
                e = env(d)
                for i in range(3):
                    acc[i] += mult[i] * e[i]
            break
    elif tt > 0:
        q = add(o, d, tt)
        landing = (q[0], q[1], 1.0)
    else:
        e = env(d)
        for i in range(3):
            acc[i] += mult[i] * e[i]
    lit0 = 0.0
    if landing is not None and landing[1] > TABLE_Y1:
        landing = None
    if landing is not None:
        lit0 = table_lit(landing[0], landing[1], landing[2])
        tr_ = table_rgb(lit0)
        for i in range(3):
            acc[i] += mult[i] * tr_[i]
    rc, ra = ring_cover(CAM, d0, RIM_R, Z_TOP, 0.30)
    mc, ma = ring_cover(CAM, d0, MEN_R, Z_WATER, 0.26)
    for cov, ang, k in ((rc, ra, RIM_K), (mc, ma, RIM_K * 0.5)):
        if cov > 0:
            side = 0.35 + 0.65 * max(0.0, (math.cos(ang) * L[0] + math.sin(ang) * L[1]) / math.hypot(L[0], L[1]))
            for i in range(3):
                acc[i] += cov * k * (0.05 + LCOL[i] * 0.5 * side)
    if limb > 0 and (hits is None or p[2] >= Z_BASE):
        side = 0.3 + 0.7 * max(0.0, -(d0[0] * L[0] + d0[1] * L[1]))
        for i in range(3):
            acc[i] += limb * LIMB_K * (0.04 + LCOL[i] * 0.3 * side)
    return acc, mult, segs, landing, lit0


_orb = [(ORBIT_R * math.cos(TAU * k / NBIN), ORBIT_R * math.sin(TAU * k / NBIN), ORBIT_Z) for k in range(NBIN)]
FISH_BR = FISH_L + TAIL_L + 0.6
_BR2 = (FISH_BR + 0.9 + ORBIT_R * math.pi / NBIN + 0.3) ** 2


def seg_bins(p, d, t):
    mask = 0
    for k, c in enumerate(_orb):
        wx, wy, wz = c[0] - p[0], c[1] - p[1], c[2] - p[2]
        tc = wx * d[0] + wy * d[1] + wz * d[2]
        tc = 0.0 if tc < 0 else (t if tc > t else tc)
        ex, ey, ez = wx - d[0] * tc, wy - d[1] * tc, wz - d[2] * tc
        if ex * ex + ey * ey + ez * ez < _BR2:
            mask |= 1 << k
    return mask


def pix_dir(i, j, ox=0.0, oy=0.0):
    v = (CY * H - (j + 0.5 + oy)) * PX
    u = (i + 0.5 + ox - W / 2) * PX
    return norm((_f[0] * _D + _r[0] * u + _u[0] * v, _f[1] * _D + _r[1] * u + _u[1] * v, _f[2] * _D + _r[2] * u + _u[2] * v))


base = [[(0.0, 0.0, 0.0)] * W for _ in range(H)]
pix_fish = []
pix_land = []
for j in range(H):
    for i in range(W):
        acc, mult, segs, landing, lit0 = cam_trace(pix_dir(i, j))
        base[j][i] = (acc[0], acc[1], acc[2])
        if landing is not None:
            pix_land.append((i, j, landing[0], landing[1], lit0, tuple(mult)))
        mask = 0
        for p, dd, t, m, a in segs:
            mask |= seg_bins(p, dd, t)
        if mask:
            pix_fish.append((i, j, mask))
bins = [[pf for pf in pix_fish if pf[2] & (1 << b)] for b in range(NBIN)]
sub_cache = {}


def sub_rays(i, j):
    subs = sub_cache.get((i, j))
    if subs is None:
        subs = []
        for ox, oy in SUB:
            sa, sm, ss, sl, _ = cam_trace(pix_dir(i, j, ox, oy))
            subs.append((tuple(sa), ss))
        sub_cache[(i, j)] = subs
    return subs

# ── fish motion tables: two laps, one hover, integer tail beats → seamless ──
_spd = []
for f in range(N):
    df = (f - F_HOVER + N / 2) % N - N / 2
    _spd.append(1.0 - 0.94 * math.exp(-(df / HOVER_W) ** 2))
_tot = sum(_spd)
theta = []
_th = -math.pi / 2 - TAU * LAPS * 0.5
for f in range(N):
    theta.append(_th)
    _th += TAU * LAPS * _spd[f] / _tot
_rate = [TAIL_IDLE + TAIL_K * s for s in _spd]
_beats = max(1, round(sum(_rate) * 0.05))
_scale = _beats * TAU / sum(_rate)
tail_ph = []
_tp = 0.0
for f in range(N):
    tail_ph.append(_tp)
    _tp += _rate[f] * _scale


def prof(s):
    e = 1.0 - ((s - 0.58) / 0.44) ** 2
    body = math.sqrt(e) if e > 0 else 0.0
    return max(body, 0.26 if s < 0.6 else 0.0)


_PT = [prof(k / 128) for k in range(129)]


def profx(x):
    s = (x + FISH_L) / (2 * FISH_L) * 128
    if s <= 0 or s >= 128:
        return 0.0
    k = int(s)
    return _PT[k] + (_PT[k + 1] - _PT[k]) * (s - k)


def body_hit(pl, dl, tmax):
    a, b, c = FISH_L, FISH_B * 1.02, FISH_C * 1.02
    px_, py_, pz_ = pl[0] / a, pl[1] / b, pl[2] / c
    dx_, dy_, dz_ = dl[0] / a, dl[1] / b, dl[2] / c
    A = dx_ * dx_ + dy_ * dy_ + dz_ * dz_
    B = px_ * dx_ + py_ * dy_ + pz_ * dz_
    C = px_ * px_ + py_ * py_ + pz_ * pz_ - 1.0
    disc = B * B - A * C
    if disc <= 0:
        return None
    sq = math.sqrt(disc)
    t_in, t_out = (-B - sq) / A, (-B + sq) / A
    if t_out <= 0 or t_in > tmax:
        return None
    t = max(t_in, 0.0)
    step = 0.16
    prev_t = None
    while t < t_out:
        h = profx(pl[0] + dl[0] * t)
        if h > 0:
            y = (pl[1] + dl[1] * t) / (FISH_B * h)
            z = (pl[2] + dl[2] * t) / (FISH_C * h)
            if y * y + z * z < 1.0:
                if prev_t is not None:
                    lo, hi = prev_t, t
                    for _ in range(4):
                        mid = 0.5 * (lo + hi)
                        h = profx(pl[0] + dl[0] * mid)
                        if h <= 0:
                            lo = mid
                            continue
                        y = (pl[1] + dl[1] * mid) / (FISH_B * h)
                        z = (pl[2] + dl[2] * mid) / (FISH_C * h)
                        if y * y + z * z < 1.0:
                            hi = mid
                        else:
                            lo = mid
                    t = hi
                return t if t <= tmax else None
        prev_t = t
        t += step
    return None


def body_normal(hp):
    h = max(profx(hp[0]), 0.05)
    dh = (profx(hp[0] + 0.03) - profx(hp[0] - 0.03)) / 0.06
    ny = 2 * hp[1] / (FISH_B * FISH_B * h * h)
    nz = 2 * hp[2] / (FISH_C * FISH_C * h * h)
    nx = -(hp[1] * hp[1] / (FISH_B * FISH_B) + hp[2] * hp[2] / (FISH_C * FISH_C)) * 2 * dh / (h * h * h)
    return norm((nx, ny, nz))


def tail_h(x):
    u = min(1.0, max(0.0, -x / TAIL_L))
    return 0.30 + 1.15 * u ** 0.85


def tail_fork(x):
    u = min(1.0, max(0.0, (-x / TAIL_L - 0.55) / 0.45))
    return FORK * u


def fin_h(x):
    u = (x - FIN_X0) / (FIN_X1 - FIN_X0)
    return FIN_H * math.sin(math.pi * u) if 0.0 < u < 1.0 else 0.0


def tone(v):
    return int(255 * (1.0 - math.exp(-v * EXPO)))


_style = {}


def sty(t, b):
    key = (t, b)
    s = _style.get(key)
    if s is None:
        s = Style(color=Color.from_rgb(*t), bgcolor=Color.from_rgb(*b))
        _style[key] = s
    return s


def shade(nw, alb, tint, d, two_sided=False):
    dif = dot(nw, LW)
    dif = abs(dif) if two_sided else max(0.0, dif)
    fill = FILL * max(0.0, -nw[2])
    hv = norm((LW[0] - d[0], LW[1] - d[1], LW[2] - d[2]))
    sp = max(0.0, dot(nw, hv)) ** 28 * SPEC
    return ((alb[0] * (AMB[0] * 2 + LCOL[0] * dif * 0.9 + 0.9 * fill) * FISH_GAIN + sp * LCOL[0]) * tint[0],
            (alb[1] * (AMB[1] * 2 + LCOL[1] * dif * 0.9 + 0.8 * fill) * FISH_GAIN + sp * LCOL[1]) * tint[1],
            (alb[2] * (AMB[2] * 2 + LCOL[2] * dif * 0.9 + 0.7 * fill) * FISH_GAIN + sp * LCOL[2]) * tint[2])


def fish_colour(p, d, tmax, m, a, fc, hx, hy, ct, st_, tail_root):
    wx, wy, wz = fc[0] - p[0], fc[1] - p[1], fc[2] - p[2]
    tc = wx * d[0] + wy * d[1] + wz * d[2]
    tcc = 0.0 if tc < 0 else (tmax if tc > tmax else tc)
    ex, ey, ez = wx - d[0] * tcc, wy - d[1] * tcc, wz - d[2] * tcc
    if ex * ex + ey * ey + ez * ez > FISH_BR * FISH_BR:
        return None
    pl = (-(wx * hx + wy * hy), -(-wx * hy + wy * hx), -wz)
    dl = (d[0] * hx + d[1] * hy, -d[0] * hy + d[1] * hx, d[2])
    tb = body_hit(pl, dl, tmax)
    ptx, pty = pl[0] - tail_root, pl[1]
    qx, qy = ptx * ct + pty * st_, -ptx * st_ + pty * ct
    dqx, dqy = dl[0] * ct + dl[1] * st_, -dl[0] * st_ + dl[1] * ct
    tt_ = None
    if abs(dqy) > 1e-6:
        tq = -qy / dqy
        if 0 < tq <= tmax:
            xq = qx + dqx * tq
            zq = pl[2] + dl[2] * tq
            if -TAIL_L <= xq <= 0.0 and tail_fork(xq) < abs(zq) <= tail_h(xq):
                tt_ = tq
    if abs(dl[1]) > 1e-6:
        tf = -pl[1] / dl[1]
        if 0 < tf <= tmax and (tt_ is None or tf < tt_):
            xf = pl[0] + dl[0] * tf
            zf = pl[2] + dl[2] * tf
            top = FISH_C * profx(xf)
            if top < zf <= top + fin_h(xf):
                tt_ = tf
    if tb is None and tt_ is None:
        return None
    col = None
    if tb is not None:
        hp = (pl[0] + dl[0] * tb, pl[1] + dl[1] * tb, pl[2] + dl[2] * tb)
        nl = body_normal(hp)
        nw = (hx * nl[0] - hy * nl[1], hy * nl[0] + hx * nl[1], nl[2])
        k = 0.5 + 0.5 * min(1.0, max(-1.0, hp[2] / (FISH_C * max(0.3, profx(hp[0])))))
        alb = (FISH_TOP[0] * k + FISH_BELLY[0] * (1 - k), FISH_TOP[1] * k + FISH_BELLY[1] * (1 - k), FISH_TOP[2] * k + FISH_BELLY[2] * (1 - k))
        ex_ = (hp[0] - FISH_L * 0.72) ** 2 + (abs(hp[1]) - FISH_B * 0.78) ** 2 + (hp[2] - 0.25) ** 2
        if ex_ < 0.09:
            alb = (0.08, 0.05, 0.04)
        tint = (math.exp(-SIG[0] * tb), math.exp(-SIG[1] * tb), math.exp(-SIG[2] * tb))
        col = shade(nw, alb, tint, d)
        tfar = tb
    if tt_ is not None and (tb is None or tt_ < tb):
        nl = (-st_, ct, 0.0)
        nw = (hx * nl[0] - hy * nl[1], hy * nl[0] + hx * nl[1], 0.0)
        tint = (math.exp(-SIG[0] * tt_), math.exp(-SIG[1] * tt_), math.exp(-SIG[2] * tt_))
        tc_ = shade(nw, TAIL_COL, tint, d, True)
        tc_ = (tc_[0] + TAIL_GLOW[0] * tint[0], tc_[1] + TAIL_GLOW[1] * tint[1], tc_[2] + TAIL_GLOW[2] * tint[2])
        if col is None:
            return ("tail", tc_, tt_)
        col = (tc_[0] * TAIL_ALPHA + col[0] * (1 - TAIL_ALPHA), tc_[1] * TAIL_ALPHA + col[1] * (1 - TAIL_ALPHA), tc_[2] * TAIL_ALPHA + col[2] * (1 - TAIL_ALPHA))
        tfar = tt_
    return ("body", col, tfar)


for frame in range(N):
    canvas.clear()
    if len(_style) > 6000:
        _style.clear()
    th = theta[frame]
    bob = 0.8 * math.sin(TAU * 3 * frame / N + 1.0)
    fc = (ORBIT_R * math.cos(th), ORBIT_R * math.sin(th), ORBIT_Z + bob)
    tph = tail_ph[frame]
    psi = BODY_AMP * math.sin(tph)
    hd = th + math.pi / 2 + psi
    hx, hy = math.cos(hd), math.sin(hd)
    tau_ = TAIL_AMP * math.sin(tph + 0.9)
    ct, st_ = math.cos(tau_), math.sin(tau_)
    tail_root = -FISH_L * 0.97
    bin_ = int(round(th / TAU * NBIN)) % NBIN

    R_ = [[0.0] * W for _ in range(H)]
    G_ = [[0.0] * W for _ in range(H)]
    B_ = [[0.0] * W for _ in range(H)]
    for j in range(H):
        rowb = base[j]
        rr, gg, bb = R_[j], G_[j], B_[j]
        for i in range(W):
            c = rowb[i]
            rr[i], gg[i], bb[i] = c[0], c[1], c[2]

    tsh = (fc[2] - Z_BASE) / LW[2]
    scx, scy = fc[0] - LW[0] * tsh, fc[1] - LW[1] * tsh
    for i, j, lx, ly, lit0, m in pix_land:
        ux = (lx - scx) * hx + (ly - scy) * hy
        uy = -(lx - scx) * hy + (ly - scy) * hx
        e = (ux / (FISH_L + 1.8)) ** 2 + (uy / (FISH_B + 1.3)) ** 2
        if e < 2.0:
            sh = SHADOW * max(0.0, min(1.0, 2.0 - e)) * lit0
            R_[j][i] -= m[0] * TABLE_A[0] * LCOL[0] * sh
            G_[j][i] -= m[1] * TABLE_A[1] * LCOL[1] * sh
            B_[j][i] -= m[2] * TABLE_A[2] * LCOL[2] * sh

    for i, j, mask in bins[bin_]:
        subs = sub_rays(i, j)
        tr, tg, tb_ = 0.0, 0.0, 0.0
        hit = False
        for sa, segs in subs:
            cr, cg, cb = sa
            for p, d, tmax, m, a in segs:
                res = fish_colour(p, d, tmax, m, a, fc, hx, hy, ct, st_, tail_root)
                if res is None:
                    continue
                hit = True
                kind, col, tfar = res
                sc = 1.0 - math.exp(-tfar / SCAT_L)
                if kind == "tail":
                    beyond = ((cr - a[0]) / max(1e-6, m[0]), (cg - a[1]) / max(1e-6, m[1]), (cb - a[2]) / max(1e-6, m[2]))
                    col = (col[0] * TAIL_ALPHA + beyond[0] * (1 - TAIL_ALPHA), col[1] * TAIL_ALPHA + beyond[1] * (1 - TAIL_ALPHA), col[2] * TAIL_ALPHA + beyond[2] * (1 - TAIL_ALPHA))
                cr = a[0] + m[0] * (col[0] + SCAT[0] * sc)
                cg = a[1] + m[1] * (col[1] + SCAT[1] * sc)
                cb = a[2] + m[2] * (col[2] + SCAT[2] * sc)
                break
            tr += cr
            tg += cg
            tb_ += cb
        if hit:
            R_[j][i], G_[j][i], B_[j][i] = tr * 0.25, tg * 0.25, tb_ * 0.25

    for cy in range(height):
        line = Text()
        rt, gt, bt = R_[2 * cy], G_[2 * cy], B_[2 * cy]
        rb, gb, bb2 = R_[2 * cy + 1], G_[2 * cy + 1], B_[2 * cy + 1]
        prev, run = None, 0
        for x in range(W):
            s_ = sty((max(BG[0], tone(rt[x])), max(BG[1], tone(gt[x])), max(BG[2], tone(bt[x]))),
                     (max(BG[0], tone(rb[x])), max(BG[1], tone(gb[x])), max(BG[2], tone(bb2[x]))))
            if s_ is prev:
                run += 1
            else:
                if run:
                    line.append("▀" * run, prev)
                prev, run = s_, 1
        if run:
            line.append("▀" * run, prev)
        canvas.write(line)

    await sleep(0.1)

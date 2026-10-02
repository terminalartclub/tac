from rich.text import Text
from rich.style import Style
from rich.color import Color
import math

# hollow

N = 300
W = width
H = height * 2
TAU = math.pi * 2
PIX_ASPECT = 14.0 / 13.0

R = 15.0
RV = 13.0
HC = 13.0
NRIB = 10
RIB_A = 0.035
THICK = 2.2
ELL = 0.9
YAW = 0.10
YF = 17.0
FLAME_SRC = 0.5
CANDLE_R = 2.6
LID_Y = 22.4
LID_GAP = 0.09
CHIM = (3.8, 0.5, 1.7)
STEM_A = (-1.2, 24.6, 0.5)
STEM_B = (-3.6, 29.2, 0.2)
STEM_R = 1.5

Z_EDGE = -70.0
Z_WALL = 38.0
RISE = 18.0
TREAD = 28.0
PLANK = 9.0

EYE = (-6.0, 36.0, -110.0)
AT = (1.0, 4.0, -10.0)
FRAME_CM = 62.0

KEY_DIR = (-0.35, 0.80, -0.48)
KEY = 0.50
KEY_RGB = (0.55, 0.66, 0.95)
AMB = 0.022
AMB_RGB = (0.45, 0.55, 0.85)

CAND = (1.0, 0.66, 0.26)
I_IN = 420.0
IN_AMB = 0.16
ALB_IN = (0.95, 0.52, 0.16)
ALB_SKIN = (0.80, 0.30, 0.06)
FLESH = (1.0, 0.50, 0.14)
GLOW = 270.0
BOUNCE = 0.18
I_PROJ = 2400.0
ALB_FLOOR = (0.42, 0.34, 0.25)
ALB_WALL = (0.30, 0.29, 0.28)
ALB_STEM = (0.30, 0.26, 0.14)
ALB_CANDLE = (0.95, 0.82, 0.58)
EXPO = 190.0
BLEACH = 0.10

FOG_Y = 4.0
FOG_K = 0.55
FOG_COOL = 0.030
FOG_Z0, FOG_Z1 = -22.0, -75.0

SMOKE = 80.0
SMOKE_H = 34
SMOKE_RGB = (0.55, 0.58, 0.70)

F_G = 200
G_SIG = 12.0

sqrt, sin, cos, atan2, exp = math.sqrt, math.sin, math.cos, math.atan2, math.exp


def norm(v):
    l = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / l, v[1] / l, v[2] / l)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def smooth(a, b, v):
    t = min(1.0, max(0.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


KEY_DIR = norm(KEY_DIR)
FWD = norm((AT[0] - EYE[0], AT[1] - EYE[1], AT[2] - EYE[2]))
RGT = norm(cross((0.0, 1.0, 0.0), FWD))
UPV = cross(FWD, RGT)
DIST = sqrt(sum((a - e) ** 2 for a, e in zip(AT, EYE)))
FRAME_CM *= max(1.0, sqrt((W / (H * PIX_ASPECT)) / 0.606))
FPX = (W / 2.0) / ((FRAME_CM / 2.0) / DIST)


def ray(i, j):
    px = (i + 0.5 - W / 2.0) / FPX
    py = -(j + 0.5 - H / 2.0) * PIX_ASPECT / FPX
    return norm((FWD[0] + px * RGT[0] + py * UPV[0], FWD[1] + px * RGT[1] + py * UPV[1],
                 FWD[2] + px * RGT[2] + py * UPV[2]))


def project(p):
    vx, vy, vz = p[0] - EYE[0], p[1] - EYE[1], p[2] - EYE[2]
    zc = vx * FWD[0] + vy * FWD[1] + vz * FWD[2]
    xc = vx * RGT[0] + vy * RGT[1] + vz * RGT[2]
    yc = vx * UPV[0] + vy * UPV[1] + vz * UPV[2]
    return W / 2.0 + xc / zc * FPX, H / 2.0 - yc / zc * FPX / PIX_ASPECT


SR = R / RV


def f_pump(x, y, z):
    qy = (y - HC) * SR
    rr = sqrt(x * x + qy * qy + z * z)
    return rr - R * (1.0 + RIB_A * cos(NRIB * atan2(x, -z)))


_sab = (STEM_B[0] - STEM_A[0], STEM_B[1] - STEM_A[1], STEM_B[2] - STEM_A[2])
_sl2 = _sab[0] ** 2 + _sab[1] ** 2 + _sab[2] ** 2


def f_stem(x, y, z):
    px, py, pz = x - STEM_A[0], y - STEM_A[1], z - STEM_A[2]
    h = min(1.0, max(0.0, (px * _sab[0] + py * _sab[1] + pz * _sab[2]) / _sl2))
    dx, dy, dz = px - _sab[0] * h, py - _sab[1] * h, pz - _sab[2] * h
    return sqrt(dx * dx + dy * dy + dz * dz) - STEM_R * (1.0 - 0.25 * h)


def f_scene(x, y, z):
    return min(f_pump(x, y, z), f_stem(x, y, z))


def tri_sdf(u, v, pts):
    d = -1e9
    n = len(pts)
    for k in range(n):
        ax, ay = pts[k]
        bx, by = pts[(k + 1) % n]
        ex, ey = bx - ax, by - ay
        l = sqrt(ex * ex + ey * ey)
        d = max(d, ((u - ax) * ey - (v - ay) * ex) / l)
    return d


EYE_L = ((-8.4, 14.8), (-2.6, 14.8), (-5.5, 19.6))
EYE_R = ((2.6, 14.8), (8.4, 14.8), (5.5, 19.6))
NOSE = ((-1.8, 12.2), (1.8, 12.2), (0.0, 14.7))
M_HW = 8.2
TEETH = ((-4.9, -2.5), (2.5, 4.9))
TOOTH_DN = 3.0
LOW_TOOTH = (-1.2, 1.2)
LOW_UP = 2.4


def v_lo(u):
    return 4.4 + 0.036 * u * u


def v_hi(u):
    return 11.0 - 0.0622 * u * u


def mouth_sdf(u, v):
    d = max((v_lo(u) - v) / 1.05, (v - v_hi(u)) / 1.1)
    for a, b in TEETH:
        tx = max(a - u, u - b)
        ty = v_hi(u) - TOOTH_DN - v
        d = max(d, -max(tx, ty))
    return d


def sdf_face(u, v):
    d = mouth_sdf(u, v)
    if v > 11.0:
        d = min(d, tri_sdf(u, v, EYE_L), tri_sdf(u, v, EYE_R))
    if 9.5 < v < 15.5:
        d = min(d, tri_sdf(u, v, NOSE))
    return d


def sdf_open(x, y, z, u, v):
    d = sdf_face(u, v)
    d = min(d, abs(y - LID_Y) - LID_GAP)
    if y > HC + 6:
        d = min(d, sqrt((x - CHIM[0]) ** 2 + (z - CHIM[2]) ** 2) - CHIM[1])
    return d


def face_uv(x, y, z):
    a = atan2(x, -z) - YAW
    if a > math.pi:
        a -= TAU
    elif a < -math.pi:
        a += TAU
    return R * a, y


def sph_entry(P, F):
    Dx, Dy, Dz = F[0] - P[0], F[1] - P[1], F[2] - P[2]
    px, py, pz = P[0], (P[1] - HC) * SR, P[2]
    dx, dy, dz = Dx, Dy * SR, Dz
    a = dx * dx + dy * dy + dz * dz
    b = px * dx + py * dy + pz * dz
    c = px * px + py * py + pz * pz - R * R
    disc = b * b - a * c
    if disc <= 0:
        return None
    s = (-b - sqrt(disc)) / a
    if s < 0 or s > 1:
        return None
    return s, (P[0] + Dx * s, P[1] + Dy * s, P[2] + Dz * s)


def inner_hit(p, d):
    RI, RVI = R - THICK, RV - THICK
    sri = RI / RVI
    px, py, pz = p[0], (p[1] - HC) * sri, p[2]
    dx, dy, dz = d[0], d[1] * sri, d[2]
    a = dx * dx + dy * dy + dz * dz
    b = px * dx + py * dy + pz * dz
    c = px * px + py * py + pz * pz - RI * RI
    disc = b * b - a * c
    t_in = (-b + sqrt(max(0.0, disc))) / a
    hit = (p[0] + d[0] * t_in, p[1] + d[1] * t_in, p[2] + d[2] * t_in)
    nn = norm((-hit[0], -(hit[1] - HC) * sri * sri, -hit[2]))
    kind = 0
    a2 = d[0] * d[0] + d[2] * d[2]
    if a2 > 1e-9:
        b2 = p[0] * d[0] + p[2] * d[2]
        c2 = p[0] * p[0] + p[2] * p[2] - CANDLE_R * CANDLE_R
        disc2 = b2 * b2 - a2 * c2
        if disc2 > 0:
            t_c = (-b2 - sqrt(disc2)) / a2
            if 0 < t_c < t_in:
                yc = p[1] + d[1] * t_c
                if HC - RVI + 0.5 <= yc <= YF - 1.6:
                    hit = (p[0] + d[0] * t_c, yc, p[2] + d[2] * t_c)
                    nn = norm((hit[0], 0.0, hit[2]))
                    kind = 1
                elif yc > YF - 1.6 and d[1] < 0:
                    t_top = (YF - 1.6 - p[1]) / d[1]
                    if 0 < t_top < t_in:
                        hx, hz = p[0] + d[0] * t_top, p[2] + d[2] * t_top
                        if hx * hx + hz * hz <= CANDLE_R * CANDLE_R:
                            hit = (hx, YF - 1.6, hz)
                            nn = (0.0, 1.0, 0.0)
                            kind = 1
    return hit, nn, kind


BG = (5.8, 5.8, 10.9)
F0 = (0.0, YF, 0.0)

base = [[(BG[0], BG[1], BG[2]) for _ in range(W)] for _ in range(H)]
pump = []
proj = []
fogpx = []


def plank_albedo(x, z, foot):
    k = (x + 200.0) / PLANK
    fr = k - int(k)
    dg = min(fr, 1.0 - fr) * PLANK
    g = 1.0 - 0.55 * max(0.0, 1.0 - dg / (foot + 0.25))
    pid = int(k)
    tone = 0.94 + 0.06 * ((pid * 7919) % 13) / 12.0
    grain = 1.0 + 0.05 * sin(z * 0.9 + pid * 2.1) * sin(x * 2.7)
    return g * tone * grain


def wall_albedo(x, y, foot):
    k = y / 12.0
    fr = k - int(k)
    dg = min(fr, 1.0 - fr) * 12.0
    g = 1.0 - 0.5 * max(0.0, 1.0 - dg / (foot + 0.3))
    return g * (0.9 + 0.1 * sin(x * 0.37))


def key_shadow(P):
    e = sph_entry(P, (P[0] + KEY_DIR[0] * 300, P[1] + KEY_DIR[1] * 300, P[2] + KEY_DIR[2] * 300))
    return 0.0 if e is None else 1.0


def proj_entry(P, n):
    Dx, Dy, Dz = F0[0] - P[0], F0[1] - P[1], F0[2] - P[2]
    dl = sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
    cth = (n[0] * Dx + n[1] * Dy + n[2] * Dz) / dl if n is not None else 1.0
    if cth <= 0:
        return None
    e = sph_entry(P, F0)
    if e is None:
        return None
    s, E = e
    u0, v0 = face_uv(*E)
    if sdf_face(u0, v0) > 4.0:
        return None
    J = []
    for k in range(3):
        Fk = list(F0)
        Fk[k] += 0.5
        ek = sph_entry(P, tuple(Fk))
        uk, vk = face_uv(*ek[1])
        J.append(((uk - u0) / 0.5, (vk - v0) / 0.5))
    irr = I_PROJ * cth / (dl * dl)
    rho = max(0.15, FLAME_SRC * s)
    return (u0, v0, J[0][0], J[1][0], J[2][0], J[0][1], J[1][1], J[2][1], irr, rho)


BS_C = (0.0, HC + 1.5, 0.0)
BS_R = 19.0

for j in range(H):
    for i in range(W):
        d = ray(i, j)
        ex, ey, ez = EYE
        hits = []
        if d[1] < 0:
            t = -ey / d[1]
            z = ez + d[2] * t
            x = ex + d[0] * t
            if Z_EDGE <= z <= Z_WALL:
                hits.append((t, 0, (x, 0.0, z), (0.0, 1.0, 0.0)))
            t2 = (-RISE - ey) / d[1]
            z2 = ez + d[2] * t2
            if Z_EDGE - TREAD <= z2 <= Z_EDGE:
                hits.append((t2, 2, (ex + d[0] * t2, -RISE, z2), (0.0, 1.0, 0.0)))
        if d[2] > 0:
            t = (Z_WALL - ez) / d[2]
            y = ey + d[1] * t
            if y >= 0:
                hits.append((t, 1, (ex + d[0] * t, y, Z_WALL), (0.0, 0.0, -1.0)))
            t = (Z_EDGE - ez) / d[2]
            y = ey + d[1] * t
            if -RISE <= y <= 0:
                hits.append((t, 3, (ex + d[0] * t, y, Z_EDGE), (0.0, 0.0, -1.0)))
        if not hits:
            continue
        hits.sort()
        t, kind, P, n = hits[0]
        if kind == 0 or kind == 2:
            alb = plank_albedo(P[0], P[2], t / FPX)
            ar, ag, ab = ALB_FLOOR[0] * alb, ALB_FLOOR[1] * alb, ALB_FLOOR[2] * alb
        elif kind == 1:
            alb = wall_albedo(P[0], P[1], t / FPX)
            ar, ag, ab = ALB_WALL[0] * alb, ALB_WALL[1] * alb, ALB_WALL[2] * alb
        else:
            ar, ag, ab = ALB_FLOOR[0] * 0.8, ALB_FLOOR[1] * 0.8, ALB_FLOOR[2] * 0.8
        dif = max(0.0, n[0] * KEY_DIR[0] + n[1] * KEY_DIR[1] + n[2] * KEY_DIR[2])
        sh = 1.0 - key_shadow(P) if dif > 0 else 0.0
        fall = 0.15 + 0.85 * smooth(45.0, -45.0, P[0])
        if kind == 0:
            fall *= 0.35 + 0.65 * smooth(20.0, -55.0, P[2])
        if kind == 1:
            fall *= 0.55 + 0.45 * smooth(70.0, 0.0, P[1])
        elif kind != 0:
            fall *= 0.25
        lr = (AMB * AMB_RGB[0] + KEY * KEY_RGB[0] * dif * sh) * fall
        lg = (AMB * AMB_RGB[1] + KEY * KEY_RGB[1] * dif * sh) * fall
        lb = (AMB * AMB_RGB[2] + KEY * KEY_RGB[2] * dif * sh) * fall
        base[j][i] = (BG[0] + ar * lr * 255, BG[1] + ag * lg * 255, BG[2] + ab * lb * 255)
        pe = proj_entry(P, n)
        if pe is not None:
            proj.append((j, i) + pe + (ar * CAND[0], ag * CAND[1], ab * CAND[2]))

for j in range(H):
    for i in range(W):
        d = ray(i, j)
        ox, oy, oz = EYE[0] - BS_C[0], EYE[1] - BS_C[1], EYE[2] - BS_C[2]
        b = ox * d[0] + oy * d[1] + oz * d[2]
        c = ox * ox + oy * oy + oz * oz - BS_R * BS_R
        disc = b * b - c
        if disc <= 0:
            continue
        t0 = -b - sqrt(disc)
        t1 = -b + sqrt(disc)
        t = t0
        dmin = 1e9
        hit = False
        for _ in range(48):
            x, y, z = EYE[0] + d[0] * t, EYE[1] + d[1] * t, EYE[2] + d[2] * t
            fv = f_scene(x, y, z)
            dpx = fv / (t / FPX)
            if dpx < dmin:
                dmin = dpx
            if fv < 0.004:
                hit = True
                break
            t += fv * 0.8
            if t > t1:
                break
        cov = 1.0 if hit else max(0.0, 1.0 - dmin / 0.7)
        if cov <= 0:
            continue
        if not hit:
            t = t if t <= t1 else t1
            x, y, z = EYE[0] + d[0] * t, EYE[1] + d[1] * t, EYE[2] + d[2] * t
        eps = 0.04
        nx = f_scene(x + eps, y, z) - f_scene(x - eps, y, z)
        ny = f_scene(x, y + eps, z) - f_scene(x, y - eps, z)
        nz = f_scene(x, y, z + eps) - f_scene(x, y, z - eps)
        n = norm((nx, ny, nz))
        is_stem = f_stem(x, y, z) < f_pump(x, y, z)
        dif = max(0.0, n[0] * KEY_DIR[0] + n[1] * KEY_DIR[1] + n[2] * KEY_DIR[2])
        if is_stem:
            alb = ALB_STEM
            lr = BG[0] + (AMB * AMB_RGB[0] + KEY * KEY_RGB[0] * dif) * alb[0] * 255
            lg = BG[1] + (AMB * AMB_RGB[1] + KEY * KEY_RGB[1] * dif) * alb[1] * 255
            lb = BG[2] + (AMB * AMB_RGB[2] + KEY * KEY_RGB[2] * dif) * alb[2] * 255
            pump.append((j, i, cov, (lr, lg, lb), 0.0, (0.0, 0.0, 0.0), None))
            continue
        u, v = face_uv(x, y, z)
        so = sdf_open(x, y, z, u, v)
        pxcm = t / FPX
        ocov = min(1.0, max(0.0, 0.5 - so / pxcm))
        alb = ALB_SKIN
        lr = BG[0] + (AMB * AMB_RGB[0] + KEY * KEY_RGB[0] * dif) * alb[0] * 255
        lg = BG[1] + (AMB * AMB_RGB[1] + KEY * KEY_RGB[1] * dif) * alb[1] * 255
        lb = BG[2] + (AMB * AMB_RGB[2] + KEY * KEY_RGB[2] * dif) * alb[2] * 255
        phi = atan2(x, -z)
        thick = THICK + 0.55 * cos(NRIB * phi)
        Pi = (x - n[0] * thick, y - n[1] * thick, z - n[2] * thick)
        Dx, Dy, Dz = F0[0] - Pi[0], F0[1] - Pi[1], F0[2] - Pi[2]
        dl = sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
        cth = max(0.0, -(n[0] * Dx + n[1] * Dy + n[2] * Dz) / dl)
        glow = GLOW * (0.25 + 0.75 * cth) / (dl * dl) * exp(-thick / ELL)
        under = BOUNCE * max(0.0, -n[1]) * (0.3 + 0.7 * max(0.0, -n[2]))
        gl = ((glow * FLESH[0] + under * alb[0]) * CAND[0] * 255,
              (glow * FLESH[1] + under * alb[1]) * CAND[1] * 255,
              (glow * FLESH[2] + under * alb[2]) * CAND[2] * 255)
        inner = None
        if ocov > 0:
            hp, hn, kind = inner_hit((x, y, z), d)
            inner = (hp, hn, ALB_CANDLE if kind == 1 else ALB_IN, 0.55 if kind == 1 else IN_AMB)
        pump.append((j, i, cov, (lr, lg, lb), ocov, gl, inner))

CH_S = project((CHIM[0], HC + RV + 0.3, CHIM[2]))
CH_X, CH_Y = CH_S


def tone(v):
    return int(255 * (1.0 - exp(-v / EXPO))) if v > 0 else 0


_style = {}


def sty(t, b):
    key = (t, b)
    s = _style.get(key)
    if s is None:
        s = Style(color=Color.from_rgb(*t), bgcolor=Color.from_rgb(*b))
        _style[key] = s
    return s


def flame(frame):
    t = frame / N
    A = 1.0 + 0.07 * sin(TAU * 7 * t + 0.3) + 0.05 * sin(TAU * 11 * t + 1.7) + 0.04 * sin(TAU * 19 * t + 0.9) \
        + 0.03 * sin(TAU * 29 * t + 2.4)
    dx = 0.40 * sin(TAU * 3 * t + 0.2) + 0.22 * sin(TAU * 8 * t + 1.1) + 0.12 * sin(TAU * 17 * t + 2.0)
    dz = 0.30 * sin(TAU * 5 * t + 1.3) + 0.15 * sin(TAU * 13 * t + 0.4)
    g = exp(-((frame - F_G) / G_SIG) ** 2)
    dx += -2.2 * g
    dz += 0.5 * g * sin(TAU * 41 * t)
    A *= (1.0 - 0.60 * g) * (1.0 + 0.18 * g * sin(TAU * 47 * t + 1.0))
    A *= 1.0 + 0.12 * exp(-((frame - F_G - 18) / 8.0) ** 2)
    dy = 1.2 * (A - 1.0) - 1.6 * g
    gs = exp(-((frame - F_G + 8) / G_SIG) ** 2)
    return A, dx, dy, dz, g, gs


for frame in range(N):
    canvas.clear()
    if len(_style) > 6000:
        _style.clear()
    tt = frame / N
    A, fdx, fdy, fdz, g, gs = flame(frame)
    F = (F0[0] + fdx, F0[1] + fdy, F0[2] + fdz)
    Rr = [[c[0] for c in row] for row in base]
    Gg = [[c[1] for c in row] for row in base]
    Bb = [[c[2] for c in row] for row in base]
    for j, i, u0, v0, jux, juy, juz, jvx, jvy, jvz, irr, rho, cr, cg, cb in proj:
        u = u0 + jux * fdx + juy * fdy + juz * fdz
        v = v0 + jvx * fdx + jvy * fdy + jvz * fdz
        s = sdf_face(u, v)
        cov = 0.5 - s / (2.0 * rho)
        if cov <= 0:
            continue
        if cov > 1.0:
            cov = 1.0
        l = cov * irr * A * 255
        Rr[j][i] += cr * l
        Gg[j][i] += cg * l
        Bb[j][i] += cb * l
    for j, i, cov, ext, ocov, gl, inner in pump:
        r = ext[0] + gl[0] * A
        gg = ext[1] + gl[1] * A
        b = ext[2] + gl[2] * A
        if ocov > 0:
            hp, hn, ia, iamb = inner
            Dx, Dy, Dz = F[0] - hp[0], F[1] - hp[1], F[2] - hp[2]
            dl2 = Dx * Dx + Dy * Dy + Dz * Dz
            dl = sqrt(dl2)
            cth = max(0.0, (hn[0] * Dx + hn[1] * Dy + hn[2] * Dz) / dl)
            e = I_IN * A * (min(cth / dl2, 0.012) + iamb / 150.0)
            r = r * (1 - ocov) + e * CAND[0] * ia[0] * 255 * ocov
            gg = gg * (1 - ocov) + e * CAND[1] * ia[1] * 255 * ocov
            b = b * (1 - ocov) + e * CAND[2] * ia[2] * 255 * ocov
        Rr[j][i] = Rr[j][i] * (1 - cov) + r * cov
        Gg[j][i] = Gg[j][i] * (1 - cov) + gg * cov
        Bb[j][i] = Bb[j][i] * (1 - cov) + b * cov
    lean = -0.30 * gs + 0.05 * sin(TAU * 2 * tt)
    for h in range(1, SMOKE_H):
        hy = h / SMOKE_H
        cx = CH_X + lean * h * (1 + hy) + (0.3 + 1.3 * hy) * sin(0.38 * h - TAU * 6 * tt + 0.5) \
            + (0.15 + 0.7 * hy) * sin(0.9 * h + TAU * 10 * tt + 2.0) + gs * 2.0 * hy * sin(1.5 * h + TAU * 13 * tt)
        yy = int(CH_Y) - h
        if yy < 0:
            break
        sig = 0.55 + 1.7 * hy + 1.2 * gs * hy
        br = SMOKE * A * exp(-hy * 2.0) * (0.8 + 0.2 * sin(0.7 * h - TAU * 8 * tt)) / sig * (1.0 - 0.5 * gs * hy)
        warm = exp(-hy * 3.5)
        cr_ = SMOKE_RGB[0] * (1 - warm) + CAND[0] * warm
        cg_ = SMOKE_RGB[1] * (1 - warm) + CAND[1] * 0.9 * warm
        cb_ = SMOKE_RGB[2] * (1 - warm) + CAND[2] * 0.8 * warm
        for xx in range(int(cx - 2.5 * sig), int(cx + 2.5 * sig) + 2):
            if 0 <= xx < W:
                q = (xx + 0.5 - cx) / sig
                wgt = exp(-0.5 * q * q) * br
                Rr[yy][xx] += wgt * cr_
                Gg[yy][xx] += wgt * cg_
                Bb[yy][xx] += wgt * cb_

    for cy in range(height):
        line = Text()
        rt, gt, bt = Rr[2 * cy], Gg[2 * cy], Bb[2 * cy]
        rb, gb, bb2 = Rr[2 * cy + 1], Gg[2 * cy + 1], Bb[2 * cy + 1]
        prev, run = None, 0
        for x in range(W):
            vr, vg, vb = rt[x], gt[x], bt[x]
            wv = (vr + vg + vb) * BLEACH
            top = (tone(vr + wv), tone(vg + wv), tone(vb + wv))
            vr, vg, vb = rb[x], gb[x], bb2[x]
            wv = (vr + vg + vb) * BLEACH
            bot = (tone(vr + wv), tone(vg + wv), tone(vb + wv))
            st_ = sty(top, bot)
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

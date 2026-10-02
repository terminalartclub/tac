from rich.text import Text
from rich.style import Style
from rich.color import Color
import math
import random

# hush

N = 300
DT = 0.1
T = N * DT
W = width
H = height * 2
TAU = math.pi * 2

CAM_H = 3.5
PITCH = -8.0 * math.pi / 180
DIST = 11.0
FOCAL = W * 1.65
POST_X = -1.5
LAMP_X = -0.86
LAMP_DZ = -0.64
LAMP_H = 3.5
POST_R = 0.05
ARM_Y = 3.4
HEAD_L = 0.6
HEAD_T = 0.2
ARM_LEN = 0.9
SHADOW_MAX = 0.6

NF = 1600
BOX_X = 10.0
ZB0, ZB1 = -4.0, 3.5
V0, V1 = 0.45, 1.25
NF_NEAR = 140
ZN0, ZN1 = 3.0, 5.5
VN0, VN1 = 0.40, 0.65
NF_FAR = 350
ZF0, ZF1 = 14.5, 17.5
VF0, VF1 = 0.95, 1.55
APERTURE = 0.06
AMB = 0.055
AMB_RGB = (0.76, 0.84, 1.0)
NEAR_ALPHA = 0.35
NEAR_RGB = (90, 94, 110)
YLO = -0.3
YMIN_SPAN = 6.0
EXPO = 3.0
BLOOM = 0.35
BLOOM_T = 0.6
KNEE = 1.5
SHUTTER = 0.6
GLARE = 0.9
GLARE_R = 7.0
BOUNCE = 0.35
UPLIGHT = 0.06
SIDE = 0.2
CUT_LO = 0.31
CUT_HI = 0.80
HG_G = 0.45
PH_MIX = 0.5
SWAY_A = (0.03, 0.12)
SWAY_M = (8, 20)

GUST_T = 9.0
GUST_S = 2.2
GUST_PK = 1.3
LOFT = 0.35
LULL_T = 15.5
LULL_L = 4.0
LULL_Y = 3.0
LULL_D = 0.95
HALO_G = 0.04
PH_MIX_H = 0.3
HALO_DIP = 0.65

AMBER = (255, 176, 78)
SKY_TOP = (8, 8, 14)
SKY_HOR = (11, 11, 17)
FAR_SNOW = (12, 12, 18)
GROUND_G = 26.0
GROUND_GAM = 2.0
NOISE_A = 0.12
POST_RGB = (24, 19, 16)
HEAD_RGB = (255, 232, 185)
CAP_RGB = (84, 76, 70)
SNOW_RGB = (214, 196, 168)
ARM_SNOW = (168, 156, 138)

random.seed(11)
LX, LY, LZ = LAMP_X, LAMP_H, DIST + LAMP_DZ
cp, sp = math.cos(PITCH), math.sin(PITCH)
CX, CY = W / 2.0, H / 2.0


def project(x, y, z):
    dy, dz = y - CAM_H, z
    yc = dy * cp - dz * sp
    zc = dy * sp + dz * cp
    return CX + FOCAL * x / zc, CY - FOCAL * yc / zc, zc


def smooth(a, b, v):
    if v <= a:
        return 0.0
    if v >= b:
        return 1.0
    u = (v - a) / (b - a)
    return u * u * (3 - 2 * u)


def emit(cosphi):
    if cosphi <= 0.0:
        return UPLIGHT
    return SIDE + (1.0 - SIDE) * smooth(CUT_LO, CUT_HI, cosphi)


def hg(c):
    d = 1.0 + HG_G * HG_G - 2.0 * HG_G * c
    return (1.0 - HG_G * HG_G) / (d * math.sqrt(d))


HG_N = 1.0 / hg(0.0)


def phase(c):
    return (1.0 - PH_MIX) + PH_MIX * hg(c) * HG_N


NG = 64
_rs = random.Random(3)
NOISE = [[_rs.random() for _ in range(NG)] for _ in range(NG)]


def vnoise(x, z, scale):
    u, v = x / scale, z / scale
    i, j = math.floor(u), math.floor(v)
    fu, fv = u - i, v - j
    fu = fu * fu * (3 - 2 * fu)
    fv = fv * fv * (3 - 2 * fv)
    i, j = int(i) % NG, int(j) % NG
    i1, j1 = (i + 1) % NG, (j + 1) % NG
    a = NOISE[j][i] + (NOISE[j][i1] - NOISE[j][i]) * fu
    b = NOISE[j1][i] + (NOISE[j1][i1] - NOISE[j1][i]) * fu
    return a + (b - a) * fv


def height_at(x, z):
    return NOISE_A * (vnoise(x, z, 1.4) * 0.6 + vnoise(x + 17.3, z + 9.1, 0.5) * 0.3 + vnoise(x + 5.1, z + 23.7, 0.22) * 0.1)


def ground_rad(x, z):
    hx = (height_at(x + 0.05, z) - height_at(x - 0.05, z)) / 0.1
    hz = (height_at(x, z + 0.05) - height_at(x, z - 0.05)) / 0.1
    nx, ny, nz = -hx, 1.0, -hz
    nl = math.sqrt(nx * nx + ny * ny + nz * nz)
    dx, dy, dz = LX - x, LY - height_at(x, z), LZ - z
    r2 = dx * dx + dy * dy + dz * dz
    r = math.sqrt(r2)
    cosb = (nx * dx + ny * dy + nz * dz) / (nl * r)
    if cosb < 0:
        cosb = 0.0
    e = emit(dy / r) * cosb / r2
    px, pz = x - LX, z - LZ
    qx, qz = POST_X - LX, DIST - LZ
    seg2 = px * px + pz * pz
    if seg2 > 1e-6:
        s = (qx * px + qz * pz) / seg2
        if 0.0 < s < 1.0:
            ex, ez = qx - s * px, qz - s * pz
            dpost = math.sqrt(ex * ex + ez * ez)
            lat = dpost / s
            d_pp = (1.0 - s) * math.sqrt(seg2)
            occl = min(SHADOW_MAX, (2 * POST_R / max(d_pp, 0.05)) / (HEAD_L / r))
            hw = POST_R + 0.5 * HEAD_T * d_pp / ARM_LEN
            e *= 1.0 - occl * (1.0 - smooth(0.5 * hw, hw, lat))
    return e


def lerp3(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u)


WH = W * H
BR = [0] * WH
BG = [0] * WH
BB = [0] * WH
POSTMASK = [0.0] * WH

hx_, hy_, hzc = project(LX, LY, LZ)
px_, py_, pzc = project(POST_X, LAMP_H, DIST)
bx_, by_, bzc = project(POST_X, 0.0, DIST)
ax_, ay_, _ = project(POST_X, ARM_Y, DIST)
PPM = FOCAL / hzc

for py in range(H):
    for px in range(W):
        xc = (px + 0.5 - CX) / FOCAL
        yc = (CY - py - 0.5) / FOCAL
        dx = xc
        dy = yc * cp + sp
        dz = cp - yc * sp
        if dy < -1e-4:
            s = -CAM_H / dy
            gx, gz = s * dx, s * dz
            e = ground_rad(gx, gz) * GROUND_G
            far = smooth(14.0, 60.0, gz)
            base = lerp3(FAR_SNOW, SKY_HOR, far)
            lum = (1.0 - math.exp(-e)) ** GROUND_GAM
            r = base[0] + AMBER[0] * lum
            g = base[1] + AMBER[1] * lum
            b = base[2] + AMBER[2] * lum
            hot = smooth(0.55, 1.0, lum)
            r += (255 - r) * hot * 0.3
            g += (255 - g) * hot * 0.3
            b += (255 - b) * hot * 0.3
        else:
            u = smooth(-0.02, 0.5, dy)
            r, g, b = lerp3(SKY_HOR, SKY_TOP, u)
        i = py * W + px
        BR[i], BG[i], BB[i] = min(255, int(r)), min(255, int(g)), min(255, int(b))

post_w = 2 * POST_R * PPM
for py in range(max(0, int(py_) - 1), H):
    for px in range(int(px_ - 3), int(px_ + 4)):
        if 0 <= px < W:
            d = abs(px + 0.5 - px_) - post_w / 2
            cov = 1.0 - smooth(-0.5, 0.5, d)
            if py < py_:
                cov *= smooth(py_ - 1.0, py_, py + 0.5)
            if py > by_:
                cov *= 1.0 - smooth(by_, by_ + 1.0, py + 0.5)
            if cov > 0.01:
                i = py * W + px
                hgt = LAMP_H * (by_ - py) / (by_ - py_)
                lit = 0.9 / (1.0 + (LAMP_H - hgt) ** 2 * 1.4) * emit((LAMP_H - hgt) / math.hypot(LAMP_H - hgt, 1.0))
                lit *= 0.35 + 0.65 * max(0.0, (px + 0.5 - px_) / max(post_w, 1.0)) if px + 0.5 > px_ - 1 else 0.3
                cr = POST_RGB[0] + AMBER[0] * lit * 0.5
                cg = POST_RGB[1] + AMBER[1] * lit * 0.5
                cb = POST_RGB[2] + AMBER[2] * lit * 0.5
                BR[i] = int(BR[i] + (min(255, cr) - BR[i]) * cov)
                BG[i] = int(BG[i] + (min(255, cg) - BG[i]) * cov)
                BB[i] = int(BB[i] + (min(255, cb) - BB[i]) * cov)
                POSTMASK[i] = max(POSTMASK[i], cov)

for px in range(int(min(ax_, hx_)) - 1, int(max(ax_, hx_)) + 2):
    if 0 <= px < W:
        u = (px + 0.5 - ax_) / (hx_ - ax_) if hx_ != ax_ else 0
        if -0.05 < u < 1.0:
            yy = ay_ + (hy_ - ay_) * u
            for py in range(int(yy) - 1, int(yy) + 2):
                if 0 <= py < H:
                    cov = 1.0 - smooth(0.3, 0.9, abs(py + 0.5 - yy))
                    if cov > 0.01:
                        i = py * W + px
                        BR[i] = int(BR[i] + (24 - BR[i]) * cov)
                        BG[i] = int(BG[i] + (20 - BG[i]) * cov)
                        BB[i] = int(BB[i] + (18 - BB[i]) * cov)
                        POSTMASK[i] = max(POSTMASK[i], cov)
            py = int(yy) - 1
            if 0 <= py < H and u > 0.12:
                cov = 0.7 * (1.0 - smooth(0.3, 0.9, abs(py + 1.5 - yy)))
                if cov > 0.01:
                    i = py * W + px
                    BR[i] = int(BR[i] + (ARM_SNOW[0] - BR[i]) * cov)
                    BG[i] = int(BG[i] + (ARM_SNOW[1] - BG[i]) * cov)
                    BB[i] = int(BB[i] + (ARM_SNOW[2] - BB[i]) * cov)
                    POSTMASK[i] = max(POSTMASK[i], cov)

HEADMASK = []
hw, ht = HEAD_L * PPM / 2, HEAD_T * PPM / 2
for py in range(int(hy_ - ht) - 2, int(hy_ + ht) + 3):
    for px in range(int(hx_ - hw) - 2, int(hx_ + hw) + 3):
        if 0 <= px < W and 0 <= py < H:
            ex = (px + 0.5 - hx_) / hw
            ey = (py + 0.5 - hy_) / ht
            e = math.sqrt(ex * ex + ey * ey)
            cov = 1.0 - smooth(0.85, 1.15, e)
            if cov > 0.02:
                i = py * W + px
                c = SNOW_RGB if py + 0.5 < hy_ - ht * 0.35 else HEAD_RGB
                BR[i] = int(BR[i] + (c[0] - BR[i]) * cov)
                BG[i] = int(BG[i] + (c[1] - BG[i]) * cov)
                BB[i] = int(BB[i] + (c[2] - BB[i]) * cov)
                HEADMASK.append(i)
                POSTMASK[i] = max(POSTMASK[i], cov)

CT = [0.0] * WH
lvx, lvy, lvz = LX, LY - CAM_H, LZ
lvn = math.sqrt(lvx * lvx + lvy * lvy + lvz * lvz)
lvx, lvy, lvz = lvx / lvn, lvy / lvn, lvz / lvn
HALO = {}
for py in range(H):
    for px in range(W):
        xc = (px + 0.5 - CX) / FOCAL
        yc = (CY - py - 0.5) / FOCAL
        dx, dy, dz = xc, yc * cp + sp, cp - yc * sp
        dn = math.sqrt(dx * dx + dy * dy + dz * dz)
        dx, dy, dz = dx / dn, dy / dn, dz / dn
        c = dx * lvx + dy * lvy + dz * lvz
        i = py * W + px
        CT[i] = c
        acc = 0.0
        s = 0.6
        while s < 26.0:
            ds = 0.35 + s * 0.12
            fx, fy, fz = s * dx, CAM_H + s * dy, s * dz
            if fy < 0:
                break
            rx, ry, rz = fx - LX, fy - LY, fz - LZ
            r2 = rx * rx + ry * ry + rz * rz + 0.2
            r = math.sqrt(r2)
            cc = c if fz < LZ else -c
            ph = (1.0 - PH_MIX_H) + PH_MIX_H * hg(cc) * HG_N
            acc += emit(-ry / r) * ph / r2 * ds
            s += ds
        v = acc * HALO_G * 0.02
        if v > 0.002:
            HALO[i] = v

for py in range(int(hy_) - 30, int(hy_) + 31):
    for px in range(int(hx_) - 30, int(hx_) + 31):
        if 0 <= px < W and 0 <= py < H:
            d = math.hypot(px + 0.5 - hx_, py + 0.5 - hy_)
            v = GLARE * math.exp(-d / GLARE_R) * (1.0 - smooth(18.0, 29.0, d))
            if v > 0.002:
                i = py * W + px
                HALO[i] = HALO.get(i, 0.0) + v

SUBK = {}
for a in range(8):
    for b in range(8):
        fx, fy = (a + 0.5) / 8, (b + 0.5) / 8
        wx = [max(0.0, 1.0 - abs(j + 0.5 - fx)) for j in (-1, 0, 1)]
        wy = [max(0.0, 1.0 - abs(j + 0.5 - fy)) for j in (-1, 0, 1)]
        sx_, sy_ = sum(wx), sum(wy)
        SUBK[(a, b)] = [(jy * W + jx, wx[jx + 1] * wy[jy + 1] / (sx_ * sy_))
                        for jy in (-1, 0, 1) for jx in (-1, 0, 1)
                        if wx[jx + 1] * wy[jy + 1] > 1e-9]

LUTN = 2048
LUTS = 64.0
RAMP_R = [0] * LUTN
RAMP_G = [0] * LUTN
RAMP_B = [0] * LUTN
for k in range(LUTN):
    v = k / LUTS
    lum = 255.0 * (1.0 - math.exp(-v))
    q = v / (v + 4.0)
    RAMP_R[k] = int(lum)
    RAMP_G[k] = int(lum * (0.60 + 0.38 * q))
    RAMP_B[k] = int(lum * (0.22 + 0.72 * q))
RAMPC_R = [int(255.0 * (1.0 - math.exp(-k / LUTS)) * AMB_RGB[0]) for k in range(LUTN)]
RAMPC_G = [int(255.0 * (1.0 - math.exp(-k / LUTS)) * AMB_RGB[1]) for k in range(LUTN)]
RAMPC_B = [int(255.0 * (1.0 - math.exp(-k / LUTS)) * AMB_RGB[2]) for k in range(LUTN)]

KER = {}


def kernel(rad):
    n = int(rad * 4 + 0.5)
    k = KER.get(n)
    if k is None:
        sg = max(0.8, n / 4.0 * 0.85)
        m = int(sg * 2.2) + 1
        pts = []
        for dy in range(-m, m + 1):
            for dx in range(-m, m + 1):
                w = math.exp(-(dx * dx + dy * dy) / (2 * sg * sg))
                if w > 0.03:
                    pts.append((dx, dy, w))
        KER[n] = pts
        k = pts
    return k

FLAKES = []
for fi in range(NF + NF_NEAR + NF_FAR):
    if fi < NF_NEAR:
        cls = 0
        zf = random.uniform(ZN0, ZN1)
        v = random.uniform(VN0, VN1)
        b0 = 0.9 + 0.6 * random.random()
    elif fi < NF_NEAR + NF:
        cls = 1
        zf = DIST + random.uniform(ZB0, ZB1)
        v = V0 + (V1 - V0) * random.random() ** 0.8
        b0 = 0.3 + 0.9 * random.random() ** 2 + 0.2 * (v - V0) / (V1 - V0)
    else:
        cls = 2
        zf = random.uniform(ZF0, ZF1)
        v = random.uniform(VF0, VF1)
        b0 = 0.5 + 0.5 * random.random() ** 2
    k = max(1, int(v * T / YMIN_SPAN))
    span = v * T / k
    coc = FOCAL * APERTURE * abs(1.0 / zf - 1.0 / DIST)
    FLAKES.append((
        random.uniform(-BOX_X / 2, BOX_X / 2),
        zf,
        v,
        span,
        random.uniform(0.0, span),
        b0,
        random.uniform(*SWAY_A),
        random.randint(*SWAY_M),
        random.random(),
        kernel(coc / 2) if cls == 0 else None,
        AMB,
    ))

GUST_INT = GUST_PK * GUST_S * math.sqrt(TAU)
BASE_U = (BOX_X - GUST_INT) / T


def wind_disp(t):
    g = GUST_INT * 0.5 * (1.0 + math.erf((t - GUST_T) / (GUST_S * math.sqrt(2))))
    return BASE_U * t + g


def loft(t):
    u = (t - GUST_T) % T
    if u > T / 2:
        u -= T
    u /= GUST_S * 1.6
    return LOFT * math.exp(-0.5 * u * u)


def lull_w(e):
    s = ((e - LULL_T) % T) / LULL_L
    if s >= 1.0:
        return 1.0
    b = min(1.0, s / 0.22, (1.0 - s) / 0.22)
    return 1.0 - LULL_D * b


def halo_f(t):
    u = (t - LULL_T - 4.0) % T
    if u > T / 2:
        u -= T
    return 1.0 - HALO_DIP * math.exp(-0.5 * (u / 3.2) ** 2)


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
    t = frame * DT
    LF = [0.0] * WH
    LB = [0.0] * WH
    AF = [0.0] * WH
    AB = [0.0] * WH
    AN = [0.0] * WH
    D = wind_disp(t)
    D1 = wind_disp(t - DT)
    Yl = loft(t)
    Yl1 = loft(t - DT)
    halo_k = halo_f(t)
    for (x0, z, v, span, y0, b0, sa, sm, sph, ker, amb) in FLAKES:
        yy = (y0 - v * t + Yl) % span + YLO
        if yy < 0.0 or yy > 6.5:
            continue
        sw = sa * math.sin(TAU * (sm * t / T + sph))
        xx = (x0 + D + sw + BOX_X / 2) % BOX_X - BOX_X / 2
        dy = yy - CAM_H
        yc = dy * cp - z * sp
        zc = dy * sp + z * cp
        f = FOCAL / zc
        sx = CX + f * xx
        sy = CY - f * yc
        if sx < -4 or sx >= W + 4 or sy < -4 or sy >= H + 4:
            continue
        w = lull_w(t - (LULL_Y - yy) / v)
        if w < 0.03:
            continue
        rx, ry, rz = xx - LX, yy - LY, z - LZ
        r2 = rx * rx + ry * ry + rz * rz
        r = math.sqrt(r2)
        em = emit(-ry / r)
        ci = int(sy) * W + int(sx)
        c = CT[ci] if 0 <= ci < WH else 0.0
        ph = phase(c if z < LZ else -c)
        bl = b0 * w * EXPO * em * ph / (r2 + 0.15)
        if yy < 2.5:
            gx, gz = xx - LX, z - LZ
            g2 = gx * gx + gz * gz + LY * LY
            bl += b0 * w * EXPO * BOUNCE * LY / (g2 * math.sqrt(g2)) * (1.0 - yy / 2.5)
        bl = bl / (1.0 + bl / KNEE)
        ba = b0 * w * amb
        if bl < 0.004:
            bl = 0.0
        if bl == 0.0 and ba < 0.004:
            continue
        yy1 = (y0 - v * (t - DT) + Yl1) % span + YLO
        xx1 = xx - (D - D1)
        dy1 = yy1 - CAM_H
        yc1 = dy1 * cp - z * sp
        zc1 = dy1 * sp + z * cp
        f1 = FOCAL / zc1
        ddx = (sx - CX - f1 * xx1) * SHUTTER
        ddy = (sy - CY + f1 * yc1) * SHUTTER
        if abs(ddy) > 20 or abs(ddx) > 20:
            ddx, ddy = 0.0, 0.0
        ns = min(10, int(math.hypot(ddx, ddy)) + 1)
        front = z < LZ
        buf = LF if front else LB
        abuf = AF if front else AB
        if ker is not None:
            ns = min(ns, 4)
            al = NEAR_ALPHA * w / ns
            for k in range(ns):
                u = (k + 0.5) / ns - 0.5
                ix, iy = int(sx + ddx * u + 0.5), int(sy + ddy * u + 0.5)
                for dx, dy_, kw in ker:
                    jx, jy = ix + dx, iy + dy_
                    if 0 <= jx < W and 0 <= jy < H:
                        AN[jy * W + jx] += al * kw
            continue
        wl, wa = bl / ns, ba / ns
        for k in range(ns):
            u = (k + 0.5) / ns - 0.5
            qx, qy = sx + ddx * u, sy + ddy * u
            if qx < 1.0 or qy < 1.0 or qx >= W - 1 or qy >= H - 1:
                continue
            ix, iy = int(qx), int(qy)
            i = iy * W + ix
            sk = SUBK[(int((qx - ix) * 8), int((qy - iy) * 8))]
            for o, kw in sk:
                abuf[i + o] += wa * kw
            if not wl:
                continue
            for o, kw in sk:
                buf[i + o] += wl * kw
            if bl > BLOOM_T and 1 < ix < W - 2 and 1 < iy < H - 2:
                bw = (bl - BLOOM_T) * BLOOM / ns
                b2 = bw * 0.5
                buf[i - 1] += bw
                buf[i + 1] += bw
                buf[i - W] += bw
                buf[i + W] += bw
                buf[i - W - 1] += b2
                buf[i - W + 1] += b2
                buf[i + W - 1] += b2
                buf[i + W + 1] += b2
                buf[i - 2] += b2
                buf[i + 2] += b2
                buf[i - 2 * W] += b2
                buf[i + 2 * W] += b2

    for i in HEADMASK:
        LB[i] = 0.0
        AB[i] = 0.0
    R = BR[:]
    G = BG[:]
    B = BB[:]
    for i, hv in HALO.items():
        LB[i] += hv * halo_k
    for i in range(WH):
        vb = LB[i]
        vf = LF[i]
        ab = AB[i]
        af = AF[i]
        m = POSTMASK[i] if (vb or ab) else 0.0
        if m:
            vb *= 1.0 - m
            ab *= 1.0 - m
        vl = vb + vf
        va = ab + af
        an = AN[i]
        if not vl and not va and not an:
            continue
        r, g, b = R[i], G[i], B[i]
        if vl:
            k = int(vl * LUTS)
            if k >= LUTN:
                k = LUTN - 1
            r += RAMP_R[k]
            g += RAMP_G[k]
            b += RAMP_B[k]
        if va:
            k = int(va * LUTS)
            if k >= LUTN:
                k = LUTN - 1
            r += RAMPC_R[k]
            g += RAMPC_G[k]
            b += RAMPC_B[k]
        if an:
            if an > 0.85:
                an = 0.85
            r = int(r + (NEAR_RGB[0] - r) * an)
            g = int(g + (NEAR_RGB[1] - g) * an)
            b = int(b + (NEAR_RGB[2] - b) * an)
        R[i] = 255 if r > 255 else r
        G[i] = 255 if g > 255 else g
        B[i] = 255 if b > 255 else b

    for cy in range(height):
        line = Text()
        o1 = 2 * cy * W
        o2 = o1 + W
        prev, run = None, 0
        for x in range(W):
            st_ = sty((R[o1 + x], G[o1 + x], B[o1 + x]), (R[o2 + x], G[o2 + x], B[o2 + x]))
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

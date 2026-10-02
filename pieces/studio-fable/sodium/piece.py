from rich.text import Text
from rich.style import Style
from rich.color import Color
import math

# sodium

N = 360
DT = 0.08
W = width
H = height * 2
TAU = math.pi * 2

R = 700.0
HW = 4.5
WALL_H = 3.0
ARCH_H = 3.0
CAM_U = 1.75
CAM_H = 1.2
V = 5.0
LAMP_S = 12.0
STAG = 6.0
DASH_S = 8.0
DASH_ON = 2.0
LAMP_U = 2.8
LAMP_LEN = 1.0
LAMP_WID = 0.3
LAMP_DROP = 0.25
RES = 0.2
PITCH = 0.08
F = 94.0
ASPECT = 14.0 / 13.0
TMAX = 420.0

LAMP_I = 20.0
LAMP_DIR = 0.8
EMIT = 100.0
AMB = 0.015
FOG = 60.0
HAZE = 0.08
TONE = 1.1
TONE_GAMMA = 1.1
HALO_K = 0.6
HALO_R = 0.9
HALO_MIN = 2.5
ROUGH_NEAR = 0.4
ROUGH_FAR = 0.04
BLUR_MIN_S = 1.0
BLUR_MIN_L = 0.5
GRAZE = 1.5
F0 = 0.03
WET0 = 0.3
WET1 = 0.75
WET_SIG = 0.5
ALB_ASPH = 0.09
ALB_PAINT = 0.22
ALB_TILE = 0.42
ALB_GRIME = 0.18
ALB_ARCH = 0.22
ALB_SOOT = 0.05
ALB_TRIM = 0.6
TRIM_W = 0.12
COURSE = 0.35
COURSE_S = 0.4
RIB = 0.65
RIB_S = 4.0
GROOVE = 0.4
DEAD_UNITS = ((8, 1), (8, -1), (9, 1))
PAINT_W = 0.10
TILE_TOP = 2.4
TILE_FADE = 0.3
CAR = True
CAR_D = 14.0
CAR_U = 1.75
CAR_W = 1.8
CAR_H = 1.45
CAR_Y0 = 0.28
WINDOW = 0.3
TAIL_U = 0.62
TAIL_H = 0.9
TAIL_R = 0.18
TAIL_RGB = (255, 36, 22)
TAIL_STREAK = 10.0
TAIL_K = 0.7
TAIL_BLOOM = 0.55
TAIL_SIG = 2.6

XC = -(R + CAM_U)
LAMP_H = WALL_H + ARCH_H * math.sqrt(1.0 - (LAMP_U / HW) ** 2)
P = int(round(DASH_S / RES))
PL = int(round(LAMP_S / RES))
STEP = int(round(V * DT / RES))
LOOP_M = N * V * DT
PER = int(round(LOOP_M / RES))
NLAMP = int(round(LOOP_M / LAMP_S))
assert abs(STEP * RES - V * DT) < 1e-9 and abs(PER * RES - LOOP_M) < 1e-9
assert PER % P == 0 and PER % PL == 0 and abs(NLAMP * LAMP_S - LOOP_M) < 1e-9


def inside(u, h):
    if h <= 0.0:
        return False
    au = abs(u)
    if h < WALL_H:
        return au < HW
    b = (h - WALL_H) / ARCH_H
    return (au / HW) ** 2 + b * b < 1.0


def bound(u, h):
    au = abs(u)
    d = h
    if h < WALL_H:
        d = min(d, HW - au)
        b = 0.0
    else:
        b = (h - WALL_H) / ARCH_H
    rho = math.hypot(au / HW, b)
    d = min(d, ARCH_H * (1.0 - rho))
    return d


def local(x, y, z):
    r = math.hypot(x - XC, z)
    return r - R, y + CAM_H, r


def trace(ox, oy, oz, dx, dy, dz, t0=0.0):
    t = t0
    x, y, z = ox + t * dx, oy + t * dy, oz + t * dz
    u, h, _ = local(x, y, z)
    if not inside(u, h):
        return None
    while t < TMAX:
        st = bound(u, h) * 0.95
        if st < 0.02:
            st = 0.02
        tp = t
        t += st
        x, y, z = ox + t * dx, oy + t * dy, oz + t * dz
        u, h, _ = local(x, y, z)
        if not inside(u, h):
            lo, hi = tp, t
            for _ in range(14):
                m = (lo + hi) * 0.5
                x, y, z = ox + m * dx, oy + m * dy, oz + m * dz
                u, h, _ = local(x, y, z)
                if inside(u, h):
                    lo = m
                else:
                    hi = m
            t = (lo + hi) * 0.5
            x, y, z = ox + t * dx, oy + t * dy, oz + t * dz
            u, h, r = local(x, y, z)
            s = R * math.atan2(z, x - XC)
            return t, x, y, z, u, h, s, r
    return None


def surf_kind(u, h):
    au = abs(u)
    dr = h
    dw = HW - au if h < WALL_H else 9.0
    if h >= WALL_H:
        b = (h - WALL_H) / ARCH_H
        da = ARCH_H * (1.0 - math.hypot(au / HW, b))
    else:
        da = 9.0
    if dr <= dw and dr <= da:
        return 0
    if dw <= da:
        return 1 if u > 0 else 3
    return 2


perim = []
nr = int(round(2 * HW / RES))
for i in range(nr + 1):
    perim.append((-HW + 2 * HW * i / nr, 0.0, 0.0, 1.0, "road"))
ROW_ROAD0 = 0
ROW_WALLR0 = len(perim)
nw = int(round(WALL_H / RES))
for i in range(nw + 1):
    perim.append((HW, WALL_H * i / nw, -1.0, 0.0, "wall"))
ROW_ARCH0 = len(perim)
na = 60
for i in range(na + 1):
    a = math.pi * i / na
    u, h = HW * math.cos(a), WALL_H + ARCH_H * math.sin(a)
    gx, gy = u / (HW * HW), (h - WALL_H) / (ARCH_H * ARCH_H)
    gl = math.hypot(gx, gy)
    perim.append((u, h, -gx / gl, -gy / gl, "arch"))
ROW_WALLL0 = len(perim)
for i in range(nw + 1):
    perim.append((-HW, WALL_H * (1 - i / nw), 1.0, 0.0, "wall"))
NROW = len(perim)


def row_of(kind, u, h):
    if kind == 0:
        return ROW_ROAD0 + int(round((u + HW) / RES))
    if kind == 1:
        return ROW_WALLR0 + int(round(h / RES))
    if kind == 3:
        return ROW_WALLL0 + int(round((WALL_H - h) / RES))
    a = math.atan2((h - WALL_H) / ARCH_H, u / HW)
    a = min(math.pi, max(0.0, a))
    return ROW_ARCH0 + int(round(a * na / math.pi))


def albedo_of(row):
    u, h, nu, nh, kind = perim[row]
    if kind == "road":
        return ALB_ASPH
    if kind == "wall":
        a = ALB_GRIME + (ALB_TILE - ALB_GRIME) * min(1.0, max(0.0, (h - 0.5) / 0.6))
        k = min(1.0, max(0.0, (h - TILE_TOP) / TILE_FADE))
        return a + (ALB_ARCH - a) * k
    return ALB_ARCH + (ALB_SOOT - ALB_ARCH) * ((h - WALL_H) / ARCH_H) ** 2


def course_of(row):
    u, h, nu, nh, kind = perim[row]
    if kind != "wall" or h > TILE_TOP - 0.05 or h < 0.45:
        return 0.0
    return -COURSE * albedo_of(row) if int(round(h / RES)) % int(round(COURSE_S / RES)) == 0 else 0.0


_lg = (LAMP_U / (HW * HW), (LAMP_H - WALL_H) / (ARCH_H * ARCH_H))
_ll = math.hypot(*_lg)
LN = (-_lg[0] / _ll, -_lg[1] / _ll)
LAMPS = {1: (LAMP_U + LN[0] * LAMP_DROP, LAMP_H + LN[1] * LAMP_DROP, LN[0], LN[1]),
         -1: (-LAMP_U - LN[0] * LAMP_DROP, LAMP_H + LN[1] * LAMP_DROP, -LN[0], LN[1])}


def lamp_e(row, ds, side):
    u, h, nu, nh, kind = perim[row]
    lu, lh, lnu, lnh = LAMPS[side]
    du, dh = lu - u, lh - h
    d2 = du * du + dh * dh + ds * ds
    if d2 < 0.09:
        d2 = 0.09
    d = math.sqrt(d2)
    cp = (nu * du + nh * dh) / d
    if cp <= 0.0:
        return 0.0
    cl = -(lnu * du + lnh * dh) / d
    if cl <= 0.0:
        return 0.0
    return LAMP_I * cp * cl ** LAMP_DIR / d2


def emit_of(row, ds, side):
    u, h, nu, nh, kind = perim[row]
    if kind != "arch" or abs(ds) > LAMP_LEN * 0.5:
        return 0.0
    lu, lh, lnu, lnh = LAMPS[side]
    if math.hypot(u - (lu - lnu * LAMP_DROP), h - (lh - lnh * LAMP_DROP)) <= LAMP_WID * 0.5 + 0.05:
        return EMIT
    return 0.0


SPAN = int(round(60.0 / RES))
E1 = {side: [[lamp_e(row, (j - SPAN) * RES, side) for j in range(2 * SPAN + 1)] for row in range(NROW)]
      for side in (1, -1)}
EM1 = {side: [[emit_of(row, (j - SPAN) * RES, side) for j in range(2 * SPAN + 1)] for row in range(NROW)]
       for side in (1, -1)}
DEAD = set(DEAD_UNITS)


def lit(k, side):
    return (k % NLAMP, side) not in DEAD


EROW, JROW, LROW = [], [], []
for row in range(NROW):
    alb = albedo_of(row)
    kind = perim[row][4]
    er = [0.0] * PER
    lr = [0.0] * PER
    for k in range(NLAMP):
        for side in (1, -1):
            if not lit(k, side):
                continue
            base = int(round((k * LAMP_S + (STAG if side < 0 else 0.0)) / RES))
            e1, em1 = E1[side][row], EM1[side][row]
            for j in range(2 * SPAN + 1):
                idx = (base + j - SPAN) % PER
                er[idx] += e1[j]
                lr[idx] += em1[j]
    jr = [0.0] * PER
    for j in range(PER):
        er[j] += AMB
        if kind != "road":
            sj = (j * RES) % RIB_S
            if sj < RES * 0.5:
                jr[j] = RIB * er[j]
            elif sj < RES * 1.5:
                jr[j] = -GROOVE * er[j]
        lr[j] += alb * (er[j] + jr[j])
    EROW.append(er)
    JROW.append(jr)
    LROW.append(lr)

BLUR_W = (3, 5, 7, 11, 17, 25, 37, 55, 83, 125, 187, 281, 421, 631)
LAT_W = (1, 2, 3, 5, 8, 12, 18)
PS = [[0.0] * PER]
for row in range(NROW):
    PS.append([a + b for a, b in zip(PS[-1], LROW[row])])
LATAVG = {}
BLUR = {}


def latavg(row, k):
    key = (row, k)
    if key not in LATAVG:
        a, b = max(0, row - k), min(NROW - 1, row + k)
        pa, pb = PS[a], PS[b + 1]
        n = b - a + 1
        LATAVG[key] = [(y - x) / n for x, y in zip(pa, pb)]
    return LATAVG[key]


def blurred(row, level, latl):
    key = (row, level, latl)
    if key not in BLUR:
        src = latavg(row, LAT_W[latl])
        w = BLUR_W[level]
        half = w // 2
        for _pass in range(2):
            acc = sum(src[(j - half) % PER] for j in range(w))
            out = [0.0] * PER
            for j in range(PER):
                out[j] = acc / w
                acc += src[(j + half + 1) % PER] - src[(j - half) % PER]
            src = out
        BLUR[key] = src
    return BLUR[key]


def levpos(table, val):
    lv = math.log(max(val, table[0]))
    for i in range(len(table) - 1):
        a, b = math.log(table[i]), math.log(table[i + 1])
        if lv <= b:
            return i, (lv - a) / (b - a)
    return len(table) - 2, 1.0


def wet_of(u):
    v = 0.0
    for ut in (-2.55, -0.95, 0.95, 2.55):
        v += math.exp(-((u - ut) / WET_SIG) ** 2)
    return WET0 + (WET1 - WET0) * min(1.0, v)


def dashrow(fp):
    out = [0.0] * P
    for j in range(P):
        s = j * RES
        c = 0.0
        for n_ in range(-3, 4):
            c0 = DASH_S * (0.5 + n_)
            c += max(0.0, min(s + fp * 0.5, c0 + DASH_ON * 0.5) - max(s - fp * 0.5, c0 - DASH_ON * 0.5))
        out[j] = min(1.0, c / fp)
    return out


DASH_LEVELS = [0.05 * 1.5 ** i for i in range(16)]
DASHROWS = [dashrow(fp) for fp in DASH_LEVELS]

NPIX = W * H
cp, sp = math.cos(PITCH), math.sin(PITCH)
ZERO = [0.0] * PER

pix_row = [ZERO] * NPIX
pix_jrow = [ZERO] * NPIX
pix_jw = [0.0] * NPIX
pix_i0 = [0] * NPIX
pix_alb = [0.0] * NPIX
pix_add = [0.0] * NPIX
road_px, rd_i0, rd_dash, rd_lat = [], [], [], []
rd_r = [[], [], [], []]
rd_w = [[], [], [], []]

for py in range(H):
    yn = (H * 0.5 - (py + 0.5)) * ASPECT / F
    for px in range(W):
        xn = (px + 0.5 - W * 0.5) / F
        dx, dy, dz = xn, yn * cp + sp, cp - yn * sp
        ln = math.hypot(dx, dy, dz)
        dx, dy, dz = dx / ln, dy / ln, dz / ln
        p = py * W + px
        hit = trace(0.0, 0.0, 0.0, dx, dy, dz)
        if hit is None:
            pix_add[p] = HAZE
            continue
        t, x, y, z, u, h, s, r = hit
        kind = surf_kind(u, h)
        row = row_of(kind, u, h)
        fogk = math.exp(-t / FOG)
        pix_row[p] = EROW[row]
        pix_i0[p] = int(round(s / RES)) % PER
        pix_add[p] = HAZE * (1.0 - fogk)
        if kind != 0:
            fade = min(1.0, 1.5 * RES * F / t)
            alb = albedo_of(row) + course_of(row) * fade
            if kind != 2:
                fp_h = t / F * ASPECT
                cov = max(0.0, min(h + fp_h * 0.5, TILE_TOP + TRIM_W * 0.5) - max(h - fp_h * 0.5, TILE_TOP - TRIM_W * 0.5)) / fp_h
                alb += (ALB_TRIM - alb) * min(1.0, cov)
            pix_alb[p] = alb * fogk
            pix_jrow[p] = JROW[row]
            pix_jw[p] = fade
            continue
        fp_u = t / F
        edge = 0.0
        for ue in (-3.5, 3.5):
            edge += max(0.0, min(u + fp_u * 0.5, ue + PAINT_W * 0.5) - max(u - fp_u * 0.5, ue - PAINT_W * 0.5)) / fp_u
        lat = max(0.0, min(u + fp_u * 0.5, PAINT_W * 0.5) - max(u - fp_u * 0.5, -PAINT_W * 0.5)) / fp_u
        pix_alb[p] = (ALB_ASPH + (ALB_PAINT - ALB_ASPH) * min(1.0, edge)) * fogk
        fp_s = t * t / (F * CAM_H) * ASPECT
        lvl = min(range(len(DASH_LEVELS)), key=lambda i: abs(math.log(DASH_LEVELS[i] / max(fp_s, 1e-3))))
        cosv = -dy
        fr = F0 + (1.0 - F0) * (1.0 - cosv) ** 5
        rh = trace(x, y, z, dx, -dy, dz, 0.02)
        road_px.append(p)
        rd_lat.append((ALB_PAINT - ALB_ASPH) * lat * fogk)
        rd_dash.append(DASHROWS[lvl])
        if rh is None:
            rd_i0.append(0)
            for k_ in range(4):
                rd_r[k_].append(ZERO)
                rd_w[k_].append(0.0)
            continue
        t2, x2, y2, z2, u2, h2, s2, r2 = rh
        row2 = row_of(surf_kind(u2, h2), u2, h2)
        rough = ROUGH_FAR + (ROUGH_NEAR - ROUGH_FAR) * cosv
        bw = max(BLUR_MIN_S, rough * t2 / max(0.12, cosv)) / RES
        lw = max(BLUR_MIN_L, rough * t2) / RES
        li, lf = levpos(BLUR_W, bw)
        ti, tf = levpos([w_ + 0.5 for w_ in LAT_W], lw * 0.5 + 0.5)
        gain = fr * (1.0 - cosv) ** GRAZE * wet_of(u) * fogk * math.exp(-t2 / FOG) / max(0.12, cosv)
        rd_i0.append(int(round(s2 / RES)) % PER)
        for k_, (a_, b_, wgt) in enumerate(((li, ti, (1 - lf) * (1 - tf)), (li + 1, ti, lf * (1 - tf)),
                                             (li, ti + 1, (1 - lf) * tf), (li + 1, ti + 1, lf * tf))):
            rd_r[k_].append(blurred(row2, a_, b_) if wgt > 0.0 else ZERO)
            rd_w[k_].append(gain * wgt)

NRD = len(road_px)
rd_r0, rd_r1, rd_r2, rd_r3 = rd_r
rd_w0, rd_w1, rd_w2, rd_w3 = rd_w


def project(s_rel, u, h):
    phi = s_rel / R
    x = XC + (R + u) * math.cos(phi)
    z = (R + u) * math.sin(phi)
    y = h - CAM_H
    yy = y * cp - z * sp
    zz = y * sp + z * cp
    if zz < 0.3:
        return None
    return W * 0.5 + F * x / zz, H * 0.5 - F * yy / zz / ASPECT, zz


RAMP = [(10, 8, 12), (72, 28, 8), (178, 92, 18), (236, 150, 42), (255, 196, 92), (255, 236, 190)]
RAMP_T = [0.0, 0.22, 0.46, 0.66, 0.84, 1.0]


def ramp(v):
    if v <= 0.0:
        return RAMP[0]
    if v >= 1.0:
        return RAMP[-1]
    for i in range(len(RAMP) - 1):
        if v <= RAMP_T[i + 1]:
            k = (v - RAMP_T[i]) / (RAMP_T[i + 1] - RAMP_T[i])
            a, b = RAMP[i], RAMP[i + 1]
            return (int(a[0] + (b[0] - a[0]) * k), int(a[1] + (b[1] - a[1]) * k), int(a[2] + (b[2] - a[2]) * k))
    return RAMP[-1]


LUT = [ramp(i / 255.0) for i in range(256)]
_style = {}


def sty(a, b):
    s = _style.get((a, b))
    if s is None:
        ca = LUT[a] if isinstance(a, int) else a
        cb = LUT[b] if isinstance(b, int) else b
        s = Style(color=Color.from_rgb(*ca), bgcolor=Color.from_rgb(*cb))
        _style[(a, b)] = s
    return s


def tone(x):
    return int(255.0 * (1.0 - math.exp(-TONE * x)) ** TONE_GAMMA)


def car_geom(frame):
    ph = TAU * frame / N
    d = CAR_D + 1.5 * math.sin(ph) + 0.6 * math.sin(3 * ph + 1.0)
    u = CAR_U + 0.12 * math.sin(2 * ph + 0.4)
    return d, u


def splat(over, cx, cy, sig, amp, ext):
    e2 = float(ext * ext)
    for yy in range(max(0, int(cy) - ext), min(H, int(cy) + ext + 1)):
        dy2 = (yy + 0.5 - cy) ** 2
        for xx in range(max(0, int(cx) - ext), min(W, int(cx) + ext + 1)):
            dd2 = (xx + 0.5 - cx) ** 2 + dy2
            if dd2 >= e2:
                continue
            wnd = 1.0 - dd2 / e2
            p = yy * W + xx
            c = amp * wnd * wnd / (1.0 + dd2 / (sig * sig))
            if c > over.get(p, 0.0):
                over[p] = c


for frame in range(N):
    canvas.clear()
    if len(_style) > 6000:
        _style.clear()
    off = (frame * STEP) % PER
    E = [r[(i + off) % PER] + w * jr[(i + off) % PER] for r, jr, w, i in zip(pix_row, pix_jrow, pix_jw, pix_i0)]
    v = [a * e + d for a, e, d in zip(pix_alb, E, pix_add)]
    for q in range(NRD):
        p = road_px[q]
        j = (pix_i0[p] + off) % PER
        j2 = (rd_i0[q] + off) % PER
        v[p] += rd_lat[q] * rd_dash[q][j % P] * E[p] + rd_w0[q] * rd_r0[q][j2] + rd_w1[q] * rd_r1[q][j2] \
            + rd_w2[q] * rd_r2[q][j2] + rd_w3[q] * rd_r3[q][j2]
    s_cam = frame * V * DT
    k0 = int(math.floor(s_cam / LAMP_S)) - 1
    for k in range(k0, k0 + 24):
        for side in (1, -1):
            s_rel = k * LAMP_S + (STAG if side < 0 else 0.0) - s_cam
            if s_rel < -1.0 or s_rel > 260.0:
                continue
            on = lit(k, side)
            a = project(s_rel - LAMP_LEN * 0.5, side * LAMP_U, LAMP_H)
            b = project(s_rel + LAMP_LEN * 0.5, side * LAMP_U, LAMP_H)
            if a is None or b is None:
                continue
            ax, ay, az = a
            bx, by, bz = b
            fog = math.exp(-az / FOG)
            wpx = max(0.5, F * LAMP_WID / az)
            x0, x1 = int(min(ax, bx) - wpx - 1), int(max(ax, bx) + wpx + 2)
            y0, y1 = int(min(ay, by) - wpx - 1), int(max(ay, by) + wpx + 2)
            ex, ey = bx - ax, by - ay
            el2 = ex * ex + ey * ey + 1e-9
            val = (2.5 if on else 0.02) * fog
            for yy in range(max(0, y0), min(H, y1)):
                for xx in range(max(0, x0), min(W, x1)):
                    qx, qy = xx + 0.5 - ax, yy + 0.5 - ay
                    tt = max(0.0, min(1.0, (qx * ex + qy * ey) / el2))
                    dd = math.hypot(qx - tt * ex, qy - tt * ey)
                    cov = min(1.0, max(0.0, wpx * 0.5 + 0.5 - dd))
                    if cov > 0.0:
                        p = yy * W + xx
                        v[p] = v[p] * (1.0 - cov) + val * cov
            if not on:
                continue
            sig = min(8.0, max(HALO_MIN, F * HALO_R / az))
            amp = HALO_K * fog / (1.0 + az / 12.0)
            ext = int(sig * 3.5) + 1
            e2 = float(ext * ext)
            cx, cy = (ax + bx) * 0.5, (ay + by) * 0.5
            hx, hy = int(cx), int(cy)
            el = math.sqrt(el2)
            for yy in range(max(0, min(y0, hy - ext)), min(H, max(y1, hy + ext + 1))):
                for xx in range(max(0, min(x0, hx - ext)), min(W, max(x1, hx + ext + 1))):
                    qx, qy = xx + 0.5 - ax, yy + 0.5 - ay
                    tt = max(0.0, min(1.0, (qx * ex + qy * ey) / el2))
                    ddx, ddy = qx - tt * ex, qy - tt * ey
                    dd2 = ddx * ddx + ddy * ddy
                    if dd2 >= e2:
                        continue
                    wnd = 1.0 - dd2 / e2
                    v[yy * W + xx] += amp * wnd * wnd / (1.0 + dd2 / (sig * sig))
    over = {}
    if CAR:
        cd, cu = car_geom(frame)
        bl = project(cd, cu - CAR_W * 0.5, CAR_Y0)
        tr = project(cd, cu + CAR_W * 0.5, CAR_Y0 + CAR_H)
        if bl is not None and tr is not None:
            cx0, cy1, cz = bl
            cx1, cy0, _ = tr
            fog = math.exp(-cz / FOG)
            body = 0.03 + HAZE * (1.0 - fog)
            win_y = cy0 + (cy1 - cy0) * 0.3
            for yy in range(max(0, int(cy0)), min(H, int(cy1) + 1)):
                cvy = min(1.0, max(0.0, min(yy + 1.0, cy1) - max(float(yy), cy0)))
                wk = min(1.0, max(0.0, win_y - yy)) * WINDOW * fog
                for xx in range(max(0, int(cx0)), min(W, int(cx1) + 1)):
                    cvx = min(1.0, max(0.0, min(xx + 1.0, cx1) - max(float(xx), cx0)))
                    c = cvx * cvy
                    if c > 0.0:
                        p = yy * W + xx
                        v[p] = v[p] * (1.0 - c) + (body + wk) * c
            for side in (-1, 1):
                tp = project(cd, cu + side * TAIL_U, TAIL_H)
                if tp is None:
                    continue
                tx, ty, tz = tp
                rpx = max(0.45, F * TAIL_R / tz)
                for yy in range(int(ty - rpx - 1), int(ty + rpx + 2)):
                    for xx in range(int(tx - rpx - 1), int(tx + rpx + 2)):
                        if not (0 <= xx < W and 0 <= yy < H):
                            continue
                        dd = math.hypot(xx + 0.5 - tx, yy + 0.5 - ty)
                        c = min(1.0, max(0.0, rpx + 0.5 - dd)) * fog
                        if c > 0.0:
                            p = yy * W + xx
                            over[p] = max(over.get(p, 0.0), c)
                splat(over, tx, ty, TAIL_SIG * 28.0 / tz * 0.5 + TAIL_SIG * 0.5, TAIL_BLOOM * fog, int(TAIL_SIG * 3.5) + 2)
                vp_ = project(cd, cu + side * TAIL_U, -TAIL_H)
                if vp_ is None:
                    continue
                vx, vy, vz = vp_
                wet_ = wet_of(cu + side * TAIL_U)
                sl = TAIL_STREAK * 28.0 / tz
                for yy in range(int(vy), min(H, int(vy + sl * 3))):
                    dyv = yy + 0.5 - vy
                    if dyv < 0:
                        continue
                    fall = math.exp(-dyv / sl) * TAIL_K * wet_ * fog
                    for xx in range(int(vx - 1.5), int(vx + 2.5)):
                        if not (0 <= xx < W):
                            continue
                        c = fall * min(1.0, max(0.0, 1.2 - abs(xx + 0.5 - vx)))
                        if c > 0.01:
                            p = yy * W + xx
                            over[p] = max(over.get(p, 0.0), c)
    q = [tone(x) for x in v]
    cols = [None] * NPIX
    for p, c in over.items():
        base = LUT[q[p]]
        cols[p] = (int(base[0] + (TAIL_RGB[0] - base[0]) * c), int(base[1] + (TAIL_RGB[1] - base[1]) * c),
                   int(base[2] + (TAIL_RGB[2] - base[2]) * c))
    for cy in range(height):
        line = Text()
        o1, o2 = 2 * cy * W, (2 * cy + 1) * W
        prev, run = None, 0
        for x in range(W):
            ta = cols[o1 + x]
            tb = cols[o2 + x]
            st_ = sty(q[o1 + x] if ta is None else ta, q[o2 + x] if tb is None else tb)
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

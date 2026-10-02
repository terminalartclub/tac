from rich.text import Text
from rich.style import Style
from rich.color import Color
import math
import cmath
import random
import sys
import types

# wake

N = 480
W = width
H = height * 2
WS = W * 2
TAU = math.pi * 2

T = 240
ST = 0.18
G0 = 3.0
Y0F = 0.8
Y1F = 1.7
X0F = 0.4
X1F = 0.55
QS = 1.8
LS = 1.2
DSC = 0.13
D = min(W * DSC, H * DSC * 0.606)
a = D / 2
a2 = a * a
MMPX = 25.0 / D
NU4 = 4 * 1.0 / (MMPX * MMPX) * 0.1
U = D / (T * ST)
RXF = 0.40
RYF = 0.86
RX, RY = W * RXF, H * RYF
HS = 2
PORTS = ((-89, 1.02, 2, 1.0), (-35, 1.5, 2, 0.35))
LCUT = 9.0
STEP = 0.8

SXF = 1.4
SYF = 0.80
FANW = 1.0
FUP0 = 0.6
FUP1 = 1.4
LATT = 260.0
LPOW = 0.7
SHD = 0.55
HAZE = 0.5
DITH = 1.0
RIMK = 1.0
GAP = 0.97
GAP_R = 60
GAP_W = 20
BREATH = 0.25
DYE_K = 0.9
DGAM = 0.7
TAU_D = 800.0
BLOOM = 0.12
AGEHUE = 0.5
NSPK = 28
SPK = 70.0
SPC = (0.55, 0.75, 1.0)
BG = (8, 9, 14)
HZC = (3.0, 7.0, 17.0)
RIMC = (120, 170, 240)
RAMP = ((0.0, (0, 0, 0)), (0.30, (16, 70, 34)), (0.70, (118, 214, 92)), (1.0, (232, 250, 196)))
QEPS = 10
QMIN = 24


def smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def vy(tn):
    v0, v1 = 0.29 * U, 0.85 * U
    if tn < 0.5:
        return D * (Y0F + (Y1F - Y0F) * 2 * tn)
    s = min(tn - 0.5, 0.5) * T
    return D * Y1F + v0 * s + (v1 - v0) * s * s / T + v1 * max(0.0, tn - 1.0) * T


TN_END = 0.5
while vy(TN_END) < RY + 3 * D:
    TN_END += 0.05
TN_FADE = TN_END - 0.5


def vort(t):
    out = []
    csum = 0.0
    kmax = math.floor(t / (T / 2))
    for k in range(kmax - int(2 * TN_END) - 2, kmax + 1):
        tau = t - k * T / 2
        if tau < 0:
            continue
        tn = tau / T
        if tn > TN_END:
            continue
        sig = 1.0 if k % 2 == 0 else -1.0
        g = G0 * U * D * (smooth(0, 0.5, tn) if tn < 0.5 else math.exp(-(tn - 0.5) / 4)) * (1 - smooth(TN_FADE, TN_END, tn))
        zk = complex(sig * D * (X0F + (X1F - X0F) * smooth(0, 1.5, tn)), vy(tn))
        c = sig * g / TAU
        out.append((zk, c, 1.0 / ((0.15 * D) ** 2 + NU4 * tau), a2 / zk.conjugate()))
        csum += c
    return out, csum


A = 0
while vy(A / T) < RY + 6:
    A += HS
A = min(A + 4 * HS, 3 * T)
NI = A // HS

_key = ("wake", W, H, T, G0, QS, LS, Y0F, Y1F, X0F, X1F, PORTS, NSPK, RX, RY, D)
_cache = sys.modules.get("_tac_wake")
if _cache is None or getattr(_cache, "key", None) != _key:
    VS = [vort(float(t)) for t in range(T)]
    QK = QS * U * a / TAU
    ZSRC = complex(0, 0.2 * a)
    ILC = 1.0 / (LS * D) ** 2
    _exp = math.exp

    def vel(z, ti):
        vs, csum = VS[ti % T]
        acc = csum / z
        for zk, c, irc, zi in vs:
            d = z - zk
            r = abs(d)
            acc += c * (1 - _exp(-r * r * irc)) / d - c / (z - zi)
        return (-1j * U * (1 + a2 / (z * z)) - 1j * acc + QK * _exp(-abs(z - ZSRC) ** 2 * ILC) / (z - ZSRC)).conjugate()

    def path(z, t, stop):
        tr = [z]
        while not stop(z, len(tr)):
            k1 = vel(z, t)
            k2 = vel(z + k1 * (HS / 2), t + HS // 2)
            z = z + HS * k2
            r = abs(z)
            if r < a + 0.25:
                z = z / r * (a + 0.25)
            tr.append(z)
            t += HS
        return tr

    tables = []
    for ang, rf, g, _m in PORTS:
        p0 = a * rf * cmath.exp(1j * math.radians(ang))
        tables.append([path(p0, ph * g, lambda z, n: n > NI + 1) for ph in range(T // g)])
    srng = random.Random(11)
    specks = []
    for i in range(NSPK):
        z0 = complex(srng.uniform(-RX + 0.5, W - RX - 0.5), -(H - RY) - 1.0)
        rs = srng.randrange(N)
        tr = path(z0, rs, lambda z, n: z.imag > RY + 2 or n > N)
        specks.append((rs, srng.uniform(0, TAU), srng.choice((2, 3, 5)), srng.uniform(0.4, 1.0), tr))
    _cache = types.ModuleType("_tac_wake")
    _cache.key = _key
    _cache.tables = tables
    _cache.specks = specks
    sys.modules["_tac_wake"] = _cache
tables = _cache.tables
specks = _cache.specks

SX, SY = SXF * W, SYF * H
rod_d = math.hypot(RX - SX, RY - SY)
rod_th = math.atan2(RY - SY, RX - SX)
rod_al = math.asin(min(1.0, a / rod_d))
_tu = math.atan2(RY - 20 - SY, RX - SX)
FSGN = 1.0 if ((_tu - rod_th + math.pi) % TAU - math.pi) > 0 else -1.0
LIGHT = [[0.0] * WS for _ in range(H)]
BGR = [[0] * WS for _ in range(H)]
BGG = [[0] * WS for _ in range(H)]
BGB = [[0] * WS for _ in range(H)]
for y in range(H):
    for xs in range(WS):
        dx, dy = (xs + 0.5) / 2 - SX, y + 0.5 - SY
        r = math.hypot(dx, dy)
        dth = (math.atan2(dy, dx) - rod_th + math.pi) % TAU - math.pi
        up = FSGN * dth
        fan = smooth(-FANW - 0.15, -FANW + 0.15, up) * (1 - smooth(FUP0, FUP1, up))
        sh = 1.0
        if r > rod_d:
            sh = 1 - SHD + SHD * smooth(rod_al * 0.8, rod_al * 1.2 + 0.003 * (r - rod_d), abs(dth))
        L = fan * sh * (rod_d / r) ** LPOW * math.exp(-(r - rod_d) / LATT)
        LIGHT[y][xs] = L
        BGR[y][xs] = BG[0] + HZC[0] * HAZE * L
        BGG[y][xs] = BG[1] + HZC[1] * HAZE * L
        BGB[y][xs] = BG[2] + HZC[2] * HAZE * L

lx, ly = math.cos(rod_th + math.pi), math.sin(rod_th + math.pi)
for y in range(int(RY - a - 2), int(RY + a + 3)):
    for xs in range(int(2 * (RX - a - 2)), int(2 * (RX + a + 3))):
        if not (0 <= xs < WS and 0 <= y < H):
            continue
        px, py = (xs + 0.5) / 2 - RX, y + 0.5 - RY
        d = math.hypot(px, py)
        c = min(1.0, max(0.0, a - 0.2 - d))
        rim = max(0.0, (px * lx + py * ly) / max(d, 1e-6)) * max(0.0, 1.0 - abs(d - (a - 0.6)) / 0.9)
        c = max(c, min(1.0, rim * 1.5))
        if c > 0:
            BGR[y][xs] = BGR[y][xs] * (1 - c) + (7 + RIMC[0] * rim * RIMK) * c
            BGG[y][xs] = BGG[y][xs] * (1 - c) + (8 + RIMC[1] * rim * RIMK) * c
            BGB[y][xs] = BGB[y][xs] * (1 - c) + (11 + RIMC[2] * rim * RIMK) * c
            LIGHT[y][xs] *= 1 - c
drng = random.Random(3)
for y in range(H):
    for xs in range(WS):
        e = (drng.random() - 0.5) * DITH
        BGR[y][xs] += e
        BGG[y][xs] += e
        BGB[y][xs] += e
for row in (BGR, BGG, BGB):
    for y in range(H):
        row[y] = [max(0, min(255, int(v + 0.5))) for v in row[y]]

LUT = []
for i in range(256):
    c = i / 255.0
    for (c0, k0), (c1, k1) in zip(RAMP, RAMP[1:]):
        if c <= c1:
            u = (c - c0) / (c1 - c0)
            LUT.append(tuple(p + (q - p) * u for p, q in zip(k0, k1)))
            break
EXPT = [LUT[int(255.0 * (1 - math.exp(-(i / 64.0) ** DGAM)))] for i in range(64 * 12)]
WSH = [math.exp(-age / TAU_D) for age in range(A + 2)]


def bump(x, w):
    x = (x + N / 2) % N - N / 2
    return math.exp(-x * x / (w * w))


BRE = [1.0 + BREATH * math.cos(TAU * (r - GAP_R - N / 2) / N) for r in range(N)]
BOL = [BRE[r] * (1 - GAP * bump(r - GAP_R, GAP_W)) for r in range(N)]

QCH = {12: "▀", 3: "▄", 10: "▌", 5: "▐", 8: "▘", 4: "▝", 2: "▖", 1: "▗", 9: "▚", 6: "▞", 14: "▛", 13: "▜", 11: "▙", 7: "▟"}


def blur(buf, rx, ry, x0, x1):
    x0, x1 = max(0, x0 - rx - ry), min(WS, x1 + rx + ry + 1)
    out = [[0.0] * WS for _ in range(H)]
    n = 2 * rx + 1
    tmp = []
    for row in buf:
        o = [0.0] * WS
        s = sum(row[min(WS - 1, max(0, k))] for k in range(x0 - rx, x0 + rx + 1))
        for x in range(x0, x1):
            o[x] = s / n
            s += row[min(WS - 1, x + rx + 1)] - row[max(0, x - rx)]
        tmp.append(o)
    n = 2 * ry + 1
    for x in range(x0, x1):
        s = sum(tmp[min(H - 1, max(0, k))][x] for k in range(-ry, ry + 1))
        for y in range(H):
            out[y][x] = s / n
            s += tmp[min(H - 1, y + ry + 1)][x] - tmp[max(0, y - ry)][x]
    return out


_style = {}


def sty(t, b):
    key = (t, b)
    s = _style.get(key)
    if s is None:
        s = Style(color=Color.from_rgb(*t), bgcolor=Color.from_rgb(*b))
        _style[key] = s
    return s


LC2 = LCUT * LCUT * 4

for frame in range(N):
    canvas.clear()
    if len(_style) > 6000:
        _style.clear()
    sharp = [[0.0] * WS for _ in range(H)]
    soft = [[0.0] * WS for _ in range(H)]
    bx0, bx1 = WS, 0
    for (ang, rf, g, pm), table in zip(PORTS, tables):
        r0 = g * (frame // g)
        a0 = frame - r0
        half = T // (2 * g)
        nph = T // g
        for side in (1.0, -1.0):
            pts = []
            ms = []
            j = 0
            age = a0
            while age <= A:
                r = r0 - j * g
                tr = table[(r // g - (0 if side > 0 else half)) % nph]
                fi = age / HS
                i0 = int(fi)
                f = fi - i0
                z = tr[i0] * (1 - f) + tr[i0 + 1] * f
                pts.append((2 * (RX + side * z.real), RY - z.imag))
                m = g * pm * BOL[r % N]
                ws = WSH[age]
                ms.append((m * ws, m * (1 - ws)))
                j += 1
                age += g
            npt = len(pts)
            for k in range(npt - 1):
                p1 = pts[k]
                p2 = pts[k + 1]
                ddx, ddy = p2[0] - p1[0], p2[1] - p1[1]
                l2 = ddx * ddx + ddy * ddy
                if l2 > LC2 or p1[1] < -2 and p2[1] < -2:
                    continue
                p0 = pts[k - 1] if k else p1
                p3 = pts[k + 2] if k + 2 < npt else p2
                n = int(math.sqrt(l2) / STEP) + 1
                msh, mso = ms[k][0] / n, ms[k][1] / n
                for s in range(n):
                    u = (s + 0.5) / n
                    u2 = u * u
                    u3 = u2 * u
                    b0 = -0.5 * u3 + u2 - 0.5 * u
                    b1 = 1.5 * u3 - 2.5 * u2 + 1
                    b2 = -1.5 * u3 + 2 * u2 + 0.5 * u
                    b3 = 0.5 * u3 - 0.5 * u2
                    x = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0] - 0.5
                    y = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1] - 0.5
                    ix, iy = int(x), int(y)
                    if 0 <= iy < H - 1 and 0 <= ix < WS - 1:
                        if ix < bx0:
                            bx0 = ix
                        if ix > bx1:
                            bx1 = ix
                        fx, fy = x - ix, y - iy
                        w00, w10, w01, w11 = (1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy
                        ra, rb = sharp[iy], sharp[iy + 1]
                        ra[ix] += msh * w00
                        ra[ix + 1] += msh * w10
                        rb[ix] += msh * w01
                        rb[ix + 1] += msh * w11
                        ra, rb = soft[iy], soft[iy + 1]
                        ra[ix] += mso * w00
                        ra[ix + 1] += mso * w10
                        rb[ix] += mso * w01
                        rb[ix + 1] += mso * w11

    if bx1 < bx0:
        bx0, bx1 = 0, 0
    soft = blur(soft, 2, 1, bx0, bx1 + 1)
    for y in range(H):
        sr, tr_ = sharp[y], soft[y]
        for x in range(max(0, bx0 - 4), min(WS, bx1 + 6)):
            sr[x] += tr_[x]
    glow = blur(sharp, 4, 2, bx0, bx1 + 1)
    R = [row[:] for row in BGR]
    Gc = [row[:] for row in BGG]
    B = [row[:] for row in BGB]
    xa_, xb_ = max(0, bx0 - 12), min(WS, bx1 + 14)
    for y in range(H):
        dr, gr, lr, so = sharp[y], glow[y], LIGHT[y], soft[y]
        oR, oG, oB = R[y], Gc[y], B[y]
        for x in range(xa_, xb_):
            v = (dr[x] + BLOOM * gr[x]) * lr[x] * DYE_K
            if v > 0.004:
                cr, cg, cb = EXPT[min(767, int(v * 64))]
                f = AGEHUE * so[x] / (dr[x] + 1e-6)
                if f > 1.0:
                    f = AGEHUE
                oR[x] = min(255, int(oR[x] + cr * (1 - 0.4 * f)))
                oG[x] = min(255, int(oG[x] + cg * (1 - 0.06 * f)))
                oB[x] = min(255, int(oB[x] + cb + 0.3 * cg * f))

    for rs, sph, sk, sw, tr in specks:
        for age in ((frame - rs) % N, (frame - rs) % N + N):
            fi = age / HS
            i0 = int(fi)
            if i0 + 1 >= len(tr):
                continue
            f = fi - i0
            z = tr[i0] * (1 - f) + tr[i0 + 1] * f
            x = 2 * (RX + z.real) - 0.5
            y = RY - z.imag - 0.5
            ix, iy = int(x), int(y)
            if 0 <= iy < H - 1 and 0 <= ix < WS - 1:
                fx, fy = x - ix, y - iy
                e = SPK * sw * LIGHT[iy][ix] * (0.55 + 0.45 * math.sin(TAU * sk * frame / N + sph))
                for xx, yy, w_ in ((ix, iy, (1 - fx) * (1 - fy)), (ix + 1, iy, fx * (1 - fy)), (ix, iy + 1, (1 - fx) * fy), (ix + 1, iy + 1, fx * fy)):
                    R[yy][xx] = min(255, int(R[yy][xx] + e * w_ * SPC[0]))
                    Gc[yy][xx] = min(255, int(Gc[yy][xx] + e * w_ * SPC[1]))
                    B[yy][xx] = min(255, int(B[yy][xx] + e * w_ * SPC[2]))

    for cy in range(height):
        line = Text()
        Rt, Gt, Bt = R[2 * cy], Gc[2 * cy], B[2 * cy]
        Rb, Gb, Bb = R[2 * cy + 1], Gc[2 * cy + 1], B[2 * cy + 1]
        prev, pch, run = None, "", 0
        for cx in range(W):
            xa = 2 * cx
            xb = xa + 1
            r0_, g0_, b0_ = Rt[xa], Gt[xa], Bt[xa]
            r1_, g1_, b1_ = Rt[xb], Gt[xb], Bt[xb]
            r2_, g2_, b2_ = Rb[xa], Gb[xa], Bb[xa]
            r3_, g3_, b3_ = Rb[xb], Gb[xb], Bb[xb]
            l0 = 2 * r0_ + 5 * g0_ + b0_
            l1 = 2 * r1_ + 5 * g1_ + b1_
            l2 = 2 * r2_ + 5 * g2_ + b2_
            l3 = 2 * r3_ + 5 * g3_ + b3_
            lo = min(l0, l1, l2, l3)
            hi = max(l0, l1, l2, l3)
            if abs(l0 - l1) + abs(l2 - l3) <= QEPS * 8 or hi - lo < QMIN * 8:
                ch = "▀"
                fg = ((r0_ + r1_) >> 1, (g0_ + g1_) >> 1, (b0_ + b1_) >> 1)
                bg = ((r2_ + r3_) >> 1, (g2_ + g3_) >> 1, (b2_ + b3_) >> 1)
            else:
                t_ = (lo + hi) >> 1
                bits = (8 if l0 > t_ else 0) | (4 if l1 > t_ else 0) | (2 if l2 > t_ else 0) | (1 if l3 > t_ else 0)
                fr = fg_ = fb = br = bg_ = bb = nf = 0
                for bit, cr, cg, cb in ((8, r0_, g0_, b0_), (4, r1_, g1_, b1_), (2, r2_, g2_, b2_), (1, r3_, g3_, b3_)):
                    if bits & bit:
                        fr += cr
                        fg_ += cg
                        fb += cb
                        nf += 1
                    else:
                        br += cr
                        bg_ += cg
                        bb += cb
                nb = 4 - nf
                ch = QCH[bits]
                fg = (fr // nf, fg_ // nf, fb // nf)
                bg = (br // nb, bg_ // nb, bb // nb)
            st_ = sty(fg, bg)
            if st_ is prev and ch == pch:
                run += 1
            else:
                if run:
                    line.append(pch * run, prev)
                prev, pch, run = st_, ch, 1
        if run:
            line.append(pch * run, prev)
        canvas.write(line)

    await sleep(0.1)

# ember
from rich.style import Style
import math
N, dt = 20, 0.1
_styles = {}
for frame in range(N):
    canvas.clear()
    ph = 2 * math.pi * frame / N
    for y in range(height):
        t = Text()
        for x in range(0, width, 4):
            v = int(8 * (0.5 + 0.5 * math.sin(ph + x * 0.1 + y * 0.05)))
            st = _styles.get(v)
            if st is None:
                st = _styles[v] = Style(color=f"rgb({40 + 24 * v},{20 + 6 * v},15)", bgcolor="rgb(8,8,15)")
            t.append("▀▀▀▀", st)
        canvas.write(t)
    await sleep(dt)

"""Vector source for Big Bold Garden, one drawing per art page.

Written for the audience the concept names: adults with low vision, arthritis or
tremor. That decides every constant here — a thick uniform stroke, few shapes,
large enclosed regions, and a generous margin so nothing runs off the page.

Geometry is computed rather than hand-typed so that stroke weight and level of
simplification really are identical across forty pages, which is what the prompt
plan asks for and what a reader notices when it is missing.

Units are hundredths of an inch: the viewBox is the 7.875 x 10.5 in live area at
100 units per inch, so a stroke of 10 is 0.10 in, about 7pt — well over the
2.5pt floor the plan sets.

Run from kdp-studio/:  python3 books/bold-easy-garden/build_art.py
"""

import math
from pathlib import Path

W, H = 787.5, 1050.0
CX, CY = W / 2, H / 2
MARGIN = 62                      # nothing may touch the page edge
# One weight for the whole book. The prompt plan asks for stroke identical to
# every other page, so complexity varies by how many shapes a page carries,
# never by how heavy its line is. 10 units is 0.10 in, about 7pt.
BOLD = FINE = 10
OUT = Path(__file__).resolve().parent / "art"

WHITE = "#ffffff"


class Art:
    """An accumulating drawing. Later shapes paint over earlier ones."""

    def __init__(self, stroke=BOLD):
        self.stroke = stroke
        self.parts = []

    # -- primitives ---------------------------------------------------------

    def circle(self, cx, cy, r, fill="none"):
        self.parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"/>')

    def ellipse(self, cx, cy, rx, ry, fill="none", rot=None):
        t = f' transform="rotate({rot:.1f} {cx:.1f} {cy:.1f})"' if rot else ""
        self.parts.append(
            f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{fill}"{t}/>'
        )

    def rect(self, x, y, w, h, r=0, fill="none"):
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{r:.1f}" fill="{fill}"/>'
        )

    def path(self, d, fill="none"):
        self.parts.append(f'<path d="{d}" fill="{fill}"/>')

    def line(self, x1, y1, x2, y2):
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )

    def poly(self, points, fill="none", closed=True):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        tag = "polygon" if closed else "polyline"
        self.parts.append(f'<{tag} points="{pts}" fill="{fill}"/>')

    # -- compound shapes ----------------------------------------------------

    def petals(self, cx, cy, n, r0, r1x, r1y, half, swell=1.2, rot=-math.pi / 2):
        """A ring of closed petals whose tips ride an ellipse."""
        for i in range(n):
            t = 2 * math.pi * i / n + rot
            tip = 1 / math.hypot(math.cos(t) / r1x, math.sin(t) / r1y)
            p = lambda r, a: (cx + r * math.cos(a), cy + r * math.sin(a))
            lx, ly = p(r0, t - half)
            rx, ry = p(r0, t + half)
            tx, ty = p(tip, t)
            c1 = p(tip * 0.62, t - half * swell)
            c2 = p(tip * 0.99, t - half * 0.8)
            c3 = p(tip * 0.99, t + half * 0.8)
            c4 = p(tip * 0.62, t + half * swell)
            self.path(
                f"M{lx:.1f} {ly:.1f}C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} "
                f"{tx:.1f} {ty:.1f}C{c3[0]:.1f} {c3[1]:.1f} {c4[0]:.1f} {c4[1]:.1f} "
                f"{rx:.1f} {ry:.1f}Z"
            )

    def leaf(self, cx, cy, length, width, angle=0.0, vein=True, fill="none"):
        """A pointed leaf, tip-to-tip along ``angle``."""
        a = math.radians(angle)
        ux, uy = math.cos(a), math.sin(a)
        px, py = -uy, ux
        tip = (cx + ux * length / 2, cy + uy * length / 2)
        base = (cx - ux * length / 2, cy - uy * length / 2)
        s1 = (cx + px * width / 2, cy + py * width / 2)
        s2 = (cx - px * width / 2, cy - py * width / 2)
        self.path(
            f"M{base[0]:.1f} {base[1]:.1f}"
            f"Q{s1[0]:.1f} {s1[1]:.1f} {tip[0]:.1f} {tip[1]:.1f}"
            f"Q{s2[0]:.1f} {s2[1]:.1f} {base[0]:.1f} {base[1]:.1f}Z",
            fill=fill,
        )
        if vein:
            self.line(*base, *tip)

    def scalloped(self, cx, cy, r, lobes, depth=0.16, fill="none"):
        """A round outline made of bumps — a tree crown, a cup rim."""
        d = []
        for i in range(lobes + 1):
            t = 2 * math.pi * i / lobes - math.pi / 2
            x, y = cx + r * math.cos(t), cy + r * math.sin(t)
            if i == 0:
                d.append(f"M{x:.1f} {y:.1f}")
            else:
                mt = t - math.pi / lobes
                mr = r * (1 + depth)
                d.append(f"Q{cx + mr * math.cos(mt):.1f} {cy + mr * math.sin(mt):.1f} {x:.1f} {y:.1f}")
        self.path("".join(d) + "Z", fill=fill)

    def spiral(self, cx, cy, r0, r1, turns, steps=180):
        d = []
        for i in range(steps + 1):
            f = i / steps
            t = f * turns * 2 * math.pi
            r = r0 + (r1 - r0) * f
            x, y = cx + r * math.cos(t), cy + r * math.sin(t)
            d.append(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}")
        self.path("".join(d))

    def pot(self, cx, top_y, bottom_y, top_w, bottom_w, rim=26):
        """A tapered pot with a rim band. Used with different proportions."""
        self.path(
            f"M{cx - top_w / 2:.1f} {top_y:.1f}L{cx + top_w / 2:.1f} {top_y:.1f}"
            f"L{cx + bottom_w / 2:.1f} {bottom_y:.1f}"
            f"Q{cx:.1f} {bottom_y + 22:.1f} {cx - bottom_w / 2:.1f} {bottom_y:.1f}Z",
            fill=WHITE,
        )
        self.rect(cx - top_w / 2 - 14, top_y - rim, top_w + 28, rim, r=10, fill=WHITE)

    # -- output -------------------------------------------------------------

    def svg(self):
        body = "\n".join("    " + p for p in self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">\n'
            f'  <rect x="0" y="0" width="{W}" height="{H}" fill="{WHITE}"/>\n'
            f'  <g fill="none" stroke="#000000" stroke-width="{self.stroke}" '
            f'stroke-linecap="round" stroke-linejoin="round">\n{body}\n  </g>\n</svg>\n'
        )


PAGES = {}


def page(page_id, stroke=BOLD):
    def register(fn):
        PAGES[page_id] = (fn, stroke)
        return fn

    return register


# --- complexity 1: one object, few shapes -----------------------------------


@page("PAGE-004")
def sunflower(a):                       # a single wide-petalled sunflower head
    disc = 168
    a.petals(CX, CY, 14, disc * 0.72, W / 2 - MARGIN - 5, H / 2 - MARGIN - 5,
             math.pi / 14 * 0.94, swell=1.18)
    a.circle(CX, CY, disc, fill=WHITE)
    a.circle(CX, CY, disc * 0.55)


@page("PAGE-006")
def watering_can(a):                    # one watering can, side on
    x, y = CX - 40, CY + 60
    a.path(f"M{x - 150} {y - 150}L{x + 150} {y - 150}L{x + 120} {y + 170}"
           f"Q{x} {y + 205} {x - 120} {y + 170}Z", fill=WHITE)
    a.rect(x - 172, y - 196, 344, 48, r=20, fill=WHITE)          # rim
    a.path(f"M{x + 150} {y - 110}L{x + 300} {y - 250}L{x + 336} {y - 208}"
           f"L{x + 186} {y - 62}Z", fill=WHITE)                  # spout
    a.circle(x + 318, y - 229, 46, fill=WHITE)                   # rose head
    a.path(f"M{x - 150} {y - 130}C{x - 300} {y - 190} {x - 296} {y + 30} "
           f"{x - 150} {y - 10}", fill=WHITE)                    # handle
    a.path(f"M{x - 60} {y - 196}C{x - 30} {y - 290} {x + 60} {y - 290} "
           f"{x + 90} {y - 196}", fill=WHITE)                    # top handle


@page("PAGE-008")
def pot_three_leaves(a):                # a plant pot with three round leaves
    a.pot(CX, CY + 150, CY + 400, 320, 230)
    for dx, dy, r in ((-150, -80, 96), (0, -180, 110), (150, -80, 96)):
        a.line(CX, CY + 120, CX + dx * 0.8, CY + dy + r * 0.7)
    for dx, dy, r in ((-150, -80, 96), (0, -180, 110), (150, -80, 96)):
        a.circle(CX + dx, CY + dy, r, fill=WHITE)


@page("PAGE-010")
def butterfly(a):                       # a large simple butterfly, wings open
    for side in (-1, 1):
        a.path(f"M{CX} {CY - 40}C{CX + side * 340} {CY - 330} {CX + side * 330} "
               f"{CY - 30} {CX + side * 120} {CY + 20}Z", fill=WHITE)
        a.path(f"M{CX} {CY + 30}C{CX + side * 280} {CY + 90} {CX + side * 240} "
               f"{CY + 350} {CX + side * 70} {CY + 190}Z", fill=WHITE)
        a.path(f"M{CX + side * 26} {CY - 190}C{CX + side * 90} {CY - 300} "
               f"{CX + side * 130} {CY - 330} {CX + side * 150} {CY - 300}")
    a.ellipse(CX, CY + 10, 40, 210, fill=WHITE)
    a.circle(CX, CY - 190, 44, fill=WHITE)


@page("PAGE-012")
def snail_on_leaf(a):                   # one snail on a broad leaf
    # The foot overlaps the leaf's upper surface, so the snail is on the leaf
    # rather than hovering over it. Note the leaf is given a nominal width well
    # over what it renders at: `leaf` shapes with quadratic curves, which reach
    # about half their control offset, so the drawn leaf is roughly half as
    # wide as the number asks for.
    a.leaf(CX, CY + 250, 680, 470, angle=-6, vein=False, fill=WHITE)
    a.path(f"M{CX - 320} {CY + 268}Q{CX} {CY + 176} {CX + 320} {CY + 214}")
    a.path(f"M{CX - 240} {CY + 155}Q{CX - 220} {CY + 40} {CX - 96} {CY + 40}"
           f"L{CX + 60} {CY + 40}L{CX + 60} {CY + 155}Z", fill=WHITE)
    a.circle(CX + 76, CY - 40, 178, fill=WHITE)
    a.spiral(CX + 76, CY - 40, 24, 148, 2.2)
    a.line(CX - 206, CY + 56, CX - 262, CY - 76)
    a.line(CX - 126, CY + 42, CX - 150, CY - 96)
    a.circle(CX - 266, CY - 94, 20)
    a.circle(CX - 154, CY - 114, 20)


@page("PAGE-014")
def spade(a):                           # a garden spade standing in soil
    a.path(f"M{CX - 120} {CY + 120}L{CX + 120} {CY + 120}L{CX + 100} {CY + 350}"
           f"Q{CX} {CY + 420} {CX - 100} {CY + 350}Z", fill=WHITE)
    a.rect(CX - 34, CY - 300, 68, 430, r=16, fill=WHITE)
    a.path(f"M{CX - 34} {CY - 300}L{CX - 110} {CY - 400}L{CX + 110} {CY - 400}"
           f"L{CX + 34} {CY - 300}Z", fill=WHITE)
    a.rect(CX - 110, CY - 430, 220, 60, r=28, fill=WHITE)
    a.path(f"M{MARGIN + 20} {CY + 330}Q{CX} {CY + 250} {W - MARGIN - 20} {CY + 330}")


@page("PAGE-016")
def pebbles(a):                         # three round pebbles stacked
    a.ellipse(CX, CY + 300, 300, 150, fill=WHITE)
    a.ellipse(CX + 10, CY + 60, 235, 175, fill=WHITE, rot=-6)
    a.ellipse(CX - 20, CY - 190, 165, 130, fill=WHITE, rot=8)


@page("PAGE-018")
def teacup(a):                          # a wide teacup on a saucer
    a.ellipse(CX, CY + 300, 330, 76, fill=WHITE)
    a.path(f"M{CX + 250} {CY - 60}C{CX + 400} {CY - 40} {CX + 400} {CY + 130} "
           f"{CX + 210} {CY + 140}", fill=WHITE)
    a.path(f"M{CX - 270} {CY - 110}L{CX + 270} {CY - 110}"
           f"Q{CX + 236} {CY + 250} {CX} {CY + 260}"
           f"Q{CX - 236} {CY + 250} {CX - 270} {CY - 110}Z", fill=WHITE)
    a.ellipse(CX, CY - 110, 270, 66, fill=WHITE)


@page("PAGE-020")
def tulip(a):                           # one open tulip on a straight stem
    a.rect(CX - 16, CY - 60, 32, 420, r=8, fill=WHITE)
    a.leaf(CX - 130, CY + 200, 380, 130, angle=-72, fill=WHITE)
    a.leaf(CX + 130, CY + 230, 330, 120, angle=-108, fill=WHITE)
    a.path(f"M{CX - 210} {CY - 300}Q{CX - 230} {CY + 30} {CX} {CY + 60}"
           f"Q{CX + 230} {CY + 30} {CX + 210} {CY - 300}"
           f"Q{CX + 120} {CY - 150} {CX + 70} {CY - 300}"
           f"Q{CX} {CY - 150} {CX - 70} {CY - 300}"
           f"Q{CX - 120} {CY - 150} {CX - 210} {CY - 300}Z", fill=WHITE)


@page("PAGE-022")
def bird_bath(a):                       # a bird bath seen from the front
    # Wide and shallow on a short, sturdy pedestal — a goblet is what you get
    # when the bowl is deep and the stem is long.
    a.path(f"M{CX - 340} {CY - 150}L{CX + 340} {CY - 150}"
           f"Q{CX + 300} {CY + 20} {CX} {CY + 30}"
           f"Q{CX - 300} {CY + 20} {CX - 340} {CY - 150}Z", fill=WHITE)
    a.ellipse(CX, CY - 150, 340, 74, fill=WHITE)
    a.ellipse(CX, CY - 150, 250, 52)
    a.path(f"M{CX - 110} {CY + 24}L{CX - 90} {CY + 250}L{CX + 90} {CY + 250}"
           f"L{CX + 110} {CY + 24}Z", fill=WHITE)
    a.path(f"M{CX - 260} {CY + 380}L{CX - 210} {CY + 250}L{CX + 210} {CY + 250}"
           f"L{CX + 260} {CY + 380}Z", fill=WHITE)
    a.ellipse(CX, CY + 380, 260, 56, fill=WHITE)


@page("PAGE-024")
def bumblebee(a):                       # a fat bumblebee above a flower
    a.petals(CX, CY + 290, 8, 40, 150, 150, math.pi / 8 * 0.9)
    a.circle(CX, CY + 290, 58, fill=WHITE)
    for side in (-1, 1):
        a.ellipse(CX + side * 130, CY - 230, 150, 78, fill=WHITE, rot=side * -26)
    a.ellipse(CX, CY - 90, 230, 175, fill=WHITE)
    for dx in (-80, 10, 100):
        a.path(f"M{CX + dx} {CY - 250}Q{CX + dx + 26} {CY - 90} {CX + dx} {CY + 70}")
    a.circle(CX - 230, CY - 150, 40, fill=WHITE)
    a.line(CX - 250, CY - 185, CX - 300, CY - 280)
    a.line(CX - 205, CY - 190, CX - 215, CY - 290)


@page("PAGE-026")
def welly(a):                           # one wellington boot with a daisy in it
    # The daisy sits high and well to one side, and the boot is narrower than a
    # watering can body, so the two pages do not share a silhouette.
    a.rect(CX - 180, CY - 250, 26, 240, r=8, fill=WHITE)
    a.petals(CX - 168, CY - 330, 12, 34, 128, 128, math.pi / 12 * 0.9)
    a.circle(CX - 168, CY - 330, 50, fill=WHITE)
    a.path(f"M{CX - 240} {CY - 60}L{CX - 10} {CY - 60}L{CX - 10} {CY + 190}"
           f"L{CX + 210} {CY + 220}Q{CX + 268} {CY + 240} {CX + 258} {CY + 340}"
           f"L{CX - 240} {CY + 340}Z", fill=WHITE)
    a.rect(CX - 266, CY - 112, 282, 58, r=24, fill=WHITE)
    a.line(CX - 240, CY + 258, CX + 262, CY + 258)


@page("PAGE-028")
def birdhouse(a):                       # a simple wooden birdhouse
    a.rect(CX - 40, CY + 180, 80, 260, r=10, fill=WHITE)
    a.rect(CX - 230, CY - 120, 460, 310, r=14, fill=WHITE)
    a.path(f"M{CX - 290} {CY - 110}L{CX} {CY - 380}L{CX + 290} {CY - 110}Z", fill=WHITE)
    a.circle(CX, CY - 10, 92, fill=WHITE)
    a.rect(CX - 18, CY + 100, 36, 90, r=12, fill=WHITE)


@page("PAGE-030")
def pumpkin(a):                         # a round pumpkin with a curled stem
    py = CY - 40                        # sits high, with a leaf to one side
    a.ellipse(CX, py, 320, 276, fill=WHITE)
    for dx in (-155, -53, 53, 155):
        a.path(f"M{CX + dx} {py - 262}Q{CX + dx * 1.28} {py} {CX + dx} {py + 262}")
    a.path(f"M{CX - 20} {py - 272}L{CX - 20} {py - 372}"
           f"Q{CX + 90} {py - 442} {CX + 130} {py - 342}")
    a.leaf(CX - 190, CY + 330, 380, 190, angle=20, fill=WHITE)
    a.path(f"M{CX - 40} {py + 250}Q{CX - 120} {CY + 300} {CX - 300} {CY + 300}")


@page("PAGE-032")
def apple(a):                           # one apple with a leaf
    a.path(f"M{CX} {CY - 160}C{CX - 130} {CY - 300} {CX - 340} {CY - 180} "
           f"{CX - 320} {CY + 60}C{CX - 306} {CY + 260} {CX - 150} {CY + 380} "
           f"{CX} {CY + 300}C{CX + 150} {CY + 380} {CX + 306} {CY + 260} "
           f"{CX + 320} {CY + 60}C{CX + 340} {CY - 180} {CX + 130} {CY - 300} "
           f"{CX} {CY - 160}Z", fill=WHITE)
    a.path(f"M{CX} {CY - 180}Q{CX + 20} {CY - 300} {CX - 10} {CY - 380}")
    a.leaf(CX + 130, CY - 350, 250, 110, angle=-24, fill=WHITE)


@page("PAGE-034")
def mushroom(a):                        # a broad mushroom cap on a thick stalk
    a.path(f"M{CX - 110} {CY - 20}L{CX - 130} {CY + 300}"
           f"Q{CX} {CY + 350} {CX + 130} {CY + 300}L{CX + 110} {CY - 20}Z", fill=WHITE)
    a.path(f"M{CX - 330} {CY - 10}Q{CX - 300} {CY - 330} {CX} {CY - 330}"
           f"Q{CX + 300} {CY - 330} {CX + 330} {CY - 10}"
           f"Q{CX} {CY + 70} {CX - 330} {CY - 10}Z", fill=WHITE)
    a.path(f"M{CX - 200} {CY + 20}Q{CX} {CY + 84} {CX + 200} {CY + 20}")


@page("PAGE-036")
def watering_rose(a):                   # a watering rose spraying three drops
    a.path(f"M{CX - 120} {CY - 330}L{CX + 120} {CY - 330}L{CX + 170} {CY - 60}"
           f"L{CX - 170} {CY - 60}Z", fill=WHITE)
    a.ellipse(CX, CY - 50, 180, 56, fill=WHITE)
    for i, dx in enumerate((-190, 0, 190)):
        y = CY + 150 + abs(dx) * 0.28
        a.path(f"M{CX + dx} {y - 110}C{CX + dx + 92} {y + 10} {CX + dx + 62} {y + 130} "
               f"{CX + dx} {y + 130}C{CX + dx - 62} {y + 130} {CX + dx - 92} {y + 10} "
               f"{CX + dx} {y - 110}Z", fill=WHITE)


@page("PAGE-038")
def ladybird(a):                        # one ladybird on a wide leaf
    a.leaf(CX, CY + 40, 700, 420, angle=12, fill=WHITE)
    a.ellipse(CX + 20, CY - 30, 215, 185, fill=WHITE)
    a.path(f"M{CX + 20} {CY - 215}L{CX + 20} {CY + 155}")
    a.path(f"M{CX - 195} {CY - 30}Q{CX - 175} {CY - 200} {CX - 20} {CY - 205}", fill=WHITE)
    for dx, dy in ((-110, -60), (-95, 70), (115, -60), (100, 70)):
        a.circle(CX + 20 + dx, CY - 30 + dy, 44, fill=WHITE)


@page("PAGE-040")
def terracotta_pot(a):                  # a plain terracotta pot, empty
    a.pot(CX, CY - 200, CY + 320, 480, 330, rim=52)
    a.ellipse(CX, CY - 200, 240, 60, fill=WHITE)


@page("PAGE-042")
def daisy(a):                           # a large daisy seen face on
    a.rect(CX - 14, CY + 190, 28, 300, r=8, fill=WHITE)
    a.leaf(CX - 120, CY + 400, 260, 96, angle=-155, fill=WHITE)
    a.petals(CX, CY - 80, 20, 60, 300, 300, math.pi / 20 * 0.86, swell=1.05)
    a.circle(CX, CY - 80, 96, fill=WHITE)


# --- complexity 2: the same line, a few more shapes -------------------------


@page("PAGE-044", FINE)
def fork_and_trowel(a):                 # a garden fork crossed with a trowel
    a.path(f"M{CX - 250} {CY + 360}L{CX + 130} {CY - 300}", )
    a.path(f"M{CX - 268} {CY + 350}L{CX - 232} {CY + 370}L{CX + 148} {CY - 290}"
           f"L{CX + 112} {CY - 310}Z", fill=WHITE)
    a.rect(CX + 96, CY - 400, 90, 120, r=32, fill=WHITE)
    a.path(f"M{CX + 250} {CY + 360}L{CX - 130} {CY - 300}")
    a.path(f"M{CX + 268} {CY + 350}L{CX + 232} {CY + 370}L{CX - 148} {CY - 290}"
           f"L{CX - 112} {CY - 310}Z", fill=WHITE)
    a.rect(CX - 186, CY - 400, 90, 120, r=32, fill=WHITE)
    a.path(f"M{CX - 290} {CY + 300}L{CX - 190} {CY + 470}L{CX - 60} {CY + 400}"
           f"Q{CX - 150} {CY + 330} {CX - 290} {CY + 300}Z", fill=WHITE)
    for i in range(3):
        x = CX + 176 + i * 60
        a.path(f"M{CX + 214 + i * 0} {CY + 340}")
    a.path(f"M{CX + 190} {CY + 330}L{CX + 190} {CY + 470}", )
    a.path(f"M{CX + 250} {CY + 340}L{CX + 250} {CY + 480}")
    a.path(f"M{CX + 310} {CY + 320}L{CX + 310} {CY + 460}")
    a.path(f"M{CX + 170} {CY + 300}L{CX + 330} {CY + 300}L{CX + 320} {CY + 350}"
           f"L{CX + 180} {CY + 350}Z", fill=WHITE)


@page("PAGE-046", FINE)
def pear(a):                            # one pear on a branch
    # The stalk meets the branch. Left hanging in the air it reads as two
    # unrelated drawings that happen to share a page.
    a.path(f"M{CX - 300} {CY - 400}Q{CX - 60} {CY - 330} {CX + 290} {CY - 390}")
    a.path(f"M{CX - 30} {CY - 352}L{CX} {CY - 200}")
    a.leaf(CX + 150, CY - 300, 250, 104, angle=26, fill=WHITE)
    a.leaf(CX - 190, CY - 290, 230, 96, angle=150, fill=WHITE)
    a.path(f"M{CX} {CY - 200}C{CX - 90} {CY - 210} {CX - 150} {CY - 90} "
           f"{CX - 140} {CY + 30}C{CX - 132} {CY + 190} {CX - 250} {CY + 230} "
           f"{CX - 240} {CY + 330}C{CX - 230} {CY + 430} {CX - 110} {CY + 470} "
           f"{CX} {CY + 470}C{CX + 110} {CY + 470} {CX + 230} {CY + 430} "
           f"{CX + 240} {CY + 330}C{CX + 250} {CY + 230} {CX + 132} {CY + 190} "
           f"{CX + 140} {CY + 30}C{CX + 150} {CY - 90} {CX + 90} {CY - 210} "
           f"{CX} {CY - 200}Z", fill=WHITE)


@page("PAGE-048", FINE)
def sun_hat(a):                         # a wide-brimmed sun hat
    a.ellipse(CX, CY + 110, 340, 180, fill=WHITE)
    a.path(f"M{CX - 210} {CY + 90}Q{CX - 190} {CY - 260} {CX} {CY - 260}"
           f"Q{CX + 190} {CY - 260} {CX + 210} {CY + 90}Z", fill=WHITE)
    a.path(f"M{CX - 214} {CY + 30}Q{CX} {CY + 110} {CX + 214} {CY + 30}")
    a.path(f"M{CX - 214} {CY - 30}Q{CX} {CY + 50} {CX + 214} {CY - 30}")
    a.ellipse(CX, CY + 110, 180, 74)


@page("PAGE-050", FINE)
def robin(a):                           # a robin perched on a thick twig
    a.path(f"M{MARGIN + 46} {CY + 420}Q{CX} {CY + 330} {W - MARGIN - 46} {CY + 400}", fill=WHITE)
    a.path(f"M{MARGIN + 46} {CY + 470}Q{CX} {CY + 380} {W - MARGIN - 46} {CY + 450}")
    a.path(f"M{CX + 190} {CY + 60}C{CX + 210} {CY - 130} {CX + 90} {CY - 250} "
           f"{CX - 60} {CY - 230}C{CX - 220} {CY - 210} {CX - 280} {CY - 40} "
           f"{CX - 200} {CY + 130}C{CX - 150} {CY + 250} {CX + 110} {CY + 250} "
           f"{CX + 190} {CY + 60}Z", fill=WHITE)
    a.circle(CX + 40, CY - 200, 130, fill=WHITE)
    a.path(f"M{CX + 160} {CY - 200}L{CX + 260} {CY - 170}L{CX + 160} {CY - 130}Z", fill=WHITE)
    a.circle(CX + 80, CY - 230, 22)
    a.path(f"M{CX - 250} {CY + 60}L{CX - 400} {CY + 190}L{CX - 250} {CY + 190}Z", fill=WHITE)
    a.line(CX - 40, CY + 230, CX - 40, CY + 400)
    a.line(CX + 70, CY + 220, CX + 70, CY + 390)


@page("PAGE-052", FINE)
def acorn(a):                           # one acorn with its cup
    # Set low and to the left with an oak leaf above it. Centred and upright it
    # read as the same shape as the pumpkin and the empty pot under perceptual
    # hashing — three large round masses in the middle of a page.
    ax, ay = CX - 130, CY + 190
    a.path(f"M{ax - 185} {ay - 40}C{ax - 185} {ay + 180} {ax - 90} {ay + 300} "
           f"{ax} {ay + 300}C{ax + 90} {ay + 300} {ax + 185} {ay + 180} "
           f"{ax + 185} {ay - 40}Z", fill=WHITE)
    a.path(f"M{ax - 210} {ay - 40}Q{ax} {ay + 40} {ax + 210} {ay - 40}"
           f"Q{ax + 200} {ay - 190} {ax} {ay - 190}"
           f"Q{ax - 200} {ay - 190} {ax - 210} {ay - 40}Z", fill=WHITE)
    for dx in (-120, -40, 40, 120):
        a.path(f"M{ax + dx} {ay - 182}Q{ax + dx + 18} {ay - 100} {ax + dx} {ay - 10}")
    a.path(f"M{ax} {ay - 190}Q{ax + 60} {ay - 280} {ax + 150} {ay - 300}")
    a.leaf(CX + 90, CY - 260, 520, 260, angle=-28, fill=WHITE)


@page("PAGE-054", FINE)
def wheelbarrow(a):                     # a simple wheelbarrow, side on
    # Sitting low with a tall handle rake, rather than as a wide mass across the
    # middle of the page — which is where the watering can and the teacup live.
    y = CY + 150
    a.path(f"M{CX - 250} {y - 120}L{CX + 190} {y - 120}L{CX + 90} {y + 130}"
           f"L{CX - 160} {y + 130}Z", fill=WHITE)
    a.line(CX + 190, y - 100, CX + 300, y - 230)
    a.line(CX - 250, y - 100, CX - 330, y - 200)
    a.path(f"M{CX - 160} {y + 130}L{CX - 130} {y + 260}")
    a.path(f"M{CX + 90} {y + 130}L{CX + 80} {y + 260}")
    a.circle(CX - 190, y + 200, 130, fill=WHITE)
    a.circle(CX - 190, y + 200, 48)
    a.rect(CX + 262, y - 292, 76, 42, r=20, fill=WHITE)
    a.rect(CX - 372, y - 258, 76, 42, r=20, fill=WHITE)


@page("PAGE-056", FINE)
def carrots(a):                         # a bunch of three carrots
    for i, (dx, tilt) in enumerate(((-190, -14), (10, 0), (200, 14))):
        top = CY - 40 + abs(dx) * 0.16
        a.path(f"M{CX + dx - 90} {top}L{CX + dx + 90} {top}"
               f"L{CX + dx + tilt * 4} {top + 420}Z", fill=WHITE)
        for k in (0.28, 0.52, 0.76):
            y = top + 420 * k
            half = 90 * (1 - k) + 6
            a.line(CX + dx - half, y, CX + dx + half, y)
        for ang in (-40, 0, 40):
            a.leaf(CX + dx + math.sin(math.radians(ang)) * 120, top - 130,
                   250, 70, angle=-90 + ang, fill=WHITE)


@page("PAGE-058", FINE)
def rose(a):                            # one open rose, few petals
    a.petals(CX, CY, 8, 190, 330, 330, math.pi / 8 * 0.95, swell=1.1)
    a.petals(CX, CY, 6, 105, 205, 205, math.pi / 6 * 0.95, swell=1.1, rot=0.0)
    a.circle(CX, CY, 105, fill=WHITE)
    a.path(f"M{CX - 70} {CY - 20}Q{CX} {CY - 90} {CX + 70} {CY - 20}"
           f"Q{CX} {CY + 60} {CX - 70} {CY - 20}Z")
    a.leaf(CX - 250, CY + 400, 260, 110, angle=140, fill=WHITE)
    a.leaf(CX + 250, CY + 400, 260, 110, angle=40, fill=WHITE)


@page("PAGE-060", FINE)
def garden_gate(a):                     # a garden gate, closed
    for x in (CX - 330, CX + 330):
        a.rect(x - 42, CY - 330, 84, 700, r=14, fill=WHITE)
    a.rect(CX - 290, CY - 250, 580, 70, r=12, fill=WHITE)
    a.rect(CX - 290, CY + 150, 580, 70, r=12, fill=WHITE)
    for i in range(5):
        x = CX - 250 + i * 125
        a.path(f"M{x - 42} {CY - 300}L{x + 42} {CY - 300}L{x + 42} {CY + 330}"
               f"L{x} {CY + 380}L{x - 42} {CY + 330}Z", fill=WHITE)
    a.rect(CX - 290, CY - 250, 580, 70, r=12)
    a.rect(CX - 290, CY + 150, 580, 70, r=12)


@page("PAGE-062", FINE)
def caterpillar(a):                     # a fat caterpillar on a stem
    a.path(f"M{MARGIN + 20} {CY + 340}Q{CX} {CY + 260} {W - MARGIN - 20} {CY + 350}")
    for i in range(5):
        x = CX - 250 + i * 118
        a.circle(x, CY + 60 + math.sin(i * 0.9) * 44, 118, fill=WHITE)
    a.circle(CX + 250, CY - 10, 140, fill=WHITE)
    a.circle(CX + 295, CY - 50, 26)
    a.path(f"M{CX + 250} {CY + 40}Q{CX + 300} {CY + 76} {CX + 340} {CY + 30}")
    a.line(CX + 210, CY - 140, CX + 175, CY - 250)
    a.line(CX + 290, CY - 140, CX + 320, CY - 250)
    a.circle(CX + 170, CY - 265, 20)
    a.circle(CX + 326, CY - 265, 20)


@page("PAGE-064", FINE)
def strawberry(a):                      # one strawberry with its leaves
    a.path(f"M{CX - 300} {CY - 120}C{CX - 300} {CY + 180} {CX - 130} {CY + 420} "
           f"{CX} {CY + 430}C{CX + 130} {CY + 420} {CX + 300} {CY + 180} "
           f"{CX + 300} {CY - 120}Z", fill=WHITE)
    for dx, dy in ((-150, 20), (0, -20), (150, 20), (-80, 200), (80, 200), (0, 320)):
        a.ellipse(CX + dx, CY + dy, 34, 46, fill=WHITE, rot=12)
    for ang in (-150, -115, -65, -30):
        a.leaf(CX + math.cos(math.radians(ang)) * 170,
               CY - 120 + math.sin(math.radians(ang)) * 90,
               250, 100, angle=ang, fill=WHITE)
    a.path(f"M{CX} {CY - 190}L{CX + 20} {CY - 330}")


@page("PAGE-066", FINE)
def bee_hive(a):                        # a bee hive, simple box shape
    y = CY + 330
    for i, w in enumerate((330, 300, 270)):
        a.rect(CX - w, y - 190 * (i + 1), w * 2, 170, r=14, fill=WHITE)
    a.path(f"M{CX - 360} {y - 570}L{CX} {y - 700}L{CX + 360} {y - 570}Z", fill=WHITE)
    a.rect(CX - 90, y - 90, 180, 60, r=14, fill=WHITE)
    a.ellipse(CX, y + 30, 380, 54, fill=WHITE)


@page("PAGE-068", FINE)
def veined_leaf(a):                     # a wide leaf with clear veins
    # Veins are drawn as pairs that stop short of the outline, so they read as
    # veins. Run to the edge and overlapped they became a thicket.
    import math as _m
    ang = -74
    a.leaf(CX, CY - 10, 780, 430, angle=ang, vein=False, fill=WHITE)
    r = _m.radians(ang)
    ux, uy = _m.cos(r), _m.sin(r)
    base = (CX - ux * 390, CY - 10 - uy * 390)
    tip = (CX + ux * 390, CY - 10 + uy * 390)
    a.line(*base, *tip)
    for i in range(5):
        f = 0.16 + i * 0.15
        bx, by = base[0] + (tip[0] - base[0]) * f, base[1] + (tip[1] - base[1]) * f
        reach = 168 * (1 - abs(f - 0.42))
        for side in (-1, 1):
            px, py = -uy * side, ux * side
            a.path(f"M{bx:.1f} {by:.1f}"
                   f"Q{bx + px * reach * 0.7 + ux * 40:.1f} {by + py * reach * 0.7 + uy * 40:.1f} "
                   f"{bx + px * reach + ux * 110:.1f} {by + py * reach + uy * 110:.1f}")
    a.path(f"M{base[0]:.1f} {base[1]:.1f}L{base[0] - ux * 110:.1f} {base[1] - uy * 110:.1f}")


@page("PAGE-070", FINE)
def lemon(a):                           # one lemon on a small branch
    # A lemon has nubs at both ends, and it hangs from the branch rather than
    # sitting near it.
    a.path(f"M{CX - 320} {CY - 340}Q{CX} {CY - 400} {CX + 320} {CY - 330}")
    a.leaf(CX - 200, CY - 230, 280, 118, angle=145, fill=WHITE)
    a.leaf(CX + 210, CY - 220, 280, 118, angle=35, fill=WHITE)
    a.path(f"M{CX - 20} {CY - 372}L{CX} {CY - 200}")
    a.ellipse(CX, CY + 110, 285, 240, fill=WHITE)
    a.path(f"M{CX} {CY - 130}Q{CX + 26} {CY - 190} {CX} {CY - 208}"
           f"Q{CX - 26} {CY - 190} {CX} {CY - 130}Z", fill=WHITE)
    a.path(f"M{CX} {CY + 350}Q{CX + 26} {CY + 404} {CX} {CY + 424}"
           f"Q{CX - 26} {CY + 404} {CX} {CY + 350}Z", fill=WHITE)


@page("PAGE-072", FINE)
def bench(a):                           # a plain garden bench, side on
    a.rect(CX - 330, CY + 60, 660, 62, r=14, fill=WHITE)
    for i in range(3):
        a.rect(CX - 300, CY - 230 + i * 92, 600, 58, r=14, fill=WHITE)
    a.rect(CX - 320, CY - 300, 54, 400, r=16, fill=WHITE)
    a.rect(CX + 266, CY - 300, 54, 400, r=16, fill=WHITE)
    a.rect(CX - 300, CY + 122, 52, 250, r=14, fill=WHITE)
    a.rect(CX + 248, CY + 122, 52, 250, r=14, fill=WHITE)


@page("PAGE-074", FINE)
def dragonfly(a):                       # a dragonfly with open wings
    for side in (-1, 1):
        a.ellipse(CX + side * 155, CY - 190, 172, 58, fill=WHITE, rot=side * -12)
        a.ellipse(CX + side * 135, CY - 40, 150, 52, fill=WHITE, rot=side * 12)
    a.ellipse(CX, CY + 120, 48, 330, fill=WHITE)
    for i in range(4):
        y = CY + 20 + i * 88
        a.line(CX - 46, y, CX + 46, y)
    a.circle(CX, CY - 250, 88, fill=WHITE)
    a.circle(CX - 44, CY - 290, 30)
    a.circle(CX + 44, CY - 290, 30)


@page("PAGE-076", FINE)
def poppy(a):                           # one poppy head on a bent stem
    a.path(f"M{CX + 120} {CY + 480}C{CX + 150} {CY + 200} {CX - 60} {CY + 160} "
           f"{CX - 60} {CY - 60}")
    a.petals(CX - 60, CY - 170, 6, 90, 290, 250, math.pi / 6 * 0.98, swell=1.3)
    a.circle(CX - 60, CY - 170, 92, fill=WHITE)
    for ang in (0, 60, 120, 180, 240, 300):
        r = math.radians(ang)
        a.line(CX - 60, CY - 170, CX - 60 + math.cos(r) * 84, CY - 170 + math.sin(r) * 84)
    a.leaf(CX + 240, CY + 300, 260, 110, angle=-30, fill=WHITE)


@page("PAGE-078", FINE)
def pot_stack(a):                       # a stack of three plant pots
    # Offset sideways as they climb, so the stack is not a single centred column.
    for dx, w, y in ((-70, 320, CY + 380), (0, 275, CY + 175), (75, 230, CY - 30)):
        a.pot(CX + dx, y - 205, y, w, w * 0.72, rim=42)


@page("PAGE-080", FINE)
def tree(a):                            # a simple tree with a round crown
    a.path(f"M{CX - 70} {CY + 430}L{CX - 46} {CY + 60}L{CX + 46} {CY + 60}"
           f"L{CX + 70} {CY + 430}Z", fill=WHITE)
    a.path(f"M{CX - 40} {CY + 200}L{CX - 160} {CY + 90}")
    a.path(f"M{CX + 40} {CY + 240}L{CX + 170} {CY + 130}")
    a.scalloped(CX, CY - 130, 300, 11, depth=0.18, fill=WHITE)
    a.ellipse(CX, CY + 460, 250, 52, fill=WHITE)


@page("PAGE-082", FINE)
def seed_head(a):                       # one large seed head, few spokes
    hy = CY - 150                       # head high on the page, long stem below
    a.rect(CX - 14, hy + 90, 28, 560, r=8, fill=WHITE)
    a.leaf(CX - 130, hy + 470, 250, 96, angle=160, fill=WHITE)
    for i in range(12):
        t = 2 * math.pi * i / 12
        a.line(CX, hy, CX + math.cos(t) * 300, hy + math.sin(t) * 300)
    for i in range(12):
        t = 2 * math.pi * i / 12
        a.circle(CX + math.cos(t) * 300, hy + math.sin(t) * 300, 56, fill=WHITE)
    a.circle(CX, hy, 70, fill=WHITE)


def main(force=()):
    """Write any page that has no source yet, plus any page named in ``force``.

    Naming the pages to redo is deliberate, and the same rule the generator
    itself follows: a page that has been rendered and looked at is finished
    until somebody says otherwise.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    written, kept = [], []
    for page_id, (fn, stroke) in sorted(PAGES.items()):
        target = OUT / f"{page_id}.svg"
        if target.exists() and page_id not in force:
            # PAGE-004 has already been rendered, inspected and looked at by a
            # person. Nothing here overwrites a page that has been reviewed.
            kept.append(page_id)
            continue
        art = Art(stroke)
        fn(art)
        target.write_text(art.svg(), encoding="utf-8")
        written.append(page_id)
    print(f"wrote {len(written)}, kept {len(kept)}: {', '.join(kept) or 'none'}")


if __name__ == "__main__":
    import sys

    main(force=set(sys.argv[1:]))

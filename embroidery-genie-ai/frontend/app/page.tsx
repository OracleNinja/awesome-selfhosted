import {
  ArrowRight,
  Boxes,
  DollarSign,
  Layers,
  Mic,
  Package,
  ScanEye,
  Shirt,
  Sparkles,
  Wand2,
} from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/feedback";

const MODULES = [
  {
    icon: ScanEye,
    title: "AI design analyzer",
    body: "Measures colour count, minimum feature width and structure, then scores how well the "
      + "artwork will actually embroider — before you spend an hour digitizing it.",
  },
  {
    icon: Layers,
    title: "Vectorization engine",
    body: "Background removal, perceptual colour reduction and contour tracing turn a PNG, JPG, "
      + "HEIC or BMP into clean editable vector layers.",
  },
  {
    icon: Wand2,
    title: "Digitizing engine",
    body: "Satin, tatami fill and running stitch with automatic underlay, pull compensation, "
      + "stitch direction, trims and sew order — driven by the fabric you are sewing on.",
  },
  {
    icon: Sparkles,
    title: "3D stitch simulator",
    body: "Watch the design sew stitch by stitch on real fabric colours, with zoom, rotate, tilt "
      + "and per-colour layer control.",
  },
  {
    icon: Shirt,
    title: "Customer mockups",
    body: "Drop the stitched design onto shirts, hoodies, caps, beanies, jackets and totes at true "
      + "physical scale for approval.",
  },
  {
    icon: DollarSign,
    title: "Pricing assistant",
    body: "Machine time, thread, labour, blanks, waste and overhead build the real unit cost, then "
      + "margin produces wholesale and retail prices with quantity breaks.",
  },
  {
    icon: Boxes,
    title: "Production management",
    body: "Quote, approve, digitize, sew, complete, deliver — a validated state machine with an "
      + "audit trail, work orders and invoicing.",
  },
  {
    icon: Package,
    title: "Inventory",
    body: "Track blanks and costs. Stock is consumed when an order starts sewing, not when someone "
      + "remembers to update a spreadsheet.",
  },
  {
    icon: Mic,
    title: "Voice commands",
    body: '"Prepare a 50 shirt order." Speech recognition runs in the browser; the parser is '
      + "rule-based so it never guesses at an action that changes your data.",
  },
];

const FORMATS = ["DST", "PES", "JEF", "EXP", "VP3", "XXX", "U01", "PEC"];

export default function LandingPage() {
  return (
    <div className="app-backdrop min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between p-6">
        <span className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-primary to-accent">
            <Sparkles className="size-5 text-white" />
          </span>
          <span className="font-semibold">Embroidery Genie AI</span>
        </span>
        <nav className="flex items-center gap-2">
          <Link href="/login">
            <Button variant="ghost" size="sm">
              Sign in
            </Button>
          </Link>
          <Link href="/dashboard">
            <Button size="sm">
              Open app
              <ArrowRight />
            </Button>
          </Link>
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-6 pb-24">
        <section className="py-16 text-center sm:py-24">
          <Badge variant="outline" className="mb-5">
            Idea → embroidery file → production order
          </Badge>
          <h1 className="mx-auto max-w-3xl text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
            Artwork in. Machine-ready{" "}
            <span className="bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
              stitch files
            </span>{" "}
            out.
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-balance text-lg text-muted-foreground">
            Upload a logo, a sketch or a photo. Embroidery Genie analyses it, traces it, digitizes
            it with real satin and fill stitching, and hands you the DST, the thread chart and the
            run sheet your operator needs.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link href="/digitizer">
              <Button size="lg">
                <Wand2 />
                Create a design
              </Button>
            </Link>
            <Link href="/dashboard">
              <Button size="lg" variant="outline">
                Explore the workspace
              </Button>
            </Link>
          </div>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-2">
            <span className="text-xs uppercase tracking-wide text-muted-foreground">Exports</span>
            {FORMATS.map((format) => (
              <Badge key={format} variant="secondary" className="font-mono">
                .{format.toLowerCase()}
              </Badge>
            ))}
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {MODULES.map((module) => (
            <Card key={module.title} className="h-full">
              <CardContent className="p-5">
                <span className="mb-3 grid size-10 place-items-center rounded-lg bg-secondary/70">
                  <module.icon className="size-5 text-primary" />
                </span>
                <h2 className="text-sm font-semibold">{module.title}</h2>
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{module.body}</p>
              </CardContent>
            </Card>
          ))}
        </section>

        <section className="mt-16 rounded-2xl border border-border/70 bg-card/60 p-8 backdrop-blur">
          <h2 className="text-xl font-semibold">Built on real digitizing craft</h2>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
            The engine encodes conventional commercial practice rather than guesses: satin under
            about 10 mm and fill above, underlay laid perpendicular to the top stitching, pull
            compensation scaled to how much the fabric moves, tatami rows staggered so no split line
            appears, and tie-ins and trims placed so the file does not birdnest on the machine.
            Every fabric profile changes density, underlay, compensation, needle and head speed.
          </p>
          <div className="mt-6 grid gap-4 sm:grid-cols-3">
            <Stat value="8" label="Fabric profiles" />
            <Stat value="4" label="Underlay types" />
            <Stat value="0.1 mm" label="Internal resolution" />
          </div>
        </section>
      </main>

      <footer className="border-t border-border/70 py-8 text-center text-sm text-muted-foreground">
        Embroidery Genie AI — AI embroidery digitizing and production management.
      </footer>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-xl border border-border/60 p-4">
      <p className="text-2xl font-semibold tracking-tight">{value}</p>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
    </div>
  );
}

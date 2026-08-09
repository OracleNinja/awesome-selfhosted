# OpenCloak

An open-source, self-hostable alternative to [Cloaked](https://www.cloaked.com/): automated data-broker removal, ongoing monitoring and re-removal, email/phone aliasing, and breach alerting.

**This directory currently contains a design, not an implementation.**

| File | What it is |
|---|---|
| [`DESIGN.md`](DESIGN.md) | The full system design — architecture, stack, schema, removal engine, security model, legal posture, roadmap |
| [`catalog/schema/broker.schema.json`](catalog/schema/broker.schema.json) | JSON Schema for a broker catalog entry; encodes the safety rules as validation |
| [`catalog/brokers/EXAMPLES.yaml`](catalog/brokers/EXAMPLES.yaml) | Three worked entries: the common case, the hard case, and the commonly-misclassified case |

## The three ideas the design rests on

**1. Statute is the API; browser automation is the fallback.**
A CCPA deletion email cannot be CAPTCHA'd, starts a regulator-enforceable 45-day clock, and costs nothing. Since **2026-08-01**, California's DROP goes further — one verified request reaches all ~600 CPPA-registered brokers, who must check it every 45 days and delete within 90. Most tools in this space are scraper farms fighting an unwinnable bot-detection arms race for a weaker result than the law already guarantees. OpenCloak ranks five channels by cost and legal durability and spends its browser budget only where it must.

**2. Aliases are infrastructure, not a side feature.**
Every opt-out goes out from a per-broker alias, which serves as the request's correlation token (confirmation links route themselves), its containment boundary (a leaked opt-out list burns one alias), and a detection signal (mail from a *different* company proves resale).

**3. You cannot fill a form with data you cannot read.**
Unattended automation necessarily holds your plaintext identity at submission time. "Zero-knowledge" claims that ignore this are marketing. The design names the tradeoff and makes it selectable: **local**, **attended**, or **unattended** mode, with a published matrix of what the operator can see in each.

## Licensing intent

- **Engine / server: AGPL-3.0** — the removal engine is the valuable part, in a market full of closed $150–250/yr competitors.
- **Broker catalog: MIT** — catalog quality is a function of contributor volume, so commercial adoption is a feature. The goal is to become the industry's shared broker registry.

## Status and next step

Design complete; nothing built. Per [`DESIGN.md` §8](DESIGN.md#8-implementation-roadmap), the first milestone is **M0 — Foundations** (compose stack, schema, vault crypto), and the MVP at ~13 weeks ships the email channel plus the guided DROP flow — deliberately no browser automation, because that alone removes a user from most of the long tail.

The strongest reuse candidates are the CPPA registry CSV (authoritative ~600-broker seed) and [`digisamroc/eraser`](https://github.com/digisamroc/eraser) (MIT, 750+ broker YAML, email-first — the same architectural bet). See [`DESIGN.md` §10](DESIGN.md#10-what-to-reuse-instead-of-building).

---

*Legal details summarized from public sources as of 2026-08-09; not legal advice.*

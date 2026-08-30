# Books

One directory per book:

```
books/<book-id>/
├── research.json      the research brief
├── manifest.json      the auditable record — spec, provenance, gates, approval
├── assets/            generated artwork, every attempt   (gitignored)
├── build/             interior.pdf, cover.pdf, publication.zip (gitignored)
├── reports/           stored gate results, one per stage
└── review.md          the human review package
```

Manifests, gate reports and review packages are the auditable record and belong
in version control. Generated artwork and built PDFs do not — they are
reproducible from the manifest, and a deterministic build means the same
manifest always yields the same bytes.

Scaffold one with `/kdp:new-book <book-id> "<title>"`.

`tests/fixtures.py` builds a complete worked example ("Quiet Gardens", 50 pages,
24 art pages) that `tests/test_end_to_end.py` drives from research through to a
publication package, offline and free.

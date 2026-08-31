"""The free provider: vector source in, print-resolution raster out.

No network, no key, no cost — and no Chromium either. The renderer is a stand-in
script that honours the same two flags the real one is driven by
(``--window-size`` and ``--screenshot``), so these tests run anywhere, including
the stdlib-only CI job which has no browser.

What is being proved is mostly *refusal*: a page with no source, a renderer that
returns the wrong size, a renderer that returns nothing. A provider whose whole
justification is "it always comes back at the size the print geometry asked for"
has to fail loudly on the day it does not.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from kdp.generation import build_request, generate_book
from kdp.providers import METERED, get_provider
from kdp.providers.base import ProviderUnavailable
from kdp.providers.local_vector import (
    ART_DIR_ENV,
    CHROMIUM_ENV,
    LocalVectorProvider,
    find_chromium,
    wrap,
)
from fixtures import build_manifest

#: Every credential in sight. The free path must not care about any of them.
CREDENTIALS = (
    "KDP_GOOGLE_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_AUTH_ACCESS_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "KDP_HIGGSFIELD_API_KEY",
)

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 4">'
    '<rect width="3" height="4" fill="#fff"/>'
    '<circle cx="1.5" cy="2" r="1" fill="none" stroke="#000" stroke-width="0.1"/>'
    "</svg>"
)


def _renderer(tmp_path, *, size=None, empty=False) -> str:
    """A stand-in for Chromium, driven by the same flags.

    ``size`` forces an output size other than the one requested, which is how
    the silent-downgrade path gets tested without a real browser.
    """
    script = tmp_path / "fake-chromium"
    forced = f"({size[0]}, {size[1]})" if size else "None"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.path.insert(0, {str(tmp_path.parent)!r})\n"
        "sys.path.insert(0, " + repr(str(_repo_root())) + ")\n"
        "from kdp.providers.mock import _png\n"
        "w = h = 0\nout = None\n"
        "for a in sys.argv[1:]:\n"
        "    if a.startswith('--window-size='):\n"
        "        w, h = (int(v) for v in a.split('=', 1)[1].split(','))\n"
        "    elif a.startswith('--screenshot='):\n"
        "        out = a.split('=', 1)[1]\n"
        f"forced = {forced}\n"
        "if forced: w, h = forced\n"
        f"if not {empty!r} and out:\n"
        "    open(out, 'wb').write(_png(w, h, b'vector'))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


@pytest.fixture
def book():
    return build_manifest()


@pytest.fixture
def art(tmp_path, book):
    """A vector source for every art page."""
    directory = tmp_path / "art"
    directory.mkdir()
    for page in book.spec.art_pages:
        (directory / f"{page.page_id}.svg").write_text(SVG)
    return directory


# --- it costs nothing, and needs nothing ------------------------------------


def test_it_is_not_metered_so_it_never_asks_to_confirm_a_spend():
    assert "local-vector" not in METERED


def test_it_is_registered_and_reachable_by_name():
    assert isinstance(get_provider("local-vector"), LocalVectorProvider)


def test_it_needs_no_credential_of_any_kind(monkeypatch, art, tmp_path):
    """The point of the free path: nothing in the environment authorises it."""
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)

    provider = LocalVectorProvider(art, chromium=_renderer(tmp_path))
    assert provider.available() is True

    result = provider.generate(build_request(_prompt(build_manifest())))
    assert result.ok


def test_a_credential_in_the_environment_changes_nothing(monkeypatch, art, tmp_path):
    for name in CREDENTIALS:
        monkeypatch.setenv(name, "sk-CANARY-NOT-REAL")

    provider = LocalVectorProvider(art, chromium=_renderer(tmp_path))
    result = provider.generate(build_request(_prompt(build_manifest())))

    assert result.ok
    assert "CANARY" not in json.dumps(result.meta)
    assert "CANARY" not in repr(result)


def test_the_result_records_that_it_cost_nothing(art, tmp_path):
    result = LocalVectorProvider(art, chromium=_renderer(tmp_path)).generate(
        build_request(_prompt(build_manifest()))
    )
    assert result.meta["cost"] == "none"


def _prompt(manifest, index: int = 0):
    return manifest.prompt_plan.prompts[index]


# --- availability -----------------------------------------------------------


def test_without_an_art_directory_it_says_which_variable_to_set(monkeypatch, tmp_path):
    monkeypatch.delenv(ART_DIR_ENV, raising=False)
    provider = LocalVectorProvider(chromium=_renderer(tmp_path))

    assert provider.available() is False
    assert ART_DIR_ENV in provider.unavailable_reason()


def test_the_art_directory_comes_from_the_environment(monkeypatch, art, tmp_path):
    monkeypatch.setenv(ART_DIR_ENV, str(art))
    monkeypatch.setenv(CHROMIUM_ENV, _renderer(tmp_path))

    assert get_provider("local-vector").available() is True


def test_a_missing_art_directory_is_reported_not_created(monkeypatch, tmp_path):
    monkeypatch.setenv(ART_DIR_ENV, str(tmp_path / "nowhere"))
    provider = LocalVectorProvider(chromium=_renderer(tmp_path))

    assert provider.available() is False
    assert "does not exist" in provider.unavailable_reason()
    assert not (tmp_path / "nowhere").exists()


def test_generating_while_unavailable_raises(monkeypatch, tmp_path):
    monkeypatch.delenv(ART_DIR_ENV, raising=False)
    with pytest.raises(ProviderUnavailable):
        LocalVectorProvider(chromium=_renderer(tmp_path)).generate(
            build_request(_prompt(build_manifest()))
        )


def test_an_override_that_points_nowhere_is_not_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv(CHROMIUM_ENV, str(tmp_path / "no-such-browser"))
    assert find_chromium() is None


# --- resolution is never quietly downgraded ---------------------------------


def test_it_renders_at_exactly_the_requested_size(art, tmp_path):
    request = build_request(_prompt(build_manifest()))
    result = LocalVectorProvider(art, chromium=_renderer(tmp_path)).generate(request)

    assert result.ok
    assert (result.width, result.height) == (request.width, request.height)
    assert result.file_format == "png"


def test_a_short_render_fails_instead_of_being_resampled(art, tmp_path):
    """The 768x768 failure mode, caught locally and for free."""
    renderer = _renderer(tmp_path, size=(768, 768))
    request = build_request(_prompt(build_manifest()))
    result = LocalVectorProvider(art, chromium=renderer).generate(request)

    assert result.ok is False
    assert "768x768" in result.reason
    assert "Refusing to resample" in result.reason
    assert result.retryable is False


def test_a_renderer_that_produces_nothing_is_a_failure(art, tmp_path):
    result = LocalVectorProvider(
        art, chromium=_renderer(tmp_path, empty=True)
    ).generate(build_request(_prompt(build_manifest())))

    assert result.ok is False
    assert "no output" in result.reason


# --- settled failures are not retried ---------------------------------------


def test_a_page_with_no_vector_source_is_not_retried(tmp_path, book):
    empty = tmp_path / "art"
    empty.mkdir()
    result = LocalVectorProvider(empty, chromium=_renderer(tmp_path)).generate(
        build_request(_prompt(book))
    )

    assert result.ok is False
    assert result.retryable is False
    assert "no vector source" in result.reason


def test_a_source_that_is_not_an_svg_is_not_retried(tmp_path, book):
    directory = tmp_path / "art"
    directory.mkdir()
    page = _prompt(book).page_id
    (directory / f"{page}.svg").write_text("this is not a drawing")

    result = LocalVectorProvider(directory, chromium=_renderer(tmp_path)).generate(
        build_request(_prompt(book))
    )
    assert result.ok is False
    assert result.retryable is False


def test_the_runner_stops_after_one_attempt_on_a_settled_failure(tmp_path, book):
    """Three attempts a page across forty pages is a hundred and twenty."""
    empty = tmp_path / "art"
    empty.mkdir()
    provider = LocalVectorProvider(empty, chromium=_renderer(tmp_path))

    outcome = generate_book(
        book, provider, tmp_path / "assets", now="2026-01-01T00:00:00+00:00"
    )

    for page in book.spec.art_pages:
        attempts = [r for r in outcome.manifest.assets if r.page == page.page_id]
        assert len(attempts) == 1, f"{page.page_id} was retried"


def test_a_transient_failure_still_uses_the_whole_budget(tmp_path, book):
    """Only settled failures stop early; a flaky renderer gets its retries."""
    from kdp.providers.base import GenerationResult

    class Flaky:
        name, model = "flaky", "m"

        def available(self):
            return True

        def generate(self, request):
            return GenerationResult(
                ok=False, page_id=request.page_id, provider=self.name,
                reason="the renderer did not finish in time", retryable=True,
            )

    outcome = generate_book(
        book, Flaky(), tmp_path / "assets", now="2026-01-01T00:00:00+00:00"
    )
    page = book.spec.art_pages[0].page_id
    assert len([r for r in outcome.manifest.assets if r.page == page]) == 3


# --- the output goes through the ordinary machinery -------------------------


def test_generated_pages_enter_the_normal_attempt_ledger(art, tmp_path, book):
    provider = LocalVectorProvider(art, chromium=_renderer(tmp_path))
    outcome = generate_book(
        book, provider, tmp_path / "assets", now="2026-01-01T00:00:00+00:00"
    )

    assert outcome.ok, outcome.failed
    for page in book.spec.art_pages:
        records = [r for r in outcome.manifest.assets if r.page == page.page_id]
        assert len(records) == 1
        record = records[0]
        assert record.provider == "local-vector"
        assert record.ai_role.value == "generated"   # disclosable, and disclosed
        assert record.status.value == "pending"      # QA decides, not the maker
        assert record.width and record.height


def test_the_vector_source_is_identified_in_the_result(art, tmp_path, book):
    result = LocalVectorProvider(art, chromium=_renderer(tmp_path)).generate(
        build_request(_prompt(book))
    )
    assert result.meta["source"].endswith(".svg")
    assert len(result.meta["source_sha256"]) == 64


# --- the wrapper ------------------------------------------------------------


def test_the_wrapper_forces_the_drawing_to_fill_the_viewport():
    html = wrap(SVG)
    assert "width:100vw" in html and "height:100vh" in html
    assert "margin:0" in html
    assert SVG in html


# --- the one-page smoke test, on the free path ------------------------------


def _counting_renderer(tmp_path):
    """A renderer that records every invocation, so 'exactly one' is provable."""
    log = tmp_path / "calls.log"
    script = tmp_path / "counting-chromium"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.path.insert(0, " + repr(str(_repo_root())) + ")\n"
        "from kdp.providers.mock import _png\n"
        "w = h = 0; out = None\n"
        "for a in sys.argv[1:]:\n"
        "    if a.startswith('--window-size='):\n"
        "        w, h = (int(v) for v in a.split('=', 1)[1].split(','))\n"
        "    elif a.startswith('--screenshot='):\n"
        "        out = a.split('=', 1)[1]\n"
        f"open({str(log)!r}, 'a').write('call\\n')\n"
        "open(out, 'wb').write(_png(w, h, b'vector'))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


def test_the_smoke_test_makes_exactly_one_render(tmp_path, art, book):
    from kdp.smoke import run_smoke_test

    renderer, log = _counting_renderer(tmp_path)
    page = book.spec.art_pages[0].page_id

    run_smoke_test(
        book, page, LocalVectorProvider(art, chromium=renderer), tmp_path / "smoke"
    )
    assert log.read_text().count("call") == 1


def test_the_smoke_test_leaves_the_manifest_byte_for_byte(tmp_path, art, book):
    from kdp.smoke import run_smoke_test

    written = tmp_path / "manifest.json"
    book.write(written)
    before = written.read_bytes()

    renderer, _ = _counting_renderer(tmp_path)
    run_smoke_test(
        book,
        book.spec.art_pages[0].page_id,
        LocalVectorProvider(art, chromium=renderer),
        tmp_path / "smoke",
    )

    assert written.read_bytes() == before
    assert book.assets == ()
    assert book.stage == "research"


def test_the_smoke_test_records_provenance_and_measures_the_page(tmp_path, art, book):
    from kdp.smoke import run_smoke_test

    renderer, _ = _counting_renderer(tmp_path)
    page = book.spec.art_pages[0].page_id
    report = run_smoke_test(
        book, page, LocalVectorProvider(art, chromium=renderer), tmp_path / "smoke"
    )

    assert report.call_status == "ok"
    assert report.resolution_ok is True
    assert report.effective_dpi >= report.required_dpi
    assert report.mime_type == "image/png"
    assert report.provider == "local-vector"
    assert report.would_pass_asset_qa is True

    record = json.loads(open(report.provenance_path).read())
    assert record["ai_role"] == "generated"
    assert record["status"] == "pending"
    assert record["width"], record["height"]


def test_a_short_page_is_reported_as_failing_not_passing(tmp_path, art, book):
    """Insufficient resolution must never be mislabelled as a pass."""
    from kdp.smoke import run_smoke_test

    report = run_smoke_test(
        book,
        book.spec.art_pages[0].page_id,
        LocalVectorProvider(art, chromium=_renderer(tmp_path, size=(768, 768))),
        tmp_path / "smoke",
    )

    assert report.call_status == "failed"
    assert report.would_pass_asset_qa is False

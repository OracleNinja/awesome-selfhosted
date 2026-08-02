"""Module 2 — AI Design Analyzer.

Two layers, deliberately separated:

**Deterministic layer** (always runs). Computer vision measurements — colour
count, edge density, minimum feature width, small-part count — scored against
the physical limits of thread on fabric. This is what produces the
compatibility score, and it works with no API key, no network, and no cost.

**Semantic layer** (optional). A vision model describes *what* the artwork is:
does it contain lettering, is it a logo or a photograph, what is the subject.
Models are good at this and bad at metrology, so nothing the model says is
allowed to move the score — it only enriches the description and the
recommendations.

That split matters: an embroidery quote that changes because a language model
felt different today is not a product.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field

from app.core.config import settings
from app.embroidery.fabrics import get_fabric, list_fabrics
from app.embroidery.raster import (
    dominant_colors,
    estimate_line_widths,
    image_complexity,
)
from app.embroidery.threads import suggest_thread

log = logging.getLogger(__name__)

# Physical limits. A satin column narrower than ~1.2 mm cannot hold thread on
# most fabrics; below 0.8 mm it disappears entirely.
MIN_FEATURE_MM = 1.2
CRITICAL_FEATURE_MM = 0.8


@dataclass
class AnalysisReport:
    compatibility_score: int = 0
    verdict: str = ""
    detected: dict = field(default_factory=dict)
    colors: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    recommended_placements: list[dict] = field(default_factory=list)
    suggested_fabric: str = "cotton_shirt"
    suggested_colors: int = 4
    ai: dict | None = None

    def to_dict(self) -> dict:
        return {
            "compatibility_score": self.compatibility_score,
            "verdict": self.verdict,
            "detected": self.detected,
            "colors": self.colors,
            "recommendations": self.recommendations,
            "issues": self.issues,
            "metrics": self.metrics,
            "recommended_placements": self.recommended_placements,
            "suggested_fabric": self.suggested_fabric,
            "suggested_colors": self.suggested_colors,
            "ai": self.ai,
        }


# --------------------------------------------------------------- placements
PLACEMENTS = [
    {"key": "left_chest", "name": "Left chest", "max_width_mm": 100, "max_height_mm": 100,
     "fabric": "cotton_shirt", "typical_mm": 89},
    {"key": "full_front", "name": "Full front", "max_width_mm": 280, "max_height_mm": 300,
     "fabric": "cotton_shirt", "typical_mm": 250},
    {"key": "cap_front", "name": "Cap front", "max_width_mm": 130, "max_height_mm": 55,
     "fabric": "hat", "typical_mm": 115},
    {"key": "cap_side", "name": "Cap side", "max_width_mm": 60, "max_height_mm": 40,
     "fabric": "hat", "typical_mm": 50},
    {"key": "sleeve", "name": "Sleeve", "max_width_mm": 90, "max_height_mm": 60,
     "fabric": "cotton_shirt", "typical_mm": 75},
    {"key": "back_yoke", "name": "Back yoke", "max_width_mm": 300, "max_height_mm": 120,
     "fabric": "hoodie", "typical_mm": 260},
    {"key": "beanie_cuff", "name": "Beanie cuff", "max_width_mm": 100, "max_height_mm": 45,
     "fabric": "beanie", "typical_mm": 80},
    {"key": "patch", "name": "Leather patch", "max_width_mm": 80, "max_height_mm": 55,
     "fabric": "leather_patch", "typical_mm": 70},
]


def _placement_fit(metrics: dict, colors: int, has_fine_detail: bool) -> list[dict]:
    """Rank placements by how well the artwork survives at that size."""
    aspect = metrics.get("aspect_ratio", 1.0) or 1.0
    ranked: list[dict] = []
    for placement in PLACEMENTS:
        score = 100
        reasons: list[str] = []

        target_w = placement["typical_mm"]
        target_h = target_w / max(aspect, 0.01)
        if target_h > placement["max_height_mm"]:
            target_h = placement["max_height_mm"]
            target_w = target_h * aspect

        fabric = get_fabric(placement["fabric"])
        # Scale the measured minimum feature to this finished size.
        min_feature_mm = metrics.get("min_feature_at_100mm_mm", 2.0) * (target_w / 100.0)
        if min_feature_mm < CRITICAL_FEATURE_MM:
            score -= 45
            reasons.append(
                f"Finest detail lands at {min_feature_mm:.1f} mm — below the "
                f"{CRITICAL_FEATURE_MM} mm that thread can hold."
            )
        elif min_feature_mm < MIN_FEATURE_MM:
            score -= 20
            reasons.append(f"Fine detail at {min_feature_mm:.1f} mm will soften.")

        if has_fine_detail and placement["typical_mm"] < 90:
            score -= 12
            reasons.append("Small placement plus fine detail — simplify the artwork.")

        if fabric.category in ("cap", "knit") and colors > 8:
            score -= 10
            reasons.append(f"{colors} colours is a lot of changes for {fabric.name}.")

        if placement["key"] == "cap_front" and target_h > 55:
            score -= 15
            reasons.append("Taller than a cap front allows; it will need cropping.")

        ranked.append({
            "key": placement["key"],
            "name": placement["name"],
            "fabric": placement["fabric"],
            "score": max(0, min(100, score)),
            "width_mm": round(target_w, 1),
            "height_mm": round(target_h, 1),
            "notes": reasons,
        })

    ranked.sort(key=lambda p: -p["score"])
    return ranked


# ---------------------------------------------------------- deterministic pass
def analyze_metrics(image_bytes: bytes, max_colors: int = 8) -> tuple[dict, list[dict]]:
    complexity = image_complexity(image_bytes)
    widths = estimate_line_widths(image_bytes)
    palette = dominant_colors(image_bytes, max_colors)

    # Normalise the measured stroke width to a 100 mm wide finished design so
    # placements can be compared on one axis.
    width_px = max(1, complexity["width_px"])
    px_per_100mm = width_px / 100.0
    metrics = {
        **complexity,
        **{f"stroke_{k}": v for k, v in widths.items()},
        "min_feature_at_100mm_mm": round(widths["p05_px"] / max(px_per_100mm, 1e-6), 2),
        "median_feature_at_100mm_mm": round(widths["median_px"] / max(px_per_100mm, 1e-6), 2),
    }
    return metrics, palette


def score_design(metrics: dict, palette: list[dict]) -> tuple[int, list[dict], list[str]]:
    """Deterministic compatibility score with an explicit audit trail."""
    score = 100.0
    issues: list[dict] = []
    recommendations: list[str] = []

    color_count = len(palette)
    if color_count > 12:
        score -= 22
        issues.append({
            "level": "warning", "code": "too_many_colors",
            "message": f"{color_count} distinct colours. Most machines carry 6–15 needles and "
                       "every change costs run time. Reduce to 6 or fewer.",
        })
        recommendations.append("Reduce the palette to 6 colours or fewer before digitizing.")
    elif color_count > 8:
        score -= 10
        recommendations.append("Consider merging similar colours to cut colour changes.")

    min_feature = metrics.get("min_feature_at_100mm_mm", 2.0)
    if min_feature < CRITICAL_FEATURE_MM:
        score -= 30
        issues.append({
            "level": "error", "code": "detail_too_fine",
            "message": f"The finest strokes measure {min_feature:.1f} mm at a 100 mm finished "
                       "size. Thread cannot form a column that narrow — these details will "
                       "disappear or turn into a puddle of stitches.",
        })
        recommendations.append(
            "Thicken hairlines, or plan to run the design larger than 100 mm wide."
        )
    elif min_feature < MIN_FEATURE_MM:
        score -= 12
        issues.append({
            "level": "warning", "code": "fine_detail",
            "message": f"Finest strokes are {min_feature:.1f} mm at 100 mm. They will sew, "
                       "but softly. Going larger will hold the detail better.",
        })

    gradients = metrics.get("distinct_colors", 0)
    if gradients > 4000:
        score -= 18
        issues.append({
            "level": "warning", "code": "photographic",
            "message": "The artwork looks photographic or heavily gradient-shaded. Thread is "
                       "opaque and flat — it cannot blend. This needs to be posterised into "
                       "solid colour areas first.",
        })
        recommendations.append("Posterise the image to flat colour areas before digitizing.")

    small_parts = metrics.get("small_part_count", 0)
    if small_parts > 25:
        score -= 12
        issues.append({
            "level": "warning", "code": "many_small_parts",
            "message": f"{small_parts} very small disconnected shapes. Each one costs a trim "
                       "and a tie-off; the run time will climb steeply.",
        })

    if metrics.get("edge_density", 0) > 0.24:
        score -= 8
        recommendations.append(
            "The artwork is very busy. Simplifying it will sew faster and look sharper."
        )

    megapixels = metrics.get("megapixels", 0)
    if megapixels < 0.12:
        score -= 14
        issues.append({
            "level": "warning", "code": "low_resolution",
            "message": f"Source is only {metrics.get('width_px')}×{metrics.get('height_px')} px. "
                       "Trace quality is limited by the pixels available — a vector or a larger "
                       "raster will give cleaner edges.",
        })
        recommendations.append("Upload a vector (SVG/PDF) or a larger image if you have one.")

    if not metrics.get("has_alpha") and metrics.get("distinct_colors", 0) < 4000:
        recommendations.append(
            "Background removal will run automatically; check the vector preview before export."
        )

    if not issues:
        recommendations.append("This artwork is well suited to embroidery — proceed to digitizing.")

    return (int(max(0, min(100, round(score)))), issues, recommendations)


def verdict_for(score: int) -> str:
    if score >= 90:
        return "Excellent — this will embroider cleanly with no rework."
    if score >= 75:
        return "Good — minor adjustments recommended before production."
    if score >= 55:
        return "Workable — expect some detail loss; review the warnings."
    if score >= 35:
        return "Difficult — the artwork needs simplification to embroider well."
    return "Not embroidery-ready — rework the artwork before digitizing."


# ------------------------------------------------------------- semantic pass
VISION_PROMPT = """You are assisting an embroidery digitizer. Look at this artwork and \
answer ONLY with a JSON object, no prose, using exactly these keys:

{
  "subject": "one short phrase describing what the artwork shows",
  "artwork_type": "logo" | "lettering" | "illustration" | "photograph" | "pattern" | "mixed",
  "contains_text": true/false,
  "text_content": "the exact text you can read, or empty string",
  "smallest_text_words": ["words that appear notably smaller than the rest"],
  "has_gradients": true/false,
  "has_fine_lines": true/false,
  "has_outline_stroke": true/false,
  "dominant_shapes": ["circle", "shield", ...],
  "style_notes": "one sentence on the visual style",
  "embroidery_concerns": ["specific things that will be hard to stitch"]
}

Judge only what you can see. Do not estimate sizes in millimetres - you cannot \
know the finished size."""


def _encode_image(image_bytes: bytes, content_type: str = "image/png") -> str:
    return f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"


def _parse_json_response(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text.strip("`")
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _analyze_with_openai(image_bytes: bytes, content_type: str) -> dict | None:
    try:
        from openai import OpenAI
    except ImportError:  # pragma: no cover
        return None
    try:
        client = OpenAI(api_key=settings.openai_api_key, timeout=settings.ai_timeout_seconds)
        response = client.chat.completions.create(
            model=settings.openai_vision_model,
            max_tokens=700,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": _encode_image(image_bytes, content_type)},
                        },
                    ],
                }
            ],
        )
        parsed = _parse_json_response(response.choices[0].message.content or "")
        if parsed is not None:
            parsed["_provider"] = "openai"
            parsed["_model"] = settings.openai_vision_model
        return parsed
    except Exception as exc:
        log.warning("OpenAI vision analysis failed: %s", exc)
        return None


def _analyze_with_anthropic(image_bytes: bytes, content_type: str) -> dict | None:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return None
    try:
        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.ai_timeout_seconds
        )
        response = client.messages.create(
            model=settings.anthropic_vision_model,
            max_tokens=700,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": content_type,
                                "data": base64.b64encode(image_bytes).decode(),
                            },
                        },
                        {"type": "text", "text": VISION_PROMPT},
                    ],
                }
            ],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        parsed = _parse_json_response(text)
        if parsed is not None:
            parsed["_provider"] = "anthropic"
            parsed["_model"] = settings.anthropic_vision_model
        return parsed
    except Exception as exc:
        log.warning("Claude vision analysis failed: %s", exc)
        return None


def semantic_analysis(image_bytes: bytes, content_type: str = "image/png") -> dict | None:
    """Ask a vision model what the artwork is. Returns None if unavailable.

    This is a belt-and-braces wrapper: the per-provider helpers already swallow
    their own errors, but the deterministic analysis must survive *any* failure
    in this layer — a bad key, a network partition, an SDK that changed shape
    under us — because the compatibility score is what the customer is quoted
    against.
    """
    try:
        return _semantic_analysis(image_bytes, content_type)
    except Exception as exc:  # noqa: BLE001 - deliberately total
        log.warning("Semantic analysis layer failed, continuing without it: %s", exc)
        return None


def _semantic_analysis(image_bytes: bytes, content_type: str) -> dict | None:
    if not settings.ai_enabled():
        return None
    provider = settings.ai_provider
    if provider == "openai" or (provider == "auto" and settings.openai_api_key):
        result = _analyze_with_openai(image_bytes, content_type)
        if result is not None:
            return result
    if provider in ("anthropic", "auto") and settings.anthropic_api_key:
        return _analyze_with_anthropic(image_bytes, content_type)
    if provider == "auto" and settings.openai_api_key:
        return _analyze_with_openai(image_bytes, content_type)
    return None


# -------------------------------------------------------------------- entry
def analyze_design(
    image_bytes: bytes,
    content_type: str = "image/png",
    use_ai: bool = True,
    max_colors: int = 8,
) -> AnalysisReport:
    """Full analyzer pass. Never raises for AI failures — the deterministic
    result stands on its own."""
    metrics, palette = analyze_metrics(image_bytes, max_colors)
    score, issues, recommendations = score_design(metrics, palette)

    for entry in palette:
        thread = suggest_thread(tuple(entry["rgb"]))
        entry["thread"] = {"name": thread.name, "code": thread.code, "hex": thread.hex}

    ai_result = semantic_analysis(image_bytes, content_type) if use_ai else None

    detected = {
        "colors": len(palette),
        "shapes": metrics.get("shape_count", 0),
        "small_parts": metrics.get("small_part_count", 0),
        "transparency": metrics.get("has_alpha", False),
        "resolution": f"{metrics.get('width_px')}×{metrics.get('height_px')} px",
        "gradients": metrics.get("distinct_colors", 0) > 4000,
        "text": None,
        "artwork_type": None,
        "subject": None,
    }

    if ai_result:
        detected["text"] = bool(ai_result.get("contains_text"))
        detected["text_content"] = ai_result.get("text_content", "")
        detected["artwork_type"] = ai_result.get("artwork_type")
        detected["subject"] = ai_result.get("subject")
        detected["shapes_named"] = ai_result.get("dominant_shapes", [])

        if ai_result.get("contains_text"):
            recommendations.append(
                "Lettering detected — check the minimum letter height against the "
                "fabric profile before you commit to a placement."
            )
            if ai_result.get("smallest_text_words"):
                words = ", ".join(str(w) for w in ai_result["smallest_text_words"][:4])
                issues.append({
                    "level": "warning", "code": "small_lettering",
                    "message": f"Small lettering detected ({words}). Letters under about "
                               "5 mm cap height close up on most fabrics.",
                })
        for concern in ai_result.get("embroidery_concerns", [])[:4]:
            recommendations.append(str(concern))

    has_fine_detail = metrics.get("min_feature_at_100mm_mm", 2.0) < MIN_FEATURE_MM
    placements = _placement_fit(metrics, len(palette), has_fine_detail)

    suggested_fabric = placements[0]["fabric"] if placements else "cotton_shirt"

    return AnalysisReport(
        compatibility_score=score,
        verdict=verdict_for(score),
        detected=detected,
        colors=palette,
        recommendations=list(dict.fromkeys(recommendations))[:8],
        issues=issues,
        metrics=metrics,
        recommended_placements=placements[:4],
        suggested_fabric=suggested_fabric,
        suggested_colors=min(len(palette), 6) or 3,
        ai=ai_result,
    )


def fabric_options() -> list[dict]:
    return list_fabrics()

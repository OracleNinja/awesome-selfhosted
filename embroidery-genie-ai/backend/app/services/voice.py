"""Module 11 — Voice command mode.

Speech-to-text happens in the browser (Web Speech API); this module turns the
resulting transcript into a structured intent the UI can execute.

Deliberately rule-based rather than model-based.  Voice commands here trigger
real actions — creating orders, starting production runs — and a parser that
is wrong 5% of the time is worse than no parser.  Every match is explicit, the
confidence is reported, and anything ambiguous comes back as ``unknown`` with
suggestions rather than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy two": 72, "hundred": 100,
    "a hundred": 100, "one hundred": 100, "two hundred": 200, "five hundred": 500,
}

GARMENTS = {
    "shirt": "cotton_shirt", "t-shirt": "cotton_shirt", "tee": "cotton_shirt",
    "tshirt": "cotton_shirt", "polo": "polyester_shirt", "performance": "polyester_shirt",
    "jersey": "polyester_shirt", "hoodie": "hoodie", "sweatshirt": "hoodie",
    "hat": "hat", "cap": "hat", "beanie": "beanie", "toque": "beanie",
    "patch": "leather_patch", "leather": "leather_patch",
    "tote": "canvas", "bag": "canvas", "canvas": "canvas", "towel": "towel",
}

PLACEMENTS = {
    "left chest": "left_chest", "chest": "left_chest", "front": "full_front",
    "full front": "full_front", "back": "back_yoke", "sleeve": "sleeve",
    "cap front": "cap_front", "cuff": "beanie_cuff",
}


@dataclass
class VoiceCommand:
    intent: str
    confidence: float
    entities: dict = field(default_factory=dict)
    transcript: str = ""
    action: dict | None = None
    reply: str = ""
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 2),
            "entities": self.entities,
            "transcript": self.transcript,
            "action": self.action,
            "reply": self.reply,
            "suggestions": self.suggestions,
        }


def _extract_quantity(text: str) -> int | None:
    digits = re.search(r"\b(\d{1,5})\b", text)
    if digits:
        return int(digits.group(1))
    for phrase in sorted(NUMBER_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return NUMBER_WORDS[phrase]
    return None


def _extract_garment(text: str) -> str | None:
    for word in sorted(GARMENTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}s?\b", text):
            return GARMENTS[word]
    return None


def _extract_placement(text: str) -> str | None:
    for phrase in sorted(PLACEMENTS, key=len, reverse=True):
        if phrase in text:
            return PLACEMENTS[phrase]
    return None


def _extract_size_mm(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(mm|millimet|cm|centimet|inch|in|\")", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit.startswith("cm") or unit.startswith("centimet"):
        return value * 10
    if unit.startswith("inch") or unit in ("in", '"'):
        return value * 25.4
    return value


def _extract_text_content(text: str, raw: str) -> str | None:
    """Pull the literal string out of "say ...", "that says ...", quoted text."""
    quoted = re.search(r"[\"'“‘]([^\"'”’]{1,60})[\"'”’]", raw)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(
        r"(?:that says|saying|says|with the (?:word|words|text)|spelling|reading)\s+(.+)$",
        text,
    )
    if match:
        return match.group(1).strip(" .,").title()
    return None


RULES: list[tuple[str, list[str]]] = [
    ("create_design", [
        r"\b(create|make|start|new)\b.*\b(design|logo|artwork|embroidery)\b",
        r"\bnew (design|project)\b",
    ]),
    ("create_text_design", [
        r"\b(create|make|add|new)\b.*\b(text|lettering|name|monogram)\b",
        r"\b(that says|saying|with the word)\b",
    ]),
    ("digitize", [
        r"\b(convert|digitize|digitise|stitch)\b",
        r"\bturn (this|it) into (an? )?(embroidery|stitch)",
        r"\bprepare (this|it) for (embroidery|stitching)",
    ]),
    ("create_order", [
        r"\b(prepare|create|start|set up|book)\b.*\b(order|job|run|batch)\b",
        r"\b\d+\s+(shirt|tee|hat|cap|hoodie|polo|beanie|bag|tote|patch)s?\b",
    ]),
    ("set_fabric", [
        r"\b(use|set|switch to|change to)\b.*\b(cotton|polyester|fleece|leather|canvas|knit)\b",
        r"\bfor (a )?(hat|cap|beanie|hoodie|shirt|polo|tote|patch)\b",
    ]),
    ("export", [
        r"\b(export|download|send|save)\b.*\b(dst|pes|jef|exp|vp3|file|files)\b",
        r"\bexport (this|it|the design)\b",
    ]),
    ("show_price", [
        r"\b(price|quote|cost|how much)\b",
        r"\bwhat (would|will) (this|it) cost\b",
    ]),
    ("show_mockup", [r"\b(mockup|mock up|preview on|show me (it )?on a)\b"]),
    ("set_size", [r"\b(make it|set (the )?(size|width)|resize|scale)\b"]),
    ("open_orders", [r"\b(open|show|go to)\b.*\borders?\b"]),
    ("open_customers", [r"\b(open|show|go to)\b.*\bcustomers?\b"]),
    ("open_dashboard", [r"\b(open|show|go to)\b.*\b(dashboard|home)\b"]),
    ("start_production", [
        r"\b(start|begin|run)\b.*\b(production|sewing|the run)\b",
        r"\bsend (it |this )?to the (machine|floor)\b",
    ]),
    ("help", [r"\b(help|what can you do|commands)\b"]),
]

EXAMPLES = [
    "Create a hat logo.",
    "Convert this image for embroidery.",
    "Prepare a 50 shirt order.",
    "Make it 90 millimetres wide.",
    "Create text that says Northside Athletics.",
    "Export DST and PES.",
    "How much would 24 hoodies cost?",
    "Show me a mockup on a black cap.",
]


def parse(transcript: str) -> VoiceCommand:
    """Turn a spoken phrase into a structured command."""
    raw = (transcript or "").strip()
    text = raw.lower()
    if not text:
        return VoiceCommand(
            intent="unknown", confidence=0.0, transcript=raw,
            reply="I did not catch that.", suggestions=EXAMPLES[:4],
        )

    matches: list[tuple[str, int]] = []
    for intent, patterns in RULES:
        hits = sum(1 for pattern in patterns if re.search(pattern, text))
        if hits:
            matches.append((intent, hits))

    entities: dict = {}
    quantity = _extract_quantity(text)
    garment = _extract_garment(text)
    placement = _extract_placement(text)
    size_mm = _extract_size_mm(text)
    if quantity is not None:
        entities["quantity"] = quantity
    if garment:
        entities["fabric"] = garment
    if placement:
        entities["placement"] = placement
    if size_mm:
        entities["width_mm"] = round(size_mm, 1)

    if not matches:
        return VoiceCommand(
            intent="unknown", confidence=0.0, entities=entities, transcript=raw,
            reply="I did not understand that command.", suggestions=EXAMPLES[:4],
        )

    # A quantity plus a garment is almost always an order, whatever else matched.
    if quantity and garment and quantity > 1:
        matches.append(("create_order", 3))

    # ...unless the sentence is explicitly a pricing question. "How much would
    # 24 hoodies cost?" carries both signals and must not open an order form.
    if re.search(r"\b(how much|what would .* cost|what will .* cost|quote me)\b", text):
        matches.append(("show_price", 5))

    text_content = _extract_text_content(text, raw)
    if text_content:
        entities["text"] = text_content
        matches.append(("create_text_design", 2))

    matches.sort(key=lambda m: -m[1])
    intent = matches[0][0]
    confidence = min(0.95, 0.55 + 0.15 * matches[0][1] + (0.1 if entities else 0.0))

    action, reply = _build_action(intent, entities)
    return VoiceCommand(
        intent=intent, confidence=confidence, entities=entities,
        transcript=raw, action=action, reply=reply,
    )


def _build_action(intent: str, entities: dict) -> tuple[dict | None, str]:
    fabric = entities.get("fabric")
    quantity = entities.get("quantity")

    if intent == "create_design":
        return (
            {"type": "navigate", "route": "/studio/new", "params": entities},
            f"Starting a new design{' for ' + fabric.replace('_', ' ') if fabric else ''}.",
        )
    if intent == "create_text_design":
        return (
            {"type": "navigate", "route": "/studio/text", "params": entities},
            f"Creating lettering{': ' + entities['text'] if entities.get('text') else ''}.",
        )
    if intent == "digitize":
        return ({"type": "run", "command": "digitize", "params": entities},
                "Digitizing the current design.")
    if intent == "create_order":
        return (
            {"type": "navigate", "route": "/orders/new", "params": entities},
            f"Starting an order for {quantity or 'some'} "
            f"{(fabric or 'item').replace('_', ' ')}s.",
        )
    if intent == "set_fabric":
        return ({"type": "set", "field": "fabric", "value": fabric},
                f"Switching to the {(fabric or 'default').replace('_', ' ')} profile.")
    if intent == "set_size":
        return ({"type": "set", "field": "width_mm", "value": entities.get("width_mm")},
                f"Setting the design width to {entities.get('width_mm', '?')} mm.")
    if intent == "export":
        return ({"type": "run", "command": "export", "params": entities},
                "Preparing the export package.")
    if intent == "show_price":
        return ({"type": "navigate", "route": "/pricing", "params": entities},
                "Opening the pricing assistant.")
    if intent == "show_mockup":
        return ({"type": "run", "command": "mockup", "params": entities},
                "Generating a mockup.")
    if intent == "start_production":
        return ({"type": "run", "command": "start_production", "params": entities},
                "Moving the order into production.")
    if intent.startswith("open_"):
        route = "/" + intent.removeprefix("open_")
        return ({"type": "navigate", "route": route}, f"Opening {intent.removeprefix('open_')}.")
    if intent == "help":
        return (None, "Try one of these commands.")
    return (None, "")


def examples() -> list[str]:
    return list(EXAMPLES)

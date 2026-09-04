# accounts/history_input_schema.py
"""
PHASE 8 STEP 2/3 — server-side whitelist for the structured per-generator
inputs saved onto ChatHistory.inputs (see accounts.models.ChatHistory).

Same defensive pattern as accounts/ai_request_config.py: the frontend
(templates/main.html) sends whatever it wants, but only the keys listed
here are ever written to the database, and every value is coerced to a
plain, length-capped string first. This is deliberate on two fronts:

  * Security (Phase 8 sec.11 -- "API parameter tampering"): a client that
    tried to smuggle unbounded/nested/binary data into `inputs` (to bloat
    storage, or to store something that later gets echoed back into the
    generator page and interpreted as more than plain text) gets silently
    trimmed to the known-safe shape instead. Restoration later renders
    every one of these values as plain text into <textarea>/<input>
    elements via json_script (auto-escaped), never innerHTML -- so there
    is no new XSS surface here even though the values are user-authored.

  * Product correctness (Phase 8 "NO FAKE COMPLETION" / "do not invent
    fields"): this is the single source of truth for which fields each
    generator's form actually has. accounts.views.save_history and
    AI_GENERATORS.views.create() (restore) both import this rather than
    each keeping their own copy that could drift.

"chat" has no entry: there is no chat tab in main.html to restore inputs
into (same reasoning as _REOPENABLE_FEATURES in accounts/views.py).
"""

from __future__ import annotations

# Per-feature: (dom element id used by main.html, max stored length).
# Order matters only for documentation; restoration writes each present
# key to its element id regardless of order.
GENERATOR_INPUT_FIELDS: dict[str, dict[str, tuple[str, int]]] = {
    "photo": {
        "prompt": ("imagePrompt", 4000),
    },
    "code": {
        "language": ("codeLang", 100),
        "prompt": ("codePrompt", 4000),
    },
    "story": {
        "topic": ("storyTopic", 300),
        "characters": ("storyCharacters", 500),
        "length": ("storyLength", 50),
        "details": ("storyDetails", 2000),
    },
    "application": {
        "type": ("applicationType", 100),
        "name": ("applicantName", 200),
        "purpose": ("applicationPurpose", 300),
        "recipient": ("recipientName", 200),
        "reason": ("applicationReason", 2000),
        "details": ("applicationDetails", 2000),
    },
    "proposal": {
        "type": ("proposalType", 100),
        "title": ("proposalTitle", 300),
        "audience": ("proposalAudience", 300),
        "objective": ("proposalObjective", 2000),
        "benefits": ("proposalBenefits", 2000),
        "budget": ("proposalBudget", 200),
        "details": ("proposalDetails", 2000),
    },
}


def sanitize_inputs(feature: str, raw_inputs) -> dict:
    """
    Return a dict containing only the whitelisted keys for `feature`,
    each coerced to a length-capped string. Unknown features, a
    non-dict `raw_inputs`, or an empty result all return {} -- callers
    treat {} exactly like "no structured inputs on record" (the same
    state legacy pre-Phase-8 rows are in), never an error.
    """
    schema = GENERATOR_INPUT_FIELDS.get(feature)
    if not schema or not isinstance(raw_inputs, dict):
        return {}

    cleaned: dict[str, str] = {}
    for key, (_dom_id, max_len) in schema.items():
        value = raw_inputs.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        cleaned[key] = value[:max_len]
    return cleaned


# Same set as accounts.views._REOPENABLE_FEATURES was defined as before
# Phase 8 (every feature that has an actual generator tab in main.html,
# i.e. everything except "chat") -- now derived from this module's own
# schema so the two can't drift apart, instead of accounts/views.py and
# AI_GENERATORS/views.py each keeping their own copy of the same set.
REOPENABLE_FEATURES: frozenset[str] = frozenset(GENERATOR_INPUT_FIELDS.keys())


def dom_ids_for_feature(feature: str) -> dict:
    """{input_key: dom_element_id} for a feature, used when restoring."""
    schema = GENERATOR_INPUT_FIELDS.get(feature, {})
    return {key: dom_id for key, (dom_id, _max_len) in schema.items()}

from django.shortcuts import render

from accounts import ai_request_config
from accounts.history_input_schema import dom_ids_for_feature
from accounts.models import ChatHistory


# PHASE 6 STEP 7 (deep linking) / PHASE_2_FRONTEND_GAP_MATRIX.md GAP-05.
#
# Public param values match the same feature slugs already used
# everywhere else in this project (accounts.ai_request_config.
# ALLOWED_FEATURES, ChatHistory.feature) rather than main.html's internal
# DOM tab ids -- one consistent name per generator across backend,
# history, and URLs. "chat" is deliberately excluded: there is no chat
# tab in main.html to deep-link to. "code" maps to the "codecreator" tab
# id since that's main.html's existing (pre-Phase-6) DOM id for it.
_DEEP_LINK_FEATURE_TO_TAB_ID = {
    "photo": "photo",
    "story": "story",
    "application": "application",
    "proposal": "proposal",
    "code": "codecreator",
}


def create(request):
    # PHASE 1A Step 4: the frontend's request-size/prompt-length hints come
    # from this same config module the server enforces against -- rendered
    # via {{ ai_request_limits|json_script:"ai-request-limits" }} in
    # main.html -- so the two never drift out of sync (see
    # accounts/ai_request_config.py).

    # An unrecognized/missing ?generator= value resolves to None, which
    # main.html's inline script treats as "no deep link" and leaves the
    # server-rendered default (the Photo tab, already marked `active` in
    # the HTML) in place -- a safe, silent fallback rather than an error
    # page for a mistyped or stale URL. The value only ever selects among
    # this fixed whitelist -- never interpolated into HTML/JS/a URL, so
    # there's no injection surface regardless of what's in the query string.
    requested = request.GET.get("generator", "")
    initial_generator_tab = _DEEP_LINK_FEATURE_TO_TAB_ID.get(requested)

    # PHASE 8 STEP 3 -- History -> generator continuity ("reopen" must
    # restore inputs, not just switch tabs -- see PHASE_8 spec sec.3).
    #
    # ?reopen=<ChatHistory id> is looked up server-side (never trust a
    # client-supplied inputs blob directly) and scoped to
    # user=request.user -- this is the cross-user isolation boundary:
    # requesting someone else's row id here returns no restoration data
    # at all, same as a row that doesn't exist. Only rows whose stored
    # `feature` matches the `?generator=` being opened are honored, so a
    # stale/mismatched link can't restore, say, a Proposal row's fields
    # into the Application tab.
    #
    # restored_inputs carries the real structured inputs when the row was
    # saved after this field existed (accounts.models.ChatHistory.inputs,
    # accounts.views.save_history). For legacy rows with nothing in
    # `inputs`, we fall back to the row's plain `query` text -- which for
    # every reopenable feature is at minimum a genuine (if sometimes
    # partial, e.g. "Application: <purpose>") piece of what the user
    # typed, never an invented value -- and let the frontend decide where
    # a bare fallback string like that belongs for each generator.
    restored_inputs = None
    restored_query_fallback = None
    reopen_id = request.GET.get("reopen")
    if reopen_id and requested in _DEEP_LINK_FEATURE_TO_TAB_ID and request.user.is_authenticated:
        try:
            history_item = ChatHistory.objects.get(
                id=reopen_id, user=request.user, feature=requested
            )
        except (ChatHistory.DoesNotExist, ValueError):
            history_item = None

        if history_item is not None:
            if history_item.inputs:
                restored_inputs = {
                    dom_ids_for_feature(requested).get(key): value
                    for key, value in history_item.inputs.items()
                    if dom_ids_for_feature(requested).get(key)
                }
            else:
                restored_query_fallback = history_item.query

    return render(
        request,
        'main.html',
        {
            "ai_request_limits": ai_request_config.as_frontend_context(),
            "initial_generator_tab": initial_generator_tab,
            "restored_inputs": restored_inputs,
            "restored_query_fallback": restored_query_fallback,
            "active_nav": "home",
        },
    )

# Zentric — Test Report

Documentation of the automated test suite: what each test feeds in (**data**),
what it asserts (**expected**), and the actual run **output**.

- **Framework:** pytest 9.1.1 · Python 3.12.7 · win32
- **Command:** `./venv/Scripts/python.exe -m pytest`
- **Result:** **70 passed** in ~15s
- **Design:** all external boundaries (Groq LLM, Postgres, Redis) are mocked, so
  the suite runs offline and deterministically. It exercises the business logic
  that defines *behavior*, not the non-deterministic LLM text generation.

Shared test data (`tests/conftest.py`):
- `make_parcel(**overrides)` → a parcel dict in the shape `parcel_data._to_dict`
  produces. Defaults: `tracking_number="TRK10001"`, `customer_phone="923001234567"`,
  `status="in_transit"`, `expected_delivery_date = today - 2 days` (overdue).
- `base_state(**overrides)` → minimal `AgentState` dict
  (`user_message="hi"`, `customer_id="923001234567"`).

---

## 1. Intent classification — `tests/test_intent.py` (18 tests)

Covers `app/graph/nodes.py`: `extract_tracking_number`, `_contains_keyword`,
`rule_based_intent`, `intent_understanding_node`.

### `extract_tracking_number` — valid matches (regex `\b[A-Z]{2,5}\d{4,10}\b`)

| Input data | Expected |
|---|---|
| `"TRK12345 status?"` | `"TRK12345"` |
| `"where is trk12345"` | `"TRK12345"` (normalised to upper-case) |
| `"my parcel AB1234 please"` | `"AB1234"` |
| `"check ABCDE1234567890"` | `"ABCDE1234567890"` (5 letters + exactly 10 digits) |

### `extract_tracking_number` — no match → `None`

| Input data | Why |
|---|---|
| `"where is my order"` | `"order"` has no digits; must not read as tracking id |
| `"hello there"` | no candidate |
| `"12345"` | digits with no 2–5 letter prefix |
| `"A1"` | too short |
| `"AB123456789012"` | 12 digits exceeds `\d{4,10}` cap, no boundary to anchor |

### Rule logic

| Test | Data | Expected |
|---|---|---|
| `test_contains_keyword_respects_word_boundary` | `"order placed"` / `"thora der ho gai"` | `False` / `True` (`der` must not match inside `order`) |
| `test_delay_keyword_wins_over_tracking_number` | `"TRK12345 is delayed"` | `"delay_complaint"` (delay checked before tracking) |
| `test_rule_based_intent_track_order` | `"status of ABC1234"` | `"track_order"` |
| `test_rule_based_intent_faq` | `"what are your working hours"` | `"faq"` |
| `test_rule_based_intent_returns_none_when_no_rule_matches` | `"hmm okay then"` | `None` (triggers LLM fallback) |

### `intent_understanding_node`

| Test | Data | Expected |
|---|---|---|
| `test_node_uses_rule_and_skips_llm` | `"my parcel is late"` | `"delay_complaint"`; `llm_intent` **not** called (cost control) |
| `test_node_falls_back_to_llm` | `"tell me about something vague"` (LLM stubbed → `faq`) | `"faq"`; LLM received the message |
| `test_pending_clarification_with_tracking_number_is_continuation` | `pending_clarification` set + `"it's TRK99999"` | `intent="track_order"`, `tracking_number="TRK99999"`, no reclassification |
| `test_pending_clarification_without_tracking_number_falls_through` | `pending_clarification` set + `"huh?"` | falls through to normal classification (`"unclear"`) |

---

## 2. Decision making — `tests/test_decision.py` (17 tests)

Covers `decision_making_node` + `REASON_TO_DECISION`. The LLM (explanation text)
is stubbed; the reason→action mapping and overdue logic are what's asserted.

### `delay_reason` → decision (parametrized over the full `REASON_TO_DECISION`)

| Delay reason (data) | Expected decision |
|---|---|
| `customer_unavailable` | `notify` |
| `incorrect_address` | `notify` |
| `consignee_requested_reschedule` | `notify` |
| `weather_delay` | `notify` |
| `vehicle_breakdown` | `reroute` |
| `operational_delay` | `reroute` |
| `linehaul_delay` | `reroute` |
| `shipment_damaged` | `escalate` |
| `security_restrictions` | `escalate` |
| `payment_issue_cod` | `escalate` |

### Edge cases

| Test | Data | Expected |
|---|---|---|
| `test_unknown_reason_defaults_to_escalate` | `delay_reason="meteor_strike"`, overdue | `escalate` (safe default) |
| `test_escalate_sets_human_handoff` | `shipment_damaged`, overdue | `escalate` + `needs_human_handoff=True` |
| `test_notify_decision_does_not_force_handoff` | `customer_unavailable`, overdue | `notify`, handoff not forced |
| `test_parcel_not_overdue_yields_no_action` | expected date = today + 2d | `no_action` |
| `test_delivered_parcel_is_never_delayed` | `status="delivered"`, expected today − 10d | `no_action` |
| `test_node_is_noop_for_non_delay_intent` | `intent="track_order"` | no `decision` written |
| `test_node_is_noop_without_parcel` | `retrieved_data=None` | no `decision` written |

---

## 3. Escalation / frustration — `tests/test_escalation.py` (8 tests)

Covers `rule_based_frustration_check` and `escalation_check_node`
(session helpers + LLM tone check mocked).

| Test | Data | Expected |
|---|---|---|
| `test_explicit_human_request` | `"can I talk to a human"`, count 1 | `"explicit_human_request"` |
| `test_repeated_query_threshold` | same msg, count 3 / count 2 | `"repeated_query"` / `None` |
| `test_angry_language` | `"this is the worst service"` | `"angry_language"` |
| `test_no_frustration_signal` | `"thanks, where is TRK12345"` | `None` |
| `test_repeated_identical_message_escalates` | prior `repeat_count=2`, same message | handoff + `repeated_query`; saved count = 3 |
| `test_different_message_resets_repeat_count` | prior count 2, new message | no handoff; saved count = 1 |
| `test_llm_fallback_used_when_rules_find_nothing` | `"oh great, another delay"` (LLM → True) | handoff + `tone_detected` |
| `test_rules_short_circuit_before_llm` | `"connect me to an agent"` | `explicit_human_request`; LLM **not** called |

---

## 4. Graph routing — `tests/test_routing.py` (8 tests)

Covers `route_after_intent` / `route_after_retrieval` in `build_graph.py`.

| Test | Data | Expected next node |
|---|---|---|
| `route_after_intent` | `intent="track_order"` | `data_retrieval` |
| `route_after_intent` | `intent="delay_complaint"` | `data_retrieval` |
| `route_after_intent` | `intent="faq"` | `faq_node` |
| `route_after_intent` | `intent="unclear"` | `response_generation` |
| `route_after_intent` | `intent=None` | `response_generation` |
| `route_after_retrieval_clarification_wins` | delay_complaint + `clarification_needed` | `response_generation` |
| `route_after_retrieval_delay_goes_to_decision` | delay_complaint, no clarification | `decision_making` |
| `route_after_retrieval_track_order_goes_to_response` | track_order, no clarification | `response_generation` |

---

## 5. Parcel resolution & ownership — `tests/test_tracking_agent.py` (7 tests)

Covers `_interpret_result` (0/1/many branching) and the ownership guard on the
lookup tool (`find_parcel` mocked).

| Test | Data | Expected |
|---|---|---|
| `test_interpret_found_parcel` | `{found: True, parcel}` | returns parcel, no clarification |
| `test_interpret_tracking_number_not_found` | `{found: False, tracking_number: "TRK404"}` | clarification mentioning `TRK404` |
| `test_interpret_zero_parcels_asks_for_tracking_id` | `count: 0` | clarification asking for tracking id |
| `test_interpret_single_parcel_resolves_directly` | `count: 1` | resolves that parcel |
| `test_interpret_many_parcels_lists_them_for_clarification` | `count: 2` (`TRK1001`, `TRK1002`) | clarification listing both |
| `test_lookup_tool_returns_parcel_owned_by_caller` | parcel `customer_phone` == caller | `found: True`, parcel returned |
| `test_lookup_tool_hides_parcel_owned_by_someone_else` | parcel belongs to `920000000000` | `found: False`, **no parcel leaked** |

---

## 6. Duplicate-action prevention — `tests/test_action_service.py` (2 tests)

Covers `create_ticket` / `create_reroute_request` dedup guard. `SessionLocal` is
rigged to raise if reached, proving no duplicate DB insert occurs.

| Test | Data | Expected |
|---|---|---|
| `test_create_ticket_returns_existing_open_ticket` | open ticket `TCK-0007` exists | returns it with `already_existed=True`, no insert |
| `test_create_reroute_returns_existing_active_reroute` | active reroute `RRT-0003` exists | returns it with `already_existed=True`, no insert |

---

## 7. Request validation — `tests/test_validation.py` (10 tests)

Covers the `MessageRequest` Pydantic model on `POST /test/message`.

| Test | Data | Expected |
|---|---|---|
| `test_valid_payload` | `923001234567` / `"TRK12345 status?"` | accepted unchanged |
| `test_from_number_strips_whitespace` | `"  923001234567  "` | trimmed to `923001234567` |
| `test_message_is_stripped` | `"  hello  "` | trimmed to `"hello"` |
| `test_invalid_from_number_rejected` | `92300abc4567` | `ValidationError` (letters) |
| `test_invalid_from_number_rejected` | `+923001234567` | `ValidationError` (leading `+`) |
| `test_invalid_from_number_rejected` | `0300 123 4567` | `ValidationError` (spaces) |
| `test_invalid_from_number_rejected` | `12345` | `ValidationError` (too short) |
| `test_invalid_message_rejected` | `""` | `ValidationError` (empty) |
| `test_invalid_message_rejected` | `"   "` | `ValidationError` (whitespace-only) |
| `test_overlong_message_rejected` | 1001 chars | `ValidationError` (max 1000) |

---

## Full run output (`pytest -v`)

```
============================= test session starts =============================
platform win32 -- Python 3.12.7, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\Zentric\Zentric
configfile: pytest.ini
testpaths: tests
collected 70 items

tests/test_action_service.py::test_create_ticket_returns_existing_open_ticket PASSED [  1%]
tests/test_action_service.py::test_create_reroute_returns_existing_active_reroute PASSED [  2%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[customer_unavailable-notify] PASSED [  4%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[incorrect_address-notify] PASSED [  5%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[consignee_requested_reschedule-notify] PASSED [  7%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[weather_delay-notify] PASSED [  8%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[vehicle_breakdown-reroute] PASSED [ 10%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[operational_delay-reroute] PASSED [ 11%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[linehaul_delay-reroute] PASSED [ 12%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[shipment_damaged-escalate] PASSED [ 14%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[security_restrictions-escalate] PASSED [ 15%]
tests/test_decision.py::test_known_reason_maps_to_configured_decision[payment_issue_cod-escalate] PASSED [ 17%]
tests/test_decision.py::test_unknown_reason_defaults_to_escalate PASSED  [ 18%]
tests/test_decision.py::test_escalate_sets_human_handoff PASSED          [ 20%]
tests/test_decision.py::test_notify_decision_does_not_force_handoff PASSED [ 21%]
tests/test_decision.py::test_parcel_not_overdue_yields_no_action PASSED  [ 22%]
tests/test_decision.py::test_delivered_parcel_is_never_delayed PASSED    [ 24%]
tests/test_decision.py::test_node_is_noop_for_non_delay_intent PASSED    [ 25%]
tests/test_decision.py::test_node_is_noop_without_parcel PASSED          [ 27%]
tests/test_escalation.py::test_explicit_human_request PASSED             [ 28%]
tests/test_escalation.py::test_repeated_query_threshold PASSED           [ 30%]
tests/test_escalation.py::test_angry_language PASSED                     [ 31%]
tests/test_escalation.py::test_no_frustration_signal PASSED              [ 32%]
tests/test_escalation.py::test_repeated_identical_message_escalates PASSED [ 34%]
tests/test_escalation.py::test_different_message_resets_repeat_count PASSED [ 35%]
tests/test_escalation.py::test_llm_fallback_used_when_rules_find_nothing PASSED [ 37%]
tests/test_escalation.py::test_rules_short_circuit_before_llm PASSED     [ 38%]
tests/test_intent.py::test_extract_tracking_number_matches[TRK12345 status?-TRK12345] PASSED [ 40%]
tests/test_intent.py::test_extract_tracking_number_matches[where is trk12345-TRK12345] PASSED [ 41%]
tests/test_intent.py::test_extract_tracking_number_matches[my parcel AB1234 please-AB1234] PASSED [ 42%]
tests/test_intent.py::test_extract_tracking_number_matches[check ABCDE1234567890-ABCDE1234567890] PASSED [ 44%]
tests/test_intent.py::test_extract_tracking_number_none[where is my order] PASSED [ 45%]
tests/test_intent.py::test_extract_tracking_number_none[hello there] PASSED [ 47%]
tests/test_intent.py::test_extract_tracking_number_none[12345] PASSED    [ 48%]
tests/test_intent.py::test_extract_tracking_number_none[A1] PASSED       [ 50%]
tests/test_intent.py::test_extract_tracking_number_none[AB123456789012] PASSED [ 51%]
tests/test_intent.py::test_contains_keyword_respects_word_boundary PASSED [ 52%]
tests/test_intent.py::test_delay_keyword_wins_over_tracking_number PASSED [ 54%]
tests/test_intent.py::test_rule_based_intent_track_order PASSED          [ 55%]
tests/test_intent.py::test_rule_based_intent_faq PASSED                  [ 57%]
tests/test_intent.py::test_rule_based_intent_returns_none_when_no_rule_matches PASSED [ 58%]
tests/test_intent.py::test_node_uses_rule_and_skips_llm PASSED           [ 60%]
tests/test_intent.py::test_node_falls_back_to_llm PASSED                 [ 61%]
tests/test_intent.py::test_pending_clarification_with_tracking_number_is_continuation PASSED [ 62%]
tests/test_intent.py::test_pending_clarification_without_tracking_number_falls_through PASSED [ 64%]
tests/test_routing.py::test_route_after_intent[track_order-data_retrieval] PASSED [ 65%]
tests/test_routing.py::test_route_after_intent[delay_complaint-data_retrieval] PASSED [ 67%]
tests/test_routing.py::test_route_after_intent[faq-faq_node] PASSED      [ 68%]
tests/test_routing.py::test_route_after_intent[unclear-response_generation] PASSED [ 70%]
tests/test_routing.py::test_route_after_intent[None-response_generation] PASSED [ 71%]
tests/test_routing.py::test_route_after_retrieval_clarification_wins PASSED [ 72%]
tests/test_routing.py::test_route_after_retrieval_delay_goes_to_decision PASSED [ 74%]
tests/test_routing.py::test_route_after_retrieval_track_order_goes_to_response PASSED [ 75%]
tests/test_tracking_agent.py::test_interpret_found_parcel PASSED         [ 77%]
tests/test_tracking_agent.py::test_interpret_tracking_number_not_found PASSED [ 78%]
tests/test_tracking_agent.py::test_interpret_zero_parcels_asks_for_tracking_id PASSED [ 80%]
tests/test_tracking_agent.py::test_interpret_single_parcel_resolves_directly PASSED [ 81%]
tests/test_tracking_agent.py::test_interpret_many_parcels_lists_them_for_clarification PASSED [ 82%]
tests/test_tracking_agent.py::test_lookup_tool_returns_parcel_owned_by_caller PASSED [ 84%]
tests/test_tracking_agent.py::test_lookup_tool_hides_parcel_owned_by_someone_else PASSED [ 85%]
tests/test_validation.py::test_valid_payload PASSED                      [ 87%]
tests/test_validation.py::test_from_number_strips_whitespace PASSED      [ 88%]
tests/test_validation.py::test_message_is_stripped PASSED                [ 90%]
tests/test_validation.py::test_invalid_from_number_rejected[92300abc4567] PASSED [ 91%]
tests/test_validation.py::test_invalid_from_number_rejected[+923001234567] PASSED [ 92%]
tests/test_validation.py::test_invalid_from_number_rejected[0300 123 4567] PASSED [ 94%]
tests/test_validation.py::test_invalid_from_number_rejected[12345] PASSED [ 95%]
tests/test_validation.py::test_invalid_message_rejected[] PASSED         [ 97%]
tests/test_validation.py::test_invalid_message_rejected[   ] PASSED      [ 98%]
tests/test_validation.py::test_overlong_message_rejected PASSED          [100%]

============================= 70 passed in 16.56s =============================
```

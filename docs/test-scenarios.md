# Test scenarios and traceability

Every functional requirement in [requirements.md](requirements.md) is covered by at least one automated
test. Run them all with `python -m pytest`.

## Use cases

| ID | Use case | Actor | Main flow | Alternate flows |
|---|---|---|---|---|
| UC-1 | Connect and trade | Client | Logon, orders, Logout | Message before Logon; invalid or duplicate Logon; sequence too low or gapped; resend |
| UC-2 | Buy or sell at a limit | Client | New, routed fills, rest remainder | Rejected; IOC/FOK remainder canceled |
| UC-3 | Cancel a working order | Client | Pending Cancel, Canceled | Too late (filled); unknown order |
| UC-4 | Amend a working order | Client | Pending Replace, Replaced, re-route | Quantity below filled; side/symbol change |
| UC-5 | Trade an option | Client | New, fills with contract details echoed | Missing strike, expiry or right |
| UC-6 | Work an order over time | Client | TWAP slices released on schedule | Remainder canceled after the last slice |
| UC-7 | Several clients at once | Two clients | Each sees only its own orders' reports | One client's order fills another's resting order; a second connection for a live CompID |

## Traceability matrix

FR-10 is enforced for every engine-level test: the `engine` fixture in `tests/conftest.py` checks CumQty
against the sum of LastQty, LeavesQty, and AvgPx on every execution report the engine emits. The TCP tests
in `test_session.py` build their own engine through `fixsim.server.start` and are not audited this way.

| Requirement | Scenario | Test |
|---|---|---|
| FR-01 | Logon then trading; message before Logon; invalid Logon; Logon not at sequence 1; duplicate Logon | `test_session.py::test_logon_then_order_lifecycle_over_tcp`, `test_a_message_before_logon_closes_the_connection_without_reply`, `test_logon_with_wrong_target_or_no_heartbeat_interval_is_closed_without_reply`, `test_logon_must_start_at_sequence_one`, `test_a_second_connection_cannot_take_over_a_live_session` |
| FR-02 | Codec encoding, integrity checks, field errors and framing | `test_fix.py::test_round_trip_keeps_header_order_and_values`, `test_body_length_and_checksum_follow_the_spec`, `test_encode_refuses_a_tag_without_a_value`, `test_wrong_checksum_is_rejected`, `test_wrong_body_length_is_rejected`, `test_non_ascii_bytes_are_garbled_not_a_crash`, `test_intact_message_with_empty_field_keeps_its_header_and_records_the_problem`, `test_duplicate_and_malformed_tags_are_field_errors`, `test_stream_framing_splits_messages_and_keeps_partial_tail`, `test_framing_survives_a_read_that_ends_inside_the_begin_string`, `test_framing_finds_messages_after_newlines_and_leading_garbage`, `test_framing_is_the_same_whatever_way_tcp_splits_the_bytes`, `test_begin_string_text_inside_a_value_does_not_start_a_frame`, `test_a_truncated_message_does_not_swallow_the_next_one` |
| FR-02 | Garbled message ignored, empty field rejected, session survives | `test_session.py::test_garbled_message_is_ignored_and_empty_field_is_rejected_without_ending_the_session` |
| FR-03 | Too low, gap, recovery, duplicates, Reset, rejected GapFill, OrigSendingTime, client resend and its range, CompIDs | `test_session.py::test_sequence_number_too_low_ends_the_session_with_logout`, `test_sequence_gap_triggers_resend_request`, `test_gap_is_recovered_by_gap_fill_and_resend_and_the_discarded_order_then_executes`, `test_possible_duplicate_below_expected_sequence_is_ignored`, `test_sequence_reset_in_reset_mode_clears_a_pending_resend`, `test_rejected_gap_fill_still_uses_up_its_sequence_number`, `test_resent_message_without_orig_sending_time_is_rejected`, `test_resend_request_from_client_is_answered_with_sequence_reset_gap_fill`, `test_out_of_order_resend_request_is_answered_before_asking_for_the_gap`, `test_resend_request_gap_fills_only_the_requested_range`, `test_resend_request_with_a_bad_range_is_rejected_and_the_session_continues`, `test_comp_id_mismatch_is_rejected_and_logged_out` |
| FR-04 | TestRequest with and without TestReqID | `test_session.py::test_test_request_is_answered_with_heartbeat_echoing_the_id`, `test_test_request_without_id_is_rejected` |
| FR-05 | Missing Side; missing TransactTime | `test_order_lifecycle.py::test_missing_required_tag_is_a_session_reject`, `test_missing_transact_time_is_a_session_reject` |
| FR-06 | Unknown symbol, zero quantity, limit without price, option without strike, bad side, duplicate ClOrdID; Text on every reject | `test_order_lifecycle.py::test_invalid_orders_are_rejected_with_a_reason`, `test_duplicate_cl_ord_id_is_rejected` |
| FR-07 | New reported before fills | `test_order_lifecycle.py::test_limit_order_sweeps_best_price_then_lowest_fee_venue` |
| FR-08 | Best price, then lowest take fee | `test_order_lifecycle.py::test_limit_order_sweeps_best_price_then_lowest_fee_venue` |
| FR-09 | 851=2 when taking; 851=1 when a resting child is hit | `test_order_lifecycle.py::test_taker_fills_are_flagged_as_removing_liquidity`, `test_day_limit_remainder_rests_on_best_rebate_venue_and_fills_later` |
| FR-10 | Quantities reconcile on every report | `ReportAuditor` in `tests/conftest.py` (every engine test); `test_marketable_order_fills_completely_at_volume_weighted_average` |
| FR-11 | Remainder rests on the rebate venue and fills later | `test_order_lifecycle.py::test_day_limit_remainder_rests_on_best_rebate_venue_and_fills_later` |
| FR-12 | IOC with Text, FOK, market sweep, market remainder, market FOK | `test_ioc_cancels_the_unfilled_remainder`, `test_fok_without_enough_liquidity_is_canceled_with_no_fills`, `test_market_order_sweeps_the_book`, `test_market_order_cancels_what_the_book_cannot_fill`, `test_market_fok_checks_all_liquidity_not_a_price` |
| FR-13 | Cancel keeps CumQty; resting child removed from the venue's book | `test_cancel_goes_through_pending_cancel_and_keeps_cum_qty`, `test_cancel_pulls_the_resting_child_from_the_venue_book` |
| FR-14 | Too late, unknown, stale OrigClOrdID, another client's order | `test_cancel_is_refused_when_too_late_or_unknown`, `test_cancel_must_name_the_current_cl_ord_id`, `test_cl_ord_id_is_unique_per_client_and_a_client_cannot_touch_another_clients_order` |
| FR-15 | Replace reprices and re-routes; quantity below filled refused | `test_replace_reprices_and_reroutes_the_remaining_quantity`, `test_replace_below_filled_quantity_is_refused` |
| FR-16 | Option order; exact strike; equity symbol never matches an option; no empty tags on a rejected option | `test_option_order_routes_and_reports_contract_fields`, `test_option_strike_must_match_exactly`, `test_an_equity_symbol_never_matches_an_option_book`, `test_rejected_option_order_never_carries_empty_tags` |
| FR-17 | TWAP on a clock, over TCP, Day only, keeps working after its client disconnects | `test_twap_releases_slices_over_time_and_completes`, `test_twap_must_be_a_day_order`, `test_session.py::test_twap_slices_arrive_over_tcp_from_the_scheduler`, `test_twap_scheduler_survives_a_client_disconnecting_mid_schedule` |
| FR-18 | Self-trade prevention per client; other clients cross; price-time priority; no phantom books | `test_order_lifecycle.py::test_same_client_orders_do_not_cross_but_other_clients_do`, `test_venue.py::test_orders_from_the_same_client_never_trade_with_each_other`, `test_orders_from_different_clients_cross_and_both_see_the_fill`, `test_price_time_priority_fills_the_earlier_order_first`, `test_a_venue_never_creates_a_book_for_an_instrument_it_does_not_list` |
| FR-19 | inf, nan, exponent, underscore and padded prices; nan interval | `test_order_lifecycle.py::test_prices_must_be_plain_finite_decimals` |
| FR-20 | Unsupported message type; undefined message type; inbound 35=j | `test_session.py::test_unsupported_message_type_gets_business_message_reject`, `test_undefined_message_type_is_a_session_reject_and_an_inbound_business_reject_is_not_answered` |
| FR-21 | Reports reach only the owning client; a disconnected client's TWAP reports never reach another client; a second connection cannot take over | `test_session.py::test_reports_go_only_to_the_client_that_owns_the_order`, `test_twap_scheduler_survives_a_client_disconnecting_mid_schedule`, `test_a_second_connection_cannot_take_over_a_live_session` |

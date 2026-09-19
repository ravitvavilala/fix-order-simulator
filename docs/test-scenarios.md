# Test scenarios and traceability

Every functional requirement in [requirements.md](requirements.md) is covered by at least one automated
test. Run them all with `python -m pytest`.

## Use cases

| ID | Use case | Actor | Main flow | Alternate flows |
|---|---|---|---|---|
| UC-1 | Connect and trade | Client | Logon, orders, Logout | Message before Logon; sequence too low or gapped |
| UC-2 | Buy or sell at a limit | Client | New, routed fills, rest remainder | Rejected; IOC/FOK remainder canceled |
| UC-3 | Cancel a working order | Client | Pending Cancel, Canceled | Too late (filled); unknown order |
| UC-4 | Amend a working order | Client | Pending Replace, Replaced, re-route | Quantity below filled; side/symbol change |
| UC-5 | Trade an option | Client | New, fills with contract details echoed | Missing strike, expiry or right |
| UC-6 | Work an order over time | Client | TWAP slices released on schedule | Remainder canceled after the last slice |

## Traceability matrix

| Requirement | Scenario | Test |
|---|---|---|
| FR-01, FR-07 | Logon, then order lifecycle over TCP | `test_session.py::test_logon_then_order_lifecycle_over_tcp` |
| FR-02 | Tampered CheckSum / BodyLength rejected by the codec | `test_fix.py::test_wrong_checksum_is_rejected`, `test_wrong_body_length_is_rejected` |
| FR-02 | Stream framing across TCP reads | `test_fix.py::test_stream_framing_splits_messages_and_keeps_partial_tail` |
| FR-03 | Sequence too low ends the session | `test_session.py::test_sequence_number_too_low_ends_the_session_with_logout` |
| FR-03 | Sequence gap requests a resend | `test_session.py::test_sequence_gap_triggers_resend_request` |
| FR-04 | TestRequest answered with Heartbeat | `test_session.py::test_test_request_is_answered_with_heartbeat_echoing_the_id` |
| FR-05 | Missing Side is a session Reject | `test_order_lifecycle.py::test_missing_required_tag_is_a_session_reject` |
| FR-06 | Unknown symbol, zero quantity, limit without price, option without strike, bad side | `test_order_lifecycle.py::test_invalid_orders_are_rejected_with_a_reason` |
| FR-06 | Duplicate ClOrdID | `test_order_lifecycle.py::test_duplicate_cl_ord_id_is_rejected` |
| FR-08, FR-09 | Best price, then lowest take fee | `test_order_lifecycle.py::test_limit_order_sweeps_best_price_then_lowest_fee_venue` |
| FR-10 | CumQty, LeavesQty, AvgPx reconcile | `assert_quantities_consistent` in the lifecycle tests; `test_marketable_order_fills_completely_at_volume_weighted_average` |
| FR-11 | Remainder rests on the rebate venue and fills later | `test_order_lifecycle.py::test_day_limit_remainder_rests_on_best_rebate_venue_and_fills_later` |
| FR-12 | IOC, FOK, market | `test_ioc_cancels_the_unfilled_remainder`, `test_fok_without_enough_liquidity_is_canceled_with_no_fills`, `test_market_order_sweeps_the_book` |
| FR-13 | Cancel keeps CumQty and pulls children | `test_order_lifecycle.py::test_cancel_goes_through_pending_cancel_and_keeps_cum_qty` |
| FR-14 | Too late / unknown order | `test_order_lifecycle.py::test_cancel_is_refused_when_too_late_or_unknown` |
| FR-15 | Replace reprices and re-routes; quantity below filled refused | `test_replace_reprices_and_reroutes_the_remaining_quantity`, `test_replace_below_filled_quantity_is_refused` |
| FR-16 | Option order | `test_order_lifecycle.py::test_option_order_routes_and_reports_contract_fields` |
| FR-17 | TWAP slices on a clock, in-process and over TCP | `test_twap_releases_slices_over_time_and_completes`, `test_session.py::test_twap_slices_arrive_over_tcp_from_the_scheduler` |
| FR-18 | Self-trade prevention; different orders still cross; price-time priority | `test_venue.py::test_an_order_never_trades_against_its_own_resting_child`, `test_two_different_orders_cross_and_both_see_the_fill`, `test_price_time_priority_fills_the_earlier_order_first` |

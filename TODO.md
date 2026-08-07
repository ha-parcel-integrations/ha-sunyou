# SunYou — still to do

Generated from ha-carrier-template (`--auth none --interval configurable`)
and built out against the confirmed payload in
`carrier-research/api/sunyou/tracking.md`. All `TODO(carrier)` markers are
resolved, tests and lint are green.

## Remaining, not code

- [ ] Install it in a real Home Assistant and track one real parcel through
      at least two status changes — the unit tests use a captured sample
      (`SYAE006809461`) and constructed-but-vocabulary-accurate fixtures, not
      a live poll.
- [ ] Add `sunyou` to the aggregator's (`ha-parcel-aggregator`)
      `KNOWN_CARRIERS` and `CARRIER_EVENT_PREFIXES` — separate repo, not part
      of this build.

The full run-through lives in the template's `docs/checklist.md`.

Delete this file once it is empty.

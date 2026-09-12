# Working in this repository

Home Assistant custom integration for **SunYou** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
Where this repo diverges from it, that is recorded below under
*Divergences from the scaffold*.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

**API mechanics live in `carrier-research/sunyou/api/` (private research repo)** —
the keyless JSONP `queryTrack` endpoint, the `has:false` not-found signalling,
the payload→canonical mapping and the 28-code `status`/`eventCode`
vocabulary. Do not duplicate them here, and do not re-create a local
`docs/api/`.

## Carrier-specific decisions (integration only)

SunYou (SYPost) is a Chinese cross-border postal/logistics operator, in the
same niche as [Cainiao](https://github.com/ha-parcel-integrations/ha-cainiao):
the carrier of record for cheap Asia→EU e-commerce parcels that national
carriers only show once they arrive. It **hands off** the last leg to a
national carrier — so the same physical parcel can show up twice in the
aggregator, once as SunYou and once as the last-mile carrier
(`raw.trackingNumber` is the join key, exactly like Cainiao's `realMailNo` —
an open aggregator-side item, not something this repo resolves). SunYou
exposes nothing about the last leg (no sender/receiver/window/pickup/weight);
the `None`s in `normalize_parcel` are intentional. Reflected in `const.py`'s
`CAPABILITIES` (feeds the docs site's comparison table) — keep the two in
agreement if that ever changes.

- **`displayStatus` is never read, anywhere, including `raw`.** It is a
  coarse 3-value UI bucket, not a status — on the wire, all 16 delivered
  parcels in the research capture returned `displayStatus: "4"` (a
  reconstructed table had read that as "undelivered"), and `"5"` never
  appeared once. Status, `raw_status`, `delivered` and `delivered_at` are all
  derived from the **newest event** across every `result` leg instead (see
  `parcels.py::_newest`). If you are tempted to read `displayStatus` for
  anything, don't — that is the mistake this repo exists to avoid repeating.
- **`orderNo` is the barcode; `trackingNumber` is not.** `trackingNumber` is
  the last-mile carrier's own number for the handed-off parcel. Getting this
  backwards makes every parcel change identity at handoff — `orderNo` is what
  `barcode`, the placeholder/backfill keys in `coordinator.py`, and the
  tracking URL are all built from.
- **Per-event timestamps, not a top-level field + a constant.** SunYou's
  `createTime` is naive (`"YYYY-MM-DD HH:MM:SS"`, no offset); each event
  carries its own `timeZone` sibling instead (origin-leg events read
  `+08:00`, destination-country events carry the local offset), and
  `lastUpdate` has no `timeZone` of its own at all. `parcels.py::_event_timestamp`
  combines the two per event; a missing or malformed `timeZone` falls back to
  UTC (warned once) rather than raising, and a `createTime` that fails to
  parse drops that one event (warned once) rather than guessing.
- **`at_pickup_point` and `returning` are mapped, confirmed live 2026-08-13**
  from a user's WARNING log, not the 21-parcel research capture — per
  Cainiao's `GTMS_STA_SIGNED` rule, a pickup code is `at_pickup_point`, never
  `delivered`, and the return code is distinct from the failure events that
  precede it. `carrier-research/sunyou/api/` is updated to match — the doc's
  "there may simply be no `at_pickup_point`" reasoning is superseded, not a
  standing ruling anymore.
- **Tracking-code format is unvalidated locally — any non-empty code is
  accepted.** Every observed number happens to fit
  `^SY[A-Z0-9]{2,}\d{6,}$`, but only the `SYAE` prefix has actually been
  seen, and SunYou answers `has: false` (not an error) for anything it
  doesn't recognise, so there is nothing to gain from rejecting an unfamiliar
  channel prefix — or any other guessed shape — client-side.
- **`status_vocab` not provably closed.** The 28-code `status`/`eventCode`
  vocabulary, see `carrier-research/sunyou/api/`. Unrecognised pairs, a
  `result` leg other than `origin`, a `createTime` parse failure or missing
  `timeZone`, and the first parcel carrying `carrierName` (seen once in 21
  research parcels — that handoff block's field inventory is thin) all log a
  one-shot `WARNING` with an `issues/new?template=unrecognised_status.yml`
  link. Do not add a status mapping without evidence — a wrong mapping fires
  events for a state the parcel isn't in.

## Divergences from the scaffold

Everything not listed here follows the scaffold exactly.

*Dynamic polling* — SunYou's payload carries no delivery-forecast field at
all, so `planned_from` is always `None` and an `out_for_delivery` parcel goes
straight to the hot tier; the 1h-lookahead branch is unreachable.

## Running tests

```
python -m pytest tests/ --cov=custom_components.sunyou
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in `carrier-research/sunyou/api/` (this
carrier's own directory in the private research repo), never in this repo.

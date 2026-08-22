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
the payload→canonical mapping and the 17-code `status`/`eventCode`
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
  from a user's WARNING log, not the 21-parcel research capture: `207`
  (`ReadyForPickup`) -> `AT_PICKUP_POINT` (Cainiao's `GTMS_STA_SIGNED` rule —
  a pickup code is `at_pickup_point`, never `delivered`) and `210`
  (`Returned`) -> `RETURNING`, distinct from the `208`/`20899` failure events
  that precede it. `200` (`InTransit`) and `20622` (`ClearanceInspect`) were
  confirmed the same day, both `IN_TRANSIT`. `carrier-research/sunyou/api/`
  is updated to match — the doc's "there may simply be no `at_pickup_point`"
  reasoning is superseded, not a standing ruling anymore.
- **Poll interval is `configurable`, unlike Cainiao's `fixed`.** ~70 probe
  requests in a few minutes during research drew no 429, no block and no
  captcha — SunYou is a standalone operator, not Alibaba, so the soft-ban risk
  that keeps Cainiao's cadence hard-coded does not transfer here. The default
  (`DEFAULT_REFRESH_INTERVAL = 60`) still sits on the conservative side of the
  suite's options, though: SunYou has **no batching** (one HTTP request per
  tracked parcel per poll, unlike Cainiao's one request for ten), so a
  household tracking several parcels fans out more traffic per poll here than
  anywhere else in the suite. If a user ever reports a 429, regenerate with
  `--interval fixed` — Cainiao's `const.py`/`coordinator.py` is the worked
  example.
- **Tracking-code format stays at the loose template default**
  (`^[A-Z0-9]{6,30}$`), not the tighter `^SY[A-Z0-9]{2,}\d{6,}$` every
  observed number happens to fit — only the `SYAE` prefix has actually been
  seen, and SunYou answers `has: false` (not an error) for anything it
  doesn't recognise, so there is nothing to gain from rejecting an unfamiliar
  channel prefix client-side.
- **`status_vocab` not provably closed.** Unrecognised
  `status`/`eventCode` pairs, a `result` leg other than `origin`, a
  `createTime` parse failure or missing `timeZone`, and the first parcel
  carrying `carrierName` (seen once in 21 research parcels — that handoff
  block's field inventory is thin) all log a one-shot `WARNING` with an
  `issues/new?template=unrecognised_status.yml` link. Do not add a status
  mapping without evidence — a wrong mapping fires events for a state the
  parcel isn't in.

## Options and reloads

The options flow is one sectioned form (`data_entry_flow.section`); changes apply
without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  retunes `coordinator.update_interval` and calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately.
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

The user-tunable poll interval is a deliberate HACS divergence (see
CONVENTIONS.md); a carrier that throttles is generated with a fixed cadence and no
polling option at all.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.sunyou
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in `carrier-research/sunyou/api/` (this
carrier's own directory in the private research repo), never in this repo.

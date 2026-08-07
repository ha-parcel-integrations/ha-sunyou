# SunYou Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-sunyou.svg)](https://github.com/ha-parcel-integrations/ha-sunyou/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [SunYou](https://sypost.net) (SYPost) parcels — a Chinese cross-border postal/logistics operator used by cheap Asia→EU e-commerce shipments (AliExpress-style orders and similar). SunYou typically sees a parcel weeks before the national carrier that eventually delivers it does. No account is needed — you enter the tracking code yourself, just like on the SunYou website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Known limitation: no delivery forecast](#known-limitation-no-delivery-forecast)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of SunYou parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, origin/destination country and a tracking deep-link
- Summary sensors: incoming parcels, recently delivered parcels
- `sunyou.track_parcel` / `sunyou.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.7 or newer
- A SunYou (SYPost) tracking code, as printed on the shipping confirmation
  from the shop/marketplace that used SunYou for the cross-border leg — no
  account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-sunyou` as an **Integration**.
3. Install **SunYou** and restart Home Assistant.

### Manual

Copy `custom_components/sunyou` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → SunYou**. There is nothing to fill in: the hub is created immediately (SunYou tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`sunyou.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |
| Polling | Refresh every | 60 min | How often SunYou is checked. SunYou has no batching (one request per tracked parcel per poll), so this defaults slower than other carriers in the suite; no rate limiting has been observed, so it can be turned up if you like. |

## Removal

Standard HA removal applies: **Settings → Devices & Services → SunYou → ⋮ → Delete**. Nothing is stored on SunYou's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.sunyou_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.sunyou_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.sunyou_next_delivery` | Earliest expected delivery moment across all active parcels. Present for parity with the other carriers in the suite, but SunYou never gives a delivery forecast — see [Known limitation](#known-limitation-no-delivery-forecast) |
| `sensor.sunyou_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.sunyou_last_successful_update` | Diagnostic: when SunYou was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

A **Deliveries** calendar entity is also created, for parity with the other carriers in the suite, but it will always be empty for SunYou — see [Known limitation](#known-limitation-no-delivery-forecast).

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. SunYou's own event ladder is a pipeline of movements with no waiting state in it — nothing in the observed vocabulary maps to `at_pickup_point`, so that value never appears here (SunYou hands a parcel to a national carrier for the last mile, and any pickup-locker detail lives on *that* carrier's own tracking from then on):

| Status | Meaning |
|---|---|
| `registered` | Pre-alerted / received into SunYou's network |
| `in_transit` | Moving through SunYou's cross-border network, including customs and the handover to the last-mile carrier |
| `out_for_delivery` | With the last-mile courier today |
| `delivered` | Delivered |
| `problem` | A delivery attempt failed |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`. SunYou's status vocabulary is not proven closed (see [Debugging](#debugging)) — an unrecognised code is reported as `unknown` with a one-shot log warning rather than guessed at.

## Known limitation: no delivery forecast

SunYou's tracking API never returns an expected delivery date or window — across 21 real parcels used to confirm this payload, the only date-like field on a delivered parcel was `transitDays` (elapsed days since shipping, not an ETA). Nothing in the response is a forecast.

Three entities exist for parity with the other carriers in this suite, but are permanently inert for SunYou as a result:

- `sensor.sunyou_next_delivery` never has a state.
- The **Deliveries** calendar entity never has an event.
- `sunyou_parcel_delivery_time_changed` never fires, and its device trigger never triggers.

Every other sensor, event and service works normally.

## Events

The integration fires these on the event bus (also available as device triggers on the SunYou device):

| Event | When |
|---|---|
| `sunyou_parcel_registered` | A new parcel appears in the active list |
| `sunyou_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `sunyou_parcel_delivered` | A parcel is delivered |
| `sunyou_parcel_delivery_time_changed` | The expected delivery window changes. Wired for parity with the other carriers in the suite, but SunYou never gives a delivery forecast, so this event can never fire — see [Known limitation](#known-limitation-no-delivery-forecast) |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `sunyou.track_parcel` | `tracking_code` | Start tracking a parcel |
| `sunyou.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.sunyou: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — SunYou has not scanned it yet (their API answers `not_found` until the first scan), or the code is wrong. It will pick up automatically once scanned.
- **A status logs "Unrecognised SunYou status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-sunyou/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the SunYou consumer website. It is not affiliated with, endorsed by, or supported by SunYou. Be gentle with the polling interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)

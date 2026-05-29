# Alectra Utilities for Home Assistant

Custom integration for [Alectra Utilities](https://alectrautilities.com) that pulls electricity usage data from the My Alectra customer portal and exposes it in Home Assistant, including the Energy Dashboard.

## Features

- Hourly electricity consumption backfilled into the HA Energy Dashboard
- Daily energy sensor showing the most recent complete day's usage
- Automatic discovery of customer and meter numbers from the API
- Token-based auth with automatic re-login on expiry

## Requirements

- A My Alectra account at [myalectra.alectrautilities.com](https://myalectra.alectrautilities.com)
- Home Assistant 2026.3.2 or newer
- HACS 2.0.5 or newer (for HACS install)

## Installation

### HACS

1. Add this repository as a custom repository in HACS
2. Install **Alectra Utilities**
3. Restart Home Assistant

### Manual

Copy the `custom_components/alectra_utilities` directory into your HA `config/custom_components/` folder and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Alectra Utilities**
3. Enter your My Alectra credentials:
   - **Username** — your My Alectra login username
   - **Password** — your My Alectra password
   - **Account Number** — 10-digit account number from your bill

Customer number and meter number are discovered automatically from the API.

## Entities

| Entity | Description |
|---|---|
| `sensor.alectra_ACCOUNT_daily_energy_last_complete_day` | kWh consumed on the most recent complete day (2 days ago) |

### Energy Dashboard

The integration injects hourly statistics under the statistic ID `alectra_utilities:ACCOUNT_METER_energy_hourly`. Add this to **Settings → Energy → Individual devices** or the electricity grid section to see hourly consumption charts.

On first load, up to 30 days of historical hourly data is backfilled.

## Data Notes

- **Data lag**: Alectra's API lags by approximately 2 days. Yesterday's data is incomplete and excluded.
- **Granularity**: Hourly readings (the API endpoint is labelled `HH` but returns 60-minute intervals).
- **Timezone**: All timestamps are interpreted as `America/Toronto` (Alectra only serves Ontario).
- **Update interval**: Data is refreshed every 6 hours.

## Technical Notes

The My Alectra portal has no public API. This integration reverse-engineers the internal REST API used by the portal frontend (hosted on Smart Energy Water SCM platform). Cloudflare Bot Management sits in front of the API — `curl_cffi` with Chrome TLS impersonation is used to bypass it.

## License

MIT

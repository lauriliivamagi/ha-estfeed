# Estfeed for Home Assistant

[![Validate](https://img.shields.io/github/actions/workflow/status/tehisain/ha-estfeed/validate.yml?branch=main&label=validate)](https://github.com/tehisain/ha-estfeed/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

> [!NOTE]
> This is a personal fork of [tehisain/ha-estfeed](https://github.com/tehisain/ha-estfeed).

Imports [Elering Estfeed](https://estfeed.elering.ee/) meter data into Home Assistant's Energy dashboard, with daily, monthly and cumulative sensors.

Polls hourly. Meter readings can arrive late; this is not a real-time power monitor.

## Installation

Requires Home Assistant 2024.12 or later.

1. In HACS, open **Custom repositories** and add [this repository](https://github.com/tehisain/ha-estfeed) with type **Integration**. See the [HACS instructions](https://www.hacs.dev/docs/faq/custom_repositories/).
2. Install **Estfeed** and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration** and search for **Estfeed**.
4. Enter the `client_id` and `client_secret` from an API key created in the [Estfeed customer portal](https://estfeed.elering.ee/), plus a name for this installation.

For manual installation, copy `custom_components/estfeed` into your Home Assistant `config/custom_components` directory, restart, then follow steps 3–4.

## Energy dashboard

Setup imports 12 months of history in the background. Options allow 1–84 months of 30 days each. Import time depends on the meters and available data.

Select these external statistics in the Energy dashboard's grid settings. `<name>` is the installation name in lowercase with underscores; `<suffix>` is the meter EIC's last four alphanumeric characters, in lowercase.

| Grid setting | Energy statistic | Optional cost statistic |
| --- | --- | --- |
| Consumption | `estfeed:<name>_consumption_<suffix>` | `estfeed:<name>_cost_<suffix>` |
| Return to grid | `estfeed:<name>_production_<suffix>` | `estfeed:<name>_compensation_<suffix>` |

For costs, choose **Use an entity tracking the total costs** and select the matching statistic. For gas, select the consumption statistic under **Gas consumption**. Gas uses m³ and has no derived costs.

Electricity costs and production compensation use the EE Nord Pool prices from [Elering](https://dashboard.elering.ee/). Quarter-hour prices are averaged per hour, then multiplied by hourly energy using this tariff:

```text
EUR/kWh = spot × (1 + VAT / 100) + margin
```

Set VAT and margin in the integration options. The integration defaults to **22% VAT** and **0 EUR/kWh margin**; Estonia's [standard VAT rate is 24% from 1 July 2025](https://www.emta.ee/en/admin/content/handbook_article/39). Check these settings against your contract. One tariff applies to consumption, production and all imported history. Changing it rebuilds the configured window's costs.

These EUR estimates exclude network fees, levies and time-dependent tariffs, and approximate quarter-hour pricing. Use the dashboard's own price configuration if needed.

## Entities

Each meter gets:

- Consumption totals for today, yesterday, month to date and the previous month.
- Cumulative consumption since setup or the last reset, and a reset button.
- Matching production sensors and a reset button, disabled by default.
- A latest-interval timestamp and a data-fresh binary sensor (on when the newest cached interval is less than 30 hours old).

Periods follow Home Assistant's time zone. Find entity IDs under **Settings → Devices & services → Estfeed**; names vary with the installation, meter and language.

Cumulative baselines survive restarts. Totals use a 62-day cache plus saved contributions from older intervals. Outages longer than 62 days can leave gaps in the cumulative sensor. Moving a reset timestamp only recounts cached intervals and clears saved older contributions.

## Actions

Run these from **Developer tools → Actions**:

| Action | Fields | Effect |
| --- | --- | --- |
| `estfeed.backfill_history` | `months` (1–84, default 24), optional `entry_id` | Fetch and replace energy and cost statistics for the requested window. |
| `estfeed.set_cumulative_reset_at` | `reset_at`, optional `entry_id` | Set the cumulative baseline for consumption and production on all meters in the entry. |

Omitting `entry_id` targets every loaded Estfeed entry. A reset timestamp without a time-zone offset uses Home Assistant's time zone.

## Development

Use Python 3.12 or later in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]' --config-settings editable_mode=compat
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts
mypy
pytest tests --cov=custom_components/estfeed --cov-fail-under=85
```

The compatibility flag lets Home Assistant discover the package during tests. To check the live API with your own credentials:

```bash
ESTFEED_CLIENT_ID=... ESTFEED_CLIENT_SECRET=... python -m scripts.smoke
```

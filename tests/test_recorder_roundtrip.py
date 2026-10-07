"""Round-trip tests against a real recorder.

These run Home Assistant's actual statistics import and query paths instead
of mocking them. Mocks in the unit tests encoded wrong assumptions about the
recorder (timestamp units, accepted metadata keys) that only a real
database exposes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.helpers.recorder import get_instance
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.estfeed.api import AccountingInterval, MeterData, MeteringPoint, Period
from custom_components.estfeed.const import (
    CONF_BACKFILL_MONTHS,
    CONF_RESOLUTION,
    CONF_VAT_PERCENT,
    CommodityType,
    Kind,
    Resolution,
)
from custom_components.estfeed.coordinator import EstfeedCoordinator
from custom_components.estfeed.nps import NpsError
from custom_components.estfeed.statistics import StatisticStream, async_write_meter_statistics

EIC = "38ZEE-00720089-N"
CONSUMPTION_ID = "estfeed:home_consumption_089n"
COST_ID = "estfeed:home_cost_089n"
METER = MeteringPoint(
    eic=EIC,
    commodity_type=CommodityType.ELECTRICITY,
    periods=[Period(start=datetime(2019, 1, 1, tzinfo=UTC), end=None)],
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):  # noqa: ARG001
    """Override the conftest fixture: the recorder must start before hass."""
    yield


class FakeEstfeed:
    """Estfeed stand-in: 1.0 kWh consumed every hour of any requested window."""

    def __init__(self) -> None:
        self.recent_requests: list[dict[str, Any]] = []
        self.requested_starts: list[datetime] = []

    async def get_metering_data(
        self, start: datetime, end: datetime, resolution: Resolution, eics: list[str]
    ) -> list[MeterData]:
        del resolution, eics
        self.requested_starts.append(start)
        intervals = []
        cursor = start
        while cursor < end:
            intervals.append(
                AccountingInterval(
                    period_start=cursor,
                    consumption_kwh=1.0,
                    production_kwh=0.0,
                    consumption_m3=None,
                    production_m3=None,
                )
            )
            cursor += timedelta(hours=1)
        return [MeterData(eic=EIC, intervals=intervals)]


class FakeNps:
    """Spot-price stand-in: 0.1 EUR/kWh every hour unless ``down`` is set."""

    def __init__(self) -> None:
        self.down = False

    def evict_after(self, cutoff: datetime) -> None:
        del cutoff

    async def async_get_prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        if self.down:
            raise NpsError("NPS request failed: outage")
        prices = {}
        cursor = start
        while cursor < end:
            prices[cursor] = 0.1
            cursor += timedelta(hours=1)
        return prices


def _coordinator(hass, client: FakeEstfeed, **options: Any) -> EstfeedCoordinator:
    coordinator = EstfeedCoordinator(
        hass=hass,
        client=client,  # type: ignore[arg-type]
        slug="home",
        options={CONF_RESOLUTION: Resolution.HOUR.value, CONF_BACKFILL_MONTHS: 1, **options},
    )
    coordinator.meters = [METER]
    return coordinator


async def _tick(hass, coordinator: EstfeedCoordinator) -> None:
    """Run one hourly update and let the recorder commit it, as an hour would."""
    await coordinator._async_update_data()
    await async_wait_recording_done(hass)


async def _last_sum(hass, statistic_id: str) -> float | None:
    """Latest cumulative ``sum`` the recorder holds for ``statistic_id``."""
    await async_wait_recording_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
    )
    rows = result.get(statistic_id)
    return rows[0]["sum"] if rows else None


@pytest.mark.parametrize(
    ("unit", "kwh", "m3"),
    [("kWh", 2.5, None), ("m³", None, 2.5)],
)
async def test_meter_statistics_are_accepted_by_the_recorder(hass, unit, kwh, m3):
    """Energy and gas rows must land in the recorder on every supported HA.

    HA before 2025.11 has no ``unit_class`` metadata column and rejects the
    whole import when the key is present.
    """
    stream = StatisticStream(
        statistic_id="estfeed:home_consumption_089n",
        name="home consumption",
        unit=unit,
        kind=Kind.CONSUMPTION,
    )
    interval = AccountingInterval(
        period_start=datetime(2026, 4, 27, 10, tzinfo=UTC),
        consumption_kwh=kwh,
        production_kwh=None,
        consumption_m3=m3,
        production_m3=None,
    )

    await async_write_meter_statistics(hass, stream, [interval], prior_sum=0.0)

    assert await _last_sum(hass, stream.statistic_id) == 2.5


async def test_hourly_tick_imports_only_hours_after_the_stored_series(hass, freezer):
    """Each tick must resume where the stored series ends.

    The recorder reports row timestamps in epoch seconds. Reading them as
    milliseconds put the resume point in January 1970, so every tick
    re-imported the last 30 days on top of the running total.
    """
    client = FakeEstfeed()
    coordinator = _coordinator(hass, client)
    freezer.move_to("2026-05-05 12:05:00+00:00")
    await _tick(hass, coordinator)  # empty history: 30 days = 720 hours
    assert await _last_sum(hass, CONSUMPTION_ID) == 720.0

    freezer.move_to("2026-05-05 13:05:00+00:00")
    await _tick(hass, coordinator)  # one new hour: 12:00-13:00

    assert client.requested_starts[-1] == datetime(2026, 5, 5, 12, tzinfo=UTC)
    assert await _last_sum(hass, CONSUMPTION_ID) == 721.0


async def test_cost_for_hours_missed_in_a_price_outage_is_written_on_recovery(hass, freezer):
    """A failed spot-price fetch must not leave a permanent hole in cost.

    Energy for the outage hour is still written, so the next tick must fetch
    from where the cost series ends, not from where the energy series ends.
    """
    client, nps = FakeEstfeed(), FakeNps()
    coordinator = _coordinator(hass, client, **{CONF_VAT_PERCENT: 0.0})
    coordinator.attach_nps_client(nps)  # type: ignore[arg-type]
    freezer.move_to("2026-05-05 12:05:00+00:00")
    await _tick(hass, coordinator)  # 720 hours, all priced

    freezer.move_to("2026-05-05 13:05:00+00:00")
    nps.down = True
    await _tick(hass, coordinator)  # 12:00 energy written, price fetch fails

    freezer.move_to("2026-05-05 14:05:00+00:00")
    nps.down = False
    await _tick(hass, coordinator)

    assert await _last_sum(hass, CONSUMPTION_ID) == 722.0
    # 722 hours x 1.0 kWh x 0.1 EUR/kWh
    assert await _last_sum(hass, COST_ID) == pytest.approx(72.2)

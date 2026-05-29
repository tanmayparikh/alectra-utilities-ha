"""DataUpdateCoordinator for Alectra Utilities."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .alectra_api import AlectraAuthError, AlectraApiError, AlectraClient, UsageRecord
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_CUSTOMER_NUMBER,
    CONF_METER_NUMBER,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Yesterday's data is incomplete (partial-day reads). Only data ≤ 2 days old is reliable.
_COMPLETE_DATA_LAG_DAYS = 2

# How far back to fetch hourly history on first run
_HISTORY_LOOKBACK_DAYS = 30


class AlectraData:
    """Aggregated data returned to sensor entities."""

    def __init__(
        self,
        latest_complete: UsageRecord | None,
        meter_number: str,
        rate_plan: str,
    ) -> None:
        self.latest_complete = latest_complete
        self.meter_number = meter_number
        self.rate_plan = rate_plan


def _statistic_id(account: str, meter: str) -> str:
    return f"{DOMAIN}:{account}_{meter}_energy_hourly"


_EASTERN = ZoneInfo("America/Toronto")


def _record_to_utc(record: UsageRecord) -> datetime:
    """
    Convert a UsageRecord read_date to UTC for HA statistics.

    The API returns timestamps with a wrong +01:00 offset (server bug).
    Strip the offset and reattach America/Toronto (Alectra only operates in Ontario).
    """
    naive = record.read_date.replace(tzinfo=None)
    return naive.replace(tzinfo=_EASTERN).astimezone(dt_util.UTC)


class AlectraCoordinator(DataUpdateCoordinator[AlectraData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._entry = entry
        self._client = AlectraClient(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            account_number=entry.data[CONF_ACCOUNT_NUMBER],
            customer_number=entry.data.get(CONF_CUSTOMER_NUMBER, ""),
            meter_number=entry.data.get(CONF_METER_NUMBER, ""),
        )

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> AlectraData:
        try:
            rate_plans = await self._client.get_rate_plan()
        except AlectraAuthError as exc:
            raise UpdateFailed(f"Authentication error: {exc}") from exc
        except AlectraApiError as exc:
            raise UpdateFailed(f"API error: {exc}") from exc

        meter_number = ""
        rate_plan = ""
        if rate_plans:
            meter_number = rate_plans[0].get("meterNumber", "")
            rate_plan = rate_plans[0].get("ratePlan", "")
            if meter_number and not self._client._meter:
                self._client._meter = meter_number

        # Latest complete day = today - 2 (yesterday's data is still partial)
        last_complete_day = date.today() - timedelta(days=_COMPLETE_DATA_LAG_DAYS)

        # Fetch a 2-day window; API To is exclusive so +1 ensures last_complete_day is included
        try:
            daily = await self._client.get_usage(
                last_complete_day,
                last_complete_day + timedelta(days=1),
                periodicity="DA",
            )
        except AlectraApiError as exc:
            raise UpdateFailed(f"Usage fetch error: {exc}") from exc

        # Filter to the exact target date (API may return boundary records)
        latest_complete = next(
            (r for r in daily if r.read_date.date() == last_complete_day), None
        )

        # Inject hourly statistics into recorder (non-blocking)
        await self._inject_hourly_statistics(meter_number, rate_plan)

        return AlectraData(
            latest_complete=latest_complete,
            meter_number=meter_number,
            rate_plan=rate_plan,
        )

    # ------------------------------------------------------------------
    # Statistics injection
    # ------------------------------------------------------------------

    async def _inject_hourly_statistics(
        self, meter_number: str, rate_plan: str
    ) -> None:
        account = self._entry.data[CONF_ACCOUNT_NUMBER]
        stat_id = _statistic_id(account, meter_number or self._client._meter)

        # Find last known statistic to determine fetch window and running sum
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, True, {"sum"}
        )

        if last_stats.get(stat_id):
            last_sum = last_stats[stat_id][0].get("sum") or 0.0
            # last start is a UTC timestamp (float or datetime)
            raw_start = last_stats[stat_id][0]["start"]
            if isinstance(raw_start, (int, float)):
                last_start_utc = datetime.fromtimestamp(raw_start, tz=dt_util.UTC)
            else:
                last_start_utc = dt_util.as_utc(raw_start)
            from_date = last_start_utc.date()
        else:
            last_sum = 0.0
            last_start_utc = None
            from_date = date.today() - timedelta(days=_HISTORY_LOOKBACK_DAYS)

        # to_date_api = yesterday: ensures last_complete_day (today-2) is included
        # (API To parameter is exclusive — need To=yesterday to get today-2 data)
        to_date_api = date.today() - timedelta(days=1)
        max_complete = date.today() - timedelta(days=_COMPLETE_DATA_LAG_DAYS)

        if from_date >= to_date_api:
            _LOGGER.debug("Alectra statistics up to date, nothing to inject")
            return

        try:
            records = await self._client.get_usage(
                from_date, to_date_api, periodicity="HH"
            )
        except AlectraApiError as exc:
            _LOGGER.warning("Failed to fetch hourly usage for statistics: %s", exc)
            return

        # Drop records from yesterday and beyond (incomplete data)
        records = [r for r in records if r.read_date.date() <= max_complete]

        # Filter to only records after last known stat
        if last_start_utc:
            new_records = [
                r for r in records if _record_to_utc(r) > last_start_utc
            ]
        else:
            new_records = records

        if not new_records:
            _LOGGER.debug("No new hourly records to inject")
            return

        new_records.sort(key=lambda r: r.read_date)

        running_sum = last_sum
        stats: list[StatisticData] = []
        for record in new_records:
            running_sum += record.consumption
            stats.append(
                StatisticData(
                    start=_record_to_utc(record),
                    state=record.consumption,
                    sum=running_sum,
                )
            )

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"Alectra {account} Hourly Energy",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_of_measurement="kWh",
        )

        async_add_external_statistics(self.hass, metadata, stats)
        _LOGGER.debug(
            "Injected %d hourly statistics records for %s (sum now %.3f kWh)",
            len(stats),
            stat_id,
            running_sum,
        )

"""Sensor platform for Alectra Utilities."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_METER_NUMBER, ATTR_RATE_PLAN, DOMAIN
from .coordinator import AlectraCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AlectraCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AlectraLatestDailySensor(coordinator, entry)])


class AlectraLatestDailySensor(CoordinatorEntity[AlectraCoordinator], SensorEntity):
    """Daily energy consumption for the most recent complete day (2 days ago)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_name = "Daily Energy (last complete day)"

    def __init__(self, coordinator: AlectraCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_latest_daily"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": f"Alectra {self._entry.data.get('account_number', '')}",
            "manufacturer": "Alectra Utilities",
        }

    @property
    def native_value(self) -> float | None:
        record = self.coordinator.data.latest_complete if self.coordinator.data else None
        return record.consumption if record else None

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        record = data.latest_complete
        attrs = {
            ATTR_METER_NUMBER: data.meter_number,
            ATTR_RATE_PLAN: data.rate_plan,
        }
        if record:
            attrs["read_date"] = record.read_date.date().isoformat()
            attrs["amount_dollars"] = record.amount
        return attrs

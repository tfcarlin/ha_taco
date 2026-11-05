"""Platform for sensor integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
)

from .src.taco_config_entry import TacoConfigEntry
from .src.taco_device_info import create_device_info, create_entity_id
from .src.taco_gatt_read_transform import (
    MAX_NETWORK_PANELS,
    THERMOSTAT_INPUT_STATUS,
    ZONE_STATUS,
    ZONE_COUNT,
    ZoneInfo,
    NETWORK_AUX1,
    NETWORK_AUX2,
)
from .src.callable_entity import CallableBinarySensor, CallableDescription

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)


def _panel_zone_count(data: dict[str, any], panel: int) -> int:
    """Return the zone count for a given panel index (0 = Master, 1..19 = Expansion)."""
    counts = data.get(ZONE_COUNT)
    if isinstance(counts, list):
        return counts[panel] if panel < len(counts) else 0
    if isinstance(counts, int):
        return counts if panel == 0 else 0
    return 6 if panel == 0 else 0


def _value_fn(data: dict[str, any], key: str, panel: int, index: int) -> bool | None:
    """Returns the zone value at panel (0-based) and zone index (1-based)."""
    value = data.get(key, None)
    if not value:
        return None

    if isinstance(value, list):
        if panel >= len(value) or value[panel] is None:
            return None
        value = value[panel]
    elif panel != 0:
        return None

    return getattr(value, f"zone{index}")


def _key_prefix(panel: int) -> str:
    """Return key prefix: empty for Master (panel 0), EXP{panel}_ for expansion panels."""
    return "" if panel == 0 else f"EXP{panel}_"


def _make_zone_sensor(panel: int, index: int) -> CallableDescription:
    """Make a zone sensor, panel is 0-based, index is 1-based."""
    prefix = _key_prefix(panel)

    return CallableDescription(
        entity_description=BinarySensorEntityDescription(
            key=f"{prefix}ZONE_{index}",
            device_class=BinarySensorDeviceClass.RUNNING,
        ),
        exists_fn=lambda data, p=panel, idx=index: _panel_zone_count(data, p) >= idx,
        value_fn=lambda data, p=panel, idx=index: _value_fn(data, ZONE_STATUS, p, idx),
    )


def _make_thermostat_sensor(panel: int, index: int) -> CallableDescription:
    """Make a thermostat sensor, panel is 0-based, index is 1-based."""
    prefix = _key_prefix(panel)

    return CallableDescription(
        entity_description=BinarySensorEntityDescription(
            key=f"{prefix}THERMOSTAT_{index}",
            device_class=None,  # Deliberately none
        ),
        exists_fn=lambda data, p=panel, idx=index: _panel_zone_count(data, p) >= idx,
        value_fn=lambda data, p=panel, idx=index: _value_fn(
            data, THERMOSTAT_INPUT_STATUS, p, idx
        ),
    )


def _make_aux_sensor(index: int) -> CallableDescription:
    """Make an aux sensor, index is 1 based."""

    key = {
        1: NETWORK_AUX1,
        2: NETWORK_AUX2,
    }.get(index, None)

    return CallableDescription(
        entity_description=BinarySensorEntityDescription(
            key=f"AUX_{index}",
            device_class=BinarySensorDeviceClass.RUNNING,
        ),
        exists_fn=lambda _data: True,
        value_fn=lambda data: data.get(key, None),
    )


_SENSORS: tuple[CallableDescription, ...] = tuple(
    sensor
    for panel in range(MAX_NETWORK_PANELS)
    for sensor in (
        *(_make_zone_sensor(panel, idx) for idx in range(1, 7)),
        *(_make_thermostat_sensor(panel, idx) for idx in range(1, 7)),
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TacoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor based on a config entry."""
    _LOGGER.debug("Setting up Taco binary sensors: %s", entry)

    taco_runtime_data = entry.runtime_data
    update_coordinator = taco_runtime_data.update_coordinator
    data = update_coordinator.data

    # Note, we aren't actually guaranteed to have any
    # data at this point so read that value with caution!
    #
    # Home Assistant doesn't like it when we try to wait
    # for the data, or throw ConfigEntryNotReady exceptions.

    async_add_entities(
        CallableBinarySensor(
            update_coordinator,
            description,
            name=description.entity_description.key,
            unique_id=create_entity_id(entry, description),
            device_info=create_device_info(DOMAIN, entry),
        )
        for description in _SENSORS
        if description.exists_fn(data)
    )

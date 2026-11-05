from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from .src.taco_init import send_initial_write_requests, setup_write_loop
from .src.ble_data_update_coordinator import BleDataUpdateCoordinator
from .src.taco_config_entry import TacoConfigEntry, TacoRuntimeData
from .src.taco_gatt_read_transform import TACO_PRODUCT_INFO
from .src.ble_config_flow import BLE_CONF_DEVICE_ADDRESS

from .const import DOMAIN, taco_gatt
from .config_flow import CONF_TACO_DEVICE_PASSWORD

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


async def async_setup_entry(hass: HomeAssistant, entry: TacoConfigEntry) -> bool:
    """Set up Taco from a config entry."""
    _LOGGER.debug("Setting up Taco: %s", entry)

    address = entry.data.get(BLE_CONF_DEVICE_ADDRESS)
    password = entry.data.get(CONF_TACO_DEVICE_PASSWORD)

    # Initialize the runtime data
    entry.runtime_data = TacoRuntimeData(password)
    entry.runtime_data.ble_coordinator = BleDataUpdateCoordinator(
        hass, DOMAIN, taco_gatt, address
    )

    try:
        # Initial BLE connect and read of index/subscribe characteristics
        await entry.runtime_data.ble_coordinator.connect()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to connect to Taco device {address}: {err}") from err

    entry.runtime_data.update_coordinator = (
        entry.runtime_data.ble_coordinator.update_coordinator
    )

    # Forward setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Very important Taco setup.
    initial_data = await send_initial_write_requests(entry.runtime_data)

    # ZVC zone valve controls reject the force-zone command
    # (ATT 0xFC Write Request Rejected) even with a valid password,
    # so run those read-only: no write loop and no switches.
    product = initial_data.get(TACO_PRODUCT_INFO)
    if product is not None and product.family == "ZVC":
        entry.runtime_data.supports_force_zone = False
        _LOGGER.warning(
            "Taco device is a %s (%s, family %s). Zone force-on switches are disabled because ZVC controls reject this command.",
            product.model,
            product.name,
            product.family,
        )
    else:
        entry.runtime_data.remove_listeners = await setup_write_loop(
            hass, entry.runtime_data
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: TacoConfigEntry) -> bool:
    """Unload a config entry."""
    if hass.config_entries.async_unload_platforms is not None:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if entry.runtime_data.remove_listeners is not None:
        entry.runtime_data.remove_listeners()
    if entry.runtime_data.ble_coordinator.shutdown is not None:
        await entry.runtime_data.ble_coordinator.shutdown()

    return True

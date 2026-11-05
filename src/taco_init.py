from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.components.persistent_notification import async_create

from .taco_gatt_write_transform import (
    PROVIDE_PASSWORD,
    PING_NETWORK_DEVICE_INDEX,
    WriteRequest,
    FORCE_ZONE_ON,
    MaskedString,
)
from .taco_config_entry import TacoRuntimeData
from .taco_gatt_read_transform import ZoneInfo, NETWORK_DEVICE_INDEX
from .ble_data_update_coordinator import BleDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def _validate_ping(ble_coordinator: BleDataUpdateCoordinator) -> dict:
    """Reads then writes the mac address. Returns the data read."""
    try:
        results = await ble_coordinator.poll()
        network_device_index = results.get(NETWORK_DEVICE_INDEX)
        if network_device_index is None:
            raise Exception(
                "Could not get network device index, is the device online and connected?"
            )

        await ble_coordinator.write(
            [WriteRequest(PING_NETWORK_DEVICE_INDEX, extra=network_device_index)]
        )
    except Exception as err:
        raise ConfigEntryNotReady(err) from err
    return results


async def _validate_password(
    password: MaskedString | None, ble_coordinator: BleDataUpdateCoordinator
):
    """Checks the password is legal and actually correct for this particular device."""

    if not password or not password.value:
        _LOGGER.warning("No password provided to Taco, some features may not work.")
        return

    if len(password.value) > 20:
        raise ConfigEntryAuthFailed(
            "Cannot have a Taco password more than 20 characters."
        )

    try:
        await ble_coordinator.write([WriteRequest(PROVIDE_PASSWORD, extra=password)])
    except Exception as err:
        raise ConfigEntryAuthFailed(err) from err


async def send_initial_write_requests(runtime_data: TacoRuntimeData) -> dict:
    """Starts communication with the taco, validating passwords and connections.

    Returns the data read during the ping (includes the product info).
    """

    results = await _validate_ping(runtime_data.ble_coordinator)
    await _validate_password(runtime_data.password, runtime_data.ble_coordinator)
    return results


def _create_write_requests(
    runtime_data: TacoRuntimeData,
) -> tuple[bool, list[WriteRequest]]:
    """The write action that should take place upon a successful loop."""

    password = runtime_data.password
    if not password or not password.value:
        return False, []

    zone_info = ZoneInfo(
        zone1=runtime_data.force_zone_on[0],
        zone2=runtime_data.force_zone_on[1],
        zone3=runtime_data.force_zone_on[2],
        zone4=runtime_data.force_zone_on[3],
        zone5=runtime_data.force_zone_on[4],
        zone6=runtime_data.force_zone_on[5],
    )

    keep_alive = (
        zone_info.zone1
        or zone_info.zone2
        or zone_info.zone3
        or zone_info.zone4
        or zone_info.zone5
        or zone_info.zone6
    )

    return keep_alive, [
        WriteRequest(PROVIDE_PASSWORD, extra=password),
        WriteRequest(FORCE_ZONE_ON, extra=zone_info),
    ]


async def _loop(state: dict[str, any], runtime_data: TacoRuntimeData):
    """A single loop for the writer."""

    ble_coordinator = runtime_data.ble_coordinator
    if not ble_coordinator.is_connected():
        return

    keep_alive, write_requests = _create_write_requests(runtime_data)

    current_time = datetime.now()
    last_keep_alive = state.get("last_keep_alive", datetime.min)
    duration = current_time - last_keep_alive

    if keep_alive:
        if duration >= timedelta(seconds=2):
            await ble_coordinator.write(write_requests)
            state["last_keep_alive"] = current_time
    else:
        if duration < timedelta(seconds=1):
            await ble_coordinator.write(write_requests)
            state["last_keep_alive"] = current_time


async def setup_write_loop(hass: HomeAssistant, runtime_data: TacoRuntimeData):
    """Sets up the loop(s) that write data back to the device."""

    state = {}
    errors = []
    callback = None

    async def _stateless_loop(_time):
        try:
            await _loop(state, runtime_data)
            errors.clear()
        except Exception as err:
            errors.append(err)
            if len(errors) == 3:
                callback()
                message = "Multiple failures in ha_taco write loop, this usually means an authentication issue. Try setting the password and then restart the integration."
                async_create(
                    hass,
                    title="Taco Integration Error",
                    message=message,
                    notification_id="ha_taco_sync_error",
                )
                raise ExceptionGroup(message, errors)

    callback = async_track_time_interval(
        hass,
        _stateless_loop,
        timedelta(seconds=1),
    )
    return callback

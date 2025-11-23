"""Sensor platform for Shelly Services."""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

DOMAIN = "shelly_services"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for all Shelly devices."""
    
    device_registry = dr.async_get(hass)
    
    # Find all unique Shelly devices
    shelly_devices = {}
    
    for device in device_registry.devices.values():
        if not device.identifiers:
            continue
        
        for entry_id in device.config_entries:
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry and config_entry.domain == "shelly":
                host = config_entry.data.get("host")
                if host and host not in shelly_devices:
                    shelly_devices[host] = {
                        "device": device,
                        "host": host,
                        "entry": config_entry,
                    }
                break
    
    # Create sensors for each device
    entities = []
    for device_info in shelly_devices.values():
        entities.append(ShellyIPSensor(hass, device_info))
        entities.append(ShellyConnectivitySensor(hass, device_info))
    
    async_add_entities(entities, False)
    
    _LOGGER.info("Added %d sensors (IP + Connectivity)", len(entities))


class ShellyIPSensor(SensorEntity):
    """Sensor showing Shelly device IP address."""
    
    def __init__(self, hass: HomeAssistant, device_info: dict) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._device = device_info["device"]
        self._host = device_info["host"]
        self._entry = device_info["entry"]
        
        # Entity setup
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{self._device.id}_ip_address"
        self._attr_name = "IP Address"
        self._attr_icon = "mdi:ip-network"
        
        # Link to Shelly device
        self._attr_device_info = {
            "identifiers": self._device.identifiers,
        }
        
        # State
        self._attr_native_value = self._host
        self._attr_available = True


class ShellyConnectivitySensor(SensorEntity):
    """Sensor showing Shelly connectivity config (CoIoT for Gen1, WebSocket for Gen2/3)."""
    
    def __init__(self, hass: HomeAssistant, device_info: dict) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._device = device_info["device"]
        self._host = device_info["host"]
        self._entry = device_info["entry"]
        
        # Entity setup
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{self._device.id}_connectivity_config"
        self._attr_name = "Connectivity Config"
        self._attr_icon = "mdi:lan-connect"
        
        # Link to Shelly device
        self._attr_device_info = {
            "identifiers": self._device.identifiers,
        }
        
        # State - will be loaded
        self._attr_native_value = None
        self._attr_available = True
    
    async def async_added_to_hass(self) -> None:
        """Load connectivity config when added."""
        await self._load_connectivity_config()
        self.async_write_ha_state()
    
    async def _load_connectivity_config(self) -> None:
        """Load connectivity config based on device generation."""
        # Get credentials from Shelly integration config
        username = self._entry.data.get("username", "")
        password = self._entry.data.get("password", "")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Detect generation first
                gen = await self._detect_generation(session)
                
                if gen >= 2:
                    # Gen2/3: Get outbound WebSocket
                    await self._load_gen2_websocket(session, username, password)
                else:
                    # Gen1: Get CoIoT peer
                    await self._load_gen1_coiot(session, username, password)
        
        except Exception as err:
            _LOGGER.debug(
                "Could not load connectivity config for '%s': %s",
                self._device.name,
                err,
            )
            self._attr_native_value = "Unknown"
    
    async def _detect_generation(self, session) -> int:
        """Detect device generation."""
        try:
            url = f"http://{self._host}/shelly"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("gen", 1)
        except:
            pass
        return 1
    
    async def _load_gen1_coiot(self, session, username: str, password: str) -> None:
        """Load CoIoT peer for Gen1 devices."""
        url = f"http://{self._host}/settings"
        
        # Setup auth if available
        auth = None
        if password and username:
            auth = aiohttp.BasicAuth(login=username, password=password)
        
        async with session.get(
            url,
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                coiot = data.get("coiot", {})
                peer = coiot.get("peer", "")
                
                if not peer or peer == "":
                    self._attr_native_value = "CoIoT: mcast (multicast)"
                else:
                    self._attr_native_value = f"CoIoT: unicast {peer}"
                
                _LOGGER.debug(
                    "Gen1 CoIoT for '%s': %s",
                    self._device.name,
                    self._attr_native_value,
                )
            elif resp.status == 401:
                self._attr_native_value = "CoIoT: Unknown (auth required)"
    
    async def _load_gen2_websocket(self, session, username: str, password: str) -> None:
        """Load outbound WebSocket for Gen2/3 devices."""
        url = f"http://{self._host}/rpc/Sys.GetConfig"
        
        # Setup auth if available
        auth = None
        if password and username:
            auth = aiohttp.BasicAuth(login=username, password=password)
        
        try:
            async with session.post(
                url,
                json={},
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Try to get WebSocket server config
                    ws_config = data.get("ws", {})
                    ws_server = ws_config.get("server", "")
                    
                    if ws_server:
                        self._attr_native_value = f"WebSocket: {ws_server}"
                        _LOGGER.debug(
                            "Gen2/3 WebSocket for '%s': %s",
                            self._device.name,
                            ws_server,
                        )
                    else:
                        # No explicit WebSocket configured - this is normal
                        self._attr_native_value = "WebSocket: Not configured (uses mDNS)"
                        _LOGGER.debug(
                            "Gen2/3 WebSocket for '%s': Not explicitly configured",
                            self._device.name,
                        )
                
                elif resp.status == 401:
                    self._attr_native_value = "WebSocket: Unknown (auth required)"
        
        except Exception as err:
            _LOGGER.debug(
                "Error getting Gen2/3 WebSocket for '%s': %s",
                self._device.name,
                err,
            )
            # Default for Gen2/3 without explicit config
            self._attr_native_value = "WebSocket: Auto-discovery (mDNS)"

"""Switch platform for Shelly Services authentication control."""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

_LOGGER = logging.getLogger(__name__)

DOMAIN = "shelly_services"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up auth switches for all Shelly devices."""
    
    device_registry = dr.async_get(hass)
    credentials = hass.data[DOMAIN][entry.entry_id]
    
    # Find all unique Shelly devices (by IP)
    shelly_devices = {}
    skipped_count = 0
    
    for device in device_registry.devices.values():
        # Skip devices without identifiers
        if not device.identifiers:
            skipped_count += 1
            _LOGGER.debug(
                "Skipping device '%s' (no identifiers)",
                device.name or "Unknown",
            )
            continue
        
        for entry_id in device.config_entries:
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry and config_entry.domain == "shelly":
                host = config_entry.data.get("host")
                if not host:
                    _LOGGER.debug(
                        "Skipping Shelly device '%s' (no host in config)",
                        device.name or "Unknown",
                    )
                    break
                if host not in shelly_devices:
                    # Try to get coordinator from Shelly integration
                    coordinator = None
                    if "shelly" in hass.data and entry_id in hass.data["shelly"]:
                        coordinator = hass.data["shelly"][entry_id].get("coordinator")
                    
                    shelly_devices[host] = {
                        "device": device,
                        "host": host,
                        "entry": config_entry,
                        "coordinator": coordinator,
                    }
                break
    
    _LOGGER.info(
        "Found %d unique Shelly devices (skipped %d without identifiers)",
        len(shelly_devices),
        skipped_count,
    )
    
    # Create switch for each device
    entities = []
    for device_info in shelly_devices.values():
        entities.append(
            ShellyAuthSwitch(hass, device_info, credentials)
        )
    
    async_add_entities(entities, False)


class ShellyAuthSwitch(SwitchEntity):
    """Switch to control Shelly authentication."""
    
    def __init__(
        self,
        hass: HomeAssistant,
        device_info: dict,
        credentials: dict,
    ) -> None:
        """Initialize the switch."""
        self.hass = hass
        self._device = device_info["device"]
        self._host = device_info["host"]
        self._entry = device_info["entry"]
        self._credentials = credentials
        self._coordinator = device_info.get("coordinator")
        
        # Entity setup
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{self._device.id}_auth_control"
        self._attr_name = "Authentication"
        self._attr_icon = "mdi:shield-lock"
        
        # Link to Shelly device
        self._attr_device_info = {
            "identifiers": self._device.identifiers,
        }
        
        # Initial state - will be updated in async_added_to_hass
        self._attr_is_on = None
        self._attr_available = True
    
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        # Try to get initial state from coordinator
        if self._coordinator and hasattr(self._coordinator, "data"):
            self._update_from_coordinator()
            
            # Subscribe to coordinator updates for automatic state sync
            self.async_on_remove(
                self._coordinator.async_add_listener(
                    self._handle_coordinator_update
                )
            )
            _LOGGER.debug(
                "Subscribed to coordinator updates for '%s'",
                self._device.name,
            )
        else:
            # Fallback: check via /shelly
            await self._check_auth_status()
        
        self.async_write_ha_state()
    
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_coordinator()
        self.async_write_ha_state()
        
        _LOGGER.debug(
            "Coordinator update for '%s': auth=%s",
            self._device.name,
            self._attr_is_on,
        )
    
    def _update_from_coordinator(self) -> None:
        """Update state from Shelly coordinator data."""
        try:
            if not self._coordinator or not hasattr(self._coordinator, "data"):
                return
            
            data = self._coordinator.data
            
            # Try to get auth status from coordinator data
            # Gen2/3 devices
            if hasattr(data, "get") and callable(data.get):
                auth_en = data.get("auth_en")
                auth = data.get("auth")
                
                if auth_en is not None:
                    self._attr_is_on = auth_en
                    _LOGGER.debug(
                        "State from coordinator for '%s': auth_en=%s",
                        self._device.name,
                        auth_en,
                    )
                elif auth is not None:
                    self._attr_is_on = auth
                    _LOGGER.debug(
                        "State from coordinator for '%s': auth=%s",
                        self._device.name,
                        auth,
                    )
        except Exception as err:
            _LOGGER.debug(
                "Could not read coordinator data for '%s': %s",
                self._device.name,
                err,
            )
    
    async def _check_auth_status(self) -> None:
        """Check auth status via /shelly endpoint."""
        try:
            # Get credentials from Shelly integration (for reading)
            username = self._entry.data.get("username", "")
            password = self._entry.data.get("password", "")
            
            # Setup auth
            auth = None
            if password and username:
                auth = aiohttp.BasicAuth(login=username, password=password)
            
            async with aiohttp.ClientSession() as session:
                url = f"http://{self._host}/shelly"
                async with session.get(
                    url,
                    auth=auth,
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        gen = data.get("gen")
                        
                        # Check auth field based on generation
                        if gen == 2 or gen == 3:
                            auth_enabled = data.get("auth_en")
                        else:
                            auth_enabled = data.get("auth")
                        
                        if auth_enabled is not None:
                            self._attr_is_on = auth_enabled
                            _LOGGER.debug(
                                "Initial state for '%s': auth=%s",
                                self._device.name,
                                auth_enabled,
                            )
                        else:
                            # Fallback
                            auth_enabled = data.get("auth_en") or data.get("auth")
                            if auth_enabled is not None:
                                self._attr_is_on = auth_enabled
                    
                    elif resp.status == 401:
                        # 401 = auth is enabled
                        self._attr_is_on = True
                        _LOGGER.debug(
                            "Initial state for '%s': auth=True (HTTP 401)",
                            self._device.name,
                        )
        
        except Exception as err:
            _LOGGER.debug(
                "Could not check auth status for '%s': %s",
                self._device.name,
                err,
            )
    
    async def async_turn_on(self, **kwargs) -> None:
        """Enable authentication."""
        await self._set_auth(enable=True)
    
    async def async_turn_off(self, **kwargs) -> None:
        """Disable authentication."""
        await self._set_auth(enable=False)
    
    async def _set_auth(self, enable: bool) -> None:
        """Set authentication on/off."""
        username = self._credentials.get("username", "admin")
        password = self._credentials.get("password", "")
        
        if not password:
            _LOGGER.error(
                "No password configured for device '%s'",
                self._device.name,
            )
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                # Detect generation
                try:
                    info_url = f"http://{self._host}/shelly"
                    async with session.get(
                        info_url,
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            gen = data.get("gen", 1)
                        else:
                            gen = 1
                except:
                    gen = 1  # Assume Gen1 on error
                
                # Apply auth based on generation
                if gen >= 2:
                    # Gen2/3: RPC API
                    url = f"http://{self._host}/rpc/Sys.SetAuth"
                    
                    if enable:
                        # Enable auth
                        payload = {
                            "user": username,
                            "pass": password,
                        }
                        auth = None
                    else:
                        # Disable auth (need current credentials)
                        payload = {"user": None}
                        auth = aiohttp.BasicAuth(login=username, password=password)
                    
                    async with session.post(
                        url,
                        json=payload,
                        auth=auth,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            action = "enabled" if enable else "disabled"
                            _LOGGER.info(
                                "Auth %s on Gen%d device '%s' at %s",
                                action,
                                gen,
                                self._device.name,
                                self._host,
                            )
                            # Update state optimistically
                            self._attr_is_on = enable
                            self.async_write_ha_state()
                        else:
                            _LOGGER.error(
                                "Failed to %s auth on Gen%d device '%s': HTTP %d",
                                "enable" if enable else "disable",
                                gen,
                                self._device.name,
                                resp.status,
                            )
                
                else:
                    # Gen1: REST API
                    url = f"http://{self._host}/settings/login"
                    
                    if enable:
                        # Enable auth
                        params = {
                            "enabled": "1",
                            "username": username,
                            "password": password,
                        }
                        auth = None
                    else:
                        # Disable auth (need current credentials)
                        params = {"enabled": "0"}
                        auth = aiohttp.BasicAuth(login=username, password=password)
                    
                    async with session.get(
                        url,
                        params=params,
                        auth=auth,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            action = "enabled" if enable else "disabled"
                            _LOGGER.info(
                                "Auth %s on Gen1 device '%s' at %s",
                                action,
                                self._device.name,
                                self._host,
                            )
                            # Update state optimistically
                            self._attr_is_on = enable
                            self.async_write_ha_state()
                        else:
                            _LOGGER.error(
                                "Failed to %s auth on Gen1 device '%s': HTTP %d",
                                "enable" if enable else "disable",
                                self._device.name,
                                resp.status,
                            )
        
        except Exception as err:
            _LOGGER.error(
                "Error setting auth on device '%s' at %s: %s",
                self._device.name,
                self._host,
                err,
            )

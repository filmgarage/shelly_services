"""Config flow for Shelly Services."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

DOMAIN = "shelly_services"

DEFAULT_USERNAME = "admin"


class ShellyServicesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Shelly Services."""
    
    VERSION = 1
    
    async def async_step_user(self, user_input=None):
        """Handle user initiated config flow."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("username", default=DEFAULT_USERNAME): str,
                    vol.Required("password"): str,
                }),
            )
        
        return self.async_create_entry(
            title="Shelly Services",
            data=user_input,
        )
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow."""
        return ShellyServicesOptionsFlow(config_entry)


class ShellyServicesOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Shelly Services."""
    
    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
    
    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            # Update config entry data
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, **user_input},
            )
            return self.async_create_entry(title="", data={})
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    "username",
                    default=self.config_entry.data.get("username", "admin")
                ): str,
                vol.Required(
                    "password",
                    default=self.config_entry.data.get("password", "")
                ): str,
            }),
        )

"""配置流程：引导用户把电饭煲加进来。

不需要让用户填任何密钥 —— 账号与云端通道直接复用已配置好的官方
 Xiaomi Home (xiaomi_home) 集成。
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DID,
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
    SUPPORTED_MODEL_PREFIXES,
    XIAOMI_HOME_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _discover_cookers(hass) -> list[dict[str, Any]]:
    """从官方 xiaomi_home 集成里找出所有电饭煲。"""
    clients: dict[str, Any] = (hass.data.get(XIAOMI_HOME_DOMAIN) or {}).get(
        "miot_clients", {})
    found: list[dict[str, Any]] = []
    for client in clients.values():
        for did, info in (getattr(client, "device_list", None) or {}).items():
            model = str(info.get("model") or "")
            if model.startswith(SUPPORTED_MODEL_PREFIXES):
                found.append({
                    CONF_DID: did,
                    CONF_MODEL: model,
                    CONF_NAME: info.get("name") or model,
                    "online": info.get("online"),
                    "manufacturer": info.get("manufacturer"),
                })
    return found


class ChunmiCookerConfigFlow(ConfigFlow, domain=DOMAIN):
    """引导式添加。"""

    VERSION = 1

    def __init__(self) -> None:
        self._cookers: list[dict[str, Any]] = []
        self._picked: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries(XIAOMI_HOME_DOMAIN):
            return self.async_abort(reason="xiaomi_home_not_configured")

        self._cookers = _discover_cookers(self.hass)
        if not self._cookers:
            return self.async_abort(reason="no_cooker_found")

        # 只有一个就直接确认，多个让用户挑
        if len(self._cookers) == 1:
            return await self._async_confirm(self._cookers[0])

        options = [
            SelectOptionDict(
                value=c[CONF_DID],
                label=f"{c[CONF_NAME]} ({c[CONF_MODEL]})",
            )
            for c in self._cookers
        ]
        if user_input is not None:
            did = user_input[CONF_DID]
            picked = next(c for c in self._cookers if c[CONF_DID] == did)
            return await self._async_confirm(picked)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_DID): SelectSelector(SelectSelectorConfig(
                    options=options, mode=SelectSelectorMode.DROPDOWN)),
            }),
            description_placeholders={"count": str(len(self._cookers))},
        )

    async def _async_confirm(self, cooker: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(cooker[CONF_DID])
        self._abort_if_unique_id_configured()
        self._picked = cooker
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": cooker[CONF_NAME],
                "model": cooker[CONF_MODEL],
                "state": "在线" if cooker.get("online") else "离线/休眠",
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._picked is not None
        if user_input is None:
            # 直接展示确认页
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "name": self._picked[CONF_NAME],
                    "model": self._picked[CONF_MODEL],
                    "state": "在线" if self._picked.get("online") else "离线/休眠",
                },
            )
        return self.async_create_entry(
            title=self._picked[CONF_NAME],
            data={
                CONF_DID: self._picked[CONF_DID],
                CONF_MODEL: self._picked[CONF_MODEL],
                CONF_NAME: self._picked[CONF_NAME],
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """允许重新选择设备。"""
        return await self.async_step_user(user_input)

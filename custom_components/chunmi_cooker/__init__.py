"""Chunmi 电饭煲 for Home Assistant.

复用官方 Xiaomi Home (xiaomi_home) 集成已经配置好的账号与云端通道，
不需要用户再填任何密钥。

提供：
  * 状态实体（状态 / 菜单 / 剩余时间 / 预约完成时间 / 口感 / 故障…）
  * 服务：set_reservation / start_now / cancel / get_status / list_recipes
  * 食谱与「加热曲线」全部从淳米官方云动态获取（不硬编码）
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DID,
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_CANCEL,
    SERVICE_GET_STATUS,
    SERVICE_LIST_RECIPES,
    SERVICE_SET_RESERVATION,
    SERVICE_START_NOW,
    XIAOMI_HOME_DOMAIN,
)
from .coordinator import ChunmiCookerCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


# --------------------------------------------------------------------- 工具
def _clients(hass: HomeAssistant) -> dict[str, Any]:
    return (hass.data.get(XIAOMI_HOME_DOMAIN) or {}).get("miot_clients", {})


def _find_miot_client(hass: HomeAssistant, did: str) -> Any | None:
    for client in _clients(hass).values():
        if did in (getattr(client, "device_list", None) or {}):
            return client
    return None


def _all_coordinators(hass: HomeAssistant) -> dict[str, ChunmiCookerCoordinator]:
    return hass.data.get(DOMAIN, {})


def _resolve_coordinator(
    hass: HomeAssistant, did: str | None
) -> ChunmiCookerCoordinator:
    """按 did 取协调器；没给 did 且只有一个实例时就用它。"""
    store = _all_coordinators(hass)
    if did:
        for coord in store.values():
            if coord.did == did:
                return coord
        raise HomeAssistantError(f"没有 did={did} 的电饭煲实例")

    real = [c for c in store.values() if isinstance(c, ChunmiCookerCoordinator)]
    if not real:
        raise HomeAssistantError("还没有添加任何电饭煲")
    if len(real) > 1:
        raise HomeAssistantError("添加了多个电饭煲，请指定 target.did")
    return real[0]


# --------------------------------------------------------------------- 服务
SET_RESERVATION_SCHEMA = vol.Schema({
    vol.Required("menu"): cv.string,
    vol.Optional("finish_time"): cv.string,
    vol.Optional("delay_minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
    vol.Optional("cook_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
    vol.Optional("taste"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
    vol.Optional("auto_keep_warm"): cv.boolean,
    vol.Optional("upload", default=True): cv.boolean,
    vol.Optional("did"): cv.string,
})

START_NOW_SCHEMA = vol.Schema({
    vol.Required("menu"): cv.string,
    vol.Optional("cook_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
    vol.Optional("taste"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2)),
    vol.Optional("auto_keep_warm"): cv.boolean,
    vol.Optional("did"): cv.string,
})

TARGET_SCHEMA = vol.Schema({vol.Optional("did"): cv.string})
LIST_RECIPES_SCHEMA = vol.Schema({
    vol.Optional("refresh", default=False): cv.boolean,
    vol.Optional("did"): cv.string,
})


async def _svc_set_reservation(hass: HomeAssistant, call: ServiceCall) -> dict:
    data = dict(call.data)
    coord = _resolve_coordinator(hass, data.pop("did", None))
    return await coord.async_set_reservation(
        menu=data["menu"],
        finish_time=data.get("finish_time"),
        delay_minutes=data.get("delay_minutes"),
        cook_minutes=data.get("cook_minutes"),
        taste=data.get("taste"),
        auto_keep_warm=data.get("auto_keep_warm"),
        upload=data.get("upload", True),
    )


async def _svc_start_now(hass: HomeAssistant, call: ServiceCall) -> dict:
    data = dict(call.data)
    coord = _resolve_coordinator(hass, data.pop("did", None))
    return await coord.async_start_now(
        menu=data["menu"],
        cook_minutes=data.get("cook_minutes"),
        taste=data.get("taste"),
        auto_keep_warm=data.get("auto_keep_warm"),
    )


async def _svc_cancel(hass: HomeAssistant, call: ServiceCall) -> dict:
    coord = _resolve_coordinator(hass, call.data.get("did"))
    return {"action_out": await coord.async_cancel()}


async def _svc_get_status(hass: HomeAssistant, call: ServiceCall) -> dict:
    coord = _resolve_coordinator(hass, call.data.get("did"))
    await coord.async_request_refresh()
    return dict(coord.data or {})


async def _svc_list_recipes(hass: HomeAssistant, call: ServiceCall) -> dict:
    coord = _resolve_coordinator(hass, call.data.get("did"))
    recipes = await coord.async_get_recipes(force=call.data.get("refresh", False))
    return {
        "count": len(recipes),
        "recipes": [{k: v for k, v in r.items() if k != "cookcode"} for r in recipes],
        "note": "cookcode（含加热曲线）已省略；调用 set_reservation 时会自动使用",
    }


# --------------------------------------------------------------------- 生命周期
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def _register(name, schema, handler) -> None:
        if hass.services.has_service(DOMAIN, name):
            return

        async def _run(call: ServiceCall):
            return await handler(hass, call)

        hass.services.async_register(
            DOMAIN, name, _run,
            schema=schema, supports_response=SupportsResponse.OPTIONAL)

    await _register(SERVICE_SET_RESERVATION, SET_RESERVATION_SCHEMA, _svc_set_reservation)
    await _register(SERVICE_START_NOW, START_NOW_SCHEMA, _svc_start_now)
    await _register(SERVICE_CANCEL, TARGET_SCHEMA, _svc_cancel)
    await _register(SERVICE_GET_STATUS, TARGET_SCHEMA, _svc_get_status)
    await _register(SERVICE_LIST_RECIPES, LIST_RECIPES_SCHEMA, _svc_list_recipes)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    did = entry.data[CONF_DID]
    model = entry.data[CONF_MODEL]
    name = entry.data.get(CONF_NAME) or model

    client = _find_miot_client(hass, did)
    if client is None:
        _LOGGER.error(
            "在 xiaomi_home 里找不到设备 %s；请确认该设备仍在小爱/米家账号下并已同步", did)
        return False

    coordinator = ChunmiCookerCoordinator(
        hass, miot_client=client, did=did, model=model, name=name)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 把实体挂到官方集成建的同一个设备上，UI 里就是一个设备
    device_registry = dr.async_get(hass)
    if device := device_registry.async_get_device(
        identifiers={(XIAOMI_HOME_DOMAIN, did)}
    ):
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, did)},
            via_device=(XIAOMI_HOME_DOMAIN, did),
            name=name,
            model=model,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded

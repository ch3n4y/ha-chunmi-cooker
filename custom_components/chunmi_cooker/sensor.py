"""状态实体。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    FAULT_NAMES,
    RECIPE_TYPES,
    STATUS_NAMES,
    STATUS_NAMES_ZH,
    TASTE_NAMES,
    TASTE_NAMES_ZH,
    XIAOMI_HOME_DOMAIN,
)
from .coordinator import ChunmiCookerCoordinator


@dataclass(frozen=True, kw_only=True)
class CookerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any] = lambda data: None
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _status_zh(data: dict[str, Any]) -> str | None:
    value = data.get("status")
    return STATUS_NAMES_ZH.get(value) if isinstance(value, int) else None


SENSORS: tuple[CookerSensorDescription, ...] = (
    CookerSensorDescription(
        key="status",
        name="工作状态",
        icon="mdi:rice",
        value_fn=_status_zh,
        attrs_fn=lambda d: {
            "code": d.get("status"),
            "status": STATUS_NAMES.get(d.get("status")),
        },
    ),
    CookerSensorDescription(
        key="menu_name",
        name="当前菜单",
        icon="mdi:silverware-fork-knife",
        value_fn=lambda d: d.get("menu_name"),
        attrs_fn=lambda d: {"menu_id": d.get("menu_id")},
    ),
    CookerSensorDescription(
        key="cook_total_time",
        name="烹饪时长",
        icon="mdi:timer-sand",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: d.get("cook_total_time"),
    ),
    CookerSensorDescription(
        key="left_time",
        name="剩余时长",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda d: d.get("left_time"),
    ),
    CookerSensorDescription(
        key="pre_left_time",
        name="预约剩余时间",
        icon="mdi:clock-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: d.get("pre_left_time"),
    ),
    CookerSensorDescription(
        key="keepwarm_time",
        name="保温时长",
        icon="mdi:thermometer-lines",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: d.get("keepwarm_time"),
    ),
    CookerSensorDescription(
        key="finish_timestamp",
        name="烹饪完成时间",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.get("finish_time"),
        attrs_fn=lambda d: {"raw_timestamp": d.get("finish_timestamp")},
    ),
    CookerSensorDescription(
        key="taste",
        name="口感",
        icon="mdi:food-variant",
        value_fn=lambda d: d.get("taste_label"),
        attrs_fn=lambda d: {
            "index": d.get("taste"),
            "taste": TASTE_NAMES.get(d.get("taste")),
            "taste_zh": TASTE_NAMES_ZH.get(d.get("taste")),
        },
    ),
    CookerSensorDescription(
        key="recipe_type",
        name="食谱类型",
        icon="mdi:book-open-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: RECIPE_TYPES.get(d.get("recipe_type"), d.get("recipe_type")),
    ),
    CookerSensorDescription(
        key="rice_type",
        name="米种",
        icon="mdi:rice",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("rice_type"),
    ),
    CookerSensorDescription(
        key="fault",
        name="故障",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: FAULT_NAMES.get(d.get("fault"), d.get("fault")),
        attrs_fn=lambda d: {"code": d.get("fault")},
    ),
    CookerSensorDescription(
        key="auto_keepwarm",
        name="自动保温",
        icon="mdi:radiator",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: "开" if d.get("auto_keepwarm") else "关",
    ),
    CookerSensorDescription(
        key="boil",
        name="水开标志",
        icon="mdi:kettle-steam-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: "是" if d.get("boil") else "否",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ChunmiCookerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ChunmiCookerSensor(coordinator, description) for description in SENSORS
    )


class ChunmiCookerSensor(CoordinatorEntity[ChunmiCookerCoordinator], SensorEntity):
    """一个状态传感器。"""

    entity_description: CookerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ChunmiCookerCoordinator,
        description: CookerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.did}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.did)},
            "via_device": (XIAOMI_HOME_DOMAIN, coordinator.did),
            "name": coordinator.device_name,
            "model": coordinator.model,
            "manufacturer": "Chunmi / 知吾煮",
        }

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {
            "did": self.coordinator.did,
            "model": self.coordinator.model,
        }
        if self.coordinator.data and self.entity_description.attrs_fn:
            attrs.update(self.entity_description.attrs_fn(self.coordinator.data))
        return attrs

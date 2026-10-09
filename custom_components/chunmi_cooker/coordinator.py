"""状态协调器：读取电饭煲属性 + 动态拉取食谱/加热曲线。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    AIID_COOKING_CANCEL,
    AIID_COOKING_START,
    CONF_LAN_IP,
    CONF_SCAN_INTERVAL,
    CONF_USE_LAN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LAN_TIMEOUT,
    PIID_AUTO_KEEPWARM_FLAG,
    PIID_BOIL,
    PIID_COOK_DATA,
    PIID_COOK_TOTAL_TIME,
    PIID_FAULT,
    PIID_FINISH_TIMESTAMP,
    PIID_KEEPWARM_TIME,
    PIID_LEFT_TIME,
    PIID_MENU_ID,
    PIID_PRE_LEFT_TIME,
    PIID_RECIPE_TYPE,
    PIID_RICE_TYPE,
    PIID_STATUS,
    PIID_TASTE,
    RECIPE_CACHE_TTL,
    SIID_COOKER,
    SIID_CUSTOM,
)
from .cookprofile import MENU_NAMES, TREAT_LABELS, CookProfile
from .joyami import JoyamiClient
from .lan import MiotLanClient, MiotLanError

_LOGGER = logging.getLogger(__name__)

# 只读属性 -> (siid, piid)
PROPS: dict[str, tuple[int, int]] = {
    "status": (SIID_COOKER, PIID_STATUS),
    "fault": (SIID_COOKER, PIID_FAULT),
    "menu_id": (SIID_CUSTOM, PIID_MENU_ID),
    "cook_total_time": (SIID_CUSTOM, PIID_COOK_TOTAL_TIME),
    "left_time": (SIID_CUSTOM, PIID_LEFT_TIME),
    "keepwarm_time": (SIID_CUSTOM, PIID_KEEPWARM_TIME),
    "auto_keepwarm": (SIID_CUSTOM, PIID_AUTO_KEEPWARM_FLAG),
    "recipe_type": (SIID_CUSTOM, PIID_RECIPE_TYPE),
    "pre_left_time": (SIID_CUSTOM, PIID_PRE_LEFT_TIME),
    "rice_type": (SIID_CUSTOM, PIID_RICE_TYPE),
    "taste": (SIID_CUSTOM, PIID_TASTE),
    "boil": (SIID_CUSTOM, PIID_BOIL),
    "finish_timestamp": (SIID_CUSTOM, PIID_FINISH_TIMESTAMP),
}


class ChunmiCookerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """读取并缓存电饭煲状态；食谱（含加热曲线）按需从淳米云拉取。"""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        miot_client: Any,
        did: str,
        model: str,
        name: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        options = options or {}
        self._scan_interval = int(
            options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{did}",
            update_interval=(
                timedelta(seconds=self._scan_interval)
                if self._scan_interval > 0 else None
            ),
        )
        self.miot_client = miot_client
        self.did = did
        self.model = model
        self.device_name = name
        # 局域网直连（绕过官方集成的 mDNS 发现：跨网段时 mDNS 到不了设备）
        self._use_lan = bool(options.get(CONF_USE_LAN, False))
        self._lan_ip = str(options.get(CONF_LAN_IP) or "").strip()
        self._lan: MiotLanClient | None = None
        self._lan_error: str | None = None
        self._source: str = "unknown"
        self._recipes: list[dict] = []
        self._recipes_ts: float = 0.0

    # ------------------------------------------------------------ 局域网直连
    @property
    def lan_enabled(self) -> bool:
        return self._use_lan and bool(self._lan_ip)

    def _lan_client(self) -> MiotLanClient | None:
        """按需构造局域网客户端；token 取自官方集成（不落盘、不外泄）。"""
        if not self.lan_enabled:
            return None
        if self._lan is not None:
            return self._lan
        device = (getattr(self.miot_client, "device_list", None) or {}).get(self.did)
        if not isinstance(device, dict):
            self._lan_error = "在 xiaomi_home 里找不到该设备"
            return None
        token = device.get("token")
        if not isinstance(token, str) or len(token) != 32:
            self._lan_error = "官方集成里没有可用的 device token"
            return None
        try:
            self._lan = MiotLanClient(
                self.did, token, self._lan_ip, timeout=LAN_TIMEOUT)
            self._lan_error = None
        except MiotLanError as err:
            self._lan_error = str(err)
            return None
        return self._lan

    async def _async_lan_read(self) -> dict[str, Any] | None:
        """走局域网批量读 13 个属性；失败返回 None 交给云端兜底。"""
        client = self._lan_client()
        if client is None:
            return None
        params = [{"did": self.did, "siid": siid, "piid": piid}
                  for siid, piid in PROPS.values()]
        try:
            results = await self.hass.async_add_executor_job(
                client.get_properties_sync, params)
        except Exception as err:  # pylint: disable=broad-exception-caught
            self._lan_error = str(err)
            _LOGGER.debug("局域网读取失败，回退云端: %s", err)
            return None
        by_id = {
            (item.get("siid"), item.get("piid")): item.get("value")
            for item in results or []
            if isinstance(item, dict) and "value" in item
        }
        if not by_id:
            self._lan_error = "局域网返回空结果"
            return None
        self._lan_error = None
        return {key: by_id.get(sid) for key, sid in PROPS.items()}

    async def async_lan_test(self) -> dict[str, Any]:
        """局域网握手自检：验证 IP 可达、token 正确、回包 md5 通过。

        **不会返回 token 原文**，只给是否存在与掩码。
        """
        device = (getattr(self.miot_client, "device_list", None) or {}).get(self.did)
        token = device.get("token") if isinstance(device, dict) else None

        result: dict[str, Any] = {
            "did": self.did,
            "model": self.model,
            "lan_enabled": self.lan_enabled,
            "lan_ip": self._lan_ip or None,
            "has_device_token": isinstance(token, str) and len(token) == 32,
            "token_masked": (
                f"{token[:4]}…{token[-4:]}"
                if isinstance(token, str) and len(token) >= 8 else None),
            "current_source": self._source,
        }

        client = self._lan_client()
        if client is None:
            result["ok"] = False
            result["error"] = self._lan_error or (
                "局域网未启用：请在集成选项里打开「局域网直连」并填写设备 IP")
            return result

        try:
            result.update(await self.hass.async_add_executor_job(
                client.self_test_sync))
            self._lan_error = None
        except Exception as err:  # pylint: disable=broad-exception-caught
            result["ok"] = False
            result["error"] = str(err)
            self._lan_error = str(err)
        return result

    # ------------------------------------------------------------- 状态读取
    async def _async_read_props(self) -> dict[str, Any]:
        """批量读取属性。

        注意：**不要**直接用 ``MIoTClient.get_prop_async()`` —— 它内部是
        ``result = await ...; if result: return result``，会把合法的 ``0`` / ``False``
        当成「没取到」丢掉。官方 xiaomi_home 自身的这个缺陷会导致
        故障=0、口感=0、米种=0、食谱类型=0、水开=False、保温计时=0 全部读成 None。

        这里改成直接走云端批量接口（一次请求取全部，且对 0/False 正确），
        再用官方客户端的单点读取兜底（覆盖 LAN / 中枢网关设备）。
        """
        # 1) 局域网直连优先（可选：绕过官方集成的 mDNS 发现，跨网段也能用）
        if self.lan_enabled:
            lan_data = await self._async_lan_read()
            if lan_data is not None:
                self._source = f"lan:{self._lan_ip}"
                return lan_data

        # 2) 云端批量读取
        http = getattr(self.miot_client, "miot_http", None) or getattr(
            self.miot_client, "_http", None)
        results: list = []
        if http is not None:
            params = [
                {"did": self.did, "siid": siid, "piid": piid}
                for siid, piid in PROPS.values()
            ]
            try:
                results = await http.get_props_async(params)
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("云端批量读取失败，回退到单点读取: %s", err)

        by_id: dict[tuple[int, int], Any] = {}
        for item in results or []:
            if isinstance(item, dict) and "value" in item:
                by_id[(item.get("siid"), item.get("piid"))] = item["value"]

        data: dict[str, Any] = {}
        for key, (siid, piid) in PROPS.items():
            if (siid, piid) in by_id:
                data[key] = by_id[(siid, piid)]
                continue

            value = None
            if http is not None:
                # 单点「立即」读：底层 __get_prop_async 用 `'value' in result`
                # 判断，0 / False 不会被误丢
                try:
                    value = await http.get_prop_async(
                        self.did, siid, piid, immediately=True)
                except Exception as err:  # pylint: disable=broad-exception-caught
                    _LOGGER.debug("单点云端读取 %s 失败: %s", key, err)
            if value is None:
                # 最后回退到官方客户端（覆盖 LAN / 中枢网关设备）
                try:
                    value = await self.miot_client.get_prop_async(
                        self.did, siid, piid)
                except Exception as err:  # pylint: disable=broad-exception-caught
                    _LOGGER.debug("读取属性 %s 失败: %s", key, err)
            data[key] = value
        self._source = "cloud"
        return data

    async def _async_update_data(self) -> dict[str, Any]:
        data = await self._async_read_props()
        got_any = any(value is not None for value in data.values())

        if not got_any and self.data:
            # 设备是推送型、可能短暂休眠；保留上次数据，避免实体反复不可用
            _LOGGER.debug("本次未取到任何属性，沿用上次数据")
            return self.data
        if not got_any:
            raise UpdateFailed("无法读取电饭煲状态（设备可能离线）")

        # 派生的可读信息
        data["finish_time"] = self._ts_to_iso(data.get("finish_timestamp"))
        data["menu_name"] = MENU_NAMES.get(data.get("menu_id") or 0, "未知")
        taste = data.get("taste")
        data["taste_label"] = (
            TREAT_LABELS[taste]
            if isinstance(taste, int) and not isinstance(taste, bool)
            and 0 <= taste < len(TREAT_LABELS)
            else None
        )
        return data

    @staticmethod
    def _ts_to_iso(ts: Any) -> str | None:
        if not isinstance(ts, int) or ts <= 0:
            return None
        try:
            return dt_util.as_local(
                datetime.fromtimestamp(ts, tz=dt_util.UTC)).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    # ------------------------------------------------- 食谱 / 加热曲线（云）
    def _resolve_uid(self) -> str:
        """从小米官方集成的配置项里取账号 ID（复用其会话，不重复登录）。"""
        for entry in self.hass.config_entries.async_entries("xiaomi_home"):
            uid = entry.data.get("uid")
            if isinstance(uid, str) and uid.strip():
                return uid.strip()
        raise UpdateFailed("找不到 xiaomi_home 的账号信息")

    def _numeric_did(self) -> str:
        """淳米云的接口要纯数字的设备 ID。"""
        did = str(self.did)
        if did.isdigit():
            return did
        tail = did.rsplit("_", 1)[-1]
        return tail if tail.isdigit() else did

    async def async_get_recipes(self, force: bool = False) -> list[dict]:
        """拉取本机型可用的食谱，每种都带完整 cookcode（含加热曲线）。

        数据源：淳米云 /mijia-pot-service/portal/newIndex
        食谱是会变的，所以这里动态获取 + 缓存，不把曲线硬编码在代码里。
        """
        now = time.time()
        if not force and self._recipes and now - self._recipes_ts < RECIPE_CACHE_TTL:
            return self._recipes

        session = async_get_clientsession(self.hass)
        client = JoyamiClient(self._resolve_uid(), session)
        recipes = await client.get_recipes(self._numeric_did(), self.model)
        if recipes:
            self._recipes = recipes
            self._recipes_ts = now
            _LOGGER.debug("从淳米云拉取到 %d 个食谱", len(recipes))
        return self._recipes

    async def async_find_recipe(self, menu: str) -> dict:
        """按「菜单名」或「menuId」找食谱（找不到就报错，不瞎猜）。"""
        recipes = await self.async_get_recipes()
        if not recipes:
            raise UpdateFailed("未能从淳米云获取食谱列表")

        wanted = str(menu).strip()
        exact, by_menu = [], []
        for item in recipes:
            try:
                profile = CookProfile(item["cookcode"])
            except ValueError:
                continue
            if item.get("name") == wanted:
                exact.append((item, profile))
            if str(profile.menu_id) == wanted:
                by_menu.append((item, profile))

        pool = exact or by_menu
        if not pool:
            names = [r["name"] for r in recipes]
            raise UpdateFailed(f"没有叫「{wanted}」的食谱；可用: {names}")
        item, profile = pool[0]
        return {"recipe": item, "profile": profile}

    # ---------------------------------------------------------------- 写入
    async def _async_action(self, siid: int, aiid: int, in_list: list) -> Any:
        """下发动作：先试局域网直连，失败/被拒再走官方客户端（云端或中枢网关）。

        好处是局域网会**明确返回错误码**，不像云端路径那样失败也不吭声。
        """
        client = self._lan_client()
        if client is not None:
            try:
                result = await self.hass.async_add_executor_job(
                    client.action_sync, siid, aiid, in_list)
                code = result.get("code") if isinstance(result, dict) else None
                if code in (0, 1):
                    self._source = f"lan:{self._lan_ip}"
                    self._lan_error = None
                    return result.get("out", [])
                self._lan_error = f"设备返回 code={code}"
                _LOGGER.warning(
                    "局域网动作被设备拒绝 (code=%s)，回落云端通道重试", code)
            except Exception as err:  # pylint: disable=broad-exception-caught
                self._lan_error = str(err)
                _LOGGER.warning("局域网动作失败，回落云端通道: %s", err)
        return await self.miot_client.action_async(self.did, siid, aiid, in_list)

    async def async_send_cookcode(self, cookcode: str) -> Any:
        """cooking-start 动作：siid=3 aiid=1，入参就是完整 cookcode。"""
        return await self._async_action(
            SIID_CUSTOM, AIID_COOKING_START,
            [{"piid": PIID_COOK_DATA, "value": cookcode}])

    async def async_cancel(self) -> Any:
        return await self._async_action(SIID_CUSTOM, AIID_COOKING_CANCEL, [])

    async def async_set_reservation(
        self,
        *,
        menu: str,
        finish_time: str | None = None,
        delay_minutes: int | None = None,
        cook_minutes: int | None = None,
        taste: int | None = None,
        auto_keep_warm: bool | None = None,
        upload: bool = True,
        recipe: dict | None = None,
    ) -> dict:
        """按官方 App 的流程武装一次预约。

        1. 取该菜单的 cookcode（含加热曲线）
        2. 按需改 时长 / 口感 / 预约时长 / 自动保温，并重算 CRC
        3. （可选）上报淳米云，与官方 App 行为一致
        4. 下发 cooking-start
        """
        found = recipe or await self.async_find_recipe(menu)
        item, profile = found["recipe"], found["profile"]

        changed: list[str] = []

        order_minutes = delay_minutes
        if order_minutes is None and finish_time:
            order_minutes = self._minutes_until(finish_time)
        if order_minutes is None:
            raise ValueError("需要 finish_time (HH:MM) 或 delay_minutes 之一")

        if cook_minutes is not None:
            profile.set_cook_time(int(cook_minutes))
            changed.append(f"cook_minutes={cook_minutes}")
        if taste is not None:
            if profile.menu_id != 2:
                _LOGGER.warning(
                    "菜单「%s」官方不支持口感设置，仍会写入 index=%s",
                    item.get("name"), taste)
            profile.set_taste(int(taste))
            changed.append(f"taste={taste}")
        profile.set_is_order(True)
        profile.set_order_time(int(order_minutes))
        changed.append(f"order_minutes={order_minutes}")
        if auto_keep_warm is not None:
            profile.set_auto_keep_warm(bool(auto_keep_warm))
            changed.append(f"auto_keep_warm={auto_keep_warm}")

        cookcode = profile.to_hex()
        if not CookProfile(cookcode).crc_ok():
            raise UpdateFailed("cookcode CRC 计算异常，已中止下发")

        if upload:
            try:
                session = async_get_clientsession(self.hass)
                status = await JoyamiClient(
                    self._resolve_uid(), session).upload_cook_data(
                        order_minutes, item.get("recipe_id"), cookcode, True,
                        item.get("name") or "",
                        did=self._numeric_did(), model=self.model,
                        device_name=self.device_name)
                _LOGGER.debug("淳米云上报返回 %s", status)
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.warning("淳米云上报失败（不影响下发）: %s", err)

        out = await self.async_send_cookcode(cookcode)
        await self.async_request_refresh()
        return {
            "menu": item.get("name"),
            "recipe_id": item.get("recipe_id"),
            "changed": changed,
            "cookcode": cookcode,
            "action_out": out,
            "profile": profile.describe(),
        }

    async def async_start_now(
        self, *, menu: str, cook_minutes: int | None = None,
        taste: int | None = None, auto_keep_warm: bool | None = None,
    ) -> dict:
        """立刻开煮（不预约）。"""
        found = await self.async_find_recipe(menu)
        profile = found["profile"]
        if cook_minutes is not None:
            profile.set_cook_time(int(cook_minutes))
        if taste is not None:
            profile.set_taste(int(taste))
        if auto_keep_warm is not None:
            profile.set_auto_keep_warm(bool(auto_keep_warm))
        profile.set_is_order(False)
        cookcode = profile.to_hex()
        out = await self.async_send_cookcode(cookcode)
        await self.async_request_refresh()
        return {"menu": found["recipe"].get("name"), "cookcode": cookcode,
                "action_out": out, "profile": profile.describe()}

    @staticmethod
    def _minutes_until(finish_time: str) -> int:
        """把 'HH:MM' 换算成「从现在到该时刻」的分钟数（官方 App 的算法）。"""
        try:
            hour, minute = (int(x) for x in finish_time.strip().split(":", 1))
        except ValueError as err:
            raise ValueError(f"finish_time 格式应为 HH:MM，收到 {finish_time!r}") from err
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"finish_time 不是合法时间: {finish_time!r}")

        now = dt_util.now()
        end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if end <= now:
            end += timedelta(days=1)
        delta = (end - now).total_seconds() / 60
        # 与官方同样按整分钟计，并做半分钟修正以贴近目标时刻
        return int(round(delta - 0.5))

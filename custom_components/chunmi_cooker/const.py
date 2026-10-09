"""常量定义。"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "chunmi_cooker"
PLATFORMS: list[Platform] = [Platform.SENSOR]

# 依赖的官方小米集成（会话/云端通道复用它，不重复登录）
XIAOMI_HOME_DOMAIN = "xiaomi_home"

# 本集成支持的机型（前缀匹配）
SUPPORTED_MODEL_PREFIXES = ("chunmi.cooker.", "mijia.chunmi.cooker.")

# ---------------------------------------------------------------- MIoT 规格
# siid=2 Cooker
SIID_COOKER = 2
PIID_STATUS = 1
PIID_FAULT = 2

# siid=3 custom
SIID_CUSTOM = 3
PIID_MENU_ID = 1
PIID_COOK_TOTAL_TIME = 2
PIID_LEFT_TIME = 3
PIID_KEEPWARM_TIME = 4
PIID_AUTO_KEEPWARM_FLAG = 5
PIID_RECIPE_TYPE = 6
PIID_PRE_LEFT_TIME = 7
PIID_RICE_TYPE = 8
PIID_TASTE = 9
PIID_BOIL = 21
PIID_FINISH_TIMESTAMP = 22

# 动作
AIID_COOKING_START = 1      # in: piid10 cook-data(str)  ← 开始/预约
AIID_COOKING_CANCEL = 2
AIID_SET_MENU = 3           # in: piid11 set-menu-data(str)  "info_<cookcode>,bell_01"
PIID_COOK_DATA = 10
PIID_SET_MENU_DATA = 11

STATUS_NAMES = {
    1: "idle",
    2: "cooking",
    3: "scheduled",
    4: "keep_warm",
    5: "error",
    6: "updating",
    7: "cook_finish",
}
STATUS_NAMES_ZH = {
    1: "待机中",
    2: "烹饪中",
    3: "预约中",
    4: "保温中",
    5: "设备异常",
    6: "固件升级中",
    7: "烹饪已完成",
}
TASTE_NAMES = {0: "soft", 1: "medium", 2: "hard"}
TASTE_NAMES_ZH = {0: "软", 1: "适中", 2: "硬"}
RECIPE_TYPES = {0: "native", 1: "official", 2: "custom"}

FAULT_NAMES = {
    0: "no_faults", 1: "voltage_high", 2: "voltage_low", 3: "bottom_overheat",
    4: "top_overheat", 5: "top_sensor_broken", 6: "bottom_sensor_broken",
    7: "comm_error",
}

# 状态轮询周期（秒）。设备是推送型，这里只做轻量兜底刷新。
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 3600
# 可选档位（秒）。0 表示不主动轮询（完全依赖推送 / 手动刷新）
SCAN_INTERVAL_CHOICES = (0, 30, 60, 120, 300, 600)
# 食谱（含加热曲线）缓存有效期（秒）
RECIPE_CACHE_TTL = 6 * 3600

# 局域网（miIO）默认端口
LAN_PORT = 54321
LAN_TIMEOUT = 5.0

# 服务名
SERVICE_SET_RESERVATION = "set_reservation"
SERVICE_START_NOW = "start_now"
SERVICE_CANCEL = "cancel"
SERVICE_GET_STATUS = "get_status"
SERVICE_LIST_RECIPES = "list_recipes"
SERVICE_DIAGNOSE = "diagnose"
SERVICE_LAN_TEST = "lan_test"

CONF_DID = "did"
CONF_MODEL = "model"
CONF_NAME = "name"

# 可配置项（Options Flow）
CONF_SCAN_INTERVAL = "scan_interval"
CONF_USE_LAN = "use_lan"
CONF_LAN_IP = "lan_ip"

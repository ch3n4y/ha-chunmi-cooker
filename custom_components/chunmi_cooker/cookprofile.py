"""淳米 (chunmi) 电饭煲烹饪档案 CookCode 编解码。

布局与 CRC 逆向自米家官方插件包 mijia.chunmi.cooker.eh3 (main.bundle 的 CMCookProfile 类)，
并用淳米官方云返回的 13 份真实档案校验通过（CRC 全部复现一致）。

    data[0]      设备类型
    data[1]      子类型
    data[2]      index
    data[3..6]   menuId (big-endian uint32)
    data[7]      bit7 自定义保存 | bit6 可预约 | bit5 可设自动保温
    data[8..9]   烹饪时长 时/分      getCookTime = d[8]*60 + d[9]
    data[10..11] 时长上限 时/分
    data[12..13] 时长下限 时/分
    data[14]     bit7 = 预约标志;  低 7 位 = 预约时长「时」
    data[15]     低 7 位 = 预约时长「分」; bit7 = 自动保温
    data[19]     口感索引 (0=软 Soft / 1=适中 / 2=硬)
    末尾 2 字节  CRC16/CCITT-FALSE(poly 0x1021, init 0, 不反转, 不异或输出) 大端

注意：byte 20 之后是菜单专属的加热曲线，无法推导 —— 所以本模块只能
**改写已存在的合法档案**，不能从零构造。
"""

from __future__ import annotations

MENU_NAMES = {
    1: "超快饭",
    2: "精煮饭",
    3: "煮粥",
    4: "保温",
}
TREAT_LABELS = ["软", "适中", "硬"]


def crc16_ccitt_false(data: bytes) -> int:
    """与插件 _calcrc() 完全等价。"""
    crc = 0
    for b in data:
        crc ^= (b & 0xFF) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF


class CookProfile:
    """一份烹饪档案。"""

    def __init__(self, hex_str: str) -> None:
        hex_str = (hex_str or "").strip()
        if len(hex_str) < 50:
            raise ValueError(f"cookcode 太短: {len(hex_str)} 字符")
        if len(hex_str) % 2:
            raise ValueError("cookcode 十六进制长度为奇数")
        self.data = bytearray.fromhex(hex_str)

    # ---------- 读 ----------
    @property
    def menu_id(self) -> int:
        return int.from_bytes(self.data[3:7], "big")

    @property
    def cook_time(self) -> int:
        return self.data[8] * 60 + self.data[9]

    @property
    def cook_time_max(self) -> int:
        return self.data[10] * 60 + self.data[11]

    @property
    def cook_time_min(self) -> int:
        return self.data[12] * 60 + self.data[13]

    @property
    def is_order(self) -> bool:
        return bool(self.data[14] & 0x80)

    @property
    def order_time(self) -> int:
        return (self.data[14] & 0x7F) * 60 + (self.data[15] & 0x7F)

    @property
    def auto_keep_warm(self) -> bool:
        return bool(self.data[15] & 0x80)

    @property
    def taste(self) -> int:
        return self.data[19]

    @property
    def can_order(self) -> bool:
        return bool(self.data[7] & 0x40)

    @property
    def can_set_auto_keep_warm(self) -> bool:
        return bool(self.data[7] & 0x20)

    def can_set_time(self) -> bool:
        return self.cook_time_max > self.cook_time_min

    # ---------- 写 ----------
    def set_cook_time(self, minutes: int) -> None:
        if not self.can_set_time():
            raise ValueError(
                f"该菜单不允许改时长（固定 {self.cook_time} 分钟）")
        if not self.cook_time_min <= minutes <= self.cook_time_max:
            raise ValueError(
                f"时长 {minutes} 分钟超出允许区间 "
                f"[{self.cook_time_min}, {self.cook_time_max}]")
        self.data[8] = (minutes // 60) & 0xFF
        self.data[9] = minutes % 60

    def set_order_time(self, minutes: int) -> None:
        if not 0 <= minutes <= 1440:
            raise ValueError("预约时长必须在 0..1440 分钟")
        self.data[14] = (self.data[14] & 0x80) | ((minutes // 60) & 0xFF)
        self.data[15] = (minutes % 60) | (self.data[15] & 0x80)

    def set_is_order(self, on: bool) -> None:
        self.data[14] = self.data[14] | 0x80 if on else self.data[14] & 0x7F

    def set_auto_keep_warm(self, on: bool) -> None:
        self.data[15] = self.data[15] | 0x80 if on else self.data[15] & 0x7F

    def set_taste(self, index: int) -> None:
        if not 0 <= index <= 2:
            raise ValueError("口感索引只能是 0(软) / 1(适中) / 2(硬)")
        self.data[19] = index

    # ---------- 输出 ----------
    def compute_crc(self) -> int:
        return crc16_ccitt_false(bytes(self.data[:-2]))

    def crc_ok(self) -> bool:
        return int.from_bytes(self.data[-2:], "big") == self.compute_crc()

    def to_hex(self) -> str:
        out = bytearray(self.data)
        crc = crc16_ccitt_false(bytes(out[:-2]))
        out[-2] = (crc >> 8) & 0xFF
        out[-1] = crc & 0xFF
        return out.hex()

    def describe(self) -> dict:
        return {
            "menu_id": self.menu_id,
            "menu_name": MENU_NAMES.get(self.menu_id, f"自选({self.menu_id})"),
            "cook_minutes": self.cook_time,
            "cook_minutes_range": [self.cook_time_min, self.cook_time_max],
            "can_set_time": self.can_set_time(),
            "is_order": self.is_order,
            "order_minutes": self.order_time if self.is_order else 0,
            "auto_keep_warm": self.auto_keep_warm,
            "taste": self.taste,
            "taste_label": TREAT_LABELS[self.taste] if self.taste < 3 else str(self.taste),
            "can_order": self.can_order,
            "bytes": len(self.data),
            "crc_ok": self.crc_ok(),
        }


def gbk_hex(name: str) -> str:
    """插件 CMCommon.getGBK(): GBK 字节各自 toString(16) 后拼接，再补 '00'。"""
    return "".join(f"{b:x}" for b in name.encode("gbk")) + "00"

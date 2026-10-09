"""MIoT 局域网（miIO/LAN）最小客户端。

协议逆向自官方 ``xiaomi_home`` 的 ``miot_lan.py``，用于**绕过 mDNS 发现**、
用「已知 IP + device token」直接单播控制设备。

为什么需要它：官方集成的局域网发现走 mDNS（Zeroconf 组播），
**跨不了网段**。设备在 192.168.26.0/24、HA 在 192.168.27.0/24 时，
即使单播可达、token 也有，官方集成依然永远建立不了 LAN 通道，
于是所有读写都回落云端。本模块用单播解决这个问题。

报文格式（``>HHQI16s`` 头，共 32 字节）::

    [0:2]   magic  0x2131
    [2:4]   total_len = 32 + len(ciphertext)
    [4:12]  did      (uint64 big-endian)
    [12:16] offset
    [16:32] 先放 token，最后被整包的 md5 覆盖
    [32:]   AES128-CBC( PKCS7(json) )
            key = md5(token)          (16 字节)
            iv  = md5(key + token)    (16 字节)

校验：收包时把 [16:32] 换回 token，再比对整包 md5 —— 这一步等价于握手，
证明双方持有同一个 token。

方法（与官方一致）::

    get_properties  params=[{"did","siid","piid"}]        -> result=[{... "value"}]
    set_properties  params=[{"did","siid","piid","value"}]
    action          params={"did","siid","aiid","in":[]}  -> result={"code","out"}
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import socket
import struct
import time
from typing import Any

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

OT_HEADER = 0x2131
OT_HEADER_LEN = 32
OT_PORT = 54321
DEFAULT_TIMEOUT = 5.0


class MiotLanError(Exception):
    """局域网通信失败。"""


class MiotLanAuthError(MiotLanError):
    """md5 校验不过 —— 通常是 token 不对。"""


class MiotLanClient:
    """一个设备的局域网会话（无状态、按需建连）。

    内部是同步 socket；异步侧用 ``hass.async_add_executor_job`` 包装调用，
    避免 asyncio 协议层的复杂度。轮询频率很低（分钟级），开销可忽略。
    """

    def __init__(
        self,
        did: str,
        token_hex: str,
        ip: str,
        *,
        port: int = OT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        did = str(did).strip()
        if not did.isdigit():
            raise MiotLanError(f"did 必须是纯数字，收到 {did!r}")
        token_hex = (token_hex or "").strip()
        if len(token_hex) != 32:
            raise MiotLanError(f"token 必须是 32 位十六进制，收到 {len(token_hex)} 位")

        self.did = did
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.token = bytes.fromhex(token_hex)

        aes_key = hashlib.md5(self.token).digest()
        aes_iv = hashlib.md5(aes_key + self.token).digest()
        self._cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv))
        self._msg_id = random.randint(1, 0x7FFFFFFF)
        self._offset = 0

    # ------------------------------------------------------------- 报文编解码
    def _encode(self, payload: dict) -> bytes:
        # 注意：必须与官方 miot_lan.py 完全一致 —— 它用的是 json.dumps 的
        # **默认分隔符**（", " / ": "），不是紧凑格式。设备虽然都能解析，
        # 但逐字节对齐官方实现可以排除任何长度/格式相关的意外。
        clear = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        padder = sym_padding.PKCS7(128).padder()
        padded = padder.update(clear) + padder.finalize()
        encryptor = self._cipher.encryptor()
        body = encryptor.update(padded) + encryptor.finalize()

        total = OT_HEADER_LEN + len(body)
        head = struct.pack(
            ">HHQI16s", OT_HEADER, total, int(self.did),
            int(time.time()) - self._offset, self.token)
        packet = bytearray(head + body)
        packet[16:32] = hashlib.md5(bytes(packet)).digest()
        return bytes(packet)

    def _decode(self, data: bytes) -> dict:
        if len(data) < OT_HEADER_LEN:
            raise MiotLanError(f"报文过短: {len(data)}")
        magic, total = struct.unpack(">HH", data[0:4])
        if magic != OT_HEADER:
            raise MiotLanError(f"magic 不对: {magic:#06x}")
        if total > len(data):
            raise MiotLanError(f"报文被截断: 声明 {total} 实际 {len(data)}")

        md5_orig = data[16:32]
        buf = bytearray(data[:total])
        buf[16:32] = self.token                      # 还原后比对整包 md5
        if hashlib.md5(bytes(buf)).digest() != md5_orig:
            raise MiotLanAuthError("md5 校验失败（token 不匹配？）")

        decryptor = self._cipher.decryptor()
        padded = decryptor.update(bytes(buf[OT_HEADER_LEN:total])) + decryptor.finalize()
        unpadder = sym_padding.PKCS7(128).unpadder()
        clear = unpadder.update(padded) + unpadder.finalize()
        # 有些设备会在 JSON 末尾补 \0
        return json.loads(clear.rstrip(b"\x00").decode("utf-8"))

    # ---------------------------------------------------------------- 同步请求
    def request_sync(self, method: str, params: Any) -> dict:
        """发一条 RPC 并等它自己的响应（按 msg id 匹配）。"""
        self._msg_id += 1
        if self._msg_id > 0x7FFFFFFF:
            self._msg_id = 1
        msg_id = self._msg_id
        packet = self._encode({"id": msg_id, "method": method, "params": params})

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            sock.sendto(packet, (self.ip, self.port))
            deadline = time.monotonic() + self.timeout
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise MiotLanError(
                        f"{self.ip} 在 {self.timeout}s 内没有响应（设备休眠？）")
                sock.settimeout(remain)
                data, _ = sock.recvfrom(8192)
                try:
                    reply = self._decode(data)
                except MiotLanAuthError:
                    raise
                except MiotLanError as err:
                    _LOGGER.debug("丢弃无效报文: %s", err)
                    continue
                if reply.get("id") == msg_id:
                    if "error" in reply:
                        raise MiotLanError(f"设备返回错误: {reply['error']}")
                    return reply
        finally:
            sock.close()

    # ------------------------------------------------------------------ 高层
    def get_properties_sync(self, params: list[dict]) -> list[dict]:
        reply = self.request_sync("get_properties", params)
        result = reply.get("result")
        return result if isinstance(result, list) else []

    def action_sync(self, siid: int, aiid: int, in_list: list) -> dict:
        reply = self.request_sync(
            "action", {"did": self.did, "siid": siid, "aiid": aiid, "in": in_list})
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def self_test_sync(self) -> dict:
        """握手自检：读一个属性，确认 token 正确、回包 md5 校验通过。"""
        started = time.monotonic()
        props = self.get_properties_sync(
            [{"did": self.did, "siid": 2, "piid": 1}])   # siid2/piid1 = 工作状态
        elapsed = (time.monotonic() - started) * 1000
        value = None
        if props and isinstance(props[0], dict):
            value = props[0].get("value")
        return {
            "ok": bool(props),
            "latency_ms": round(elapsed),
            "status_raw": value,
            "ip": self.ip,
            "token_verified": True,
        }

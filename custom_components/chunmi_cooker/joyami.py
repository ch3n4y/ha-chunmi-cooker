"""淳米官方云 (joyami) 客户端 —— 签名/AES/接口全部逆向自米家官方插件包。

插件里的两处云交互：
  1) gateway.joyami.com  —— 拉取该设备的官方食谱与 cookcode（配方原始档案）
  2) post.joyami.com/device/upload-start-geo —— 官方「开始烹饪/预约」时上报（AES 加密）

签名算法（CookDataCryptUtil，无需密钥）：
    s1 = base64url( sha256( APP_KEY + nonce ) 的原始字节 )
    s2 = f"{method}&{url}&app_id={APP_ID}&{s1}"
    signature = base64url( sha1(s2) 的原始字节 )

AES（getSessionSecurity + encryptAES）：
    key = sha256(APP_KEY 字节 + nonce 原始字节)  -> 64 位 hex = 32 字节 => AES-256-CBC
    iv  = 固定的 16 字节
    padding = PKCS7, 输出 base64url
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

APP_ID = "20181116"
APP_KEY = "KAPtzxeOMyZKLlXB"
AES_IV = bytes([117, 2, 3, 11, 105, 78, 90, 110, 112, 56, 78, 23, 41, 58, 93, 96])

GATEWAY = "https://gateway.joyami.com"
UPLOAD_URL = "https://post.joyami.com/device/upload-start-geo"
UA = "chunmiapp"


def _b64url(raw: bytes) -> str:
    return base64.b64encode(raw).decode().replace("+", "-").replace("/", "_").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    text = text.replace("-", "+").replace("_", "/")
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _generate_nonce() -> str:
    ts = int(time.time() // 60)
    nt = bytes([(ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF])
    return _b64url(os.urandom(8) + nt)


def _sign(nonce: str, method: str, url: str) -> str:
    s1 = _b64url(hashlib.sha256((APP_KEY + nonce).encode()).digest())
    s2 = f"{method}&{url}&app_id={APP_ID}&{s1}"
    return _b64url(hashlib.sha1(s2.encode()).digest())


def _session_security(nonce: str) -> bytes:
    return hashlib.sha256(APP_KEY.encode() + _b64url_decode(nonce)).digest()


def _aes256_cbc(plain: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(AES_IV)).encryptor()
    return enc.update(padded) + enc.finalize()


class JoyamiClient:
    """淳米云。只用标准 aiohttp，不依赖 HA。"""

    def __init__(self, uid: str, session: aiohttp.ClientSession) -> None:
        self._uid = str(uid)
        self._session = session

    async def _get(self, path: str, params: dict[str, Any]) -> dict:
        """gateway.joyami.com 的 GET 请求（插件 CMNetUtil.fetchData 的等价物）。"""
        params = dict(params)
        params.setdefault("language", "zh_CN")
        nonce = _generate_nonce()
        ts = int(time.time() * 1000)
        # 网络层签名: sha256("{APP_ID}&{ts}&{nonce}&{method}&{url}")
        s2 = f"202007190935014&{ts}&{nonce}&GET&{path}"
        sig = _b64url(hashlib.sha256(s2.encode()).digest())
        headers = {
            "signature": sig,
            "nonce": nonce,
            "timestamp": str(ts),
            "vid": self._uid,
            "version": "ha_chunmi_cooker:1.0.0",
            "appId": "202007190935014",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        async with self._session.get(
            GATEWAY + path, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
        ) as res:
            text = await res.text()
        if res.status != 200:
            raise RuntimeError(f"joyami http {res.status}: {text[:200]}")
        obj = json.loads(text)
        if obj.get("code") != 200:
            raise RuntimeError(
                f"joyami code {obj.get('code')}: {obj.get('message')}")
        return obj.get("result") or {}

    async def get_recipes(self, did: str, model: str) -> list[dict]:
        """返回该设备可用的官方食谱（含 cookcode）。

        来源: /mijia-pot-service/portal/newIndex -> defaultRecipes + recipeExpands
        """
        result = await self._get(
            "/mijia-pot-service/portal/newIndex",
            {"deviceId": did, "model": model, "useId": self._uid},
        )
        recipes: list[dict] = []
        for key in ("defaultRecipes", "recipeExpands"):
            for item in result.get(key) or []:
                code = (item.get("cookcode") or "").strip()
                if not code:
                    continue
                recipes.append({
                    "name": item.get("name"),
                    "recipe_id": item.get("recipeId"),
                    "cookcode": code,
                    "source": key,
                    "is_self_choose": item.get("isSelfChoose"),
                })
        return recipes

    async def upload_cook_data(
        self,
        duration: int,
        recipe_id: Any,
        cookcode: str,
        is_schedule: bool,
        recipe_name: str,
        *,
        did: str,
        model: str,
        device_name: str = "",
        mac: str = "",
        app_version: str = "1.0.67",
        firmware_version: str = "",
    ) -> int:
        """复现插件 CMCookDataUpload.uploadCookData() 的上报。

        返回 HTTP 状态码；插件里这步不可失败（不阻塞主流程）。
        """
        import binascii

        params = {
            "dType": 4,
            "did": did,
            "mac": mac,
            "model": model,
            "name": device_name,
            "uAppType": 1,
            "uApp": 1,
            "uId": self._uid,
            "uLatitude": 0,
            "uLongitude": 0,
            "uName": "",
            "duration": duration,
            "recipeid": recipe_id,
            "bschedule": 1 if is_schedule else 0,
            "phoneBrand": "",
            "cookingCode": cookcode,
            "iot": 1,
            "appV": app_version,
            "firmwareV": firmware_version,
            "recipeName": recipe_name,
            # 插件 CMCommon.getGBK(): GBK 字节各自 hex 拼接后再补 "00"
            "recipeNameGBK": "".join(
                f"{b:x}" for b in recipe_name.encode("gbk", errors="replace")) + "00",
        }
        body = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode()

        nonce = _generate_nonce()
        sig = _sign(nonce, "POST", UPLOAD_URL)
        enc = _b64url(_aes256_cbc(body, _session_security(nonce)))
        headers = {
            "nonce": nonce,
            "signature": sig,
            "User-Agent": UA,
            "apiname": "chunmidc@1116tokit",
        }
        timeout = aiohttp.ClientTimeout(total=25)
        async with self._session.post(
            UPLOAD_URL, params={"data": enc}, headers=headers, data=b"", timeout=timeout
        ) as res:
            await res.read()
            return res.status

"""lan.py 的报文层单元测试。

做法：把官方 ``xiaomi_home`` 的 ``miot_lan.py`` 里 ``gen_packet`` /
``decrypt_packet`` 的逻辑**独立转写**一份作为参照，再与 ``lan.py`` 的输出
**逐字节比对**。这样能抓出转写错误，而不只是自说自话的回环测试。

跑法::

    pip install cryptography
    python tests/test_lan.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "custom_components" / "chunmi_cooker"))

from cryptography.hazmat.primitives import padding as sym_padding  # noqa: E402
from cryptography.hazmat.primitives.ciphers import (  # noqa: E402
    Cipher, algorithms, modes)
from lan import MiotLanAuthError, MiotLanClient, MiotLanError  # noqa: E402

DID = "592224307"
TOKEN_HEX = "0123456789abcdef0123456789abcdef"      # 测试用假 token
TOKEN = bytes.fromhex(TOKEN_HEX)

# ---------------------------------------------------------- 参照实现（官方）
_AES_KEY = hashlib.md5(TOKEN).digest()
_AES_IV = hashlib.md5(_AES_KEY + TOKEN).digest()
_REF_CIPHER = Cipher(algorithms.AES128(_AES_KEY), modes.CBC(_AES_IV))


def reference_gen_packet(clear_data: dict, did: str, offset: int) -> bytes:
    """照抄 miot_lan.py::gen_packet 的逻辑。"""
    clear_bytes = json.dumps(clear_data, ensure_ascii=False).encode("utf-8")
    padder = sym_padding.PKCS7(algorithms.AES128.block_size).padder()
    padded = padder.update(clear_bytes) + padder.finalize()
    encryptor = _REF_CIPHER.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    data_len = len(encrypted) + 32
    buf = bytearray(32 + len(encrypted))
    buf[:32] = struct.pack(">HHQI16s", 0x2131, data_len, int(did), offset, TOKEN)
    buf[32:data_len] = encrypted
    buf[16:32] = hashlib.md5(bytes(buf[0:data_len])).digest()
    return bytes(buf)


FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ✅ " if cond else "  ❌ ") + label)
    if not cond:
        FAILED.append(label)


def main() -> int:
    client = MiotLanClient(DID, TOKEN_HEX, "127.0.0.1")
    client._offset = int(time.time())        # 让报文里的 offset 恰为 0

    print("=== 1. 报文逐字节比对（offset 固定 0）===")
    payload = {"id": 1001, "method": "get_properties",
               "params": [{"did": DID, "siid": 2, "piid": 1}]}
    mine = client._encode(payload)
    ref = reference_gen_packet(payload, DID, 0)
    check(mine == ref, f"与官方逻辑逐字节一致（{len(mine)} 字节）")
    if mine != ref:
        print("    mine:", mine[:64].hex())
        print("    ref :", ref[:64].hex())

    print("\n=== 2. 头部字段 ===")
    magic, total = struct.unpack(">HH", mine[0:4])
    check(magic == 0x2131, f"magic = {magic:#06x}")
    check(total == len(mine), f"total_len = {total} == 实际长度")
    check(struct.unpack(">Q", mine[4:12])[0] == int(DID), "did 字段正确")
    check(struct.unpack(">I", mine[12:16])[0] == 0, "offset 字段正确")
    restored = bytearray(mine)
    restored[16:32] = TOKEN
    check(bytes(mine[16:32]) == hashlib.md5(bytes(restored)).digest(),
          "md5 覆盖 [16:32]（还原 token 后校验，同官方 decrypt_packet）")
    check(TOKEN not in mine[16:32], "明文 token 不出现在报文里")

    print("\n=== 3. 解密回环 ===")
    check(client._decode(mine) == payload, "解出的 JSON 与原文一致")

    print("\n=== 4. 非 ASCII ===")
    zh = {"id": 7, "method": "action",
          "params": {"did": DID, "siid": 3, "aiid": 1, "in": ["煮粥"]}}
    check(client._decode(client._encode(zh)) == zh, "含中文的载荷往返正常")

    print("\n=== 5. 错误 token 必须被拒 ===")
    wrong = MiotLanClient(DID, "ff" * 16, "127.0.0.1")
    try:
        wrong._decode(mine)
        check(False, "错误 token 竟然通过了校验")
    except MiotLanAuthError:
        check(True, "错误 token 触发 MiotLanAuthError")

    print("\n=== 6. 参数校验 ===")
    for args, label in (
        ((DID, "abcd", "127.0.0.1"), "token 长度不对"),
        (("abc", TOKEN_HEX, "127.0.0.1"), "did 非数字"),
    ):
        try:
            MiotLanClient(*args)
            check(False, f"应当拒绝: {label}")
        except MiotLanError:
            check(True, f"拒绝 {label}")

    print("\n结论:", "全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项 ❌")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

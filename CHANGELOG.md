# Changelog

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.4] - 2026-10-09

### 新增

- **局域网（miIO）直连控制**。官方 `xiaomi_home` 的局域网发现走 **mDNS 组播**，
  跨不了网段（HA 在 `192.168.27.x`、设备在 `192.168.26.x` 时就永远发现不到），
  导致即使设备单播可达、token 也有，读写依然全部回落云端。

  本版本自己实现了 miIO 报文层（`lan.py`），用「**已知 IP + device token**」单播直连，
  完全绕过 mDNS：

  - 报文格式与官方 `miot_lan.py` **逐字节一致**（已用独立转写的参照实现做比对测试）
  - `AES128-CBC`，`key = md5(token)`、`iv = md5(key + token)`，整包 md5 校验
  - 局域网**会明确返回设备错误码**，不再像云端路径那样失败也不吭声
  - 局域网失败自动回落云端通道，不会让集成变脆

- **集成的可配置项（Options Flow）**：
  - **状态轮询间隔**：可选「不主动轮询 / 30 / 60 / 120 / 300 / 600 秒」
  - **局域网直连开关 + 设备局域网 IP**（例如 `192.168.26.179`）
  - 选项变更后自动重载条目，立即生效

- 新增 **`chunmi_cooker.lan_test`** 服务：用配置的 IP + token 做一次单播握手并读一个属性，
  返回是否成功、耗时（毫秒）、状态原值 —— 验证局域网能否使用最直接的方法。

- `chunmi_cooker.diagnose` 服务增加本集成自身的状态：轮询间隔、当前数据来源
  （`lan:192.168.26.179` / `cloud`）、局域网开关与实际错误原因。

### 测试

- 新增 `tests/test_lan.py`：把官方 `miot_lan.py` 的 `gen_packet` / `decrypt_packet`
  逻辑独立转写一份作参照，与 `lan.py` 输出**逐字节比对**；另覆盖头部字段、
  解密回环、非 ASCII 载荷、错误 token 拒绝、参数校验。

## [0.1.3] - 2026-10-09

### 新增

- **`chunmi_cooker.diagnose` 服务**：显示这台设备「云端 / 局域网 / 中枢网关」三条通道的
  实际状态，以及是否已持有局域网控制所需的 device token（只返回长度与掩码，**不返回原文**）。

  背景：淳米电饭煲的 miIO 局域网接口监听 **UDP/54321**，但它需要 16 字节 device token
  才能建立会话（`aes_key = md5(token)`）。官方 `xiaomi_home` 是从云端设备列表里拿到
  token 再交给它的 LAN 模块的，所以 token 本来就存在 HA 里；**是否真的走局域网**
  取决于集成的控制模式（自动 / 仅云端），而这一点从外部读不到——本服务就是把它照出来。

  若 `ctrl_mode` 显示 `CLOUD`，在 Xiaomi Home 的集成选项里把控制模式改成「自动」即可
  启用局域网优先；`lan_available=true` 时写操作会自动走 LAN。

## [0.1.2] - 2026-10-09

### 修复

- **数值为 `0` / `False` 的传感器不再显示 `unknown`**。
  官方 `MIoTClient.get_prop_async()` 内部是 `result = await ...; if result: return result`，
  会把合法的 `0` / `False` 当成「没取到」丢弃，导致
  故障(`fault=0`)、口感(`taste=0`)、米种(`rice_type=0`)、食谱类型(`recipe_type=0`)、
  水开(`boil=False`)、保温计时(`keepwarm_time=0`) 全部读成 `None`。

  现在改为直接调用云端批量接口 `/app/v2/miotspec/prop/get` 一次取全部属性
  （按 `'value' in result` 判断，`0`/`False` 正确返回），
  未覆盖到的属性再走单点「立即」读，最后才回退官方客户端以兼容 LAN / 中枢网关设备。
  顺带把 13 次单点请求合并成 1 次，轮询开销明显下降。

## [0.1.1] - 2026-10-09

### 新增

- **品牌图标**：新增 `custom_components/chunmi_cooker/brand/icon.png` 与
  `dark_icon.png`（256×256），Home Assistant 2024.11+ 会在集成页面显示它，
  不再是一片空白占位。矢量源文件放在 `brand-src/`（`icon.svg` / `icon-dark.svg`），
  用 macOS 自带 `qlmanage` 即可重新栅格化。

## [0.1.0] - 2026-10-09

首个版本。

### 新增

- **引导式配置流程**：自动从官方 Xiaomi Home（`xiaomi_home`）集成已同步的设备中
  发现淳米电饭煲；多台以下拉选择，单台直接进入确认页。
  **无需填写任何账号、token 或密钥**——直接复用 Xiaomi Home 已登录的会话与云端通道。
- **13 个状态实体**：工作状态、当前菜单、烹饪时长、剩余时长、预约剩余时间、
  保温时长、烹饪完成时间（timestamp 设备类）、口感、食谱类型、米种、故障、自动保温、水开标志。
- **5 个服务**（均支持返回结果）：
  - `chunmi_cooker.set_reservation` — 按官方 App 完整流程预约烹饪，支持
    `menu` / `finish_time`(HH:MM) / `delay_minutes` / `cook_minutes` / `taste` /
    `auto_keep_warm` / `upload`
  - `chunmi_cooker.start_now` — 立即开煮
  - `chunmi_cooker.cancel` — 取消烹饪 / 预约
  - `chunmi_cooker.get_status` — 返回全部 MIoT 属性当前值
  - `chunmi_cooker.list_recipes` — 列出淳米云为本机型提供的食谱
- **加热曲线动态获取**：cookcode（烹饪档案）中的加热曲线在运行时从淳米官方云
  按「机型 + 食谱」拉取并缓存，**代码中不含任何曲线数据**；云上食谱更新后自动生效。

### 说明

- cookcode 字段布局与 CRC16/CCITT-FALSE 算法逆向自米家官方插件包
  `mijia.chunmi.cooker.eh3`，并与淳米云返回的 13 份真实档案逐一校验（CRC 全部复现）。
- 预约语义与官方一致：`orderTime` = 从现在到「**完成**时刻」的分钟数。
- 直接调用 `MIoTClient.action_async`，设备返回错误码时会抛出 `MIoTClientError`，
  不像 HA 原生实体路径那样静默吞掉错误。

### 已知限制

- 官方仅对「精煮饭」菜单开放口感设置，其它菜单写入后设备可能忽略。
- 部分菜单不允许改时长（上限 == 下限），此时传 `cook_minutes` 会报错。
- 电饭煲为 Wi-Fi 休眠设备，深度休眠时指令由云端排队，实际生效会延后到设备唤醒。
- 仅在淳米 EH 系列机型上验证过协议细节。
- 本版本已通过静态校验与协议/编解码层验证；**尚未在真实 Home Assistant 实例中完成端到端运行验证**。

[0.1.4]: https://github.com/ch3n4y/ha-chunmi-cooker/releases/tag/v0.1.4
[0.1.3]: https://github.com/ch3n4y/ha-chunmi-cooker/releases/tag/v0.1.3
[0.1.2]: https://github.com/ch3n4y/ha-chunmi-cooker/releases/tag/v0.1.2
[0.1.1]: https://github.com/ch3n4y/ha-chunmi-cooker/releases/tag/v0.1.1
[0.1.0]: https://github.com/ch3n4y/ha-chunmi-cooker/releases/tag/v0.1.0

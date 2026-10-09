# Changelog

本项目的所有重要变更都会记录在这里。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

[0.1.0]: https://github.com/ch3n4y/ha-chunmi-cooker/releases/tag/v0.1.0

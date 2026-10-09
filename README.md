# Chunmi Rice Cooker for Home Assistant

> **⚠️ 本仓库已归档，不再维护。**
>
> 最后一次发布是 `v0.1.4`，功能完整可用（状态实体、预约烹饪、局域网直连、通道诊断）。
> 后续能力已转移到**独立的前后端分离 App**（不依赖 Home Assistant）。
>
> 协议层的全部成果都在这里，可以直接复用：
> - `cookprofile.py` —— cookcode 编解码 + CRC16/CCITT-FALSE
> - `joyami.py` —— 淳米云签名 / 食谱与加热曲线 / 完成上报
> - `lan.py` —— miIO 局域网报文层（与官方 `miot_lan.py` 逐字节一致）
> - `tests/test_lan.py` —— 协议层单元测试
>
> 仍可通过 HACS 安装使用。

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

把**淳米（知吾煮 / chunmi）电饭煲**接进 Home Assistant，并且**复用官方 Xiaomi Home 集成已经登录的账号**——
不需要再填任何 token、密钥或账号密码。

> 为什么单独做一个？官方的 Xiaomi Home 集成虽然能读出电饭煲的 `siid=3` 属性，但**不能设置预约**：
> 预约需要往设备写一段私有的「烹饪档案」（cookcode），里面包含**该菜单专属的加热曲线**。
> 本集成把这套机制补齐了。

---

## 功能

- 🍚 **引导式添加**：从 Xiaomi Home 已同步的设备里自动发现电饭煲，点两下就加好
- 📊 **状态实体**：工作状态 / 当前菜单 / 烹饪时长 / 剩余时长 / 预约倒计时 / 预计完成时间 / 口感 / 故障 …
- ⏰ **预约烹饪**：按官方 App 的完整流程武装预约（改时长、口感、完成时刻）
- 🔥 **加热曲线全部动态获取**：不硬编码，随菜单从淳米云实时拉取
- 🛠 服务可返回结果，方便做自动化和调试

## 环境要求

| 项 | 要求 |
| --- | --- |
| Home Assistant | 2024.6 或更新 |
| 依赖 | 已配置好的官方 **Xiaomi Home**（`xiaomi_home`）集成 |
| 设备 | 米家 App 里的淳米电饭煲（机型以 `chunmi.cooker.` 开头，已在 Xiaomi Home 中可见） |

本集成**不依赖**米家 App 在本机运行，设备也不需要和 HA 在同一局域网。

---

## 安装

### HACS（推荐）

1. HACS → 集成 → 右上角 ⋮ → **自定义存储库**
2. 地址填本仓库，类别选 **Integration**
3. 安装后重启 Home Assistant

### 手动

把 `custom_components/chunmi_cooker` 整个目录拷到 HA 的 `config/custom_components/` 下，然后重启。

---

## 添加设备

1. 设置 → 设备与服务 → **添加集成** → 搜索 `Chunmi Rice Cooker`
2. 如果没装 Xiaomi Home，会提示你先装（本集成要用它的账号会话）
3. 发现多台电饭煲时会出现下拉列表让你选；只有一台就直接进确认页
4. 确认后即可看到一组状态实体

---

## 实体

| 实体 | 说明 |
| --- | --- |
| `sensor.<设备>_工作状态` | 待机中 / 烹饪中 / 预约中 / 保温中 / 设备异常 / 固件升级中 / 烹饪已完成 |
| `sensor.<设备>_当前菜单` | 精煮饭 / 煮粥 / 保温 …（附 `menu_id`） |
| `sensor.<设备>_烹饪时长` | 本次设定的烹饪分钟数 |
| `sensor.<设备>_剩余时长` | 秒 |
| `sensor.<设备>_预约剩余时间` | 分钟 |
| `sensor.<设备>_烹饪完成时间` | 时间戳设备类，可直接在 UI 里显示 |
| `sensor.<设备>_口感` | 软 / 适中 / 硬（附原始 index） |
| `sensor.<设备>_故障` | 无故障 / 各类传感器与电压异常 |
| 诊断类 | 食谱类型、米种、自动保温、水开标志 |

> 电饭煲是 Wi-Fi 休眠设备，状态是**推送**的，所以不一定每秒都在变。集成默认 60 秒做一次轻量兜底读取，取不到时会保留上一次的值而不是把实体打成 unavailable。

---

## 服务

### `chunmi_cooker.set_reservation` —— 预约烹饪

```yaml
action: chunmi_cooker.set_reservation
data:
  menu: 煮粥            # 也可以填 menuId，如 3
  finish_time: "06:00"  # 期望完成的时刻；今天已过则算明天
  cook_minutes: 60      # 覆盖菜单默认时长（超出该菜单允许区间会报错）
  taste: 0              # 0=软 1=适中 2=硬
  auto_keep_warm: true
  upload: true          # 是否同时做官方 App 的 uploadCookData 上报
response_variable: result
```

`finish_time` 和 `delay_minutes` 二选一（后者是「从现在起多少分钟后完成」）。

自动化例子——每天早上 6 点煮好粥：

```yaml
action: chunmi_cooker.set_reservation
data:
  menu: 煮粥
  finish_time: "06:00"
  cook_minutes: 60
  taste: 0
```

### `chunmi_cooker.start_now` —— 立刻开煮

```yaml
action: chunmi_cooker.start_now
data:
  menu: 精煮饭
```

### 其它

| 服务 | 说明 |
| --- | --- |
| `chunmi_cooker.cancel` | 取消当前烹饪 / 预约 |
| `chunmi_cooker.get_status` | 返回全部 MIoT 属性当前值（可带 `did` 指定多台中的某一台） |
| `chunmi_cooker.list_recipes` | 列出淳米云为本机型提供的所有食谱（`refresh: true` 强制刷新） |

加了多台电饭煲时，所有服务都支持 `did` 参数指定目标。

---

## 加热曲线是怎么来的

很多人会以为曲线得写死在代码里——**不用**。

米家的烹饪档案叫 **cookcode**，是一段十六进制字符串（本机型为 352 字符 / 176 字节），
`byte 20` 之后就是该菜单专属的加热曲线，末尾 2 字节是 CRC16。这个字符串由**淳米云按「机型 + 食谱」下发**：

```
GET  https://gateway.joyami.com/mijia-pot-service/portal/newIndex
        ?deviceId=<did>&model=<model>&useId=<uid>
     -> defaultRecipes[] / recipeExpands[]   每条都带完整 cookcode

GET  https://gateway.joyami.com/mijia-pot-service/recipe/list/<recipeId>
     -> 单个食谱的完整 cookcode（含曲线）

GET  https://gateway.joyami.com/microwaveoven/index/getRecipePage
     -> 分页食谱目录，可枚举更多食谱
```

本集成在需要时（带缓存）实时拉取，所以：

- 代码里**没有任何曲线数据**
- 淳米云新增/调整食谱后自动生效
- 只是「改参数」：拿一份合法的 cookcode，改掉时长/口感/预约，重算 CRC 再下发

### cookcode 字段布局

| 偏移 | 含义 |
| --- | --- |
| `[0]` | 设备类型 |
| `[1]` | 子类型 |
| `[2]` | index |
| `[3..6]` | menuId（大端 uint32） |
| `[7]` | bit7 自定义保存 · bit6 可预约 · bit5 可设自动保温 |
| `[8..9]` | 烹饪时长 时/分 |
| `[10..11]` | 时长上限 时/分 |
| `[12..13]` | 时长下限 时/分 |
| `[14]` | bit7 预约标志；低 7 位 = 预约时长「时」 |
| `[15]` | 低 7 位 = 预约时长「分」；bit7 自动保温 |
| `[19]` | 口感索引（0=软 / 1=适中 / 2=硬） |
| `[20..N-3]` | **加热曲线（菜单专属）** |
| `[N-2..N-1]` | CRC16/CCITT-FALSE(poly `0x1021`, init `0`, 不反转, 不异或输出) |

预约语义与官方 App 一致：**`orderTime` = 从现在到「完成时刻」的分钟数**（不是到开始时刻）。
若要「6 点吃上」，就按 6 点算；设备自己会用「完成时刻 − 烹饪时长」倒推开煮时间。

### 下发顺序（与官方 App 完全一致）

```js
// 官方插件 mijia.chunmi.cooker.eh3 的 startCooking()
uploadCookData(scheduleTime, recipeId, cookcode, bschedule, …)   // ① 上报淳米云
CMDeviceIoT.startCooking(cookcode)                               // ② MIoT 动作 siid=3 aiid=1
```

本集成两步都会执行（① 可用 `upload: false` 关掉）。

---

## 数据与隐私

- 集成**不采集、不上传**任何数据到第三方；它只做两件事：通过 Xiaomi Home 已建立的会话读写设备，以及向淳米官方接口取食谱。
- 集成里**不包含任何账号信息**：`uid` 在运行时从你本地的 Xiaomi Home 配置项读取，`did` 由设备列表动态发现。仓库里没有 token、没有设备 ID、没有 MAC。
- `joyami.py` 里的 `APP_ID` / `APP_KEY` 是**米家公开插件包里自带的应用级常量**（非用户凭据），仅用于给淳米云接口签名。

## 已知限制

- 官方仅对「精煮饭」菜单开放口感设置（其它菜单写入后设备可能忽略）。
- 部分菜单不允许改时长（`上限 == 下限`），此时 `cook_minutes` 会报错。
- 设备深度休眠时指令由云端排队，实际生效会延后到它唤醒。
- 仅在淳米 EH 系列上验证过；其它机型欢迎反馈 `model` 与实测结果。

## License

MIT

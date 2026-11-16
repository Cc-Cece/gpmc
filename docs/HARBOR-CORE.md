# Harbor Core — PicHarbor 共享业务核心（`gpmc/harbor_core.py`）

Android 与 iOS 共用的备份业务决策层：纯 Python，无平台导入、无网络；
除 `store_save` / `store_load` 的显式路径外不碰文件系统。两端通过锁定
快照消费同一份 `gpmc`，因此这里的接口是冻结的——Android / iOS 适配器
正按本文档写调用代码，字段名、action 名、错误码不得擅自改动。

## 0. 核心不含删除 — 清理执行交给 Google 相册官方

**本核心没有任何删除本机资产的能力：没有删除函数、没有删除 action、
没有删除后门，也不留任何可绕过判定执行删除的路径。**

依据：PicHarbor-iOS `docs/REQUIREMENTS.md` 2026-10-19 最新范围修订——
本机清理交由 Google 相册官方“释放空间”，取消 PicHarbor 自建本机清理
功能；PicHarbor 只保留备份确认和官方清理指引，不再扫描或删除本机资产。
Yuto 2026-11-13 再次明确：新版 Android 也不内置释放空间（旧版的
`CleanupPolicy` / `CleanupRepository` / `CleanupWorker` 不再复用），改为
深链/跳转到 Google 相册，把释放空间交给官方功能。

因此：

- `decide_cleanup` 的 `eligible: true` **仅表示**“云端已确认原件、证据
  新鲜，可以向用户展示官方释放空间指引/跳转”。它不是删除许可，更不是
  删除执行；客户端收到 `eligible: true` 后只能展示指引或打开 Google
  相册，绝不能据此扫描或删除本机文件。
- `eligible: false` 时客户端不得展示官方清理指引（证据不足时绝不放行）。
- 历史章节里的清理核验、风险许可、选择列表和删除执行要求已被上述修订
  取代，不再作为应用交付必需项；本核心不实现它们。

## 1. 与 `Protocol` 的分工

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 业务决策 | `gpmc/harbor_core.py` | 任务状态机、去重判定（含多资源逐项判定）、重试退避、清理指引资格判定（页级与逐项）、批量暂停/恢复、备份范围判定、显式代理配置校验、LiveLink 握手与范围判定、任务持久化、日志脱敏 |
| 协议执行 | `gpmc/protocol.py`（`Protocol`） | 准备 / 提交 / 确认 / 云端快照等 Google Photos 线路协议；真正发网络、查远端 |

典型链路：客户端创建任务（`create_task`）→ `decide_prepare` 问云端
是否已有（`remote_key` 由调用方先经 `Protocol.prepare_upload` 哈希
查找得到；核心本身不发网络）→ 未命中则按状态机推进上传，`Protocol`
执行每一步线路调用 → `confirming` / `done` 后，`decide_cleanup` 判断
证据是否足以展示官方释放空间指引。

`Protocol.check_cleanup` 保留为**纯证据查询函数**（按哈希找云端 key，
校验快照新鲜度 300 秒、云端状态、原件访问证据），它本身不删除任何
东西。**新版两端不得用它授权或触发删除**，也不得用它做清理决策；
备份确认与清理资格严格区分——“已备份/已确认”绝不自动转化为可删除。
本核心不修改、不删除该函数。

错误码复用 `gpmc/errors.py::classify` 的输出集合与 `ProtocolError` 的
稳定编码习惯；`dispatch` 永不抛出未分类异常（见 §5）。

## 2. 调用入口

```python
from gpmc.harbor_core import dispatch, dispatch_json, HarborCore

dispatch(action, value)          # value: dict -> {"ok": True, ...} 或 {"ok": False, "error": CODE}
dispatch_json(action, payload)   # payload: JSON 字符串 -> JSON 字符串（供 Chaquopy / PythonBridge）
HarborCore().dispatch(action, value)  # 同 dispatch 的对象式门面，行为完全一致
```

- 未知 action → `{"ok": False, "error": "UNKNOWN_OPERATION"}`。
- `dispatch_json` 的 payload 不是合法 JSON 对象时返回
  `{"ok": false, "error": "INVALID_JSON"}`。
- 每个 action 的裸函数（`create_task(...)` 等）返回 §3 所写的 payload；
  经 `dispatch` 调用时成功结果会额外带上 `"ok": true`，错误结果原样返回。

### Android（Chaquopy）

```python
# Python 侧：直接 import gpmc.harbor_core 后调用 dispatch / dispatch_json。
# Chaquopy 建议走 dispatch_json：Kotlin 传 JSON 字符串、收 JSON 字符串，
# 避免跨语言 dict 转换。
result = json.loads(dispatch_json("transition", json.dumps({
    "task": task_dict, "to": "uploading", "idempotency_key": op_key})))
```

### iOS（嵌入式 Python 3.13 + PythonBridge）

```python
# 同样优先使用 dispatch_json：Swift 侧以 String 往返，PythonBridge 只传字符串。
# 任务字典、store 路径等一律由 Swift 侧持有，核心不保存任何全局任务表。
```

核心是无状态的：任务以 dict 形式随每次调用传入传出，核心不跨调用
持有任务（幂等只靠“task 已处于目标状态即为重复提交”这一规则，见 §4）。
因此进程重启、桥接重建都不会丢失状态——状态的唯一真相在调用方持久
化的 store 文件里（§7）。

## 3. Action 一览

| action | `value` | 成功返回 | 错误返回 |
| --- | --- | --- | --- |
| `create_task` | `{"task_id", "asset", "idempotency_key"?}` | `{"task": {...}}` | `INVALID_ARGUMENT` / `INVALID_DIGEST` / `INVALID_MEDIA_KIND` |
| `transition` | `{"task", "to"（或 `to_state`）, "event"?, "error_code"?, "remote_key"?, "cloud_confirmed"?, "idempotency_key"?}` | `{"task": {...}}`（幂等重放时多一个 `"idempotent_replay": true`） | `INVALID_TRANSITION` / `INVALID_TASK` / `INVALID_ARGUMENT` |
| `decide_prepare` | `{"asset", "remote_key"?}`（或直接传 asset 字段） | `{"exists": true, "remote_key": ...}` 或 `{"exists": false}`；多资源资产另带 `"resources": [{role, exists, remote_key?}]`，有 scope 时另带 `"in_scope"` / `"scope_reason"`（见 §8） | `INVALID_DIGEST` / `INVALID_ARGUMENT` / `INVALID_MEDIA_KIND` / `INVALID_SCOPE` |
| `decide_cleanup` | `{"task", "evidence", "policy"?}`；`evidence` 可带 `"items"` 做逐项证据（见 §6） | `{"eligible": true}` 或 `{"eligible": false, "reason": CODE}`；逐项模式另带 `"items": [{task_id, eligible, reason?}]` | `INVALID_CHECK_POLICY` / `INVALID_TASK` / `INVALID_ARGUMENT` |
| `batch_pause` | `{"task_ids": [...], "tasks": {id: task}}` | `{"results": [{task_id, ok, task? \| error?}], "succeeded": N, "failed": M}` | `INVALID_ARGUMENT`（整批入参非法时）；单任务失败见 §12 |
| `batch_resume` | `{"task_ids": [...], "tasks": {id: task}}` | 同上（仅 `paused` / `interrupted` 可恢复，见 §12） | 同上 |
| `evaluate_scope` | `{"scope", "asset"}` | `{"in_scope": bool, "reason": str}`（见 §11） | `INVALID_SCOPE` / `INVALID_ARGUMENT` / `INVALID_MEDIA_KIND` |
| `validate_proxy` | `{"proxy": {enabled, host, port, scheme?, auth?}}`（或直接传 proxy 字段） | `{"ok": true, "enabled": bool, "scheme"?, "host"?, "port"?}` | `INVALID_PROXY` / `PROXY_TLS_VERIFICATION_DISABLED` / `INVALID_ARGUMENT` |
| `livelink_handshake` | `{"capabilities": [...]}`（或 `{name: bool}`） | `{"ok": true, "missing": [], "capabilities": [...]}` | `MISSING_CAPABILITIES` / `INVALID_ARGUMENT` |
| `livelink_scope` | `{"scope", "asset"}` | `{"in_scope": bool, "reason": str, "requires_confirmation": true}` | `INVALID_SCOPE` / `INVALID_ARGUMENT` / `INVALID_MEDIA_KIND` |
| `migrate_legacy` | `{"records": [...]}`（或 `{"tasks": [...]}`；也可直接调裸函数） | `{"tasks": [...]}` | `INVALID_ARGUMENT` |
| `retry_delay` | `{"attempt"}`（0 起） | `{"delay_seconds": N}` | `INVALID_RETRY_ATTEMPT` |
| `store_save` | `{"path", "tasks"}` | `{"saved": true, "path": ..., "count": N}` | `INVALID_TASK` / `FILE_ACCESS` |
| `store_load` | `{"path"}` | `{"tasks": {...}}` | `FILE_ACCESS` / `STORE_CORRUPT` / `STORE_VERSION_UNSUPPORTED` |
| `log_event` | `{"event": {...}}`（或直接传 event 字段） | `{"event": <脱敏后事件>}` | `INVALID_ARGUMENT` |
| `capabilities` | `{}` | `{"harbor_core_version": 1, "protocol_version": 2, "states": [...], "actions": [...]}` | — |

### Task 字段（冻结）

```jsonc
{
  "task_id": "task-1",
  "asset": {"local_id": "...", "filename": "IMG_0001.heic",
            "sha1": "<40 hex>", "sha256": "<64 hex>", "size": 123,
            "media_kind": "photo|video|live_photo", "created_at": "<ISO 8601>"},
  "state": "queued",
  "error_code": null,
  "attempts": 0,
  "idempotency_key": null,
  "remote_key": null,
  "cloud_confirmed": false,
  "created_at": "<ISO 8601 UTC>",
  "updated_at": "<ISO 8601 UTC>",
  "history": [{"state": "queued", "at": "<ISO 8601 UTC>", "event": "created"}]
}
```

- `asset` 校验与 `Protocol.digest` 同一边界：sha1 须为 40 位小写 hex、
  sha256 须为 64 位小写 hex、`size` 须为 `> 0` 的整数，否则
  `INVALID_DIGEST`；`media_kind` 不在四者之内返回 `INVALID_MEDIA_KIND`。
  `media_kind` 现为 `photo` / `video` / `live_photo` / `motion_photo`
  （第二阶段新增 `motion_photo`；Live Photo / Motion Photo 按一个媒体
  项目确认，见 §8）。`asset` 还可带 `duration_seconds`（视频 /
  Live / Motion Photo 时长，≥ 0）、`resources`（多资源组成，见 §8）
  与 `scope`（备份范围，见 §11）；`create_task` 会原样保留经校验的
  这三项。
- `attempts` 只在 `queued -> preparing` 时 +1（一次上传尝试的开始）。
  `failed` / `interrupted` / `waiting_network -> queued` 重试时清零
  `error_code`，下一次 `queued -> preparing` 再计新尝试。
- 进入 `done` 时 `cloud_confirmed` 自动置 `true`。

## 4. 状态机

状态集合（`capabilities().states`）：`queued, preparing, dedup_hit,
uploading, submitting, confirming, done, paused, failed, waiting_network,
waiting_confirmation, cancelled, interrupted`。

```
queued ──► preparing ──► uploading ──► submitting ──► confirming ──► done
              │              │              │              │
              └────► dedup_hit ──► done    │              └────► waiting_confirmation ──► done
                                            │                               │
              任一运行态 ──► paused / cancelled / failed                    │
              failed / interrupted ──► queued（重试）                      │
              waiting_network ──► queued / preparing / uploading（恢复）   │
done、cancelled 为终态，无出边。
```

合法边（完整表见 `harbor_core._ALLOWED`，以下为全部）：

- `queued`: `preparing, paused, cancelled, failed, waiting_network`
- `preparing`: `uploading, dedup_hit, paused, cancelled, failed, waiting_network, interrupted`
- `dedup_hit`: `done, paused, cancelled, failed`
- `uploading`: `submitting, paused, cancelled, failed, waiting_network, interrupted`
- `submitting`: `confirming, paused, cancelled, failed, waiting_network, interrupted`
- `confirming`: `done, waiting_confirmation, paused, cancelled, failed, waiting_network, interrupted`
- `waiting_network`: `queued, preparing, uploading, paused, cancelled, failed, interrupted`
- `waiting_confirmation`: `confirming, done, paused, cancelled, failed, interrupted`
- `paused`: `queued, cancelled, failed`
- `failed`: `queued`
- `interrupted`: `queued, cancelled, failed`
- `done` / `cancelled`: （无）

非法转移返回 `{"ok": false, "error": "INVALID_TRANSITION"}`，不抛异常。
任务本身损坏（非 dict、缺 `task_id`、`state` 不在集合内）返回
`INVALID_TASK`。

**幂等**：同一 `idempotency_key` 重复提交同一转移时，若 task 已处于
目标状态，直接返回当前 task，不增加 `attempts`、不追加 `history`、
不产生重复提交标记（提交标记由 `history` 中 `submitting` 条目派生，
重复转移不会多出一条）。响应里多带 `"idempotent_replay": true`。
注意：只有“已处于目标状态”的重复才是无操作重放；状态已前进后的
旧转移按正常非法边处理。

**批量暂停/恢复不新增状态与转移**：`batch_pause` / `batch_resume`
（见 §12）只是对一批任务逐个调用同一 `transition`，`pause` 走已有
的 `→ paused` 边，`resume` 走已有的 `paused` / `interrupted → queued`
边；状态集合与合法边表不变。

## 5. 错误码

复用 `gpmc.errors.classify` 的输出集合与 `ProtocolError` 编码习惯：

| 码 | 含义 |
| --- | --- |
| `INVALID_ARGUMENT` | 缺必填字段或字段类型错误 |
| `INVALID_TASK` | task 不是合法任务 dict（缺 `task_id` / 非法 `state`） |
| `INVALID_TRANSITION` | 状态边不合法，或目标状态不在集合内；`done` / `cancelled` 为终态 |
| `INVALID_DIGEST` | sha1/sha256/size 校验失败（与 `Protocol.digest` 同边界） |
| `INVALID_MEDIA_KIND` | `media_kind` 不在 `photo` / `video` / `live_photo` / `motion_photo` 内 |
| `INVALID_SCOPE` | 备份范围非法：未知版本（`scope<N>:` 且 N ≠ 2、dict `version` ≠ 2）、未知 `mode`、`minImageKb` 超出 10..1024、`advanced` 无相册、日期非法或 `from > to`、`photos` 与 `videos` 全关；**绝不退回全量** |
| `INVALID_PROXY` | 显式代理配置非法：启用时缺 `host` / `port`、端口超出 1..65535、`scheme` 不在 `http` / `https` 内、`host` 含 URL scheme 或空白、`auth` 非法 |
| `PROXY_TLS_VERIFICATION_DISABLED` | 代理配置关闭了 TLS 校验（`verify_tls` / `tls_verify` / `verify_ssl` / `verify` 为 `false`，或 `insecure` 为 `true`） |
| `MISSING_CAPABILITIES` | LiveLink 握手缺能力（`livelink_handshake` 的 `missing` 列出缺失项） |
| `TASK_NOT_FOUND` | 批量操作中 `task_ids` 里的 id 不在 `tasks` 表里（仅该任务失败，不中断整批） |
| `INVALID_CHECK_POLICY` | `decide_cleanup` 的 policy 缺失或非布尔（证据新鲜时才报此错） |
| `INVALID_RETRY_ATTEMPT` | `retry_delay` 的 attempt 非负整数以外的值 |
| `INVALID_JSON` | `dispatch_json` 的 payload 不是合法 JSON 对象 |
| `INVALID_STORE_VERSION` / `STORE_VERSION_UNSUPPORTED` | store 的 `version != 1` |
| `STORE_CORRUPT` | store 文件不是合法 JSON 或结构不对 |
| `FILE_ACCESS` | store 文件缺失 / 不可读 / 不可写（与 `classify` 的 OSError 映射一致） |
| `UNKNOWN_OPERATION` | 未知 action |
| `UNKNOWN_LEGACY_STATE` | 仅作迁移后任务的 `error_code`（见 §7），不是 dispatch 错误 |
| `CLOUD_STATE_EXPIRED` | 清理指引证据缺失 / 过期 / 来自未来（见 §6） |
| `REMOTE_MISSING` / `CLOUD_ORIGINAL_UNCONFIRMED` / `ORIGINAL_UNAVAILABLE` / `CLEANUP_GUIDANCE_DENIED` | `decide_cleanup` 的 `eligible: false` 原因（见 §6） |
| `ENGINE_ERROR` 等 | `dispatch` 兜底：未预料的异常经 `errors.classify` 映射，永不原样抛出 |

## 6. `decide_cleanup` — 只判“能否展示官方释放空间指引”

调用：`decide_cleanup(task, evidence)`；`evidence` 形如

```jsonc
{"cloud_state": "confirmed_original", "checked_at": 1793123456, "policy": true}
```

- `checked_at` 为 epoch 秒；证据新鲜度上限 **3600 秒**（本核心冻结接口
  的规定）。它与 `Protocol.check_cleanup` 的 300 秒快照新鲜度窗口是
  两套不同的时效，不要混淆；自建清理时代的“5 分钟清理证据”规则已随
  自建清理一并废止（文档冲突 C5）。备份确认的证据机制（15 秒确认缓
  存、确认退避 5/10/20/30 秒封顶 30 秒）也是另一套机制，不受本条影响。
  缺失 `cloud_state` / `checked_at`、时间戳非法、过期（> 3600 秒）或来自
  未来，一律返回 `{"eligible": false, "reason": "CLOUD_STATE_EXPIRED"}`。
  证据问题先于策略错误报告，且**永远只会拒绝、不会放行**。
- `policy`（是否允许对该任务/账号展示官方清理指引）须为布尔值，可
  放在 `evidence["policy"]` 或顶层 `policy`。证据新鲜的前提下，缺失
  或非布尔返回 `{"ok": false, "error": "INVALID_CHECK_POLICY"}`。
- 仅当 `cloud_state == "confirmed_original"` **且** policy 允许时返回
  `{"eligible": true}`；其余一律 `{"eligible": false, "reason": CODE}`：
  `policy` 不允许 → `CLEANUP_GUIDANCE_DENIED`；`cloud_state` 为
  `missing` / `not_found` → `REMOTE_MISSING`；为
  `original_unavailable` → `ORIGINAL_UNAVAILABLE`；其他未确认状态 →
  `CLOUD_ORIGINAL_UNCONFIRMED`。
- 本函数不判断 task 的当前状态（调用方应在备份确认后调用）；不执行
  任何删除；不扫描本机资产。

### 逐项证据（第二阶段）

`evidence` 可带 `"items"`，对一页（或一批）任务逐项判定：

```jsonc
{"items": [
  {"task_id": "t-1", "cloud_confirmed": true,
   "confirmed_at": 1793123456, "evidence_fresh_seconds": 3600},
  {"task_id": "t-2", "cloud_confirmed": true, "confirmed_at": 1793123400}
], "policy": true}
```

- 每项按自己的证据判定：`confirmed_at` 缺失/非法、过期（超过
  `evidence_fresh_seconds`，缺省 3600 秒）或来自未来 →
  `{"eligible": false, "reason": "CLOUD_STATE_EXPIRED"}`；
  `cloud_confirmed` 不为 `true` →
  `{"eligible": false, "reason": "CLOUD_ORIGINAL_UNCONFIRMED"}`。
  **无证据、证据过期、未确认的项一律不放行；核心绝不伪造证据**，
  缺证据的项不会被默认当作已确认。
- 返回 `{"eligible": bool, "items": [{task_id, eligible, reason?}]}`；
  页级 `eligible` 仅当**全部**逐项都 eligible 且 policy 允许时为
  `true`，否则为 `false` 并带页级 `reason`（首个失败项的原因；policy
  不允许时为 `CLEANUP_GUIDANCE_DENIED`，此时逐项也记该原因）。
  `items` 为空列表时页级 `eligible: false`，原因
  `CLOUD_STATE_EXPIRED`（无证据）。
- 逐项模式下顶层 `cloud_state` / `checked_at` 不需要；页级 `task`
  参数可省略（逐项自带 `task_id`），传了则仍须是合法任务 dict。
  证据全部新鲜的前提下 policy 缺失或非布尔，仍返回
  `{"ok": false, "error": "INVALID_CHECK_POLICY"}`；单项结构非法
  （非 dict、缺 `task_id`）返回 `INVALID_ARGUMENT`。
- 逐项模式仍只判“能否展示官方释放空间指引”，不执行删除；是否接逐
  项证据源（如从 reconcile 结果）由调用方决定，核心不替调用方编造
  证据来源。

## 7. 存储格式与 legacy 迁移

### `store_save` / `store_load`

文件为 JSON v1：

```json
{"version": 1, "tasks": {"<task_id>": {<task dict>}, ...}}
```

- `store_save(path, tasks)`：`tasks` 可为 `{task_id: task}` dict 或 task
  list。写入是原子的——同目录写临时文件、fsync 后 `os.replace` 覆盖，
  崩溃不会留下写一半的 store；失败时清理临时文件。调用方负责保证
  父目录存在（不存在返回 `FILE_ACCESS`）。
- `store_load(path)`：校验 `version == 1` 与每条 task 的 `state` 是否
  在状态集合内；`version != 1` → `STORE_VERSION_UNSUPPORTED`，非法
  JSON/结构 → `STORE_CORRUPT`，文件缺失/不可读 → `FILE_ACCESS`。
- 任务持久化与账户生命周期由调用方负责（与 `docs/MAINTENANCE.md`
  一致）；store 文件本身不含任何凭据，日志导出时同样不得带入凭据。

### `migrate_legacy(records)`

旧状态映射：`confirmed -> done`、
`confirmation_pending -> waiting_confirmation`、
`uploading` / `interrupted -> interrupted`。已是当前合法状态的旧记录
保持原状态；其余未知状态 → `failed` 并附
`error_code: "UNKNOWN_LEGACY_STATE"`。缺失字段按 Task 默认值补齐，
`history` 记一条 `{"event": "migrated"}`。输入可为 list 或
`{task_id: record}` dict；返回 `{"tasks": [...]}`。也可经
`dispatch("migrate_legacy", {"records": [...]})` 调用。

未知字段容错（第二阶段）：不在核心任务字段集里的旧字段会被保留在
迁移后任务上，并在 `"unknown_fields"` 里列出，不静默丢弃。例外只
有凭据与媒体本体（见下），它们永不进入核心存储/日志：

- 顶层键名含 `token` / `password` / `auth` / `credential` /
  `account_id` / `secret` 的字段、以及 `media_body` / `media_bytes`
  字段会被丢弃，丢弃的键名记在 `"redacted_fields"` 里（`asset` 内
  同类键同样处理，记为 `"asset.<key>"`）。
- `"credentials"` 块**永不迁移**：本体直接丢弃，任务只标记
  `"credentials_migrated": false`。调用方须经自己的安全流程重新建立
  凭据，核心从不持有凭据。
- `"settings"` dict 会保留，但其中凭据类键同样剥离（记为
  `"settings.<key>"` 进 `"redacted_fields"`）；非凭据设置原样保留。
  `"settings"` 非 dict 时记为空 dict。

## 8. 重试退避与去重

- `retry_delay(attempt)`：指数退避，base 5 秒、上限 300 秒，
  `delay = min(5 * 2**attempt, 300)`。`attempt` 为 0 起的已失败次数：
  0→5s、1→10s、2→20s、3→40s、4→80s、5→160s、6+→300s。
  注意与**确认轮询退避**区分：云端确认等待的退避是 5/10/20/30 秒、
  封顶 30 秒（确认语义，15 秒确认缓存），属于确认机制，不走本函数；
  本函数只用于任务重试退避。
- `decide_prepare(asset, remote_key=None)`：仅做去重判定，不发网络。
  `remote_key` 由调用方先行查得（`Protocol.prepare_upload` 的哈希
  查找）；有则 `{"exists": true, "remote_key": ...}`，无则
  `{"exists": false}`。去重命中时任务应走 `preparing -> dedup_hit -> done`。

### 多资源 / Live Photo / Motion Photo（第二阶段）

- `asset["media_kind"]` 可为 `motion_photo`（与 `live_photo` 同为
  “一个媒体项目”）；`duration_seconds`（≥ 0）随资产校验。
- `asset["resources"]` 列出组成资源，每项
  `{role: photo|video, digest?, sha1?, sha256?, size_bytes?, filename?}`，
  同一 `role` 不得重复；`digest` 为单个 40 或 64 位 hex。去重判定按
  资源逐项进行，调用方用 `remote_keys` 传入每资源的远端 key——dict
  按 `role` 或按内容哈希（`digest` / `sha1` / `sha256`）查，或与
  `resources` 等长的 list 按位置对应。返回：

  ```jsonc
  {"exists": false, "resources": [
    {"role": "photo", "filename": "IMG_0001.heic",
     "exists": true, "remote_key": "cloud-key-photo"},
    {"role": "video", "filename": "IMG_0001.mov", "exists": false}
  ]}
  ```

  顶层 `"exists"` 仅当**全部**资源都已存在时为 `true`（Live Photo /
  Motion Photo 任一组成缺失都必须上传该组成，不得只传一半；与配对
  文档“任一组成文件无法读取时不单独发送另一部分”一致，判定层面的
  含义是：配对按一个媒体项目确认）。单 `remote_key` 与 `resources`
  不可同传（歧义，返回 `INVALID_ARGUMENT`）；无 `resources` 时
  `remote_keys` 也不得传。
- `decide_prepare` 的 `scope` 参数（或 `asset["scope"]`）按 §11 语义
  评估，结果追加 `"in_scope"` / `"scope_reason"`；范围只作标注，不
  改变去重结论；范围非法返回 `INVALID_SCOPE`。

## 9. 日志脱敏（`log_event`）

与 Android 诊断设计一致：**脱敏发生在写入之前**，绝不先存明文再打码。
统一脱敏口径以 charter 为准（文档冲突 C6，总协调员 2026-11-13 裁定）：

- 文件名只允许 basename：`filename` / `file_name` / 含 `path` 的键的
  值一律截为 basename（最长 128 字符，控制字符替换）；**完整路径禁止
  写入日志**。basename 中含邮箱地址或秘密类子串时，整个值替换为
  `"[redacted]"`。
- 内容哈希禁止写入：键名含 `sha1` / `sha256` 的值一律替换为
  `"[redacted]"`。
- 凭据 / 令牌 / 账号标识禁止写入：键名含 `token` / `password` / `auth` /
  `credential` / `account_id` / `secret`（大小写不敏感、递归处理 dict
  与 list）的值一律替换为 `"[redacted]"`。
- 媒体本体不进日志：`media_body` / `media_bytes` 键直接删除；bytes
  值记为 `"[binary]"`。
- 不记录账号原文、请求/响应正文、照片本体、EXIF、SQL 参数；文件名
  只记 basename（由调用方保证不传入完整路径）。
- `log_event(event)` 只返回脱敏后的事件 dict，本身不写文件、不发网络；
  实际落盘、轮转（14 天 / 64 MiB）、导出等由各端诊断模块负责。
  诊断日志不是恢复数据库，不得据一条日志决定重传或删除。
- 本函数的脱敏是**最低口径**，不是上限：适配器可在其上叠加更严的
  字段白名单（例如同时抹掉 `remote_key` 等云端标识），仍与本核心兼容。
- 设计与实现区分（文档冲突 C7）：Android `diagnostics/README.md` 与
  `RUNBOOK.md` 是**设计提案**，`IMPLEMENTATION.md` 的 1.0.9-beta.5 首版
  清单才是已实现的功能。本核心不宣称任何未实现的能力：自动根因分类、
  Python/native 栈采集、实际 socket 发送量、DNS/TLS 分阶段时序、完整
  持久状态 revision 审计、跨设备追踪、离线分析器均**未实现**，不得在
  文档或导出说明中写成已存在。

## 10. 实现时留下的显式选择（供适配器作者注意）

- `attempts` 只在 `queued -> preparing` 时 +1；`failed -> queued` 只
  清 `error_code`，不计次。
- `transition` 进入 `done` 时 `cloud_confirmed` 强制为 `true`；其他
  转移只在调用方显式传 `cloud_confirmed` 时才改。
- `decide_cleanup` 不按 task 状态设门，只看证据 + policy；调用方应在
  备份确认后调用。
- `migrate_legacy` 对已是当前合法状态的旧记录做直通保留；未知状态才
  判 `failed` + `UNKNOWN_LEGACY_STATE`。
- `CLEANUP_GUIDANCE_DENIED`、`INVALID_RETRY_ATTEMPT`、
  `INVALID_MEDIA_KIND`、`STORE_*` 为本核心新增编码，沿用
  `ProtocolError` 的 `INVALID_*` 命名习惯；其余编码与 `errors.classify`
  / `Protocol` 保持一致。
- 第二阶段新增编码（`INVALID_SCOPE`、`INVALID_PROXY`、
  `PROXY_TLS_VERIFICATION_DISABLED`、`MISSING_CAPABILITIES`、
  `TASK_NOT_FOUND`）同样沿用该命名习惯，见 §5。
- `batch_resume` 只接受 `paused` / `interrupted`（不接受 `failed`），
  即使状态机允许 `failed -> queued`：批量恢复是“继续被暂停的任务”，
  单任务重试仍走 `transition(failed -> queued)`，两者不要混用。
- `evaluate_scope` 里 `filterCache` 的含义是“过滤掉已缓存的资产”：
  资产明确标 `cached: true` 且范围开 `filterCache` 时判出范围；核心
  本身不持有缓存状态，缓存与否由调用方在资产里标明。

## 11. 备份范围判定（`evaluate_scope`，第二阶段）

范围语义取自配对备份范围文档（`scope2` / `catalog2`）与 iOS
`LiveLinkScopeRules` / `LiveLinkScope` 的已实现规则，核心只做判定，
不枚举相册、不读文件、不发网络。

### 范围的写法

- dict（scope2）：camelCase 字段
  `{mode, albums, dateEnabled, from, to, photos, videos,
  ignoreSmallImages, minImageKb, filterCache, default_albums?}`；
  也接受 snake_case 别名 `{date_from, date_to, min_image_kb,
  ignore_small_images, date_enabled, filter_cache, types}`，其中
  `types` 为 `["photo"|"video"|"live_photo"|"motion_photo"]` 的子集，
  用于同时推导 `photos` / `videos`（camelCase 显式值优先）。
- wire 字符串：`"all"` / `"library"` / `"selected"`（与 iOS 解析一致：
  通过校验但不设 mode，即全库范围）、`"range:<from>:<to>"`（旧版日期
  范围，等价于 `mode: "date"` + `dateEnabled: true`）、
  `"scope2:<JSON>"`（结构化范围）。
- **未知版本绝不退回全量**：`"scope<N>:"`（N ≠ 2）、dict 带
  `"version"` 且 ≠ 2、未知 `mode`，一律返回
  `{"ok": false, "error": "INVALID_SCOPE"}`。

### 校验（非法范围返回 `INVALID_SCOPE`）

- `mode` 须为 `default` / `all` / `date` / `advanced`；`photos` 与
  `videos` 须为布尔且至少一个为 `true`；`minImageKb` 须为 10..1024
  的整数（缺省 100）；`albums` 须为字符串 list，`advanced` 模式下
  不能为空；`dateEnabled` 时 `from` / `to` 须为严格 `yyyy-MM-dd`
  （与 iOS 的 Gregorian / en_US_POSIX / 非宽容格式一致），且
  `from ≤ to`。

### 逐项判定（返回 `{"in_scope": bool, "reason": str}`）

资产侧字段：`media_kind` / `type`、`created_at`（ISO-8601 /
`yyyy-MM-dd` / epoch 秒；另接受 `date` / `creation_date`）、`size` /
`size_bytes`、`albums` / `album_ids` / `album_id`、`cached`、
`in_default_album`。按以下顺序判定，首个不满足的即返回：

1. 类型：`photo` / `live_photo` / `motion_photo` 走 `photos` 侧，
   `video` 走 `videos` 侧；不满足 → `TYPE_EXCLUDED`。未知
   `media_kind` 返回 `INVALID_MEDIA_KIND`。
2. 相册：`advanced` 模式要求资产至少属于一个所选相册（无相册信息的
   资产判出范围）→ `ALBUM_NOT_SELECTED`；`default` 模式在范围给出
   `default_albums` 时同样取交集，否则按资产的 `in_default_album`
   标记（未标记时不设相册门）。
3. 日期：`dateEnabled` 时 `from` 含首日、`to` 含末日；资产无日期或
   日期无法解析 → `DATE_UNKNOWN`；超出起止 → `DATE_OUT_OF_RANGE`
   （与 iOS `includesDate` 一致：无日期的资产在开日期过滤时出范围）。
4. 小图：`ignoreSmallImages` 时仅对普通静态照片（`media_kind` 为
   `photo` 或缺省）且已知大小的资产生效，小于
   `minImageKb × 1024` 字节 → `SMALL_IMAGE`；Live / Motion Photo
   组成与视频不受此条限制，大小未知的原件保留（与 iOS `select` 一致）。
5. 缓存：`filterCache` 为真且资产标 `cached: true` →
   `FILTERED_CACHE`。
6. 全部通过 → `{"in_scope": true, "reason": "IN_SCOPE"}`。

两端调用方式：Android / iOS 在枚举本地资产前（或在
`decide_prepare` 里随资产传 `scope`）逐项调用本判定，只把在范围
内的资产建成任务；`decide_prepare` 的范围标注与本函数同一语义。

## 12. 批量暂停 / 恢复（`batch_pause` / `batch_resume`，第二阶段）

```jsonc
// 入参
{"task_ids": ["t-1", "t-2", "t-3"],
 "tasks": {"t-1": {...}, "t-2": {...}, "t-3": {...}}}
// 返回
{"results": [
  {"task_id": "t-1", "ok": true, "task": {...}},
  {"task_id": "t-2", "ok": false, "error": "INVALID_TRANSITION"},
  {"task_id": "t-3", "ok": false, "error": "TASK_NOT_FOUND"}
], "succeeded": 1, "failed": 2}
```

- 入参：`task_ids` 为字符串 list，`tasks` 为调用方持有的
  `{task_id: task}` 表；核心不跨调用持有任务表。入参本身非法
  （`task_ids` 非 list、含非字符串、`tasks` 非 dict）返回
  `{"ok": false, "error": "INVALID_ARGUMENT"}`。
- 逐个任务走同一 `transition`：`batch_pause` 目标 `paused`，可暂停
  的状态即状态机里有 `paused` 出边的状态（`queued`、`preparing`、
  `dedup_hit`、`uploading`、`submitting`、`confirming`、
  `waiting_network`、`waiting_confirmation`）；`batch_resume` 目标
  `queued`，仅接受 `paused` / `interrupted`（见 §10）。
- 单个任务失败不中断整批：未知 id → 该任务
  `{"ok": false, "error": "TASK_NOT_FOUND"}`；非法转移 → 该任务
  `INVALID_TRANSITION`；任务本身损坏 → `INVALID_TASK`。已处于目标
  状态的任务按 `transition` 的幂等规则计成功（不重复记 `attempts` /
  `history`）。重复 id 只处理首次出现。
- 客户端现状：iOS `pauseJobs` / `resumeJobs` 与 Android 批量暂停是
  已有行为；接核心后应先调本批量接口拿到每任务的新状态，再持久化
  与同步传输层——核心只做状态判定，不碰传输、不取消网络任务。

## 13. 显式代理校验（`validate_proxy`，第二阶段）

只校验配置，不发网络、不存凭据、不回显凭据。

```jsonc
// 入参（proxy 缺省即关闭）
{"proxy": {"enabled": true, "host": "127.0.0.1", "port": 8080,
           "scheme": "http", "auth": {"username": "u", "password": "p"}}}
// 启用且合法
{"ok": true, "enabled": true, "scheme": "http", "host": "127.0.0.1", "port": 8080}
// 关闭（缺省）
{"ok": true, "enabled": false}
// 非法
{"ok": false, "error": "INVALID_PROXY"}
{"ok": false, "error": "PROXY_TLS_VERIFICATION_DISABLED"}
```

- 默认关：`proxy` 缺失 / `null` / `{}` / `{"enabled": false}` 一律
  返回 `{"ok": true, "enabled": false}`，其余字段忽略。
- 启用时：`host` 必填（非空字符串，不得含 `://` 或空白）、`port`
  必填（1..65535 的整数）、`scheme` 缺省 `http`，仅允许 `http` /
  `https`；违反任一返回 `INVALID_PROXY`。
- TLS 校验不得关闭：`verify_tls` / `tls_verify` / `verify_ssl` /
  `verify` 任一为 `false`，或 `insecure` 为 `true`，返回
  `PROXY_TLS_VERIFICATION_DISABLED`（与 gpmc `Api`“显式代理仍验证
  Google HTTPS 证书、忽略环境变量代理”的口径一致）。上述标志为
  `true` 或非空字符串（自定义 CA 证书路径）时视为校验开启；其他
  类型返回 `INVALID_PROXY`。
- `auth` 存在时须为字符串值的 dict，只做类型校验，值永不出现在返回
  里；凭据的保存仍走各端现有安全存储（iOS Keychain 等），核心不存。
- `dispatch` 也接受把 proxy 字段直接放在顶层（`{"enabled": ...,
  "host": ...}`）的写法。

## 14. LiveLink 配对握手与范围（第二阶段）

配对备份范围文档的约定不变：`/v1/hello` 双方通告能力，未通告完整
能力的旧设备仅提供全量、日期范围选择；任何传输均需接收方确认。

### `livelink_handshake`

```jsonc
// 入参：对端通告的能力列表（或 {name: bool}）
{"capabilities": ["scope2", "catalog2"]}
// 完整
{"ok": true, "missing": [], "capabilities": ["catalog2", "scope2"]}
// 缺能力
{"ok": false, "error": "MISSING_CAPABILITIES", "missing": ["catalog2"]}
```

- 要求同时通告 `scope2` 与 `catalog2`；缺任一即不可用，`missing`
  列出缺失项。`capabilities` 非 list / dict、含非字符串返回
  `INVALID_ARGUMENT`。本函数只做判定，不发网络、不建连接。

### `livelink_scope`

- 入参 `{"scope", "asset"}`，范围语义与 §11 的 `evaluate_scope`
  完全一致（同一实现），范围非法同样返回 `INVALID_SCOPE`，绝不退
  回全量。
- 成功时返回 `{"in_scope": bool, "reason": str,
  "requires_confirmation": true}`：**任何传输均需接收方确认**，该
  标志恒为 `true`，调用方不得据 `in_scope: true` 自行发起传输；确认
  必须由接收方 UI 作出（手机不能通过自己的 `/accept` 确认手机主动
  发送）。

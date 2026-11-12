# gpmc 整合与完成记录

## 2026-11-12：任务 GPMC-001 — 三端核心整合与依赖切换

状态：核心整合与三端源码切换已完成，本地验证已通过；根据后续指令进入提交、审核与 CI 验证阶段。未发布客户端或上传 PyPI。

### 来源盘点与保护

- 对独立 gpmc、电脑端普通目录、电脑端实际 Embedded 目录、iOS Vendor 和安卓包进行逐文件比较；基础协议文件基本一致。
- 原始来源提交和文件 SHA-256 记录在 `docs/integration-sources.json`，不以提交号替代工作树实际内容。
- 独立 gpmc 原有未提交的 `client.py` 重试与进度改动已保留；电脑端版本是在该实现上增加上传计划与相册进度能力。整合前原件另保留在本地 `.git/pre-unification-client.py`，不进入发行包。
- 四个仓库均在 `feat/unified-gpmc` 分支开展工作，没有切换发布主线。

### 已完成项目

| 项目 | 来源与处理 | 结果 |
| --- | --- | --- |
| 上传重试及结构化进度 | 保留独立库已有工作，合入电脑端扩展 | 通用实现维护于 `client.py` |
| 上传计划完整性 | 电脑端的文件范围、大小、时间及 SHA-256 校验 | 合入 `client.py`、`hash_handler.py`，保留原接口兼容 |
| 相册整理进度 | 电脑端批量上传结束后的相册阶段 | 合入通用客户端，失败不误报整理完成 |
| 分阶段协议 | iOS 的准备、提交、确认、快照及证据接口 | 移入 `protocol.py`，移除对 `harbor_ios` 的反向依赖 |
| Live Photo 协议 | iOS 和电脑端重复的 wire 逻辑 | 集中到 `live.py`；电脑与 iOS 共用构造及查询，实验标记保留 |
| 请求预算与取消 | iOS 的逐请求连接管理、超时与取消 | 移入 `deadline.py`，iOS 原导入名保留兼容 |
| 原件访问证据 | iOS 与安卓的域名、重定向和长度检查 | 集中到 `media_access.py`；补齐非法端口处理和 HEAD 501 回退 |
| 错误分类 | 两个移动端的错误映射 | 集中到 `errors.py`，保留凭据安全的错误码 |
| 认证与代理约束 | 移动端已有保护 | 通用 API 增加令牌刷新锁，代理保持 TLS 校验并使用显式代理配置 |
| 安卓平台边界 | 原 `gpmc/mobile.py` 依赖 Java 桥和诊断 | 移为客户端 `harbor_android.py`，通用能力调用中央库，未将整套安卓业务塞入 gpmc |
| 固定来源接入 | 三端内嵌副本 | 同一内容哈希、统一同步工具、离线校验；电脑同步更新真实嵌入 ZIP |
| 构建与打包保护 | Gradle、Xcode Python 打包、MSBuild | 构建前检查，包内来源及许可检查同步接入 |
| 维护文档 | 独立库及各端入口 | 本记录、维护说明、来源清单及客户端维护入口已建立 |

### 本次不迁入的内容

UI、相册系统接口、原生后台传输、任务数据库、账户生命周期、钥匙串、Java 桥、诊断文件写入与导出、电脑进程协议继续由客户端维护。本次统一的是 gpmc 的通用能力和维护来源，不代表移动端全部业务已经共享。

### 验证记录

2026-11-12 执行结果：

| 验证 | 实际结果 |
| --- | --- |
| 中央库 `python tools/check_offline.py` | 28 项通过，包含媒体 key 解码、上传计划、相册进度、并发令牌刷新、协议证据与快照漂移拒绝 |
| 独立 wheel 构建与内容检查 | `gpmc-0.9.1.dev0-py3-none-any.whl` 构建通过，共享模块、许可文件和组合许可元数据齐全 |
| iOS Python 适配 | 72 项执行，71 项通过，1 项因本机缺少 OpenSSL CLI 跳过；包含分阶段协议、Live Photo、超时取消、账户隔离和代理约束 |
| 电脑 Python 适配 | 11 项通过，实际嵌入 ZIP 与受测上传源码一致 |
| 电脑完整解决方案 | `dotnet build PicHarbor.sln --no-restore` 通过，0 警告、0 错误 |
| 安卓 Release | 现有固定密钥脚本构建、R8、24 项 JVM 检查及 Lint 通过 |
| 安卓签名 | 构建前后均匹配既有证书 `0ffd8b98405c349b15fbe6b955ed8b8875d14600691921c107f318339c63be78` |
| 安卓 Python 诊断 | 4 项通过 |
| 安卓 APK | 包含统一来源锁、共享源码或字节码、平台适配与许可证，内容检查通过 |
| 三端来源一致性 | 根目录锁文件完全一致，21 个共享包文件；离线校验全部通过 |
| 原有工作保护 | 原独立库 Client 方法均保留；原电脑 ZIP 无遗漏的独有 gpmc 修改 |
| 源码与格式 | 中央库无反向平台导入；四仓库 `git diff --check` 通过 |

三端共同锁定的源码内容 SHA-256：
`aabab4a560f043975e097530ba88c8f726df9781b7a23ea2d1de5f0849063fba`。

构建配置将 setuptools 下限修正为 77.0.3，以匹配现有 SPDX 许可元数据和 `license-files` 用法，依据 [setuptools 官方配置说明](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html)。旧本地 setuptools 的无隔离构建曾失败，采用声明的构建依赖隔离后通过；不是忽略失败。

本阶段结束时的限制：本机为 Windows，没有 macOS/Xcode，当时尚未构建 IPA 或执行 iOS 原生回归，核心 CI 也尚未推送运行。后续云端构建、原生回归与合并结果见 GPMC-002；未安装新 APK 覆盖用户应用，未使用真实 Google 账户执行上传、清理、Live Photo 配对及后台恢复验收。

本地 Android APK 是验证产物，仍使用当前开发版本号，没有重新发布 beta.5 或改变现有 Release。后续正式发布需按各端正常版本流程处理。


## 2026-11-12：任务 GPMC-002 — 提交审核、限定范围合并与构建完善

收尾状态：根据用户“只做必要的”指示停止继续扩大排查。中央库、安卓和电脑端已合并；iOS 已提交并保留 PR，最新 CI 尚在运行，未合并，也未标记全部完成。后续只需查看最新 CI，处理仍实际阻塞的项目后再合并 iOS。

用户明确本次仅合并 gpmc 改动到各端原功能分支，不将既有未合并功能并入 main。中央库目标为 main；安卓目标为 feat/android-diagnostics；iOS 和电脑目标为 feat/paired-backup-scope。

审核修复：电脑 ZIP 与源码桥按 LF 归一化比较，避免跨系统误报；安卓源码 ZIP 增加根来源锁；桌面 CI 明确 Python 版本并执行适配检查；iOS PR 同时验证模拟器和无签名 IPA；中央库 CI 检查跨系统 Python 契约及 wheel/sdist 内容。

本次审核为实现者自审，在各 PR 留下了绑定具体提交的 COMMENT 审核记录，不冒充独立维护者批准。审核后发现的问题均通过修复和重新运行检查处理，未跳过失败的功能测试。

### PR 与限定范围合并

| 仓库 | PR | 合并目标 | 结果 |
| --- | --- | --- | --- |
| 中央 gpmc | [#1](https://github.com/Cc-Cece/gpmc/pull/1) | `main` | 已合并，`c33e3bbf14b002c48717f60d5c2c6bf98891bea8` |
| 安卓 | [#3](https://github.com/Cc-Cece/PicHarbor-Android/pull/3) | `feat/android-diagnostics` | 已合并，`4d4150bcf197f20750a248073713844739fc32bf` |
| 电脑 | [#13](https://github.com/Cc-Cece/PicHarbor/pull/13) | `feat/paired-backup-scope` | 已合并，`a4b687688e245f60f8149c9a3e1d09471925109d` |
| iOS | [#2](https://github.com/Cc-Cece/PicHarbor-iOS/pull/2) | `feat/paired-backup-scope` | 待合并；最新提交 `90ac7e16130ecdfb4845bc86225c23a1e65698f9`，CI 运行中 |

三个客户端的 main 与原有功能 PR 均未由本任务合并。没有发布新应用版本，也没有发布 PyPI 版本。安卓手动测试构建的发布步骤已确认跳过；公开 beta.5 APK 的 SHA-256 仍为 `30f4fb518280a60dab97f4bbfc946db7fbe54569ded23ef9e5ccd5491d9cff6b`。

### 构建流程与审核修复

- 中央库：Linux/Windows × Python 3.11/3.13 契约检查，以及 wheel/sdist 构建、包内容与许可元数据检查。
- 安卓：不需要发布密钥的 PR 构建检查；手动签名构建默认只上传构建产物，公开发布需明确选择，标签必须匹配应用版本，拒绝覆盖已有 Release。
- 安卓：源码 ZIP 包含根来源锁，并在临时目录解压后独立运行验证；新 Windows 检出的 LICENSE 也固定为基线需要的 LF 格式。
- 安卓：重新生成过期的 Debug 依赖锁。核对 Release 编译与运行依赖的版本没有变化，保留完整依赖锁定。
- 电脑：构建与发布 CI 明确 Python 环境，并执行适配契约。PR 格式检查针对本 PR 修改的 C#；整体构建与功能测试仍完整执行，主分支格式检查仍为全量。
- 电脑：密钥扫描仅精确排除已核实的公开 SHA-256 行与三个本地化资源标识；新增生成式假凭据检查，确认实际凭据形态仍被检测。
- 电脑：修正旧测试夹具未显式对齐文件时间、导致受 CI 调度延迟影响的问题，原有判定和断言保留；为 Vault 测试项目补齐已有 CI 使用的日志组件。
- iOS：PR 增加无签名 IPA 构建和包内源码检查；堆叠 PR 也运行模拟器检查。移除测试目标对宿主已静态链接的 SelfRefreshKit 的重复链接，修复 SideSign 重复链接错误。
- iOS：针对模拟器冷构建长时间没有进度的问题，将 build-for-testing 与 test-without-building 分开，编译阶段限制并发并关闭索引存储；编译完成后才启动模拟器。完整输出保存在构建产物中，每分钟输出进度与 CPU/内存摘要，分阶段设定超时。日志包装器已验证保留真实成功/失败退出码，不缩减完整测试范围。
- iOS：原始测试日志定位到应用启动缺失 `/usr/lib/swift/libswiftWebKit.dylib`：Xcode 26 SDK 被配到了列表中较早的 iOS 18.5 模拟器。增加 SDK/运行时匹配选择，禁止默默选择旧运行时，未匹配时提前失败；三个回归用例覆盖旧运行时在前、无匹配项、不可用/非手机设备。
- 四仓库工作流均通过本地 actionlint，三端干净检出验证与来源锁一致性均通过。
- iOS：匹配运行时后完整测试实际执行 149 项，暴露二维码识别与归档输入失败；二维码夹具补齐四模块留白和软件渲染，归档输入改为右侧编辑区域选中数字替换，并直接断言输入值，保留原业务断言。iOS 26.2 模拟器仍出现 Vision 推理编译错误和授权 UI 不稳定，后续运行还触及 15 分钟上限；IPA 固定 Xcode 26.3，完整原生回归固定 Xcode 16.4，运行时仍匹配 SDK，测试阶段放宽到 25 分钟。不得将此基线通过表述为 iOS 26 真机验证。

### CI 执行证据

| 验证 | 结果与链接 |
| --- | --- |
| 中央库跨系统契约及发行包 | [43178157606](https://github.com/Cc-Cece/gpmc/actions/runs/43178157606)，全部通过 |
| 安卓 PR 验证 | [43179406157](https://github.com/Cc-Cece/PicHarbor-Android/actions/runs/43179406157)，全部通过 |
| 安卓固定密钥签名预览、APK 与源码 ZIP | [43179503103](https://github.com/Cc-Cece/PicHarbor-Android/actions/runs/43179503103)，通过，公开发布步骤跳过 |
| 电脑全量 CI | [43180289492](https://github.com/Cc-Cece/PicHarbor/actions/runs/43180289492)，构建/单元测试、EXE E2E、发布包启动、覆盖率与密钥扫描通过 |
| iOS 原生 IPA 构建及内容检查 | 上轮 [43222248898](https://github.com/Cc-Cece/PicHarbor-iOS/actions/runs/43222248898) 通过；最终工具链固定后的 [43226154463](https://github.com/Cc-Cece/PicHarbor-iOS/actions/runs/43226154463) 收尾时运行中 |
| iOS 模拟器 | 上轮 [43222248911](https://github.com/Cc-Cece/PicHarbor-iOS/actions/runs/43222248911) 失败并超时，日志用于定位 Vision、输入焦点及授权 UI 问题；修复后的完整 [43226154508](https://github.com/Cc-Cece/PicHarbor-iOS/actions/runs/43226154508) 收尾时运行中，尚不宣称通过 |

仍不属于本次已完成验收：真实 Google 账户操作、真实手机后台恢复及云端 Live Photo 表现。没有卸载用户应用或清除用户数据。

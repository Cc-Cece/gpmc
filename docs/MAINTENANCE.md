# gpmc 统一维护约定

## 唯一维护源

PicHarbor 的通用 Google Photos 协议代码统一维护于本仓库：
`C:\Users\Kanbara\Code\01_projects\01_PicHarbor\gpmc`，远端为 `Cc-Cece/gpmc`。

电脑、iOS 和安卓仓库保留可离线构建的源码快照，但这些目录是生成物，不再作为独立分叉编辑。每次更新需在本仓库完成修改、验证，再显式同步到客户端；不会在运行时下载库，也不会在构建时自动拉取浮动的 main。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `api.py`、`auth.py`、`message_types.py` | 认证、基础请求与 protobuf 定义 |
| `client.py`、`hash_handler.py` | 通用文件上传、重试与进度、上传计划完整性校验、相册整理 |
| `protocol.py` | 原生文件传输前后的准备、提交和确认接口，以及完整云端快照和清理证据查询 |
| `live.py` | 实验性的 Live Photo 联合提交、关联查询与共享 wire 构造；不负责媒体导出或系统删除 |
| `deadline.py` | 请求级超时、取消和独立连接管理；保留原 iOS 的进程挂起补偿语义 |
| `media_access.py` | 受限原件地址校验、重定向控制、HEAD/单字节 GET 访问证据 |
| `errors.py` | 脱离平台的错误码分类与取消类型 |

`Protocol` 接受提供 `api` 和 `staging` 的上下文，不依赖 Swift、Kotlin 或任何客户端仓库。任务持久化与账户生命周期由调用方负责；同一个带云端快照的实例不可无保护地并发调用或跨账户复用。iOS 已有账户隔离与请求上下文池继续负责该约束。

安卓的 `harbor_android.py`、`harbor_diagnostics.py`，iOS 的 `harbor_ios.py`，电脑的 `gpmc_bridge.py` 和 `live_pair.py` 仍是平台适配。Java 媒体桥、钥匙串、应用数据目录、系统调度、进度输出和 UI 不迁入本库。三个 iOS `harbor_protocol/live/deadline` 模块仅保留兼容导入，真实实现位于本库。

## 更新客户端

在任一客户端仓库执行：

```sh
python tools/sync_gpmc.py --source ../gpmc
python tools/sync_gpmc.py --check
```

也可以在本仓库执行：

```sh
python tools/sync_gpmc.py --source . --target ../android
python tools/sync_gpmc.py --source . --target ../ios
python tools/sync_gpmc.py --source . --target ../desktop
```

同步器只更新明确的 gpmc 快照、统一维护工具及相关锁定文件；电脑端还重建嵌入 ZIP，iOS 更新来源清单，安卓更新适配层清单和 APK 中的来源锁。适配代码自身的迁移需要另行实现和验证，不由同步命令猜测修改。

`gpmc.lock.json` 中的完整文件哈希及 `source_tree_sha256` 是版本依据。文本按 LF 归一化，避免不同系统的换行造成假差异。`source_base_commit` 只是来源基点，若整合尚未提交，不应把它误认为完整代码版本。

同步脚本只维护本仓库 `tools/sync_gpmc.py` 这一份，各客户端脚本通过同步生成并锁定。不要分别修三份同步工具。

正常构建不会自动升级依赖：

- Android：Gradle `preBuild` 调用源码和适配层校验，现有 APK 检查继续核验打包内容。
- iOS：运行环境准备与 Xcode 打包脚本先校验，IPA 检查核对每个 vendored 文件。
- 电脑：Core 项目的 MSBuild 在构建前校验两处快照及真正嵌入的 `gpmc_bundle.zip`。Python 命令可通过 `GpmcPython` 属性指定。

## 验证与交付

核心离线检查：

```sh
python tools/check_offline.py
```

该命令只选择离线套件。历史 `client_test.py`、`album_test.py`、`rich_conflict_test.py` 包含真实账户操作，不作为自动回归入口。

客户端验证：

```sh
# iOS：Python 适配契约；Xcode 构建另需 macOS
python -m unittest discover -s tests_python -v

# 电脑：适配检查及 .NET 编译
python -m unittest discover -s tests/python -v
dotnet build src/PicHarbor.Core/PicHarbor.Core.csproj

# 安卓：基础检查；完整 release 构建通过现有固定密钥脚本执行
python tools/verify_engine.py
python -m unittest discover -s tools -p test_diagnostics.py -v
```

变更必须记录到 `INTEGRATION-LOG.md`：来源、纳入或保留平台层的取舍、实际验证结果和未完成的验证。通过 mock 与本地 HTTP 测试不等于完成真实 Google Photos、实机后台运行或 Live Photo 云端表现验收。

## 许可证与兼容

保留原 gpmc 的 MIT 声明；移入的 PicHarbor 代码保留原项目 GPL-3.0 条款，组合分发的声明已记录在包级 `NOTICE.md` 和 `_licenses/` 中，不能把整个组合包描述成仅 MIT。参考项目的 Live Photo 许可也随包分发。

客户端快照更新不改变应用包名、签名、账号或本地数据库。安卓仍沿用已固定的发布密钥。旧 v1.0.8 的签名兼容问题不由本次 gpmc 整合解决。

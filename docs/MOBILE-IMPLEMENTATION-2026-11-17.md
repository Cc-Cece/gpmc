# 2026-11-17 移动端统一实施记录

规范入口：[MOBILE-PRODUCT-CONTRACT.md](MOBILE-PRODUCT-CONTRACT.md)。三仓实施分支均为 `feat/mobile-product-unification`。

## 起点与范围

产品蓝本：iOS `90ac7e1`，不以 11 月 13 日新增产品需求替代蓝本。
复用实现起点：Android `450e9d2`、iOS `f45b021`、gpmc `156caa1`。
后两者包含已存在的共享核心接入与测试交互修复；本轮进一步调整原生数据库持久化、修订检查和导航。

## 已实施代码

- Android 新包名、新版本线、独立签名目录和 secrets 名称；旧密钥保留。
- 原生四栏、独立活动页、来源/状态筛选、分组菜单、批次操作、归档、归档设置和统一设置入口。
- 新安装全部已授权范围、相簿同步默认关闭、账号偏好恢复、同版本取消抑制、任务创建时固定内容策略。
- 核心快照写入 Android Room / iOS BackupStore；修订号校验与明确完成证据；暂停和等待确认保留独立语义。
- Android 代理配置与订阅、相机和图片二维码、加密配置存储、节点选择/测速，以及同 iOS 版本的 Clash 原生进程。
- Android 电脑直传的 TLS 固定证书、设备身份、配对/会话、电脑确认、清单和分块上传、取消及结果核对。
- 读取显式 Apple 媒体配对元数据，禁止按文件名猜配对；Android 原始资源联合提交/分步提交经共享协议确认。
- 两端共享日志保留约定；新增 iOS 持续事件日志、日期范围导出和流式 ZIP。
- Android 语言和外观入口、基于既有 iOS 翻译的资源表；未知文案仍须补齐，不能认定翻译验收完成。

## 必须继续核验或补齐

以下是开发缺口，不是可忽略的平台差异，未完成前不能称为整个计划完成：

| 项目 | 现状/后续验收 |
| --- | --- |
| 动态照片封装 | 已实现 JPEG/HEIC + 原始视频封装、XMP 合并和原始图像编码保留；要求明确关键帧证据，特殊/未知元数据结构明确失败。HEIC 真实媒体验收仍由用户执行 |
| 电脑直传完整对齐 | 已补逻辑资产多资源清单、电脑发起范围/目录交互、前台生命周期和明确确认；跨端恢复场景由用户后续测试 |
| 活动逐项交互 | 已补平铺逐项筛选、详情、暂停/恢复/移除和选择归档；修复重试全部只触发第一批的问题 |
| 代理完整对齐 | 已补任务级策略、共享节点选择和失败后退避重选；Android 等待并发传输结束再重连 |
| 编辑版本 | 两端已接共享 select_versions；iOS 使用 Photos 明确资源，Android MediaStore/SAF 当前实现只声明当前文件，UI 禁用无法验证的编辑前原件选项并说明限制，另存副本作为独立文件保留；不通过文件名猜测历史版本 |
| iOS 业务核心 | 生产代理节点选择和 LiveLink 逐资产范围判断已接共享核心；仍需继续审查全部通用业务规则 |
| 本地化 | Android 已有独立可编辑 TSV、993 条双语资源和编号占位符匹配；特有动态文案仍需补齐 |
| 验收 | 首包已于 17:01 安装至设备 691a42fe；用户自行测试，后续仅编译和安装，不再操作手机或运行测试 |

## 验证记录

共享核心目标检查新增修订/完成证据、真实元数据配对、代理选择与取消重启等案例；当前 71 项通过。
iOS Python 检查在新增原生日志前运行结果为 86 项通过、1 跳过；后续修改后应重新检查。
Android 首包 release 构建、签名校验和安装成功。后续任务状态、翻译、网络重试、相册恢复和照片库元数据索引已通过 Kotlin release 编译，未运行测试。iOS `79ba24c` 的只编译构建成功（GitHub Actions `44055508085`）；后续 `2020c46` 仅同步新增的共享 Motion Photo 识别函数。

第二份 APK 于 17:34 安装成功。定时脚本首轮构建和签名成功，但 Windows PowerShell 的子进程退出码读取为空导致跳过安装；已补装，并改为提前保留进程句柄再读取退出码。下一次触发时间为 18:01:01。

后续源码 `7c0d9d7` 已补：普通照片/实况照片的独立相册重试、无关文件无权限时继续元数据索引、刷新后保留配对标识、按显式标识跨文件夹配对、缺失原始资源时拒绝降级为静态照片，以及单文件 JPEG 动态照片识别。单文件识别按照 [Android Motion Photo 格式规范](https://developer.android.com/media/platform/motion-photo-format) 检查 XMP 声明、尾部视频长度和容器头，保留上传字节。

## 定时安装

18:01 这轮已于 18:03:24 自动安装成功，未启动应用或运行测试。随后新增的 HEIC、版本策略及原始格式读取已通过 Android 编译；iOS `96d8a7a` 的只编译构建为 `44063204160`，仍以最终 CI 结果为准。

HEIC 写入遵循 [Motion Photo 格式](https://developer.android.com/media/platform/motion-photo-format) 的 `mpvd` 和 Padding 规则：保留旧图像 extent 的绝对位置，将旧 meta 置为同尺寸 free，再追加 XMP、更新后的 meta 和视频；不重编码图像。读取原始格式使用 [MediaStore.EXTRA_ACCEPT_ORIGINAL_MEDIA_FORMAT](https://developer.android.com/reference/android/provider/MediaStore#EXTRA_ACCEPT_ORIGINAL_MEDIA_FORMAT)，它用于禁止系统转码，并不提供编辑历史，也不解除 EXIF 敏感元数据脱敏。

Windows 任务 `PicHarbor-Mobile-APK-to-691a42fe` 每 30 分钟执行 `android/tools/device-preview.ps1`，首个定时触发为 17:31:31。仅构建、校验签名及覆盖安装，不启动应用、不运行测试、不清除数据。电脑需要开机且当前用户已登录；断连或编译失败时保留已安装版本。状态写入 `%LOCALAPPDATA%/PicHarborMobileBuild/device-preview/latest.json`。

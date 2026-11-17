# 手机端统一开发交付 · 2026-11-17

本轮开发实现与构建交付完成。功能矩阵、平台差异和验收边界见 [实施记录](MOBILE-IMPLEMENTATION-2026-11-17.md)，产品范围见 [共同规范](MOBILE-PRODUCT-CONTRACT.md)。实机功能与云端测试按用户要求交由用户执行，尚未宣称最终统一验收通过。

## 源码与构建

| 项目 | 最终功能源码 | 结果 |
| --- | --- | --- |
| Android | `c6b8904b4b616d5003cc2640bc4b82874f77536f` | 2.0.0-alpha.1、arm64 release，19:33:29 安装至 `691a42fe` |
| iOS | `e0c495c9b23dba5b40eaff7e01b3acd597847096` | [最终无签名 IPA 构建成功](https://github.com/Cc-Cece/PicHarbor-iOS/actions/runs/44074231281)，`run_tests=false`；产物 `PicHarbor-0.2.30-107.ipa` |
| gpmc | `ce7fb2b86839582281a571254bd183eababe10e0` | 两端 29 文件共享核心快照一致，内容哈希 `0a4472b9ffd4537b0e4b4c14e5fb2ba66aeec757fdba91cfd4e046ff77fe2215` |

三仓功能代码与交付文档均保留在 `feat/mobile-product-unification`，未自动合并或公开发布。

## 本地产物

工作区 `delivery/2026-11-17/` 保存 APK、含固定代理源码及许可证的 Android 源码 ZIP、无签名 IPA、校验清单和安装信息。

- APK：`PicHarbor-2.0.0-alpha.1-arm64.apk`
- APK SHA-256：`5da7fbe997c7580480869690752dc09ebf7026f84bd962f381a8b664a770fc64`
- Android 源码 ZIP SHA-256：`f4d796624bd500a563aaaca1ffbe9a0df60a6a05ae6de7a14fc4fe3a9729477e`
- Android 签名证书 SHA-256：`65de9c795940ee1a05dade807814a8e70ca395f6a1ab1e0075faab7cea2133f7`
- iOS IPA SHA-256：`a6f42ee605b3f142447e311b507f60dde324b910fc8bc3055382e6146792e78b`

iOS IPA 未签名，需要使用者自己的签名流程后安装；本轮没有对 iPhone 执行安装。GitHub 构建产物保留至 2026-12-01，本地副本独立保留。

## 持续安装

保留 Windows 任务 `PicHarbor-Mobile-APK-to-691a42fe`，每 30 分钟编译、验证固定证书并覆盖安装连接设备，不启动应用、不运行测试、不清除数据。19:33 轮次成功，下一轮预计 20:01。电脑开机、登录并保持设备连接时生效。

故障状态及日志位于 `%LOCALAPPDATA%/PicHarborMobileBuild/device-preview/`。密钥在仓库外独立保护，未放入交付 ZIP。

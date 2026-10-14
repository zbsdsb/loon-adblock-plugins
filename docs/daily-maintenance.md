# SE2 真机每日维护

## 当前交付边界

**2026-10-14 事故停用：** Frida 启动/进程枚举期间设备产生两份 `initproc exited` 内核崩溃记录。已停止该路线。
`maintenance/device-policy.json` 默认禁用观察器，`private/FRIDA_DISABLED.json` 保存本机隔离记录；未经独立兼容性与恢复审查，不得解除、重试或启用每日任务。

维护主机为 Windows/Paseo，时区 `Asia/Shanghai`，目标 SSH 别名 `zbs-se2`。
计划每天 09:00；**真机采集、候选加载、恢复和 Telegram 实际送达都验收前，不创建或启用定时任务**。
脚本下载、单元测试、进程退出 0 均不证明手机无广告。

电池保护沿用设备现有 80% 设置。在 SE2 的 Safari 打开 `http://127.0.0.1:8769/`；可添加书签或主屏幕入口。
没有执行低电量到80%的完整充电周期时，不能声称阈值周期已验证，也不要为此强制放电或修改系统。

## 运行与通知

事故恢复阶段的安全入口（已有 Python、SSH Skill）：

```powershell
python -m maintenance.recovery
python -m maintenance.loon_audit
```

`loon_audit` 只读配置、偏好与脚本记录，不启动 App 或 Frida。偏好中选中的文件、隧道进程、插件 enabled 标记分别报告，不能证明实时脚本版本或证书信任。
Windows 当前未发现可用的原生 iPhone 截图 CLI；替代采集链路尚未验收，不能进行或声称三款真机开屏回归。

下面的采集入口保留供后续适配，**当前被事故策略阻止，不可用于自动测试**：

```powershell
python -m maintenance.daily preflight
python -m maintenance.daily capture
python -m maintenance.daily review --run-dir private/runs/RUN_ID --review private/runs/RUN_ID/review.json
python -m maintenance.daily notify --run-dir private/runs/RUN_ID
```

所有运行证据保存在 `.gitignore` 排除的 `private/`。本地证据可能含私人 UI、URL 与请求内容，不能上传公开仓库。
已停用的观察器原型不能视为支持能力；禁止为完成验收解除隔离。替代观察器也不得修改广告逻辑或点击跳过冒充 Loon 效果。
锁屏、联网失败、没有有效截图、第一秒画面缺失、帧间空窗、版本无法确认均闭合失败。

Telegram 通过当前进程环境变量 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`，或本机 DPAPI 密文读取：

```powershell
powershell -NoProfile -File maintenance/setup-telegram.ps1
python -m maintenance.daily test-notify
```

配置脚本在你的本机终端交互输入，不把 Token 发到聊天中。用户已授权复用 `dhcc-zbs` 上的抖音服务时，可只读导入：

```powershell
python -m maintenance.import_telegram --alias dhcc-zbs --container douyin-auto-fire
python -m maintenance.daily test-notify
```

导入只读远端容器两个通知字段；不会改配置、读取 Cookie 或重启抖音服务。明文仅进入 SSH/本机内存，经 stdin 送入 DPAPI 加密，不进入参数或日志；已有文件拒绝覆盖。密文由当前 Windows 用户解密。2026-10-14 已成功导入并发送 Telegram 验收消息。
未配置不会虚报送达。`private/telegram-test.json` 保存实际 API 送达结果，不保存 Token。

## 证据与发布门

`review.json` 的 `apps` 字段有 netease/jd/taobao 三组，各三条。每条引用采集 `directory` 和 `manifest_sha256`，附看图结论；模板字段见 `maintenance/schedule-prompt.txt`。
运行版本证据单独保存在 trial 目录的 `runtime-version.json`：`source=loon_execution_evidence`、实际 `tree_sha256`、`capture_manifest_sha256` 必须绑定原始采集。它是对真实运行日志的证据摘要，不是允许凭空填写的成功标记。
未实际确认当前配置、MITM、候选加载或恢复，不能建立发布证据。当前工具**不擅自修改 Loon 数据库/缓存来模拟加载**。

完整候选证据 `evidence.json` 包含 `source=physical_device`、`changed_app`、`candidate_attempt`（1或2）、`tree_sha256`，以及 `unit_tests_passed`、`har_regression_passed`、`rollback_verified`、`config_restored`、`runtime_version_verified` 与三款真实 trial 结论。

```powershell
python -m maintenance.daily hash
python -m maintenance.daily release-check --evidence private/runs/RUN_ID/evidence.json
```

release-check 不通过绝不能发布修复。通过后再推候选分支、创建 PR，等待 `Plugin and maintenance regression` CI 成功，再合并 main，检查 raw 插件及其脚本可下载，Telegram 通知适用版本、变更摘要和刷新入口。禁止 force push、绕过 CI 或自动合并未经真机验收的广告修改。
基础维护工具可以在独立分支交付，不应因此宣称插件已修复或每日任务已运行。

## 源码兼容与许可

保留网易云 `NeteaseCloudMusic_splash_patch.lpx` 入口；京东正式入口为仓库的 `JD_remove_ads_v2.lpx`，从旧 Gist 切换须先备份并实测加载。
淘宝当前外部入口及作者记录在 `maintenance/plugins.json`。上游仓库未查到明确许可证，暂不复制或冒称它是本仓库已验证插件。
JD 默认合成样本为27项；可通过 `JD_BASIC_CONFIG_FIXTURE`、`JD_STARTUP_FIXTURE` 指定本地私有响应，不再依赖 `/tmp`。合成测试与 HAR 实测在报告中分开。

## 故障和恢复

运行使用原生文件锁；采集子进程最长29分钟，为通知与清理保留预算。超时后不能假设手机端已清理，次轮必须先确认上轮创建的 Frida PID 和配置恢复。
不保存解锁密码；测试时电脑不休眠、SE2解锁联网。主机关机期间无法实时通知，恢复后摘要补报漏跑。
广告两次复现才触发修复；一个候选至少完成同输入的原版/修改/回滚验证。超过两个候选或30分钟停止并通知，保留最后有效版本。

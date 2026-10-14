# 插件维护执行约束

- 只维护网易云、京东、淘宝广告规则；不更改支付、账号、订单等行为。
- 运行入口与验收要求见 `docs/daily-maintenance.md`。任何私有证据、凭据、截图、HAR 只能放 `private/`，禁止提交或打印凭据。
- 复用 SSH Skill；Frida 因两次真实 kernel panic 已隔离，禁止重启服务、枚举或注入。用户说“继续”不等于解除隔离。允许 recovery / loon_audit 只读核验，以及用户批准的 native_capture 原生截图与目标 App 冷启动；后者必须先由 Agent 看图确认本轮画面已解锁，不得绕锁或保存/盲输密码。
- 测试：`node tests/jd-test.js`、`node tests/netease-test.js`、`python -m unittest discover -s maintenance/tests -v`。
- 原生 helper 在 `maintenance/native-capture/` 内执行 `go test ./...`。不使用 go-ios 自带的全网卡 MJPEG 服务；截图经标准管道/本机 private 文件传递。原生画面没有 UIKit applicationState，禁止填造0；以绑定哈希的逐帧看图记录证明目标界面。
- 每款三次真实冷启动并逐帧复核；脚本下载、单测、缓存存在、退出0都不是去广告成功。缺少画面或运行版本证据时闭合失败。
- 两次广告复现才修复；每轮最多两个候选、30分钟。同输入原版/修改/回滚留证，恢复哈希必须一致。
- 候选临时部署须备份与恢复；不自动更新用户正式配置。不改 Loon 数据库/缓存冒充生效。
- 发布广告修复前必须有真实三款回归、运行版本、HAR 回归、恢复证据，并通过 release-check 及仓库 CI；不 force push、不绕过门控。
- 淘宝上游许可未确认前只引用外部来源；不复制到本仓库并冒称原创或已验证。
- 真机采集、候选加载/恢复、Telegram 送达没全部验收，09:00任务不启用。基础工具分支交付不代表广告修复或每日实测已成功。

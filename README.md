# Loon 去广告 / 修复插件合集

<p align="center">
    <a href="https://linux.do" alt="LINUX DO">
        <img src="https://img.shields.io/badge/LINUX-DO-FFB003.svg?logo=data:image/svg%2bxml;base64,DQo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCIgaGVpZ2h0PSIxMDAiPjxwYXRoIGQ9Ik00Ni44Mi0uMDU1aDYuMjVxMjMuOTY5IDIuMDYyIDM4IDIxLjQyNmM1LjI1OCA3LjY3NiA4LjIxNSAxNi4xNTYgOC44NzUgMjUuNDV2Ni4yNXEtMi4wNjQgMjMuOTY4LTIxLjQzIDM4LTExLjUxMiA3Ljg4NS0yNS40NDUgOC44NzRoLTYuMjVxLTIzLjk3LTIuMDY0LTM4LjAwNC0yMS40M1EuOTcxIDY3LjA1Ni0uMDU0IDUzLjE4di02LjQ3M0MxLjM2MiAzMC43ODEgOC41MDMgMTguMTQ4IDIxLjM3IDguODE3IDI5LjA0NyAzLjU2MiAzNy41MjcuNjA0IDQ2LjgyMS0uMDU2IiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZWNlY2VjO2ZpbGwtb3BhY2l0eToxIi8+PHBhdGggZD0iTTQ3LjI2NiAyLjk1N3EyMi41My0uNjUgMzcuNzc3IDE1LjczOGE0OS43IDQ5LjcgMCAwIDEgNi44NjcgMTAuMTU3cS00MS45NjQuMjIyLTgzLjkzIDAgOS43NS0xOC42MTYgMzAuMDI0LTI0LjM4N2E2MSA2MSAwIDAgMSA5LjI2Mi0xLjUwOCIgc3R5bGU9InN0cm9rZTpub25lO2ZpbGwtcnVsZTpldmVub2RkO2ZpbGw6IzE5MTkxOTtmaWxsLW9wYWNpdHk6MSIvPjxwYXRoIGQ9Ik03Ljk4IDcwLjkyNmMyNy45NzctLjAzNSA1NS45NTQgMCA4My45My4xMTNRODMuNDI2IDg3LjQ3MyA2Ni4xMyA5NC4wODZxLTE4LjgxIDYuNTQ0LTM2LjgzMi0xLjg5OC0xNC4yMDMtNy4wOS0yMS4zMTctMjEuMjYyIiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZjlhZjAwO2ZpbGwtb3BhY2l0eToxIi8+PC9zdmc+" />
    </a>
</p>

本仓库为开源项目，已链接认可 [LINUX DO](https://linux.do) 社区（开源推广）。

个人自用的 Loon 插件，均已用抓包（HAR）实测验证。修复后可直接从这里分享给其他人。

## 插件列表

| 插件 | 功能 | 适用版本 | 验证日期 | Loon 要求 |
|---|---|---|---|---|
| [京东去广告 v2](JD_remove_ads_v2.lpx) | 移除开屏广告、首页/我的/订单/物流页推广楼层、弹窗、搜索热词 | 京东 App **≥ 15.9.50**（兼容旧版协议） | 2026-08-12 | 3.2.4+ |
| [网易云开屏广告拦截补丁](NeteaseCloudMusic_splash_patch.lpx) | 拦截开屏广告（xeapi/eapi/api/ad 全系 + 素材域名） | 网易云音乐 **9.5.x** | 2026-08-10 | 3.2.0+ |

## 导入方式

Loon → 配置 → 插件 → 右上角 `+` → 粘贴 URL：

```text
https://raw.githubusercontent.com/zbsdsb/loon-adblock-plugins/main/JD_remove_ads_v2.lpx
https://raw.githubusercontent.com/zbsdsb/loon-adblock-plugins/main/NeteaseCloudMusic_splash_patch.lpx
```

或直接点击上面表格里的插件文件名，在 GitHub 页面查看源码后手动复制内容创建插件。

## 使用注意

1. **MITM 证书**：需信任 Loon 的 MITM 证书并开启 HTTPS 解密（两个插件都自带 `[MitM]` hostname，导入时按提示允许）
2. **停用旧插件**：京东插件会替换旧版 kelee `JD_remove_ads.lpx`；两个插件不要同时启用（脚本会重复处理同一条响应）
3. **生效验证**：导入后**完全杀掉 App 再重启**（后台切换不够），确认开屏广告消失
4. **失效排查**：如果 App 更新后广告回潮，按 [Loon 插件失效排查流程](docs/loon-plugin-troubleshooting.md) 抓 HAR 排查

## 为什么京东插件要叫 v2

京东 App 15.9.50 起协议升级：

- `client.action` 的 `functionId` 从 URL query 迁移到 **base64 编码的 POST body**
- 响应体从明文 JSON 变为**整体 base64 编码**
- 开屏广告接口 `start` → `startup`

旧插件所有规则基于 URL `functionId=` 正则匹配 + `JSON.parse` 明文响应，全部失配。v2 改为：

- 拦截所有 `client.action` 响应
- 自动识别明文 / base64 并解码
- 按**响应内容特征**（`images` / `floorList` / `floors` 楼层黑名单等）清洗广告
- 按原格式（明文 / base64）原样回写；无广告特征时不做任何改写（零误伤）

脚本源码：[JD_remove_ads_v2.js](JD_remove_ads_v2.js)（Loon 远程加载），单元测试：[tests/jd-test.js](tests/jd-test.js)

## 目录结构

```text
├── JD_remove_ads_v2.lpx              # 京东去广告插件（引用下方脚本）
├── JD_remove_ads_v2.js               # 京东清洗脚本（base64/明文双协议兼容）
├── NeteaseCloudMusic_splash_patch.lpx # 网易云开屏广告补丁
├── tests/
│   └── jd-test.js                    # 京东脚本单元测试（node 直接运行）
└── docs/
    └── loon-plugin-troubleshooting.md # 插件失效排查流程
```

## 更新

- 修改脚本后跑 `node tests/jd-test.js` 回归，通过后提交推送
- Loon 端插件脚本缓存：可在 Loon 插件详情里刷新脚本（或重启 Loon）
- 欢迎提 Issue / PR

## License

MIT

# Loon 插件失效排查流程

> 以「京东去广告插件失效」实战为例沉淀的方法论（2026-08-12）。适用场景：App 更新后广告回潮、插件突然不生效、规则疑似失配。

## 0. 前置认知

Loon 插件的四段式结构（.lpx 纯文本）：

| 段 | 作用 | 常见失效原因 |
|---|---|---|
| `[Rule]` | 域名/IP/正则 REJECT、DIRECT | 域名迁移、新增 CDN、规则顺序被覆盖 |
| `[Rewrite]` | URL 重写、body 改写（reject-dict / response-body-json-jq） | URL 参数变化、响应加密/编码化 |
| `[Script]` | http-request / http-response 远程脚本 | 远程脚本源站失联、脚本未适配新响应结构 |
| `[MitM]` | 需要解密的 hostname 列表 | 新域名未加入、证书失效 |

**关键：所有匹配都基于 URL 正则 + 明文响应处理。App 端任何"协议迁移"都会让整段规则静默失配。**

## 1. 收集证据（两条线并行）

### 1a. 插件源码（验证"插件原本想做什么"）

- 用户提供的插件 URL 直接抓取（Loon 插件通常托管在个人站点，可能有 CDN 拦截，抓不到很正常）
- 抓不到就搜 GitHub 镜像：`<插件名> github`、`<作者名> loon`，社区镜像仓库很多（如 `mihoyo-typ/KeleeOne`、`wxs0625/loon-plugins`、`zwjtano/kelee-loon-surge-modules`）
- 关注：`#!date`（最后更新日期，停更=未适配新协议）、script-path 引用的远程脚本内容

### 1b. 抓包证据（验证"App 实际在做什么"）

用户导出 HAR（Loon 高级功能可导出），分析要点：

- **host 分布**：`python3` + `json` 解析 HAR，统计各域名请求数，看主 API 在哪
- **functionId 位置**：逐个请求检查 `functionId` 在 URL query 还是 POST body（body 可能是 base64，解码后才是 form 参数）
- **响应编码**：响应体是明文 JSON 还是 base64（`eyJ` 开头=base64 JSON）、是否 gzip
- **接口改名**：旧 functionId（如 `start`）在 HAR 中是否消失，出现的新名字（如 `startup`）是什么
- **REJECT 规则验证**：被规则 REJECT 的请求不应出现在 HAR 中；若出现完整响应（带 `_loonOriginal`），说明规则失配或插件未启用

## 2. 对照定位失效点

| 现象 | 结论 |
|---|---|
| URL 无 functionId（在 base64 body 里） | Rewrite/Script 的 `?functionId=` 正则全部失配 |
| 响应体 base64 化 | `JSON.parse($response.body)` 抛错；`response-body-json-jq` 无法解析 |
| 接口改名 | Script pattern 匹配不到新 functionId |
| 远程脚本源站 403/失联 | Script 段静默失效（Loon 缓存过期后拉取失败） |
| 被 REJECT 的请求仍出现在 HAR | Rule 未生效（插件未启用 / 规则顺序 / 语法不兼容） |

## 3. 适配重写（核心思路）

新协议下 URL 正则规则"无解"（Loon 不支持匹配 POST body），必须换思路：

1. **放宽匹配**：pattern 从 `client.action?functionId=xxx` 放宽为 `client.action`
2. **双协议兼容**：先 `JSON.parse(body)`，失败则 base64 解码再 parse
3. **特征识别接口**：不再依赖 functionId，按响应内容特征判断（开屏=`images` 数组、首页=`floorList`、楼层页=`floors` + mId 黑名单）
4. **原格式回写**：明文进来明文出去、base64 进来 base64 出去（App 端解码失败会兜底或报错）
5. **零误伤原则**：无广告特征时原样放行（`$done({})`），大响应（>2MB）直接放行保性能
6. **安全编码**：脚本里所有 `$done()` 前加 `return`（Loon 环境 $done 后可能继续执行）；base64 工具函数手写纯 JS（不依赖 atob/btoa），UTF-8 用 escape/unescape 往返保证中文无损

## 4. 验证（必须做，别直接发布）

用 `new Function` 包装脚本模拟 Loon 环境（`$request`/`$response`/`$done`），写两个层级的测试：

1. **单元用例**：构造各接口响应（明文 + base64 各一份），断言清洗结果
2. **HAR 回归**：把用户 HAR 里所有真实响应体跑一遍脚本，断言 0 误伤 0 报错（重点是 basicConfig 这类大响应）

> 踩坑：`eval` 多次执行脚本会因 `const` 重复声明污染状态，必须用 `new Function` 每次独立作用域。

## 5. 发布

- 建公开 GitHub 仓库（raw URL 可直接给 Loon 导入），插件内 script-path 指向仓库 raw 文件（单点维护）
- README 写清楚：**适用 App 版本**（实测验证的版本号 + 日期）、导入方式、注意事项
- 本地留档测试脚本，后续 App 再升级时改规则 → 跑回归 → 推送即可

## 实战案例档案

- 京东去广告 v2（2026-08-12）：协议迁移（functionId→base64 body、响应→base64、start→startup）+ kelee.one 源站被 Cloudflare 拉黑，双重失效 → 特征识别 + 双协议兼容重写，27 用例 + 57 响应回归全过
- 网易云开屏补丁（2026-08-10）：9.5.x 新增 `xeapi` 前缀广告接口，旧插件只匹配 `eapi/api` → 正则改 `x?e?api` 覆盖全系
- 网易云去广告 v2（2026-08-14）：9.5.67 开屏 `/x?e?api/ad` 仍被拦截，但广告迁到 AES+gzip 加密的 `link/page/rcmd/resource/show`（`PAGE_RECOMMEND_BANNER_1`）、`sp/flow/popup/query`（启动免流弹窗）、`delivery/batch-deliver`（会员投放卡）。密钥不是公开 eapi 的 `e82ckenh8gbtaden`，而是社区脚本同款 `e82ckenh8dichen8`。整段广告接口用 reject-dict，混排接口解密后按 `BANNER` / 坑位码清洗

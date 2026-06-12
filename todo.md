# TODO

新增需求：核实、修复 GHC 验证机制和请求头格式。

## 需求背景

1. GHC 代理服务（`src/ghcproxy/`，Python/FastAPI）已完成态交付：鉴权 → 前端用户 1:1 粘性绑定后端
   GHC 账号 → 转发模型流量（OpenAI/Anthropic 两种 wire format，含流式 SSE）→ 用量/prompt/审计事件投
   Kafka；refresher 定时校验凭证存活；admin API 提供账号导入、Device Flow 登录、签发前端 Key；
   PostgreSQL 持久化账号池、用户、Key、绑定、用量滚动汇总（`usage_rollup`）。
2. 当前服务是**纯后端 API**，没有任何前端页面，运维只能直接查库或调零散的 admin API（仅
   `GET /accounts`、`POST /accounts`、`POST /users`、Device Flow 的 start/poll，且都只用一个静态
   `X-Admin-Token` 头鉴权）。缺少可视化手段查看账号绑定、token 用量、管理用户与 Key、管理后端账号状态。
3. **prompt 日志不在本需求范围内**：prompt 明细只流转给 Kafka，未留存在数据库，面板不提供 prompt
   日志的读取/检索能力。
4. 当前 workspace 所在服务器已经登录一个 GHC 账号，你可以分析它的登录凭证保存机制来了解技术实现路径；
5. 当前 shell 环境登录了一个 azure 账号，订阅名为 ME-MngEnvMCAP012397-zhuhonglei-1，如有必要可以用 azure cli 在 japan east 区域创建资源组 rg-dev2 部署必要的云资源，完成测试等开发任务（当前 workspace 所在 VM 位于 vnet-dev-jpe VNet 下，从当前服务器发起到测试资源的请求需要先打通 VNet Peering 才能基于内网 IP 访问，注意自己查询资源和网络现状）；**rg-dev2 和内部资源在前序实现时已经创建，可以重用；**

## 任务需求

本项目中将 github copilot 请求服务器的过程理解为基于 gho_ 的单一验证机制，并且声称测试过了属实。但是我从其他信息源看到的反馈是：

- GHC 认证方式是两级的：第一层登录的 token A （gho/ghu 的key）是长效的，至少能用 1 年；第二层验证是用 gho 向github copilot 获取临时 token B 用于访问 GHC API，这个 token B 的过期时间 30 分钟，需要定期刷新；
- 最终用 token B 访问 GHC API 的 http 请求 header 也要构造，多参考 OpenClaw/OpenCode/litellm 的实现，如果实现的不好会影响 Cache，也可能被后台抓为异常；（比如 cc switch 就没构造好这个，造成 cache 命中低等问题）；

请整体分析 ghc-proxy 项目代码，搜索网络，调研核实后回答：

1. GHC 认证是只需要维护 gho_ 还是需要按长短期两个 token 实现；
2. 项目中请求 GHC 的请求头是否如 OpenCode/litellm 等实现做了正确处理；

注意多方面获取信息，交叉验证结论。**并且确保先有调研结论，和我确认后再修复实现！！**


## 交付条件

1. 如果有变更，要保证前端、后端代码互相功能匹配，实现目标需求。
3. 文档同步更新：README、设计文档（`ghc-proxy-design.md`）及项目设计过程中创建的示例。
4. 所有脚本和配置均使用占位符，不写入真实租户 ID、密钥、token 或客户敏感信息。
5. 利用本地环境 Azure CLI 账号和 Azure 资源执行完整的部署测试。

## 注意事项

- 复用现有数据模型（`src/ghcproxy/db/schema.sql`）与 repo 方法（`src/ghcproxy/db/repo.py`），
  按需补充只读查询/状态变更方法。
- Key 一律只存哈希（`api_keys.key_hash`），面板任何场景都不得回显明文或哈希，明文仅在签发瞬间返回一次。
- 检查 `.gitignore` 合理性，前端构建产物（如 `node_modules/`、`dist/`）不入库。

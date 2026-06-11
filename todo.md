# TODO

新增需求：为 GHC 代理服务开发一个**运维管理面板（Admin Console）**。

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

开发一个面向**运维操作人员**的 Web 管理面板，能力如下：

### 1. 账号绑定可视化
- 查看「前端用户 ↔ 后端 GHC 账号」的 1:1 粘性绑定关系（来自 `bindings` 表）：绑定状态
  （active/released）、绑定时间、最近活跃时间。
- 支持手动解绑（释放绑定，使账号回到 idle 可被重新分配）。

### 2. token 用量可视化（读取 `usage_rollup`，不读 prompt 明细）
按以下**全部维度**展示用量（prompt_tokens / completion_tokens / requests）：
- **按时间趋势**：每日/区间的 token 与请求数趋势图。
- **按前端用户**：各用户的 token 消耗排行与明细。
- **按后端账号**：各 GHC 后端账号承载的流量分布。
- **按模型**：各模型（GPT / Claude 等）的 token 占比。

### 3. 前端用户与 Key 全生命周期管理
- 列出所有前端用户（`users`）及其名下 Key（`api_keys`，仅展示元数据，**不展示明文/哈希**）：
  Key 名称、scopes、状态、限速、创建时间、最近使用时间。
- 创建用户并签发默认 Key（明文 Key 仅在创建时返回一次）。
- 为既有用户新增 / 轮换 / 吊销 Key。
- 停用 / 启用前端用户。

### 4. 后端 GHC 账号状态管理
- 查看账号池（`accounts`）：login、plan、api_base、status
  （logging_in/idle/bound/quarantined/disabled）、last_error、last_seen_at、下次刷新时间。
- 变更账号状态（如 disable / 解除 quarantine 回到 idle）。
- 导入新账号（既有能力）。
- 对登录失效的账号触发 Device Flow 重新登录，并展示返回的 `user_code` / `verification_uri`
  供操作人员在浏览器完成真人授权（start + poll，既有能力）。

### 5. 鉴权
- 面板沿用现有**静态 admin token** 机制：操作人员在面板登录时输入 `GHCPROXY_ADMIN_TOKEN`，
  前端持该 token 调用 admin API（`X-Admin-Token` 头）。不新增操作员账号体系。

## 技术形态

- **独立 SPA + JSON API**：前端为单页应用（现代框架），通过扩展后的 admin JSON API 与后端交互；
  前后端分离、独立构建与部署。
- 后端在现有 `src/ghcproxy/admin/api.py` 基础上**补齐当前缺失的 JSON 端点**，至少包括：
  - 用量查询：按 用户 / 账号 / 模型 / 时间区间 聚合查询 `usage_rollup`。
  - 用户与 Key：列出用户、列出/新增/轮换/吊销 Key、停用/启用用户。
  - 账号：变更账号状态、查看绑定关系、手动解绑。
  - （账号导入、Device Flow start/poll 已存在，复用即可。）
- 所有新增端点继续走 `require_admin`（静态 admin token）鉴权。

## 范围与非目标

- ❌ 不读取/检索 prompt 日志（未留存于数据库，仅在 Kafka）。
- ❌ 不实现操作员账号/SSO/会话登录（沿用静态 admin token）。
- ❌ 不消费 Kafka、不做数据分析（与既有项目范围一致）。
- ✅ 仅面向运维操作人员，非面向前端终端用户。

## 交付条件

1. 后端新增 admin 端点实现完整并有测试覆盖（沿用既有 TDD 风格，`tests/` 下补充用例）。
2. SPA 实现上述四类管理能力，能通过 admin token 调通后端，本地可运行。
3. 文档同步更新：README、设计文档（`ghc-proxy-design.md`）补充管理面板章节，部署说明
   （docker/k8s）补充面板的构建与托管方式。
4. 所有脚本和配置均使用占位符，不写入真实租户 ID、密钥、token 或客户敏感信息。
5. 利用本地环境 Azure CLI 账号和 Azure 资源执行完整的部署测试。

## 注意事项

- 复用现有数据模型（`src/ghcproxy/db/schema.sql`）与 repo 方法（`src/ghcproxy/db/repo.py`），
  按需补充只读查询/状态变更方法。
- Key 一律只存哈希（`api_keys.key_hash`），面板任何场景都不得回显明文或哈希，明文仅在签发瞬间返回一次。
- 检查 `.gitignore` 合理性，前端构建产物（如 `node_modules/`、`dist/`）不入库。

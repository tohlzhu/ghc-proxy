# TODO

按照下面罗列的要求完成代码开发。

## 需求背景

1. 已知 Github Copilot(GHC) 的服务器域名清单如下，If your company employs security measures like a firewall or proxy server, you should add the following URLs, ports, and protocols to an allowlist to ensure Copilot works as expected: [# Copilot allowlist reference](https://docs.github.com/en/copilot/reference/copilot-allowlist-reference#github-public-urls)；
2. 已知互联网上有将 GHC 作为 Claude Code 和 Codex 和 OpenClaw 的模型端点 provider，参考项目有 [# Copilot API Proxy for Claude Code and Codex](https://github.com/voidsteed/copilot-proxy-api#copilot-api-proxy-for-claude-code-and-codex) 和 OpenClaw doc [## Three ways to use Copilot in OpenClaw](https://docs.openclaw.ai/providers/github-copilot#three-ways-to-use-copilot-in-openclaw)；
3. 我的客户自己实现了一个 GHC 代理服务把 GHC 的模型 API 包装成可以直接接入 Claude Code/Codex/OpenClaw 的模型端点，最终效果是前端用户拿一个 GHC 代理服务分发的 key 认证后经过代理访问 GHC 的所有模型，每一个前端用户流量只对应一个后端 GHC 账号，避免 GHC 官方把代理识别为多人使用一个账号（会导致封号）。通过统一端点代理数百个前端用户到 GHC 账号的 prompt 流量，可以实现统一的 token 流量统计、prompt 日志保留（可以审计、数据挖掘）、用户行为分析，以及用 GHC 账号的模型端点支持 Claude Code 等软件运行；（鉴于 GHC 官方主动支持 OpenClaw 使用 GHC 作为模型端点，这种代理服务是受 GHC 官方支持的用法，并不违反官方政策）
4. 上述 GHC 代理服务在后台登录 GHC CLI 过程中把 GHC 返回的 device 验证信息，如 `To authenticate, visit https://github.com/login/device and enter code XXXX-XXXX.`，吐给前端用户，让真实前端用户在本地浏览器完成账号登录和设备授权。授权成功后 GHC 代理服务会保存 GHC 账号在后台的登录认证凭证（保存到数据库，这样不需要通过 N 个 docker 隔离 N 个 GHC 账号登录状态），每次发起到 GHC 服务器请求时自动附带凭证，并且在凭证过期时间内主动刷新它（为避免用户一直不访问，可以用定时任务主动刷新凭证），这样实现登录状态长时间保持，前端用户可以直接使用 Key 访问模型，不需要关注登录状态；
5. 如果用户登录 GHC 本地客户端，GHC 代理服务就无法拿到本地客户端产生的 prompt，要避免这个情况可以考虑由企业统一注册一批 GHC 账号在 GHC 代理服务器上使用，由操作人员做每个 GHC 账号的实际登录、设备授权，前端用户完全不需要知道用了哪个 GHC 账号。这种做法需要 GHC 代理服务器自动管理前端用户到某一个 GHC 账号的对应关系，并在该 GHC 账号登录失效时自动将前端用户路由给一个闲置 GHC 账号，登录失效的 GHC 账号让操作人员重新登录（当然，要用定时任务主动刷新登录凭证有效期，避免频繁触发登出）；
6. 你当前 workspace 所在服务器已经登录一个 GHC 账号，你可以分析它的登录凭证保存机制来了解技术实现路径；
7. 当前环境登录了一个 azure 账号，订阅名为 `ME-MngEnvMCAP012397-zhuhonglei-1`，如有必要可以用 azure cli 在 japan east 区域创建资源组 `rg-dev2` 部署必要的云资源，完成测试等开发任务（**当前 workspace 所在 VM 位于 `vnet-dev-jpe` VNet 下，从当前服务器发起到测试资源的请求需要先打通 VNet Peering 才能基于内网 IP 访问**，注意自己查询资源和网络现状）；
8. 你已经基于1-6所述需求完成设计文件 `ghc-proxy-design.md` 描述了整个系统的原理、架构、各服务调用关系、时序信息。

## 任务需求

1. 首先检查设计文件 `ghc-proxy-design.md` 的设想，确认核心思路可行后着手开发一个完整的 ghc-proxy 服务；
2. 我要离开一段时间，请尽可能独立实现整个项目，目标是以完成态交付所有代码，且代码应该进行过部署测试；
3. 在Azure 订阅 `ME-MngEnvMCAP012397-zhuhonglei-1` 下测试，你可以创建需要的VNet、VM、数据库、k8s 等所有资源，注意最终保留一套部署以供我事后验证；**注意该订阅是微软为员工提供的 external tenant，能开云资源，但是网络管制非常严格，你的测试应该从 VNet 内部执行，避免基于公网测试、连接服务器，以及开通资源以满足测试为目标，尽可能开最小规格；
4. 你可以利用本地环境登录的 GHC 账号的登录凭证执行完整链路的测试，**但是务必注意你自己无法完成 GHC 在浏览器登录过程，这个过程不需要你测，直接用你拿到的登录凭证测试 GHC 账号登录以外的所有流程**；
5. **日志、prompt 流转给 kafka 保存即可，不需要实现消费kafka、执行数据分析的过程，这些不属于本项目功能范围；**
6. 考虑基于 TDD 开发模式的可行性，并适当运用；

### 交付条件

1. 代码实现所有业务逻辑，通过本地测试、云端部署测试，文档已更新为与最新代码意图一致；
2. 陷入死循环或者保留 GHC 凭证的逻辑可行性被证伪，则在 todo.md 描述情况，然后退出任务，不要盲目重试。

## 注意事项

如有必要，更新相关项目文档、代码，注意检查 .gitignore 文件合理性、更新 README.md。
请注意测试，注意发挥主动性寻找最新信息，通过多个渠道核实准确性。所有脚本和配置均使用占位符，不写入真实租户 ID、密钥、token 或客户敏感信息。

---

## 交付状态（2026-06-10 完成）

✅ **已完成态交付，全部测试通过。**

### 可行性结论（凭证逻辑**未被证伪**，且较设计更简单）
用 mitmproxy 抓取本机 Copilot CLI 1.0.61 真实流量实测：**当前 CLI 直接用 `gho_` 长效 token
作为模型 API 的 Bearer**，不走 `copilot_internal/v2/token` 短效换取（该接口对 CLI token 返回 404）。
故「数据库存 N 行加密 `gho_`、无需 N 容器」的核心思路成立且更简单。设计文档 §2 已据实修正。

### 完成项
1. **完整 Python 实现**（`src/ghcproxy/`，FastAPI + httpx + asyncpg + redis + aiokafka）：
   鉴权 → 1:1 粘性绑定 → 转发（含 buffered/streaming 失效自动改路由重试）→ 用量/prompt 投 Kafka；
   refresher 存活性校验单写者；admin API（导入账号 / Device Flow start+poll / 签发 Key）；Prometheus 指标。
2. **测试**：`pytest` 73 项单元 + 接口测试全绿（TDD 编写：crypto / keys / config / 绑定 / 转发改路由 /
   流式错误处理 / 用量解析 / 鉴权 / device flow / refresher / 接口 / 打包）。
3. **本地全栈集成**：docker-compose（PG+Redis+Kafka+proxy+refresher），真实 `gho_` 跑通
   GPT（`/chat/completions`）、Claude（`/v1/messages` 与 `/chat/completions`）、流式 SSE、
   用量入 Postgres、事件入 Kafka。
4. **Azure 云端部署测试**：`rg-dev2`（japaneast）VM 全栈 docker-compose，从 `vnet-dev-jpe`
   经 VNet Peering 内网（私网 IP，无公网暴露）调用 GPT/Claude 通过。部署保留供验证。
5. **文档**：README、设计文档、k8s/部署说明、.gitignore、.dockerignore 均已更新对齐。

### 范围说明
- Kafka 仅做 prompt/用量/审计的**生产**；消费与数据分析不在本项目范围（按要求）。
- GHC 浏览器登录（Device Flow 真人授权）不由本任务测试；用既有 `gho_` 凭证验证了登录以外全链路。

### Azure 资源（供事后验证）
- RG `rg-dev2`、VNet `vnet-ghcproxy`(10.90.0.0/16) ↔ `vnet-dev-jpe` 双向 Peering、
  VM `vm-ghcproxy`@10.90.1.4（Standard_D4ls_v6，无公网 IP，NSG 仅放行 10.89.0.0/16 的 22/8080）。
- 部署密钥在 VM 的 `~/ghc-proxy/.deploy-env`（未入库）。

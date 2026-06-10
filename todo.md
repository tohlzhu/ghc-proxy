# TODO

按照下面罗列的要求编写设计文件 `ghc-proxy-design.md` 和其他相关脚本、配置示例代码。

## 需求背景

1. 已知 Github Copilot(GHC) 的服务器域名清单如下，If your company employs security measures like a firewall or proxy server, you should add the following URLs, ports, and protocols to an allowlist to ensure Copilot works as expected: [# Copilot allowlist reference](https://docs.github.com/en/copilot/reference/copilot-allowlist-reference#github-public-urls)；
2. 已知互联网上有将 GHC 作为 Claude Code 和 Codex 和 OpenClaw 的模型端点 provider，参考项目有 [# Copilot API Proxy for Claude Code and Codex](https://github.com/voidsteed/copilot-proxy-api#copilot-api-proxy-for-claude-code-and-codex) 和 OpenClaw doc [## Three ways to use Copilot in OpenClaw](https://docs.openclaw.ai/providers/github-copilot#three-ways-to-use-copilot-in-openclaw)；
3. 我的客户自己实现了一个 GHC 代理服务把 GHC 的模型 API 包装成可以直接接入 Claude Code/Codex/OpenClaw 的模型端点，最终效果是前端用户拿一个 GHC 代理服务分发的 key 认证后经过代理访问 GHC 的所有模型，每一个前端用户流量只对应一个后端 GHC 账号，避免 GHC 官方把代理识别为多人使用一个账号（会导致封号）。通过统一端点代理数百个前端用户到 GHC 账号的 prompt 流量，可以实现统一的 token 流量统计、prompt 日志保留（可以审计、数据挖掘）、用户行为分析，以及用 GHC 账号的模型端点支持 Claude Code 等软件运行；（鉴于 GHC 官方主动支持 OpenClaw 使用 GHC 作为模型端点，这种代理服务是受 GHC 官方支持的用法，并不违反官方政策）
4. 上述 GHC 代理服务在后台登录 GHC CLI 过程中把 GHC 返回的 device 验证信息，如 `To authenticate, visit https://github.com/login/device and enter code XXXX-XXXX.`，吐给前端用户，让真实前端用户在本地浏览器完成账号登录和设备授权。授权成功后 GHC 代理服务会保存 GHC 账号在后台的登录认证凭证（保存到数据库，这样不需要通过 N 个 docker 隔离 N 个 GHC 账号登录状态），每次发起到 GHC 服务器请求时自动附带凭证，并且在凭证过期时间内主动刷新它（为避免用户一直不访问，可以用定时任务主动刷新凭证），这样实现登录状态长时间保持，前端用户可以直接使用 Key 访问模型，不需要关注登录状态；
5. 如果用户登录 GHC 本地客户端，GHC 代理服务就无法拿到本地客户端产生的 prompt，要避免这个情况可以考虑由企业统一注册一批 GHC 账号在 GHC 代理服务器上使用，由操作人员做每个 GHC 账号的实际登录、设备授权，前端用户完全不需要知道用了哪个 GHC 账号。这种做法需要 GHC 代理服务器自动管理前端用户到某一个 GHC 账号的对应关系，并在该 GHC 账号登录失效时自动将前端用户路由给一个闲置 GHC 账号，登录失效的 GHC 账号让操作人员重新登录（当然，要用定时任务主动刷新登录凭证有效期，避免频繁触发登出）；
6. 你当前 workspace 所在服务器已经登录一个 GHC 账号，你可以分析它的登录凭证保存机制来了解技术实现路径。

## 任务需求

1. 调研开发一套 GHC Proxy 的可行性，原理和功能与需求背景描述的 GHC 代理服务类似，尤其要搞清楚 GHC 本地登录凭证如何提取保存、自动刷新，如何实现在不创建独立容器或虚拟化环境情况下同时管理一批 GHC CLI 的登录状态；
2. 如果你对可行性很有把握，可以直接编写设计文件 `ghc-proxy-design.md`，详细描述原理、架构、各服务调用关系、时序等；如果不确定，首先和我反馈原因、讨论，然后再决定如何继续执行任务；
3. 技术选型优先基于 ts 或 python，GHC Proxy 应具备高可用和水平扩展能力，部署运行环境是 k8s，数据库优先基于 PostgreSQL 和 Redis，用户日志、Prompt 留存可基于 Kafka；

## 注意事项

如有必要，更新相关 markdown 文件或按需调整配置示例文件。

请注意测试，注意发挥主动性寻找最新素材，并通过多个渠道核实准确性。所有脚本和配置均使用占位符，不写入真实租户 ID、密钥、token 或客户敏感信息。

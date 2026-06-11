# GHC Proxy — Kubernetes 部署

后端为同一镜像、两类工作负载（`ROLE` 环境变量区分），外加一个独立的面板镜像：

- **proxy**：无状态前端入口，`Deployment` + `Service` + `HPA`（按 CPU 扩缩 3→20）。
- **refresher**：账号存活性校验单写者，`Deployment` 1 副本（Redis 锁保证每账号单写者，>1 也安全）。
- **console**：运维管理面板（独立 nginx 镜像，托管 React SPA 并反代 `/admin/*` 到 `ghc-proxy` Service），
  `Deployment` + `Service`（`40-console.yaml`）。

## 前置依赖

PostgreSQL、Redis、Kafka 由集群内 StatefulSet 或托管服务提供（不在本清单内），通过
`ghc-proxy-secrets` 注入连接串。

## 部署步骤

```bash
# 1) 构建并推送镜像（后端 + 面板两个镜像）
docker build -t <REGISTRY>/ghc-proxy:<TAG> .
docker push <REGISTRY>/ghc-proxy:<TAG>
docker build -t <REGISTRY>/ghc-proxy-console:<TAG> ./frontend
docker push <REGISTRY>/ghc-proxy-console:<TAG>

# 2) 准备命名空间与配置
kubectl apply -f 00-namespace.yaml
#   将 10-config.yaml 中的 Secret 占位换成真实值（建议用 sealed-secrets /
#   External Secrets / CI 注入，切勿提交明文）：
#     - GHCPROXY_POSTGRES__URL / GHCPROXY_REDIS__URL / GHCPROXY_KAFKA__BROKERS
#     - GHCPROXY_CRYPTO__DATA_KEY_B64（32 字节 base64；建议经 KMS 信封加密）
#     - GHCPROXY_ADMIN_TOKEN
kubectl apply -f 10-config.yaml

# 3) 在 20-/30-/40- 清单里把 REGISTRY/...:TAG 替换为实际镜像
kubectl apply -f 20-proxy.yaml -f 30-refresher.yaml -f 40-console.yaml

# 4) 首次需建表：任一 proxy Pod 设 GHCPROXY_INIT_SCHEMA=1 启动一次，
#    或对数据库执行 src/ghcproxy/db/schema.sql。refresher 启动时也会幂等建表。
```

## 管理面板（console）

- 独立 nginx 镜像，托管 React SPA 静态产物，并把 `/admin/*` 反向代理到 `ghc-proxy` Service
  （`PROXY_UPSTREAM=ghc-proxy:80`，已在 `40-console.yaml` 设置）。
- 通过你的 Ingress 控制器（建议 TLS 终结）或将 `ghc-proxy-console` Service 改为 `LoadBalancer`
  对**运维操作人员**暴露；面板用静态 `GHCPROXY_ADMIN_TOKEN` 鉴权，务必限制访问来源。
- 面板本身无状态，可多副本（清单默认 2 副本）。

## 健康探针

- proxy：HTTP `GET /healthz`（readiness + liveness）。
- refresher：心跳文件新鲜度（worker 每个 tick 重写 `/tmp/ghcproxy-refresher.heartbeat`）。

## 可观测性

- Prometheus 指标：proxy 的 `GET /metrics`（QPS、上游时延、改路由次数、隔离/空闲账号数等）。
- prompt / 用量 / 审计事件投递 Kafka topic：`ghcproxy.prompts`、`ghcproxy.usage`、`ghcproxy.audit`。

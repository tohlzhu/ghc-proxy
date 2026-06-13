# TODO

修复 account Re-login 功能。

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
4. 当前 workspace 所在服务器已经登录一个 GHC 账号，你可以分析它的登录凭证保存机制来了解技术实现路径，也可以用它的 copilotTokens 执行测试，配置文件路径为 `/home/azureuser/.copilot/config.json`；测试时没有人类配合你执行 GitHub device flow 的浏览器登录授权，请在成功获得 device code 后跳过浏览器登录、授权设备的过程，在任务结束时指出你无法自动测试的部分功能。
5. 当前 shell 环境登录了一个 azure 账号，订阅名为 ME-MngEnvMCAP012397-zhuhonglei-1，如有必要可以用 azure cli 在 japan east 区域创建资源组 rg-dev2 部署必要的云资源，完成测试等开发任务（当前 workspace 所在 VM 位于 vnet-dev-jpe VNet 下，从当前服务器发起到测试资源的请求需要先打通 VNet Peering 才能基于内网 IP 访问，注意自己查询资源和网络现状）；**rg-dev2 和内部资源在前序实现时已经创建，可以重用；**

## 任务需求

我通过 `docker compose` 启动，在浏览器测试 `Backend GHC Accounts` 页面中的 `Re-login` 按钮发现前端页面报 `500 Internal Server Error` 错，docker-proxy-1 容器的输出显示如下错误：

```txt

huhonglei@honglei-book1:~/github/ghc-proxy/deploy/docker$ docker attach docker-proxy-1
INFO:     127.0.0.1:39846 - "GET /healthz HTTP/1.1" 200 OK
2026-06-12 13:47:45,095 INFO httpx HTTP Request: POST https://github.com/login/device/code "HTTP/1.1 404 Not Found"
INFO:     172.18.0.7:58276 - "POST /admin/accounts/zhuhonglei_microsoft/login/start HTTP/1.0" 500 Internal Server Error
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/uvicorn/protocols/http/httptools_impl.py", line 421, in run_asgi
    result = await app(  # type: ignore[func-returns-value]
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/uvicorn/middleware/proxy_headers.py", line 62, in __call__
    return await self.app(scope, receive, send)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/fastapi/applications.py", line 1159, in __call__
    await super().__call__(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/applications.py", line 90, in __call__
    await self.middleware_stack(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/errors.py", line 186, in __call__
    raise exc
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/errors.py", line 164, in __call__
    await self.app(scope, receive, _send)
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/exceptions.py", line 63, in __call__
    await wrap_app_handling_exceptions(self.app, conn)(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
    raise exc
  File "/usr/local/lib/python3.12/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
    await app(scope, receive, sender)
  File "/usr/local/lib/python3.12/site-packages/fastapi/middleware/asyncexitstack.py", line 18, in __call__
    await self.app(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/routing.py", line 660, in __call__
    await self.middleware_stack(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/routing.py", line 680, in app
    await route.handle(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/routing.py", line 276, in handle
    await self.app(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 134, in app
    await wrap_app_handling_exceptions(app, request)(scope, receive, send)
  File "/usr/local/lib/python3.12/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
    raise exc
  File "/usr/local/lib/python3.12/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
    await app(scope, receive, sender)
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 120, in app
    response = await f(request)
               ^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 674, in app
    raw_response = await run_endpoint_function(
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/fastapi/routing.py", line 328, in run_endpoint_function
    return await dependant.call(**values)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/ghcproxy/admin/api.py", line 231, in start_login
    dc = await ctx.device_flow.request_device_code()
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/ghcproxy/credential/device_flow.py", line 56, in request_device_code
    raise DeviceFlowError(f"device code request failed: {status} {body}")
ghcproxy.credential.device_flow.DeviceFlowError: device code request failed: 404 {'error': 'Not Found'}
INFO:     127.0.0.1:56484 - "GET /healthz HTTP/1.1" 200 OK
INFO:     127.0.0.1:37188 - "GET /healthz HTTP/1.1" 200 OK

```

请整体分析 ghc-proxy 项目代码，修复 re-login 行为：

1. 确保 re-login 能正确触发 GitHub Device FLow。
2. 由于现在看不到 re-login 按钮正确执行的结果，请确保 re-login 按钮成功触发 GitHub Device FLow 后在前端页面弹窗显示一封邮件格式的文本，文本内容为要求用户使用后台返回的device code到指定网址完成登录授权，措辞要专业、礼貌，请用户配合。操作人员会将该邮件格式的文本复制、发送电子邮件给用户，所以请确保弹窗内容有一个复制按钮，方便操作。如果现有实现满足这一点，可以不做修改。

## 交付条件

1. 如果有变更，要保证前端、后端代码互相功能匹配，实现目标需求。
2. 文档同步更新：README、设计文档（`ghc-proxy-design.md`）及项目设计过程中创建的示例。
3. 所有脚本和配置均使用占位符，不写入真实租户 ID、密钥、token 或客户敏感信息。
4. 利用本地环境 Azure CLI 账号和 Azure 资源执行完整的部署测试。

## 注意事项

- 复用现有数据模型（`src/ghcproxy/db/schema.sql`）与 repo 方法（`src/ghcproxy/db/repo.py`），
  按需补充只读查询/状态变更方法。
- Key 一律只存哈希（`api_keys.key_hash`），面板任何场景都不得回显明文或哈希，明文仅在签发瞬间返回一次。
- 检查 `.gitignore` 合理性，前端构建产物（如 `node_modules/`、`dist/`）不入库。
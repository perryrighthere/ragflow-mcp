# RAGFlow Raw API CLI / FastAPI Proxy

这个仓库现在只保留两种交互方式：

- CLI 命令
- FastAPI `/docs` 页面

不再提供前端页面，也不再封装新的业务 API。服务只暴露当前仓库里用到的原始 RAGFlow API 路径，并把上游响应按原样透传回来。

## 当前能力

- `GET /v1/system/healthz`
- `POST /api/v1/retrieval`
- `GET /api/v1/datasets/{dataset_id}/documents`
- `POST /api/v1/datasets/{dataset_id}/documents`
- `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`
- `POST /api/v1/datasets/{dataset_id}/chunks`

所有上游调用都会直接打印到 CLI 输出里，包括：

- 请求方法和 URL
- 完整请求头
- 原始请求体
- 等价的 `curl` 原始命令
- 返回的 HTTP 状态码
- 返回体

注意：为了便于排查问题，请求日志会直接打印 `Authorization` 头，请只在受控调试环境中使用这些日志。

## 依赖安装

项目现在依赖 FastAPI、Uvicorn、python-multipart 和 httpx。

如果你已经在项目根目录有 `.venv`，直接安装：

```bash
.venv/bin/pip install -r requirements.txt
```

## 环境变量

支持系统环境变量和仓库根目录 `.env`。

可用字段：

- `RAGFLOW_BASE_URL`
- `RAGFLOW_API_KEY`
- `RAGFLOW_TIMEOUT`
- `SERVICE_HOST`
- `SERVICE_PORT`

示例：

```bash
cp .env.example .env
```

或者直接 export：

```bash
export RAGFLOW_BASE_URL="http://127.0.0.1:9380"
export RAGFLOW_API_KEY="your-api-key"
export RAGFLOW_TIMEOUT="60"
export SERVICE_HOST="0.0.0.0"
export SERVICE_PORT="8080"
```

## 启动 FastAPI Docs

```bash
.venv/bin/python main.py serve
```

启动后访问：

```bash
http://127.0.0.1:8080/docs
```

如果还没配置 `RAGFLOW_BASE_URL` / `RAGFLOW_API_KEY`，`/docs` 页面仍然能打开，但实际调用上游接口时会返回 `503`。

## CLI 用法

统一入口：

```bash
.venv/bin/python main.py request METHOD PATH [--json JSON] [--query JSON] [--file PATH]
```

说明：

- `METHOD`：HTTP 方法，例如 `GET`、`POST`、`PUT`
- `PATH`：原始 RAGFlow 路径，例如 `/api/v1/retrieval`
- `--json`：JSON 请求体，必须是 JSON 对象
- `--query`：查询参数，必须是 JSON 对象
- `--file`：上传文件路径，可重复传入
- `--no-auth`：跳过 `Authorization` 头，适合 `/v1/system/healthz`
- `--base-url` / `--api-key` / `--timeout`：只覆盖当前命令

## CLI 示例

健康检查：

```bash
.venv/bin/python main.py request GET /v1/system/healthz --no-auth --base-url http://127.0.0.1:9380
```

检索 chunks：

```bash
.venv/bin/python main.py request POST /api/v1/retrieval \
  --json '{"question":"五看六定是什么？","dataset_ids":["kb_123"],"page_size":6,"highlight":true}'
```

查询文档列表：

```bash
.venv/bin/python main.py request GET /api/v1/datasets/kb_123/documents \
  --query '{"page":1,"page_size":20,"run":["DONE"]}'
```

上传文档：

```bash
.venv/bin/python main.py request POST /api/v1/datasets/kb_123/documents \
  --file ./contract.pdf \
  --file ./manual.txt
```

更新单个文档：

```bash
.venv/bin/python main.py request PUT /api/v1/datasets/kb_123/documents/doc_456 \
  --json '{"meta_fields":{"tenant":"acme","department":"legal"},"enabled":1}'
```

触发解析：

```bash
.venv/bin/python main.py request POST /api/v1/datasets/kb_123/chunks \
  --json '{"document_ids":["doc_456"]}'
```

## FastAPI 路由说明

这里的 FastAPI 只负责两件事：

- 提供 `/docs`
- 把请求原样转发到上游 RAGFlow

也就是说：

- 不再提供 `/api/v1/settings`
- 不再提供 `/api/v1/documents/upload`
- 不再提供 `/api/v1/documents/.../metadata`
- 不再提供 `/api/v1/qa/answer`

## 测试

在项目虚拟环境里执行：

```bash
.venv/bin/python -m unittest tests.test_config tests.test_ragflow_client tests.test_http_server_api tests.test_main
```

## 代码结构

- `main.py`：CLI 入口，支持 `serve` 和 `request`
- `ragflow_service/config.py`：读取 `.env` 和系统环境变量
- `ragflow_service/ragflow_client.py`：原始 RAGFlow HTTP 调用和 CLI 日志输出
- `ragflow_service/http_server.py`：FastAPI 应用和 `/docs`
- `tests/test_config.py`：配置测试
- `tests/test_ragflow_client.py`：上游调用测试
- `tests/test_http_server_api.py`：FastAPI 路由测试
- `tests/test_main.py`：CLI 测试

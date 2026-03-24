# RAGFlow Knowledge Base QA Service

这个仓库现在同时提供三种交互方式：

- CLI 命令
- FastAPI `/docs` 页面
- 零依赖前端页面 `/`

除了原始 RAGFlow 代理接口之外，服务新增了一个知识库问答 API：

1. 先调用 `POST /api/v1/retrieval`
2. 只提取每个 chunk 的 `document_keyword` 和 `content`
3. 把这些精简后的知识片段发给一个 OpenAI 兼容的 LLM
4. 返回最终答案和来源片段

## 当前能力

- `GET /v1/system/healthz`
- `POST /api/v1/retrieval`
- `POST /api/v1/qa/answer`
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
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_TIMEOUT`
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
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="your-llm-api-key"
export LLM_MODEL="gpt-4o-mini"
export LLM_TIMEOUT="60"
export SERVICE_HOST="0.0.0.0"
export SERVICE_PORT="8080"
```

说明：

- `LLM_BASE_URL` 需要指向 OpenAI 兼容接口根路径，例如 `https://api.openai.com/v1`
- 服务会自动请求 `POST {LLM_BASE_URL}/chat/completions`

## 启动服务

```bash
.venv/bin/python main.py serve
```

启动后访问：

```bash
http://127.0.0.1:8080/
```

可选入口：

- 前端控制台：`http://127.0.0.1:8080/`
- 接口文档：`http://127.0.0.1:8080/docs`

如果还没配置 `RAGFLOW_BASE_URL` / `RAGFLOW_API_KEY` / `LLM_*`，页面仍然能打开，但对应调用会返回 `503`。

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

## 知识库问答 API

请求：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/answer \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "五看六定是什么？",
    "dataset_ids": ["kb_123"],
    "page_size": 6,
    "temperature": 0.2
  }'
```

返回示例：

```json
{
  "code": 0,
  "data": {
    "question": "五看六定是什么？",
    "answer": "根据知识库内容，五看包括看行业、看市场、看用户、看竞争、看自己；六定包括定位、定标、定价、定配、定本、定量。",
    "sources": [
      {
        "document_keyword": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
        "content": "..."
      }
    ],
    "source_count": 1,
    "retrieval_total": 1,
    "model": "gpt-4o-mini"
  }
}
```

`/api/v1/qa/answer` 支持的关键字段：

- `question`: 必填，用户问题
- `dataset_ids`: 可选，限定知识库
- `document_ids`: 可选，限定文档
- `page_size`: 可选，默认 `6`
- `temperature`: 可选，透传给 LLM
- `max_tokens`: 可选，透传给 LLM

接口内部会先调用 RAGFlow 检索，但只会把每条命中的 `document_keyword` 和 `content` 提供给 LLM，不会把相似度等其他字段拼进提示词。

## 测试

在项目虚拟环境里执行：

```bash
.venv/bin/python -m unittest tests.test_config tests.test_ragflow_client tests.test_http_server_api tests.test_qa_service tests.test_main
```

## 代码结构

- `main.py`：CLI 入口，支持 `serve` 和 `request`
- `ragflow_service/config.py`：读取 `.env` 和系统环境变量
- `ragflow_service/ragflow_client.py`：原始 RAGFlow HTTP 调用和 CLI 日志输出
- `ragflow_service/llm_client.py`：OpenAI 兼容 LLM 调用
- `ragflow_service/qa_service.py`：检索结果后处理和问答编排
- `ragflow_service/http_server.py`：FastAPI 应用、前端静态页和 `/docs`
- `frontend/`：无构建的前端页面
- `tests/test_config.py`：配置测试
- `tests/test_ragflow_client.py`：上游调用测试
- `tests/test_http_server_api.py`：FastAPI 路由测试
- `tests/test_qa_service.py`：问答编排测试
- `tests/test_main.py`：CLI 测试

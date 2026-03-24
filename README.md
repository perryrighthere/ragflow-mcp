# RAGFlow Knowledge Base QA Service

一个轻量后端，提供：

- RAGFlow 原始代理接口
- 知识库问答接口 `POST /api/v1/qa/answer`
- 知识门户文档同步下载接口 `POST /api/v1/knowledge-portal/documents/sync`
- 前端控制台 `/`
- Swagger 文档 `/docs`

注意：上游请求日志会打印完整请求头，包括 `Authorization`，只建议在受控调试环境中使用。

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py serve
```

激活虚拟环境后，也可以直接运行：

```bash
./main.py serve
```

启动后访问：

- `http://127.0.0.1:8080/`
- `http://127.0.0.1:8080/docs`

## 常用命令

启动服务：

```bash
python main.py serve
```

自定义 host / port：

```bash
python main.py serve --host 0.0.0.0 --port 8080
```

发送原始 RAGFlow 请求：

```bash
python main.py request METHOD PATH [--json JSON] [--query JSON] [--file PATH]
```

示例：

```bash
python main.py request GET /v1/system/healthz --no-auth --base-url http://127.0.0.1:9380
python main.py request POST /api/v1/retrieval --json '{"question":"五看六定是什么？"}'
```

运行测试：

```bash
python -m unittest tests.test_config tests.test_ragflow_client tests.test_http_server_api tests.test_knowledge_portal_service tests.test_qa_service tests.test_main
```

## 环境变量

支持系统环境变量和仓库根目录 `.env`。

必填：

- `RAGFLOW_BASE_URL`
- `RAGFLOW_API_KEY`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`

可选：

- `RAGFLOW_TIMEOUT`
- `LLM_TIMEOUT`
- `SERVICE_HOST`
- `SERVICE_PORT`

说明：

- `LLM_BASE_URL` 需要指向 OpenAI 兼容接口根路径，例如 `https://api.openai.com/v1`
- 如果没配置 RAGFlow / LLM，页面仍能打开，但对应接口会返回 `503`

## 主要接口

- `GET /v1/system/healthz`
- `POST /api/v1/retrieval`
- `POST /api/v1/qa/answer`
- `POST /api/v1/knowledge-portal/documents/sync`
- `GET /api/v1/datasets/{dataset_id}/documents`
- `POST /api/v1/datasets/{dataset_id}/documents`
- `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`
- `POST /api/v1/datasets/{dataset_id}/chunks`

## 知识门户文档同步

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/knowledge-portal/documents/sync \
  --header 'Content-Type: application/json' \
  --data '{
    "base_url": "https://km.seres.cn",
    "community_id": "your-community-id",
    "username": "your-username",
    "password": "your-password",
    "type": "mutildoc",
    "page_size": 100,
    "max_download_files": 50
  }'
```

行为：

- 遍历列表接口分页，收集全部文档 `fdId`
- 逐条拉取详情，保存 `detail.json` 和 `content.md`
- 根据 `fdCoverImg.fileId` 和 `fdFile[].fileId` 下载附件
- 附件保存在 `output/attachments/`
- `max_download_files` 用来限制二进制附件下载总量

## 代码结构

- `main.py`：CLI 入口
- `ragflow_service/http_server.py`：FastAPI 应用和路由
- `ragflow_service/ragflow_client.py`：RAGFlow 客户端
- `ragflow_service/knowledge_portal_client.py`：知识门户客户端
- `ragflow_service/knowledge_portal_service.py`：知识门户下载编排
- `ragflow_service/qa_service.py`：知识库问答编排
- `tests/`：单元测试
- `ragflow_service/qa_service.py`：检索结果后处理和问答编排
- `ragflow_service/http_server.py`：FastAPI 应用、前端静态页和 `/docs`
- `frontend/`：无构建的前端页面
- `tests/test_config.py`：配置测试
- `tests/test_ragflow_client.py`：上游调用测试
- `tests/test_http_server_api.py`：FastAPI 路由测试
- `tests/test_knowledge_portal_service.py`：知识门户文档下载编排测试
- `tests/test_qa_service.py`：问答编排测试
- `tests/test_main.py`：CLI 测试

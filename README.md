# RAGFlow 文档服务

这个仓库现在提供了一个可直接运行的 Python 服务，用来封装你当前最需要的三类能力：

- 上传文档，并在上传后立刻补写 `meta_fields`
- 基于 `metadata_condition` 做检索过滤
- 更新单个或批量文档的 metadata 标签

实现完全基于 `http_api_reference.md` 中已经明确给出的接口：

- `POST /api/v1/datasets/{dataset_id}/documents`
- `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`
- `POST /api/v1/datasets/{dataset_id}/chunks`
- `POST /api/v1/retrieval`
- `GET /api/v1/datasets/{dataset_id}/documents`
- `GET /v1/system/healthz`

## 设计说明

RAGFlow 的上传接口本身不能直接携带 metadata，因此服务层采用两段式编排：

1. 先调用上传接口，拿到 `document_id`
2. 再逐个调用文档更新接口写入 `meta_fields`
3. 如果需要，最后触发解析

这也是你当前业务里“上传文档时必须带 metadata 标签”最稳妥的实现方式。

## 环境变量

运行前设置。你有两种方式：

### 方式 1：使用 `.env`

先复制示例文件：

```bash
cp .env.example .env
```

然后把 `.env` 里的 `RAGFLOW_BASE_URL` 和 `RAGFLOW_API_KEY` 改成你的实际值。

服务启动时会优先读取系统环境变量；如果系统环境变量没设置，就读取仓库根目录的 `.env`。

### 方式 2：直接 export

```bash
export RAGFLOW_BASE_URL="http://127.0.0.1:9380"
export RAGFLOW_API_KEY="your-api-key"
export SERVICE_HOST="0.0.0.0"
export SERVICE_PORT="8080"
export RAGFLOW_TIMEOUT="60"
```

## 启动

```bash
python main.py
```

启动后直接访问：

```bash
http://127.0.0.1:8080/
```

页面会展示一个零依赖前端控制台，用来直接验证上传文档、metadata 更新、metadata 过滤检索和文档列表查询。
前端页面顶部还支持读取和保存当前运行配置；保存后会写入仓库根目录的 `.env`，并立即切换服务运行时使用的 RAGFlow 地址和 API Key。`SERVICE_HOST` 与 `SERVICE_PORT` 会写入 `.env`，但需要重启服务后生效。
如果启动时还没配置 `RAGFLOW_BASE_URL` / `RAGFLOW_API_KEY`，服务现在也可以正常启动；此时先打开页面填写配置即可。未配置阶段，依赖 RAGFlow 的业务接口会返回 `503`。

## 对外接口

### 1. 健康检查

```bash
curl http://127.0.0.1:8080/health
```

### 0. 读取/更新运行配置

读取当前配置：

```bash
curl http://127.0.0.1:8080/api/v1/settings
```

更新配置。`ragflow_base_url`、`ragflow_api_key`、`request_timeout` 会立即作用到运行时；`server_host`、`server_port` 需要重启后生效：

```bash
curl --request PUT \
  --url http://127.0.0.1:8080/api/v1/settings \
  --header 'Content-Type: application/json' \
  --data '{
    "ragflow_base_url": "http://127.0.0.1:9380",
    "ragflow_api_key": "your-api-key",
    "request_timeout": 60,
    "server_host": "0.0.0.0",
    "server_port": 8080
  }'
```

### 2. 上传文档并写 metadata

`shared_meta_fields` 会作用到所有上传文件，`per_file_meta_fields` 可以按文件名覆盖或补充。

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/documents/upload \
  --form 'dataset_id=kb_123' \
  --form 'parse_after_upload=true' \
  --form 'enabled=1' \
  --form 'shared_meta_fields={"tenant":"acme","project":"rag"}' \
  --form 'per_file_meta_fields={"contract.pdf":{"department":"legal"}}' \
  --form 'files=@./contract.pdf' \
  --form 'files=@./manual.txt'
```

### 3. 更新单个文档 metadata

```bash
curl --request PUT \
  --url http://127.0.0.1:8080/api/v1/documents/kb_123/doc_456/metadata \
  --header 'Content-Type: application/json' \
  --data '{
    "meta_fields": {
      "tenant": "acme",
      "department": "finance",
      "year": "2026"
    },
    "enabled": 1
  }'
```

### 4. 批量更新 metadata

```bash
curl --request PUT \
  --url http://127.0.0.1:8080/api/v1/documents/metadata \
  --header 'Content-Type: application/json' \
  --data '{
    "dataset_id": "kb_123",
    "documents": [
      {
        "document_id": "doc_1",
        "meta_fields": {"tenant": "acme", "department": "hr"}
      },
      {
        "document_id": "doc_2",
        "meta_fields": {"tenant": "acme", "department": "legal"}
      }
    ]
  }'
```

### 5. 使用 Retrieve Chunks 检索并按 metadata 过滤

这里直接使用文档里的 `POST /api/v1/retrieval` 接口字段。
metadata 字段来自文档的 `meta_fields` JSON，不需要预先在 RAGFlow 中单独配置字段 schema。

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/retrieval \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "合同终止条件是什么？",
    "dataset_ids": ["kb_123"],
    "page": 1,
    "page_size": 10,
    "highlight": true,
    "metadata_condition": {
      "conditions": [
        {
          "name": "tenant",
          "comparison_operator": "=",
          "value": "acme"
        },
        {
          "name": "department",
          "comparison_operator": "=",
          "value": "legal"
        }
      ]
    }
  }'
```

### 6. 列出数据集文档

直接透传到 RAGFlow 的文档列表接口，便于查询已有文档 ID。

```bash
curl 'http://127.0.0.1:8080/api/v1/datasets/kb_123/documents?page=1&page_size=20&run=DONE'
```

## 代码结构

- `/home/perry/ragflow-mcp/main.py`：启动入口
- `/home/perry/ragflow-mcp/ragflow_service/ragflow_client.py`：RAGFlow HTTP 调用封装
- `/home/perry/ragflow-mcp/ragflow_service/document_service.py`：上传、metadata 更新、检索过滤编排
- `/home/perry/ragflow-mcp/ragflow_service/http_server.py`：对外 HTTP 服务
- `/home/perry/ragflow-mcp/frontend/index.html`：前端页面
- `/home/perry/ragflow-mcp/frontend/app.js`：前端交互逻辑
- `/home/perry/ragflow-mcp/frontend/app.css`：前端样式
- `/home/perry/ragflow-mcp/tests/test_document_service.py`：核心业务逻辑测试

## 当前边界

- 文档实现里只使用了 `http_api_reference.md` 明确写出的接口与字段。
- metadata 的写入方式遵循文档：通过 `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}` 的 `meta_fields` 直接写入 JSON。
- RAGFlow 官方“设置元数据”指南说明元数据是逐文档设置的，不支持原生批量设置；这里的批量更新能力是服务层循环调用单文档更新接口实现的。
- 单文档 metadata 更新默认按调用方传入的 `meta_fields` 整体提交，不做“先读后 merge”的隐式补齐，因为接口文档没有提供稳定的文档详情读取结构用于安全合并。

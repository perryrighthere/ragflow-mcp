# RAGFlow MCP 服务 API 文档

本文档描述当前仓库实际对外提供的 HTTP 服务接口，内容基于以下实现与测试整理：

- `main.py`
- `ragflow_service/http_server.py`
- `ragflow_service/qa_service.py`
- `ragflow_service/document_service.py`
- `ragflow_service/knowledge_portal_service.py`
- `ragflow_service/conversation_store.py`
- `tests/test_http_server_api.py`
- `tests/test_qa_service.py`
- `tests/test_document_service.py`

当前服务 OpenAPI 信息：

- 服务名：`RAGFlow Knowledge Base QA Service`
- 版本：`3.0.0`
- Swagger：`GET /docs`
- ReDoc：`GET /redoc`
- OpenAPI JSON：`GET /openapi.json`

## 1. 总览

### 1.1 Base URL

默认地址：

```text
http://127.0.0.1:8080
```

### 1.2 接口分组

| 分组 | 说明 |
| --- | --- |
| 页面与文档 | 提供前端控制台和 Swagger/OpenAPI 页面 |
| RAGFlow Raw APIs | 透传到上游 RAGFlow 的基础接口 |
| Knowledge Base QA | 组合 RAGFlow 检索与 LLM 生成的问答接口 |
| Knowledge Portal | 从赛力斯知识门户同步文档并导入 RAGFlow |

### 1.3 认证与依赖

- 该服务当前没有对入站 HTTP 请求做额外鉴权。
- `RAGFlow Raw APIs` 依赖服务端环境变量 `RAGFLOW_BASE_URL` 与 `RAGFLOW_API_KEY`。
- 问答接口依赖服务端环境变量 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。
- 知识门户接口不依赖服务端预配置门户账号，而是由调用方在请求体中传入 `base_url`、`community_id`、`username`、`password`。

### 1.4 通用响应约定

#### JSON 接口

- 透传型接口通常直接返回上游 RAGFlow 的 HTTP 状态码与响应体。
- 服务自有 JSON 接口通常返回：

```json
{
  "code": 0,
  "data": {}
}
```

#### 流式接口

- `POST /api/v1/qa/answer/stream`
- `POST /api/v1/qa/conversations/answer/stream`

均返回：

```text
Content-Type: application/x-ndjson
Cache-Control: no-cache
```

每一行是一条独立 JSON 事件，事件类型包括：

- `context`
- `answer_delta`
- `done`
- `error`

#### 常见错误响应

| HTTP 状态码 | 来源 | 结构 | 说明 |
| --- | --- | --- | --- |
| `400` | 服务端业务校验失败 | `{"detail":"..."}` | 例如缺少 `user_id`、`dataset_id`、`question` |
| `422` | FastAPI 请求体验证失败 | FastAPI 默认结构 | 例如类型不匹配、缺失必填字段、传了禁止的额外字段 |
| `502` | 上游连接失败或返回非法结构 | `{"detail":"..."}` 或 `{"detail":"...","payload":...}` | 常见于 RAGFlow 或知识门户不可达 |
| `503` | 服务配置缺失 | `{"detail":"..."}` | 例如未配置 RAGFlow 或 LLM |

### 1.5 路由清单

| 方法 | 路径 | 分组 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/` | 页面与文档 | 前端控制台首页 |
| `GET` | `/docs` | 页面与文档 | Swagger UI |
| `GET` | `/redoc` | 页面与文档 | ReDoc |
| `GET` | `/openapi.json` | 页面与文档 | OpenAPI JSON |
| `GET` | `/v1/system/healthz` | RAGFlow Raw APIs | RAGFlow 健康检查透传 |
| `POST` | `/api/v1/retrieval` | RAGFlow Raw APIs | RAGFlow 检索透传 |
| `GET` | `/api/v1/datasets/{dataset_id}/documents` | RAGFlow Raw APIs | 查询 RAGFlow 文档列表 |
| `POST` | `/api/v1/datasets/{dataset_id}/documents` | RAGFlow Raw APIs | 上传文档到 RAGFlow |
| `PUT` | `/api/v1/datasets/{dataset_id}/documents/{document_id}` | RAGFlow Raw APIs | 更新 RAGFlow 文档元数据 |
| `POST` | `/api/v1/datasets/{dataset_id}/chunks` | RAGFlow Raw APIs | 批量触发文档解析 |
| `GET` | `/api/v1/qa/prompt-templates` | Knowledge Base QA | 获取默认提示词模板 |
| `POST` | `/api/v1/qa/answer/stream` | Knowledge Base QA | 单轮问答流式接口 |
| `POST` | `/api/v1/qa/conversations/answer/stream` | Knowledge Base QA | 带会话历史的流式问答接口 |
| `GET` | `/api/v1/qa/conversations` | Knowledge Base QA | 按用户查询历史会话列表 |
| `DELETE` | `/api/v1/qa/conversations/{conversation_id}` | Knowledge Base QA | 删除用户历史会话 |
| `POST` | `/api/v1/knowledge-portal/documents/sync` | Knowledge Portal | 同步并下载知识门户文档 |
| `POST` | `/api/v1/knowledge-portal/documents/import` | Knowledge Portal | 同步知识门户文档并导入 RAGFlow |

## 2. 页面与文档接口

### 2.1 `GET /`

返回前端控制台页面；若前端入口文件不存在，则重定向到 `/docs`。

### 2.2 `GET /docs`

Swagger UI 页面。

### 2.3 `GET /redoc`

ReDoc 页面。

### 2.4 `GET /openapi.json`

OpenAPI 文档原始 JSON。

## 3. RAGFlow Raw APIs

这一组接口是服务对上游 RAGFlow 的代理封装，核心特点是：

- 请求由当前服务代发到已配置的 RAGFlow。
- 成功时通常直接返回上游的状态码与响应体。
- 失败时可能返回透传的上游错误，也可能由服务包装成 `detail/payload` 结构。

### 3.1 `GET /v1/system/healthz`

检查上游 RAGFlow 是否可用。

请求示例：

```bash
curl http://127.0.0.1:8080/v1/system/healthz
```

成功响应示例：

```json
{
  "code": 0,
  "data": {
    "status": "ok"
  }
}
```

### 3.2 `POST /api/v1/retrieval`

调用 RAGFlow 检索接口。

请求头：

```text
Content-Type: application/json
```

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `question` | `string` | 是 | 用户问题 |
| `dataset_ids` | `string[] \| null` | 否 | 检索的数据集 ID 列表 |
| `document_ids` | `string[] \| null` | 否 | 检索的文档 ID 列表 |
| `page` | `integer \| null` | 否 | 分页页码 |
| `page_size` | `integer \| null` | 否 | 每页数量 |
| `similarity_threshold` | `number \| null` | 否 | 相似度阈值 |
| `vector_similarity_weight` | `number \| null` | 否 | 向量相似度权重 |
| `top_k` | `integer \| null` | 否 | Top-K 检索数量 |
| `rerank_id` | `string \| integer \| null` | 否 | 重排配置 ID |
| `keyword` | `boolean \| null` | 否 | 是否启用关键词检索 |
| `highlight` | `boolean \| null` | 否 | 是否高亮结果 |
| `cross_languages` | `string[] \| null` | 否 | 跨语言检索参数 |
| `metadata_condition` | `object \| null` | 否 | 元数据过滤条件 |
| `use_kg` | `boolean \| null` | 否 | 是否启用知识图谱能力 |
| 其他字段 | 任意 | 否 | 服务接受额外字段，但不会主动补充默认值 |

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/retrieval \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "五看六定是什么？",
    "dataset_ids": ["kb_123"],
    "highlight": true
  }'
```

说明：

- 该接口不会像问答接口那样自动补 `page_size=6`。
- 透传响应内容由上游 RAGFlow 决定。

### 3.3 `GET /api/v1/datasets/{dataset_id}/documents`

查询指定数据集下的文档列表。

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `dataset_id` | `string` | 是 | 数据集 ID |

Query 参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `page` | `integer` | 否 | 页码 |
| `page_size` | `integer` | 否 | 每页数量 |
| `keywords` | `string` | 否 | 关键词 |
| `name` | `string` | 否 | 文档名称 |
| `run` | `string[]` | 否 | 运行状态，可重复传参 |
| `suffix` | `string[]` | 否 | 后缀过滤，可重复传参 |
| `orderby` | `string` | 否 | 排序字段 |
| `desc` | `boolean` | 否 | 是否倒序 |
| `id` | `string` | 否 | 文档 ID |

请求示例：

```bash
curl --get \
  --url http://127.0.0.1:8080/api/v1/datasets/kb_123/documents \
  --data-urlencode page=1 \
  --data-urlencode page_size=20 \
  --data-urlencode run=DONE \
  --data-urlencode run=UNSTART
```

### 3.4 `POST /api/v1/datasets/{dataset_id}/documents`

上传文件到指定数据集。

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `dataset_id` | `string` | 是 | 数据集 ID |

请求类型：

```text
multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `files` | file[] | 是 | 要上传的文件，可重复传多个 |

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/datasets/kb_123/documents \
  --form 'files=@/path/to/a.txt' \
  --form 'files=@/path/to/b.pdf'
```

说明：

- 入站接口使用的表单字段名是 `files`。
- 服务转发到上游 RAGFlow 时会重新封装 multipart 请求。

### 3.5 `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`

更新指定文档的元数据或解析配置。

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `dataset_id` | `string` | 是 | 数据集 ID |
| `document_id` | `string` | 是 | 文档 ID |

请求体：

- JSON 对象
- 服务允许任意字段透传给上游 RAGFlow

请求示例：

```bash
curl --request PUT \
  --url http://127.0.0.1:8080/api/v1/datasets/kb_123/documents/doc_001 \
  --header 'Content-Type: application/json' \
  --data '{
    "enabled": 1,
    "name": "流程说明书",
    "meta_fields": {
      "source": "knowledge_portal"
    }
  }'
```

### 3.6 `POST /api/v1/datasets/{dataset_id}/chunks`

批量触发文档解析。

路径参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `dataset_id` | `string` | 是 | 数据集 ID |

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `document_ids` | `string[]` | 是 | 待解析文档 ID 列表 |
| 其他字段 | 任意 | 否 | 会原样透传给上游 RAGFlow |

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/datasets/kb_123/chunks \
  --header 'Content-Type: application/json' \
  --data '{
    "document_ids": ["doc_001", "doc_002"]
  }'
```

## 4. Knowledge Base QA

### 4.1 `GET /api/v1/qa/prompt-templates`

获取默认提示词模板元数据。

请求示例：

```bash
curl http://127.0.0.1:8080/api/v1/qa/prompt-templates
```

响应示例：

```json
{
  "code": 0,
  "data": {
    "system_prompt": "你是一个知识库问答助手。",
    "user_prompt_template": "Question:\n{{question}}\n\nKnowledge snippets:\n{{knowledge_snippets}}",
    "direct_answer_defaults": {
      "system_prompt": "你是一个问答助手。",
      "user_prompt_template": "{{question}}"
    },
    "supported_variables": {
      "{{question}}": "The original user question.",
      "{{knowledge_snippets}}": "The merged retrieval snippets built from document names and content only."
    }
  }
}
```

说明：

- `system_prompt` 与 `user_prompt_template` 对应检索增强模式的默认值。
- `direct_answer_defaults` 对应不走检索、直接调用 LLM 时的默认值。

### 4.2 `POST /api/v1/qa/answer/stream`

单轮问答流式接口。

#### 行为说明

- 当请求体中包含且不为 `null` 的 `dataset_ids` 字段时，服务会先调用 `/api/v1/retrieval` 检索。
- 当请求体未传 `dataset_ids` 时，服务直接调用 LLM，不依赖 RAGFlow。
- 检索模式下如果既没有显式传 `page_size`，也没有传 `top_k`，服务会自动补 `page_size=6`。
- 检索无结果时不会调用 LLM，而是直接返回固定答案：

```text
知识库中没有检索到可用于回答当前问题的内容，请尝试补充关键词或缩小范围。
```

#### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `question` | `string` | 是 | 用户问题 |
| `dataset_ids` | `string[] \| null` | 否 | 传入后进入检索增强模式 |
| `document_ids` | `string[] \| null` | 否 | 检索过滤的文档 ID |
| `page` | `integer \| null` | 否 | 检索分页参数 |
| `page_size` | `integer \| null` | 否 | 检索分页大小 |
| `similarity_threshold` | `number \| null` | 否 | 检索阈值 |
| `vector_similarity_weight` | `number \| null` | 否 | 检索权重 |
| `top_k` | `integer \| null` | 否 | 检索数量 |
| `rerank_id` | `string \| integer \| null` | 否 | 重排配置 |
| `keyword` | `boolean \| null` | 否 | 是否启用关键词检索 |
| `highlight` | `boolean \| null` | 否 | 是否高亮 |
| `cross_languages` | `string[] \| null` | 否 | 跨语言配置 |
| `metadata_condition` | `object \| null` | 否 | 元数据过滤 |
| `use_kg` | `boolean \| null` | 否 | 是否启用知识图谱能力 |
| `temperature` | `number \| null` | 否 | LLM 温度参数 |
| `max_tokens` | `integer \| null` | 否 | LLM 最大输出 token |
| `system_prompt` | `string \| null` | 否 | 覆盖默认系统提示词 |
| `user_prompt_template` | `string \| null` | 否 | 覆盖默认用户提示词模板 |
| 其他字段 | 任意 | 否 | 服务允许额外字段，但不会参与核心逻辑 |

#### 请求示例

检索增强模式：

```bash
curl -N --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/answer/stream \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "五看是什么？",
    "dataset_ids": ["kb_123"],
    "page_size": 3
  }'
```

直连 LLM：

```bash
curl -N --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/answer/stream \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "五看是什么？",
    "temperature": 0.3
  }'
```

#### NDJSON 事件格式

`context` 事件：

```json
{
  "type": "context",
  "data": {
    "question": "五看是什么？",
    "sources": [
      {
        "reference_index": 1,
        "document_keyword": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
        "content": "五看包括看行业、看市场、看用户、看竞争、看自己。"
      }
    ],
    "referenced_documents": [
      {
        "index": 1,
        "document_name": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
        "dataset_id": "kb_123",
        "document_id": "doc_001"
      }
    ],
    "source_count": 1,
    "retrieval_total": 1,
    "llm_messages": [
      {
        "role": "system",
        "content": "..."
      },
      {
        "role": "user",
        "content": "..."
      }
    ],
    "prompt_templates": {
      "system_prompt": "...",
      "user_prompt_template": "..."
    },
    "model": "test-qa-model"
  }
}
```

`answer_delta` 事件：

```json
{
  "type": "answer_delta",
  "delta": "五看包括看行业、看市场、"
}
```

`done` 事件：

```json
{
  "type": "done",
  "data": {
    "question": "五看是什么？",
    "answer": "五看包括看行业、看市场、看用户、看竞争、看自己。",
    "sources": [
      {
        "reference_index": 1,
        "document_keyword": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
        "content": "五看包括看行业、看市场、看用户、看竞争、看自己。"
      }
    ],
    "referenced_documents": [
      {
        "index": 1,
        "document_name": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
        "dataset_id": "kb_123",
        "document_id": "doc_001"
      }
    ],
    "source_count": 1,
    "retrieval_total": 1,
    "llm_messages": [
      {
        "role": "system",
        "content": "..."
      },
      {
        "role": "user",
        "content": "..."
      }
    ],
    "prompt_templates": {
      "system_prompt": "...",
      "user_prompt_template": "..."
    },
    "model": "test-qa-model",
    "usage": {
      "total_tokens": 123
    }
  }
}
```

`error` 事件：

```json
{
  "type": "error",
  "message": "Unexpected streaming error."
}
```

说明：

- `sources` 是给前端或调用方展示的检索片段列表。
- `referenced_documents` 是引用文档去重后的映射表，`index` 与答案中的 `[1]`、`[2]` 编号保持一致。
- `llm_messages` 返回的是服务实际发给 LLM 的消息数组，便于排查提示词拼接结果。
- 检索模式下，传给 LLM 的内容只包含文档名与正文，不包含相似度等附加字段。
- 路径 `/api/v1/qa/answer` 并不存在，仅提供 `/api/v1/qa/answer/stream`。

### 4.3 `POST /api/v1/qa/conversations/answer/stream`

带 SQLite 历史会话能力的流式问答接口。

#### 行为说明

- `user_id` 必填，用于隔离不同用户的会话。
- `conversation_id` 选填；不传时自动创建新会话。
- 若传入的 `conversation_id` 已归属于其他 `user_id`，接口返回 `400`。
- 历史消息存储在 SQLite 中，默认路径为 `output/conversations.sqlite3`。
- 服务保留最近若干轮原始消息，并把更早历史压缩为文本摘要。
- 首轮回答结束后，如果会话尚无标题，服务会额外调用一次 LLM 生成 `conversation_title`。

相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CONVERSATION_DB_PATH` | `output/conversations.sqlite3` | SQLite 文件路径 |
| `CONVERSATION_RECENT_TURNS` | `6` | 保留的最近原始轮数 |
| `CONVERSATION_SUMMARY_MAX_CHARS` | `4000` | 历史摘要最大字符数 |

#### 请求体字段

除继承单轮问答接口全部字段外，还增加：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | `string` | 是 | 逻辑用户 ID |
| `conversation_id` | `string \| null` | 否 | 会话 ID；不传则自动创建 |

请求示例：

```bash
curl -N --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/conversations/answer/stream \
  --header 'Content-Type: application/json' \
  --data '{
    "user_id": "user_001",
    "question": "五看是什么？",
    "dataset_ids": ["kb_123"]
  }'
```

续聊示例：

```bash
curl -N --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/conversations/answer/stream \
  --header 'Content-Type: application/json' \
  --data '{
    "user_id": "user_001",
    "conversation_id": "b01eed84b85611efa0e90242ac120005",
    "question": "那六定呢？",
    "dataset_ids": ["kb_123"]
  }'
```

#### 与单轮问答相比新增的事件字段

`context.data` 额外包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | `string` | 当前用户 ID |
| `conversation_id` | `string` | 当前会话 ID |
| `conversation_title` | `string` | 已存在标题；首次请求时通常为空 |
| `conversation_created` | `boolean` | 当前请求是否新建了会话 |
| `history_summary` | `string` | 被压缩的较早历史摘要 |
| `history_messages` | `object[]` | 当前窗口内保留的最近原始消息 |

`done.data` 额外包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | `string` | 当前用户 ID |
| `conversation_id` | `string` | 当前会话 ID |
| `conversation_title` | `string` | 当前会话标题 |

错误示例：

```json
{
  "detail": "conversation_id does not belong to the provided user_id"
}
```

### 4.4 `GET /api/v1/qa/conversations`

按用户查询本地 SQLite 中保存的历史会话列表。

#### 行为说明

- `user_id` 必填，用于查询该用户自己的会话。
- 返回结果按 `updated_at` 倒序排列。
- `page` 和 `page_size` 支持分页；`page_size` 最大为 `100`。
- 每条会话包含标题、压缩后的较早历史摘要、当前保留窗口内的原始消息。
- 该接口只读取本地会话库，不会调用 RAGFlow 或 LLM。

#### 查询参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `user_id` | `string` | 是 | 无 | 逻辑用户 ID |
| `page` | `integer` | 否 | `1` | 页码，从 1 开始 |
| `page_size` | `integer` | 否 | `20` | 每页数量，范围 `1` 到 `100` |

请求示例：

```bash
curl --request GET \
  --url 'http://127.0.0.1:8080/api/v1/qa/conversations?user_id=user_001&page=1&page_size=20'
```

响应结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `integer` | 固定为 `0` |
| `data.user_id` | `string` | 当前用户 ID |
| `data.total` | `integer` | 当前用户的会话总数 |
| `data.page` | `integer` | 当前页码 |
| `data.page_size` | `integer` | 每页数量 |
| `data.conversations` | `object[]` | 会话列表 |

`data.conversations[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `conversation_id` | `string` | 会话 ID |
| `conversation_title` | `string` | 会话标题 |
| `history_summary` | `string` | 被压缩的较早历史摘要 |
| `history_messages` | `object[]` | 当前窗口内保留的最近原始消息 |
| `created_at` | `string` | 会话创建时间，UTC ISO 8601 |
| `updated_at` | `string` | 会话最近更新时间，UTC ISO 8601 |

`history_messages[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `role` | `string` | `user` 或 `assistant` |
| `content` | `string` | 消息内容 |

响应示例：

```json
{
  "code": 0,
  "data": {
    "user_id": "user_001",
    "total": 1,
    "page": 1,
    "page_size": 20,
    "conversations": [
      {
        "conversation_id": "b01eed84b85611efa0e90242ac120005",
        "conversation_title": "五看首轮问答",
        "history_summary": "",
        "history_messages": [
          {"role": "user", "content": "五看是什么？"},
          {"role": "assistant", "content": "五看包括看行业、看市场、看用户、看竞争、看自己。"}
        ],
        "created_at": "2026-04-23T08:00:00+00:00",
        "updated_at": "2026-04-23T08:00:10+00:00"
      }
    ]
  }
}
```

### 4.5 `DELETE /api/v1/qa/conversations/{conversation_id}`

按用户删除本地 SQLite 中保存的一条历史会话。

#### 行为说明

- `user_id` 必填，用于确认只能删除该用户自己的会话。
- 删除会话时会同步删除该会话的原始消息；历史摘要保存在会话记录中，也会一并删除。
- 如果 `conversation_id` 不存在，接口返回 `400`。
- 如果 `conversation_id` 属于其他用户，接口返回 `400`。
- 该接口只写入本地会话库，不会调用 RAGFlow 或 LLM。

#### 路径参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `conversation_id` | `string` | 是 | 要删除的会话 ID |

#### 查询参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `user_id` | `string` | 是 | 无 | 逻辑用户 ID |

请求示例：

```bash
curl --request DELETE \
  --url 'http://127.0.0.1:8080/api/v1/qa/conversations/b01eed84b85611efa0e90242ac120005?user_id=user_001'
```

响应结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `integer` | 固定为 `0` |
| `data.user_id` | `string` | 当前用户 ID |
| `data.conversation_id` | `string` | 已删除的会话 ID |
| `data.deleted` | `boolean` | 固定为 `true` |

响应示例：

```json
{
  "code": 0,
  "data": {
    "user_id": "user_001",
    "conversation_id": "b01eed84b85611efa0e90242ac120005",
    "deleted": true
  }
}
```

错误示例：

```json
{
  "detail": "conversation_id does not belong to the provided user_id"
}
```

## 5. Knowledge Portal

### 5.1 `POST /api/v1/knowledge-portal/documents/sync`

从赛力斯知识门户拉取文档列表、详情与附件，并把内容缓存到本地输出目录。

#### 请求体字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `base_url` | `string` | 是 | 无 | 门户地址，例如 `https://km.seres.cn` |
| `community_id` | `string` | 是 | 无 | 门户 access key |
| `username` | `string` | 是 | 无 | Basic Auth 用户名 |
| `password` | `string` | 是 | 无 | Basic Auth 密码 |
| `type` | `string` | 否 | `mutildoc` | 门户文档类型 |
| `page_size` | `integer` | 否 | `100` | 分页抓取大小 |
| `max_download_files` | `integer \| null` | 否 | `null` | 最多下载的二进制文件数，按全局累计 |
| `begin_time` | `string \| null` | 否 | `null` | 仅抓取此时间之后更新的文档 |
| `fd_cate_id` | `string \| null` | 否 | `null` | 门户分类 ID 过滤 |
| `timeout` | `number \| null` | 否 | 服务默认值 | 请求超时秒数 |

说明：

- 如果 `base_url` 不包含协议头，服务会自动补成 `https://`。
- HTTP 接口层没有暴露 `include_attachments` 和 `include_cover_image` 参数，因此同步接口默认会尝试下载附件和封面图。
- `max_download_files` 限制的是附件/封面等二进制文件总数，不是文档条数。

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/knowledge-portal/documents/sync \
  --header 'Content-Type: application/json' \
  --data '{
    "base_url": "https://km.seres.cn",
    "community_id": "community",
    "username": "user",
    "password": "pass",
    "page_size": 50,
    "max_download_files": 2
  }'
```

响应结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `integer` | 固定为 `0` |
| `data.base_url` | `string` | 门户根地址 |
| `data.output_dir` | `string` | 本地输出目录 |
| `data.total_documents` | `integer` | 列表接口发现的文档总数 |
| `data.downloaded_document_count` | `integer` | 实际成功拉取详情的文档数 |
| `data.downloaded_file_count` | `integer` | 实际下载的二进制文件总数 |
| `data.max_download_files` | `integer \| null` | 下载上限 |
| `data.download_limit_reached` | `boolean` | 是否触达下载上限 |
| `data.documents` | `object[]` | 每篇文档的本地缓存信息 |
| `data.errors` | `object[]` | 抓取过程中的错误列表 |

`data.documents[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `fdId` | `string` | 门户文档 ID |
| `fdName` | `string` | 文档标题 |
| `saved_dir` | `string` | 本地缓存目录 |
| `detail_json_path` | `string` | 详情 JSON 文件路径 |
| `content_path` | `string \| null` | 渲染出的 `content.md` 路径 |
| `downloaded_files` | `object[]` | 已下载的附件/封面 |

`data.documents[].downloaded_files[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kind` | `string` | `attachment` 或 `cover` |
| `file_id` | `string` | 门户文件 ID |
| `file_name` | `string` | 保存后的文件名 |
| `path` | `string` | 本地文件路径 |
| `size_bytes` | `integer` | 文件大小 |

### 5.2 `POST /api/v1/knowledge-portal/documents/import`

先同步知识门户文档，再上传到 RAGFlow、更新元数据，并可选触发解析。

#### 请求体字段

该接口继承同步接口字段，并增加：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `dataset_id` | `string` | 是 | 无 | RAGFlow 数据集 ID |
| `document_update` | `object \| null` | 否 | `null` | 上传后统一调用文档更新接口的请求体 |
| `parse_after_upload` | `boolean` | 否 | `false` | 是否在全部更新完成后调用批量解析 |
| `include_attachments` | `boolean` | 否 | `true` | 是否上传门户附件 |
| `include_cover_image` | `boolean` | 否 | `false` | 是否上传封面图 |
| `fallback_to_content_markdown` | `boolean` | 否 | `true` | 无可上传二进制文件时，是否回退上传 `content.md` |

额外约束：

- `document_update` 若存在，必须是对象。
- `document_update.meta_fields` 若存在，必须是对象。
- 上传文件扩展名为 `.pptx` 时，服务会在文档更新阶段自动将该文件对应文档的 `chunk_method` 设置为 `presentation`，覆盖共享的 `document_update.chunk_method`。
- `include_attachments=false`、`include_cover_image=false` 且 `fallback_to_content_markdown=false` 时，接口返回 `400`。
- 当 `fallback_to_content_markdown=false` 且触达 `max_download_files` 上限后，服务会停止继续请求后续门户文档，因为后续文档已不可能再产出可上传文件。

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/knowledge-portal/documents/import \
  --header 'Content-Type: application/json' \
  --data '{
    "base_url": "https://km.seres.cn",
    "community_id": "community",
    "username": "user",
    "password": "pass",
    "dataset_id": "kb_123",
    "parse_after_upload": true,
    "document_update": {
      "enabled": 1,
      "meta_fields": {
        "source": "knowledge_portal"
      }
    },
    "include_cover_image": false
  }'
```

响应结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `integer` | 固定为 `0` |
| `data.dataset_id` | `string` | 目标数据集 ID |
| `data.base_url` | `string` | 门户根地址 |
| `data.output_dir` | `string` | 本地缓存目录 |
| `data.total_documents` | `integer` | 门户列表中的文档总数 |
| `data.downloaded_document_count` | `integer` | 成功抓取的文档数 |
| `data.downloaded_file_count` | `integer` | 下载的二进制文件数 |
| `data.max_download_files` | `integer \| null` | 下载上限 |
| `data.download_limit_reached` | `boolean` | 是否触达下载上限 |
| `data.imported_document_count` | `integer` | 至少成功上传 1 个文件的门户文档数 |
| `data.uploaded_file_count` | `integer` | 成功上传到 RAGFlow 的文件总数 |
| `data.updated_document_count` | `integer` | 成功调用文档更新接口的数量 |
| `data.parse_requested` | `boolean` | 是否请求了解析 |
| `data.parsed_document_count` | `integer` | 进入解析列表的文档数量 |
| `data.parse_result` | `object \| null` | 批量解析接口返回体 |
| `data.documents` | `object[]` | 导入成功文档详情 |
| `data.skipped_documents` | `object[]` | 因无可上传文件而跳过的文档 |
| `data.errors` | `object[]` | 下载、上传、更新、解析、清理过程中的错误 |

`data.documents[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `fdId` | `string` | 门户文档 ID |
| `fdName` | `string` | 文档标题 |
| `saved_dir` | `string` | 本地缓存目录 |
| `upload_sources` | `object[]` | 本次上传所使用的文件来源 |
| `ragflow_documents` | `object[]` | 对应生成的 RAGFlow 文档 |

`data.documents[].upload_sources[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kind` | `string` | `attachment`、`cover` 或 `content_markdown` |
| `path` | `string` | 本地文件路径 |
| `file_id` | `string \| null` | 门户文件 ID |
| `file_name` | `string` | 门户文件名 |

`data.documents[].ragflow_documents[]` 结构：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `document_id` | `string` | RAGFlow 文档 ID |
| `name` | `string` | RAGFlow 返回的文档名 |
| `status` | `string` | `updated` 或 `uploaded` |
| `upload_source` | `object` | 与该 RAGFlow 文档对应的上传源 |
| `meta_fields` | `object` | 仅在更新成功时出现 |

自动注入的 `meta_fields`：

- `knowledgeDatabaseId`：RAGFlow dataset id
- `ragFileId`：当前文档在 RAGFlow 中的 document id
- `originFileId`：文档在知识门户中的 `fdId`
- `tenantId`：当前 RAGFlow API key
- `knowledge_portal_fd_id`
- `knowledge_portal_fd_no`
- `knowledge_portal_fd_name`
- `knowledge_portal_fd_cate_id`
- `knowledge_portal_fd_publish_time`
- `knowledge_portal_fd_creator_no`
- `knowledge_portal_fd_creator_name`
- `knowledge_portal_fd_link`
- `knowledge_portal_file_kind`
- `knowledge_portal_file_id`
- `knowledge_portal_file_name`

说明：

- 自动注入字段会与调用方传入的 `document_update.meta_fields` 合并。
- 每个 RAGFlow 文档更新成功后，服务会将 `knowledgeDatabaseId`、`ragFileId`、`originFileId`、`tenantId` 同步到 `RAG_INFO_SYNC_URL` 指向的数据表接口；同步失败会记录到 `data.errors[]`，不阻断其他文档继续导入。
- 若门户文档没有附件或封面，但存在正文内容，且 `fallback_to_content_markdown=true`，服务会上传自动生成的 `content.md`。
- 每处理完一篇门户文档，服务都会尝试清理本地缓存目录；清理失败会记入 `errors`。

## 6. 补充说明

### 6.1 服务端环境变量

| 变量 | 必填 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `RAGFLOW_BASE_URL` | 否 | 空 | Raw API 与检索增强问答访问上游 RAGFlow |
| `RAGFLOW_API_KEY` | 否 | 空 | 上游 RAGFlow API Key |
| `RAG_INFO_SYNC_URL` | 否 | `http://paas.dev.seres.cn/kwb-oa/v1/kwRagFileInfo/syncRagInfo` | 导入 RAGFlow 后同步文档身份字段的数据表接口 |
| `LLM_BASE_URL` | 否 | 空 | 问答接口使用的 OpenAI 兼容 LLM 根地址 |
| `LLM_API_KEY` | 否 | 空 | LLM API Key |
| `LLM_MODEL` | 否 | 空 | LLM 模型名 |
| `RAGFLOW_TIMEOUT` | 否 | `60` | 上游 RAGFlow 超时 |
| `LLM_TIMEOUT` | 否 | `60` | LLM 超时 |
| `SERVICE_HOST` | 否 | `0.0.0.0` | 服务监听地址 |
| `SERVICE_PORT` | 否 | `8080` | 服务监听端口 |

### 6.2 重要行为差异

- `POST /api/v1/retrieval` 不自动补默认分页参数。
- `POST /api/v1/qa/answer/stream` 在检索增强模式下会自动补 `page_size=6`，除非显式传入 `page_size` 或 `top_k`。
- `POST /api/v1/qa/answer/stream` 与 `POST /api/v1/qa/conversations/answer/stream` 只提供流式版本，不提供非流式 `/answer` 路由。
- 同步接口 `POST /api/v1/knowledge-portal/documents/sync` 当前不支持通过 HTTP 参数关闭附件或封面下载；导入接口支持细粒度控制上传来源。

### 6.3 推荐排查顺序

1. 先访问 `/docs` 或 `/openapi.json` 确认服务已启动。
2. 调 `/v1/system/healthz` 验证 RAGFlow 配置。
3. 调 `/api/v1/qa/prompt-templates` 验证问答服务基础路由可用。
4. 使用不带 `dataset_ids` 的 `/api/v1/qa/answer/stream` 验证 LLM 直连。
5. 最后再验证检索增强问答和知识门户同步/导入。

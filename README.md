# RAGFlow Knowledge Base QA Service

一个轻量后端，提供：

- RAGFlow 原始代理接口
- 知识库问答接口 `POST /api/v1/qa/answer/stream`
- 知识门户文档同步下载接口 `POST /api/v1/knowledge-portal/documents/sync`
- 知识门户文档导入 RAGFlow 接口 `POST /api/v1/knowledge-portal/documents/import`
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
- 完整接口文档：[`docs/service-api.md`](docs/service-api.md)

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
python -m unittest tests.test_config tests.test_ragflow_client tests.test_http_server_api tests.test_knowledge_portal_service tests.test_qa_service tests.test_main tests.test_document_service
```

## Docker

构建镜像：

```bash
docker build -t ragflow-mcp:latest .
```

准备容器环境变量：

```bash
cp .env.docker.example .env.docker
```

然后按你的实际环境修改 `.env.docker` 中的配置。

说明：

- 如果 RAGFlow 或 LLM 服务运行在宿主机上，Docker 内不能使用 `127.0.0.1` 指向宿主机
- 可以将 `RAGFLOW_BASE_URL`、`LLM_BASE_URL` 写成 `http://host.docker.internal:<port>`
- Linux 上运行 `docker run` 时，建议同时加上 `--add-host=host.docker.internal:host-gateway`

启动容器：

```bash
docker run --rm \
  --name ragflow-mcp \
  --publish 8080:8080 \
  --env-file .env.docker \
  --add-host=host.docker.internal:host-gateway \
  ragflow-mcp:latest
```

启动后访问：

- `http://127.0.0.1:8080/`
- `http://127.0.0.1:8080/docs`

补充说明：

- 镜像默认启动命令为 `python main.py serve`
- 即使暂时未配置 RAGFlow 或 LLM，容器也可以启动，前端和文档页仍可访问；对应接口会返回 `503`
- 如果你希望改宿主机端口，可调整 `--publish 18080:8080`

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
- `RAG_INFO_SYNC_URL`
- `LLM_TIMEOUT`
- `SERVICE_HOST`
- `SERVICE_PORT`

说明：

- `LLM_BASE_URL` 需要指向 OpenAI 兼容接口根路径，例如 `https://api.openai.com/v1`
- 如果没配置 RAGFlow / LLM，页面仍能打开，但对应接口会返回 `503`

## 主要接口

- `GET /v1/system/healthz`
- `POST /api/v1/retrieval`
- `POST /api/v1/qa/answer/stream`
- `POST /api/v1/qa/conversations/answer/stream`
- `GET /api/v1/qa/conversations`
- `DELETE /api/v1/qa/conversations/{conversation_id}`
- `POST /api/v1/knowledge-portal/documents/sync`
- `POST /api/v1/knowledge-portal/documents/import`
- `GET /api/v1/datasets/{dataset_id}/documents`
- `POST /api/v1/datasets/{dataset_id}/documents`
- `PUT /api/v1/datasets/{dataset_id}/documents/{document_id}`
- `POST /api/v1/datasets/{dataset_id}/chunks`

## 知识库问答接口

接口：`POST /api/v1/qa/answer/stream`

行为：

- 如果请求中传入 `dataset_ids` 字段，服务端会先调用 RAGFlow `POST /api/v1/retrieval` 检索 chunks，
  再将检索出的文档名和正文内容组装为提示词，调用配置好的 OpenAI 兼容 LLM 回答问题
- 如果未传 `dataset_ids` 字段，则跳过知识库检索，直接调用配置好的 OpenAI 兼容 LLM 回答问题
- 接口始终按 `NDJSON` 方式流式返回

请求示例：

```bash
curl -N --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/answer/stream \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "五看六定是什么？",
    "dataset_ids": ["kb_123"],
    "page_size": 6
  }'
```

说明：

- `dataset_ids` 是可选字段；传入时走知识库检索，不传时走直连 LLM
- 返回 `application/x-ndjson`，事件类型包括 `context`、`answer_delta`、`done`、`error`
- 检索模式下，最终 JSON 会额外返回 `referenced_documents` 字段，按编号列出引用文档的 `document_name`、`dataset_id`、`document_id`
- 检索模式下，LLM 回答中的 `[1]`、`[2]` 等引用编号会与 `referenced_documents` 中的 `index` 保持一致
- 常用可选参数还包括 `document_ids`、`similarity_threshold`、`vector_similarity_weight`、`top_k`、`metadata_condition`、`temperature`、`max_tokens`

直连 LLM 示例：

```bash
curl -N --request POST \
  --url http://127.0.0.1:8080/api/v1/qa/answer/stream \
  --header 'Content-Type: application/json' \
  --data '{
    "question": "五看六定是什么？",
    "temperature": 0.2
  }'
```

## 带历史会话的问答接口

接口：`POST /api/v1/qa/conversations/answer/stream`

行为：

- 基于 SQLite 持久化 `user_id + conversation_id` 的会话历史
- `user_id` 必填，用于隔离不同用户的历史消息
- `conversation_id` 可选；不传时自动创建新会话，传入时会继续该会话
- 服务端会保留最近 `N` 轮原始对话，并把更早的历史压缩进摘要，再与当前问题一起发给 LLM
- 如果某个 `conversation_id` 已经属于其他 `user_id`，接口会返回 `400`

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

说明：

- 返回流同样是 `context`、`answer_delta`、`done`、`error`
- `context` 和最终 `done` 数据中会包含 `user_id`、`conversation_id`
- 新建会话的首轮回答完成后，服务端会额外调用一次 LLM 生成 `conversation_title`
- `context.data.history_summary` 展示被压缩后的较早历史摘要
- `context.data.history_messages` 展示当前保留在窗口中的最近几轮原始消息
- `history_messages` 中每条消息会包含 `referenced_documents`；无引用时为空数组
- `done.data.conversation_title` 为当前会话标题；后续续聊时会在 `context` 中直接返回已保存标题

相关环境变量：

- `CONVERSATION_DB_PATH`：SQLite 文件路径，默认 `output/conversations.sqlite3`
- `CONVERSATION_RECENT_TURNS`：保留的最近原始轮数，默认 `6`
- `CONVERSATION_SUMMARY_MAX_CHARS`：历史摘要最大字符数，默认 `4000`

## 历史会话查询接口

接口：`GET /api/v1/qa/conversations`

行为：

- 按 `user_id` 查询该用户的历史会话列表
- 返回会话标题、压缩后的较早历史摘要、当前保留窗口内的原始消息
- 原始消息会带出保存的 `referenced_documents` 引用来源列表
- 默认按 `updated_at` 倒序返回，支持分页
- 该接口只读取本地 SQLite 会话库，不会调用 RAGFlow 或 LLM

请求示例：

```bash
curl --request GET \
  --url 'http://127.0.0.1:8080/api/v1/qa/conversations?user_id=user_001&page=1&page_size=20'
```

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
          {"role": "user", "content": "五看是什么？", "referenced_documents": []},
          {
            "role": "assistant",
            "content": "五看包括看行业、看市场、看用户、看竞争、看自己。",
            "referenced_documents": [
              {
                "index": 1,
                "document_name": "IPD-2.2.3.1-002 整车产品项目任务书开发流程说明书.docx",
                "dataset_id": "kb_123",
                "document_id": "doc_001"
              }
            ]
          }
        ],
        "created_at": "2026-04-23T08:00:00+00:00",
        "updated_at": "2026-04-23T08:00:10+00:00"
      }
    ]
  }
}
```

## 历史会话删除接口

接口：`DELETE /api/v1/qa/conversations/{conversation_id}`

行为：

- 按 `user_id + conversation_id` 删除该用户自己的历史会话
- 会一并删除该会话的原始消息和历史摘要
- 如果 `conversation_id` 不存在，或不属于当前 `user_id`，接口返回 `400`
- 该接口只写入本地 SQLite 会话库，不会调用 RAGFlow 或 LLM

请求示例：

```bash
curl --request DELETE \
  --url 'http://127.0.0.1:8080/api/v1/qa/conversations/b01eed84b85611efa0e90242ac120005?user_id=user_001'
```

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
- `max_download_files` 用来限制二进制附件下载总量；达到上限后，后续文档仍会继续保存 `detail.json` 和 `content.md`

## 知识门户文档导入到 RAGFlow

请求示例：

```bash
curl --request POST \
  --url http://127.0.0.1:8080/api/v1/knowledge-portal/documents/import \
  --header 'Content-Type: application/json' \
  --data '{
    "base_url": "https://km.seres.cn",
    "community_id": "your-community-id",
    "username": "your-username",
    "password": "your-password",
    "dataset_id": "kb_123",
    "page_size": 100,
    "max_download_files": 50,
    "include_attachments": true,
    "include_cover_image": false,
    "fallback_to_content_markdown": true,
    "parse_after_upload": true,
    "document_update": {
      "enabled": 1,
      "chunk_method": "naive",
      "parser_config": {
        "chunk_token_num": 256
      },
      "meta_fields": {
        "source": "knowledge_portal",
        "business_line": "ipd"
      }
    }
  }'
```

行为：

- 先复用知识门户同步流程，拉取文档详情、生成 `content.md`，并按需下载附件
- 导入按文档流式执行：单个文档准备完成后会立即上传到 RAGFlow，而不是等待全部文档下载完再统一上传
- 默认上传 `fdFile` 中的原始附件，不上传封面图；若当前文档没有可上传的二进制文件，则回退上传 `content.md`
- 当 `fallback_to_content_markdown=false` 且 `max_download_files` 已耗尽时，导入流程会停止继续请求后续门户文档，因为剩余文档已不可能再生成可上传文件
- 每个上传到 RAGFlow 的文件都会再调用一次文档更新接口，批量写入 `document_update`
- 文档更新时会自动向 `document_update.meta_fields` 注入 `knowledgeDatabaseId`、`ragFileId`、`originFileId`、`tenantId`，分别对应 RAGFlow dataset id、RAGFlow document id、知识门户 `fdId`、当前 RAGFlow API key
- 每个 RAGFlow 文档更新成功后，会调用 `RAG_INFO_SYNC_URL` 指向的接口同步上述四个字段到业务数据表；默认地址为 `http://paas.dev.seres.cn/kwb-oa/v1/kwRagFileInfo/syncRagInfo`
- 上传文件扩展名为 `.pptx` 时，文档更新接口会自动将 `chunk_method` 设置为 `presentation`，以便后续解析使用演示文稿解析方式
- `document_update.meta_fields` 会自动合并一组知识门户来源标签，例如 `knowledge_portal_fd_id`、`knowledge_portal_fd_name`、`knowledge_portal_fd_no`、`knowledge_portal_file_kind`、`knowledge_portal_file_id`、`knowledge_portal_file_name`
- 当 `parse_after_upload=true` 时，所有更新成功的 RAGFlow 文档会在最后统一触发一次批量解析
- 单个文档处理完成后，会删除该文档在本地的暂存目录和附件缓存
- 返回值同时包含知识门户下载摘要、RAGFlow 导入摘要、逐文档上传结果和错误列表，便于排查部分成功/部分失败的场景

## 代码结构

- `main.py`：CLI 入口
- `ragflow_service/http_server.py`：FastAPI 应用和路由
- `ragflow_service/document_service.py`：知识门户到 RAGFlow 的文档导入编排
- `ragflow_service/ragflow_client.py`：RAGFlow 客户端
- `ragflow_service/knowledge_portal_client.py`：知识门户客户端
- `ragflow_service/knowledge_portal_service.py`：知识门户下载编排
- `ragflow_service/qa_service.py`：知识库问答编排
- `frontend/`：无构建的前端页面
- `tests/`：单元测试
- `tests/test_config.py`：配置测试
- `tests/test_document_service.py`：知识门户导入到 RAGFlow 的编排测试
- `tests/test_ragflow_client.py`：上游调用测试
- `tests/test_http_server_api.py`：FastAPI 路由测试
- `tests/test_knowledge_portal_service.py`：知识门户文档下载编排测试
- `tests/test_qa_service.py`：问答编排测试
- `tests/test_main.py`：CLI 测试

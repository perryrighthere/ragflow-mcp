const requestPreview = document.querySelector("#request-preview");
const responsePreview = document.querySelector("#response-preview");
const activityLog = document.querySelector("#activity-log");
const healthIndicator = document.querySelector("#health-indicator");
const filterList = document.querySelector("#filter-list");
const filterTemplate = document.querySelector("#filter-template");
const settingsForm = document.querySelector("#settings-form");

document.querySelector("#health-check").addEventListener("click", () => runSafely(checkHealth));
document.querySelector("#clear-output").addEventListener("click", clearOutput);
document.querySelector("#add-filter").addEventListener("click", () => addFilterRow());
document.querySelector("#load-settings").addEventListener("click", () => runSafely(loadSettings));
settingsForm.addEventListener("submit", (event) => runSafely(() => handleSettingsUpdate(event)));

document.querySelector("#upload-form").addEventListener("submit", (event) => runSafely(() => handleUpload(event)));
document
  .querySelector("#single-metadata-form")
  .addEventListener("submit", (event) => runSafely(() => handleSingleMetadataUpdate(event)));
document
  .querySelector("#batch-metadata-form")
  .addEventListener("submit", (event) => runSafely(() => handleBatchMetadataUpdate(event)));
document.querySelector("#retrieve-form").addEventListener("submit", (event) => runSafely(() => handleRetrieve(event)));
document.querySelector("#list-form").addEventListener("submit", (event) => runSafely(() => handleListDocuments(event)));

addFilterRow();
runSafely(loadSettings);

async function checkHealth() {
  await sendRequest({
    label: "检查后端状态",
    method: "GET",
    url: "/health",
    onSuccess: (payload) => {
      const status = payload?.data?.status || payload?.data?.data?.status || "ok";
      healthIndicator.textContent = status;
    },
    onError: () => {
      healthIndicator.textContent = "异常";
    },
  });
}

async function loadSettings() {
  await sendRequest({
    label: "读取当前配置",
    method: "GET",
    url: "/api/v1/settings",
    onSuccess: (payload) => {
      const settings = payload?.data?.settings || {};
      settingsForm.ragflow_base_url.value = settings.ragflow_base_url || "";
      settingsForm.request_timeout.value = settings.request_timeout || "";
      settingsForm.server_host.value = settings.server_host || "";
      settingsForm.server_port.value = settings.server_port || "";
      settingsForm.masked_api_key.value = settings.ragflow_api_key || "";
      settingsForm.ragflow_api_key.value = "";
    },
  });
}

async function handleSettingsUpdate(event) {
  event.preventDefault();
  const payload = {
    ragflow_base_url: settingsForm.ragflow_base_url.value.trim(),
  };

  assignIfPresent(payload, "ragflow_api_key", blankToNull(settingsForm.ragflow_api_key.value));
  assignIfPresent(payload, "request_timeout", parseOptionalNumber(settingsForm.request_timeout.value, "request_timeout"));
  assignIfPresent(payload, "server_host", blankToNull(settingsForm.server_host.value));
  assignIfPresent(payload, "server_port", parseOptionalInteger(settingsForm.server_port.value, "server_port"));

  await sendRequest({
    label: "保存运行配置",
    method: "PUT",
    url: "/api/v1/settings",
    json: payload,
    onSuccess: async () => {
      await loadSettings();
      await checkHealth();
    },
  });
}

async function handleUpload(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData();
  data.append("dataset_id", form.dataset_id.value.trim());

  const files = form.files.files;
  if (!files.length) {
    throwUserError("请先选择至少一个文件。");
    return;
  }

  for (const file of files) {
    data.append("files", file);
  }

  appendOptionalFormField(data, "shared_meta_fields", parseOptionalJsonObject(form.shared_meta_fields.value, "shared_meta_fields"));
  appendOptionalFormField(data, "per_file_meta_fields", parseOptionalJsonObject(form.per_file_meta_fields.value, "per_file_meta_fields"));
  appendOptionalFormField(data, "enabled", blankToNull(form.enabled.value));
  appendOptionalFormField(data, "chunk_method", blankToNull(form.chunk_method.value));
  appendOptionalFormField(data, "parser_config", parseOptionalJsonObject(form.parser_config.value, "parser_config"));
  data.append("parse_after_upload", String(form.parse_after_upload.checked));

  await sendRequest({
    label: "上传文档并写 Metadata",
    method: "POST",
    url: "/api/v1/documents/upload",
    body: data,
    requestBodyPreview: summarizeFormData(data),
  });
}

async function handleSingleMetadataUpdate(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const datasetId = form.dataset_id.value.trim();
  const documentId = form.document_id.value.trim();

  const payload = {
    meta_fields: parseRequiredJsonObject(form.meta_fields.value, "meta_fields"),
  };

  assignIfPresent(payload, "enabled", parseOptionalInteger(form.enabled.value, "enabled"));
  assignIfPresent(payload, "name", blankToNull(form.name.value));
  assignIfPresent(payload, "chunk_method", blankToNull(form.chunk_method.value));
  assignIfPresent(payload, "parser_config", parseOptionalJsonObject(form.parser_config.value, "parser_config"));

  await sendRequest({
    label: "更新单个文档 Metadata",
    method: "PUT",
    url: `/api/v1/documents/${encodeURIComponent(datasetId)}/${encodeURIComponent(documentId)}/metadata`,
    json: payload,
  });
}

async function handleBatchMetadataUpdate(event) {
  event.preventDefault();
  const form = event.currentTarget;
  let documents;
  try {
    documents = JSON.parse(form.documents.value);
  } catch (error) {
    throwUserError("documents 必须是合法 JSON 数组。");
    return;
  }

  if (!Array.isArray(documents)) {
    throwUserError("documents 必须是 JSON 数组。");
    return;
  }

  const payload = {
    dataset_id: form.dataset_id.value.trim(),
    documents,
  };

  await sendRequest({
    label: "批量更新 Metadata",
    method: "PUT",
    url: "/api/v1/documents/metadata",
    json: payload,
  });
}

async function handleRetrieve(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    question: form.question.value.trim(),
    page: parseOptionalInteger(form.page.value, "page") ?? 1,
    page_size: parseOptionalInteger(form.page_size.value, "page_size") ?? 10,
    keyword: form.keyword.checked,
    highlight: form.highlight.checked,
  };

  assignIfPresent(payload, "dataset_ids", parseCsv(form.dataset_ids.value));
  assignIfPresent(payload, "document_ids", parseCsv(form.document_ids.value));
  assignIfPresent(payload, "similarity_threshold", parseOptionalNumber(form.similarity_threshold.value, "similarity_threshold"));
  assignIfPresent(payload, "vector_similarity_weight", parseOptionalNumber(form.vector_similarity_weight.value, "vector_similarity_weight"));
  assignIfPresent(payload, "top_k", parseOptionalInteger(form.top_k.value, "top_k"));
  assignIfPresent(payload, "rerank_id", blankToNull(form.rerank_id.value));
  assignIfPresent(payload, "cross_languages", parseCsv(form.cross_languages.value));
  if (form.use_kg.checked) {
    payload.use_kg = true;
  }

  const filters = collectMetadataFilters();
  if (filters.length) {
    payload.metadata_condition = { conditions: filters };
  }

  await sendRequest({
    label: "执行 Retrieve Chunks",
    method: "POST",
    url: "/api/v1/retrieval",
    json: payload,
  });
}

async function handleListDocuments(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const datasetId = form.dataset_id.value.trim();
  const params = new URLSearchParams();

  appendQueryParam(params, "page", form.page.value);
  appendQueryParam(params, "page_size", form.page_size.value);
  appendQueryParam(params, "keywords", form.keywords.value);
  appendQueryParam(params, "name", form.name.value);
  appendRepeatedQueryParam(params, "run", parseCsv(form.run.value));
  appendRepeatedQueryParam(params, "suffix", parseCsv(form.suffix.value));
  appendQueryParam(params, "orderby", form.orderby.value);
  params.set("desc", String(form.desc.checked));

  const query = params.toString();
  const url = `/api/v1/datasets/${encodeURIComponent(datasetId)}/documents${query ? `?${query}` : ""}`;

  await sendRequest({
    label: "查询文档列表",
    method: "GET",
    url,
  });
}

async function sendRequest({ label, method, url, json, body, requestBodyPreview, onSuccess, onError }) {
  const options = { method, headers: {} };

  if (json !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(json);
  } else if (body !== undefined) {
    options.body = body;
  }

  renderRequestPreview({
    label,
    method,
    url,
    body: requestBodyPreview ?? json ?? summarizeFormData(body),
  });

  try {
    const response = await fetch(url, options);
    const text = await response.text();
    const payload = parseMaybeJson(text);

    renderResponsePreview({
      status: response.status,
      ok: response.ok,
      payload,
    });

    if (!response.ok) {
      appendLog(`${label} 失败`, payload?.error?.message || text || `HTTP ${response.status}`);
      if (onError) {
        onError(payload);
      }
      return;
    }

    appendLog(`${label} 成功`, `HTTP ${response.status}`);
    if (onSuccess) {
      onSuccess(payload);
    }
  } catch (error) {
    renderResponsePreview({
      status: "NETWORK_ERROR",
      ok: false,
      payload: { message: error.message },
    });
    appendLog(`${label} 异常`, error.message);
    if (onError) {
      onError(error);
    }
  }
}

function addFilterRow(initial = {}) {
  const node = filterTemplate.content.firstElementChild.cloneNode(true);
  node.querySelector('[data-field="name"]').value = initial.name || "";
  node.querySelector('[data-field="operator"]').value = initial.operator || "=";
  node.querySelector('[data-field="value"]').value = initial.value || "";
  node.querySelector(".remove-filter").addEventListener("click", () => node.remove());
  filterList.appendChild(node);
}

function collectMetadataFilters() {
  const rows = [...filterList.querySelectorAll(".filter-row")];
  const filters = [];

  for (const row of rows) {
    const name = row.querySelector('[data-field="name"]').value.trim();
    const operator = row.querySelector('[data-field="operator"]').value;
    const value = row.querySelector('[data-field="value"]').value.trim();

    if (!name && !value) {
      continue;
    }

    const filter = { name, comparison_operator: operator };
    if (!["empty", "not empty"].includes(operator)) {
      filter.value = value;
    }
    filters.push(filter);
  }

  return filters;
}

function parseRequiredJsonObject(text, fieldName) {
  const value = parseOptionalJsonObject(text, fieldName);
  if (!value) {
    throwUserError(`${fieldName} 不能为空，且必须是 JSON 对象。`);
    throw new Error("invalid json object");
  }
  return value;
}

function parseOptionalJsonObject(text, fieldName) {
  const raw = blankToNull(text);
  if (raw === null) {
    return null;
  }
  try {
    const value = JSON.parse(raw);
    if (!isPlainObject(value)) {
      throw new Error("not object");
    }
    return value;
  } catch (error) {
    throwUserError(`${fieldName} 必须是合法 JSON 对象。`);
    throw error;
  }
}

function parseOptionalInteger(text, fieldName) {
  const raw = blankToNull(text);
  if (raw === null) {
    return null;
  }
  const value = Number.parseInt(raw, 10);
  if (Number.isNaN(value)) {
    throwUserError(`${fieldName} 必须是整数。`);
    throw new Error("invalid integer");
  }
  return value;
}

function parseOptionalNumber(text, fieldName) {
  const raw = blankToNull(text);
  if (raw === null) {
    return null;
  }
  const value = Number(raw);
  if (Number.isNaN(value)) {
    throwUserError(`${fieldName} 必须是数字。`);
    throw new Error("invalid number");
  }
  return value;
}

function parseCsv(text) {
  const raw = blankToNull(text);
  if (raw === null) {
    return null;
  }
  const items = raw.split(",").map((item) => item.trim()).filter(Boolean);
  return items.length ? items : null;
}

function appendOptionalFormField(formData, key, value) {
  if (value === null || value === undefined || value === "") {
    return;
  }
  if (isPlainObject(value) || Array.isArray(value)) {
    formData.append(key, JSON.stringify(value));
    return;
  }
  formData.append(key, String(value));
}

function appendQueryParam(params, key, value) {
  const normalized = blankToNull(value);
  if (normalized !== null) {
    params.set(key, normalized);
  }
}

function appendRepeatedQueryParam(params, key, values) {
  if (!values) {
    return;
  }
  for (const value of values) {
    params.append(key, value);
  }
}

function assignIfPresent(target, key, value) {
  if (value !== null && value !== undefined && value !== "") {
    target[key] = value;
  }
}

function blankToNull(value) {
  if (value === null || value === undefined) {
    return null;
  }
  const normalized = String(value).trim();
  return normalized ? normalized : null;
}

function summarizeFormData(formData) {
  if (!(formData instanceof FormData)) {
    return null;
  }
  const summary = {};
  for (const [key, value] of formData.entries()) {
    const rendered = value instanceof File ? `[File ${value.name}, ${value.size} bytes]` : value;
    if (summary[key] === undefined) {
      summary[key] = rendered;
    } else if (Array.isArray(summary[key])) {
      summary[key].push(rendered);
    } else {
      summary[key] = [summary[key], rendered];
    }
  }
  return summary;
}

function parseMaybeJson(text) {
  try {
    return JSON.parse(text);
  } catch (error) {
    return text;
  }
}

function renderRequestPreview(payload) {
  requestPreview.textContent = JSON.stringify(payload, null, 2);
}

function renderResponsePreview(payload) {
  responsePreview.textContent = JSON.stringify(payload, null, 2);
}

function appendLog(title, detail) {
  const item = document.createElement("li");
  const stamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  item.innerHTML = `<time>${stamp}</time> ${escapeHtml(title)}<br>${escapeHtml(detail)}`;
  activityLog.prepend(item);
}

function clearOutput() {
  requestPreview.textContent = "暂无";
  responsePreview.textContent = "暂无";
  activityLog.innerHTML = "";
}

async function runSafely(callback) {
  try {
    await callback();
  } catch (error) {
    if (error?.message !== "invalid json object" && error?.message !== "invalid integer" && error?.message !== "invalid number") {
      appendLog("前端异常", error?.message || "未知错误");
      responsePreview.textContent = JSON.stringify({ ok: false, message: error?.message || "未知错误" }, null, 2);
    }
  }
}

function throwUserError(message) {
  appendLog("输入错误", message);
  responsePreview.textContent = JSON.stringify({ ok: false, message }, null, 2);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

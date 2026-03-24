const form = document.getElementById("qa-form");
const submitButton = document.getElementById("submit-button");
const resetPromptsButton = document.getElementById("reset-prompts-button");
const statusText = document.getElementById("status-text");
const promptStatusText = document.getElementById("prompt-status-text");
const metaText = document.getElementById("meta-text");
const answerOutput = document.getElementById("answer-output");
const requestOutput = document.getElementById("request-output");
const responseOutput = document.getElementById("response-output");
const llmPromptOutput = document.getElementById("llm-prompt-output");
const sourcesOutput = document.getElementById("sources-output");
const logOutput = document.getElementById("log-output");
const systemPromptInput = document.getElementById("system_prompt");
const userPromptTemplateInput = document.getElementById("user_prompt_template");

let defaultPromptTemplates = null;

function parseCsv(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseOptionalNumber(value) {
  if (value === "") {
    return undefined;
  }
  return Number(value);
}

function parseOptionalJson(value, label) {
  const trimmed = value.trim();
  if (trimmed === "") {
    return undefined;
  }

  try {
    return JSON.parse(trimmed);
  } catch (error) {
    throw new Error(`${label} must be valid JSON.`);
  }
}

function setStatus(text, busy) {
  statusText.textContent = text;
  submitButton.disabled = busy;
}

function appendLog(text, tone = "info") {
  const item = document.createElement("p");
  item.className = `log-item log-${tone}`;
  item.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
  logOutput.prepend(item);
}

function renderJson(target, value) {
  target.textContent = JSON.stringify(value, null, 2);
}

function renderLlmMessages(messages) {
  renderJson(llmPromptOutput, Array.isArray(messages) ? messages : []);
}

function renderSources(sources) {
  sourcesOutput.innerHTML = "";

  if (!Array.isArray(sources) || sources.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No sources returned.";
    sourcesOutput.appendChild(empty);
    return;
  }

  sources.forEach((source, index) => {
    const card = document.createElement("article");
    card.className = "source-card";

    const title = document.createElement("h3");
    title.textContent = source.document_keyword || `Snippet ${index + 1}`;

    const body = document.createElement("pre");
    body.textContent = source.content || "";

    card.appendChild(title);
    card.appendChild(body);
    sourcesOutput.appendChild(card);
  });
}

function applyPromptTemplates(templates) {
  if (!templates) {
    return;
  }

  systemPromptInput.value = templates.system_prompt || "";
  userPromptTemplateInput.value = templates.user_prompt_template || "";
  defaultPromptTemplates = templates;
}

async function loadPromptTemplates() {
  promptStatusText.textContent = "Loading default prompt templates...";

  try {
    const response = await fetch("/api/v1/qa/prompt-templates");
    const rawText = await response.text();
    const payload = rawText ? JSON.parse(rawText) : {};

    if (!response.ok) {
      throw new Error(payload.detail || "Unable to load prompt templates.");
    }

    applyPromptTemplates(payload.data || {});
    promptStatusText.textContent = "Using backend default prompt templates.";
    appendLog("Loaded prompt templates.", "success");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to load prompt templates.";
    promptStatusText.textContent = "Prompt template load failed. Blank fields will use backend defaults.";
    appendLog(message, "error");
  }
}

async function sendRequest(payload) {
  renderJson(requestOutput, payload);
  appendLog("Sending QA request.");

  const response = await fetch("/api/v1/qa/answer", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const rawText = await response.text();
  let data;
  try {
    data = rawText ? JSON.parse(rawText) : {};
    renderJson(responseOutput, data);
  } catch (error) {
    data = null;
    responseOutput.textContent = rawText || "<empty response>";
  }

  if (!response.ok) {
    const detail = data?.detail || rawText || `Request failed with status ${response.status}.`;
    throw new Error(detail);
  }

  if (!data) {
    throw new Error("Service returned a non-JSON success response.");
  }

  return data;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const formData = new FormData(form);
  const payload = {
    question: String(formData.get("question") || "").trim(),
  };

  const datasetIds = parseCsv(String(formData.get("dataset_ids") || ""));
  const documentIds = parseCsv(String(formData.get("document_ids") || ""));
  const pageSize = parseOptionalNumber(String(formData.get("page_size") || ""));
  const similarityThreshold = parseOptionalNumber(String(formData.get("similarity_threshold") || ""));
  const vectorSimilarityWeight = parseOptionalNumber(String(formData.get("vector_similarity_weight") || ""));
  const rerankId = String(formData.get("rerank_id") || "").trim();
  const temperature = parseOptionalNumber(String(formData.get("temperature") || ""));
  const maxTokens = parseOptionalNumber(String(formData.get("max_tokens") || ""));
  const systemPrompt = String(formData.get("system_prompt") || "");
  const userPromptTemplate = String(formData.get("user_prompt_template") || "");

  if (!payload.question) {
    appendLog("Question is required.", "error");
    answerOutput.textContent = "Question is required.";
    return;
  }

  try {
    const metadataCondition = parseOptionalJson(
      String(formData.get("metadata_condition") || ""),
      "Metadata condition"
    );

    if (datasetIds.length > 0) {
      payload.dataset_ids = datasetIds;
    }
    if (documentIds.length > 0) {
      payload.document_ids = documentIds;
    }
    if (!Number.isNaN(pageSize) && pageSize !== undefined) {
      payload.page_size = pageSize;
    }
    if (!Number.isNaN(similarityThreshold) && similarityThreshold !== undefined) {
      payload.similarity_threshold = similarityThreshold;
    }
    if (!Number.isNaN(vectorSimilarityWeight) && vectorSimilarityWeight !== undefined) {
      payload.vector_similarity_weight = vectorSimilarityWeight;
    }
    if (rerankId) {
      payload.rerank_id = rerankId;
    }
    if (metadataCondition !== undefined) {
      payload.metadata_condition = metadataCondition;
    }
    if (!Number.isNaN(temperature) && temperature !== undefined) {
      payload.temperature = temperature;
    }
    if (!Number.isNaN(maxTokens) && maxTokens !== undefined) {
      payload.max_tokens = maxTokens;
    }
    if (systemPrompt.trim()) {
      payload.system_prompt = systemPrompt;
    }
    if (userPromptTemplate.trim()) {
      payload.user_prompt_template = userPromptTemplate;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid form input.";
    appendLog(message, "error");
    answerOutput.textContent = message;
    metaText.textContent = "Fix form input";
    return;
  }

  setStatus("Running...", true);
  metaText.textContent = "Calling retrieval and LLM...";

  try {
    const response = await sendRequest(payload);
    const data = response.data || {};
    answerOutput.textContent = data.answer || "";
    renderSources(data.sources || []);
    renderLlmMessages(data.llm_messages);
    metaText.textContent = `${data.model || "No model"} · ${data.source_count || 0} sources`;
    appendLog("Received QA response.", "success");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error.";
    answerOutput.textContent = message;
    renderSources([]);
    renderLlmMessages([]);
    metaText.textContent = "Request failed";
    appendLog(message, "error");
  } finally {
    setStatus("Ready", false);
  }
});

resetPromptsButton.addEventListener("click", () => {
  if (!defaultPromptTemplates) {
    promptStatusText.textContent = "Default prompt templates are not loaded yet.";
    appendLog("Default prompt templates are not loaded yet.", "info");
    return;
  }

  applyPromptTemplates(defaultPromptTemplates);
  promptStatusText.textContent = "Prompt templates reset to backend defaults.";
  appendLog("Prompt templates reset to defaults.", "success");
});

loadPromptTemplates();

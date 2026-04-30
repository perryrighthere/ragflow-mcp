const doc = typeof document !== "undefined" ? document : null;

function createFallbackElement() {
  return {
    textContent: "",
    innerHTML: "",
    value: "",
    disabled: false,
    className: "",
    scrollTop: 0,
    scrollHeight: 0,
    prepend() {},
    appendChild() {},
    addEventListener() {},
  };
}

function getElementById(id) {
  if (doc && typeof doc.getElementById === "function") {
    return doc.getElementById(id) || createFallbackElement();
  }
  return createFallbackElement();
}

function createElement(tagName) {
  if (doc && typeof doc.createElement === "function") {
    return doc.createElement(tagName);
  }
  return createFallbackElement();
}

const form = getElementById("qa-form");
const submitButton = getElementById("submit-button");
const loadHistoryButton = getElementById("load-history-button");
const resetPromptsButton = getElementById("reset-prompts-button");
const statusText = getElementById("status-text");
const promptStatusText = getElementById("prompt-status-text");
const historyStatusText = getElementById("history-status-text");
const metaText = getElementById("meta-text");
const answerOutput = getElementById("answer-output");
const requestOutput = getElementById("request-output");
const responseOutput = getElementById("response-output");
const llmPromptOutput = getElementById("llm-prompt-output");
const sourcesOutput = getElementById("sources-output");
const referencedDocumentsOutput = getElementById("referenced-documents-output");
const historyOutput = getElementById("history-output");
const logOutput = getElementById("log-output");
const datasetIdsInput = getElementById("dataset_ids");
const userIdInput = getElementById("user_id");
const conversationIdInput = getElementById("conversation_id");
const systemPromptInput = getElementById("system_prompt");
const userPromptTemplateInput = getElementById("user_prompt_template");

let defaultPromptTemplates = null;
let currentPromptMode = "direct";
let answerMarkdown = "";

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

function setHistoryStatus(text, busy = false) {
  historyStatusText.textContent = text;
  loadHistoryButton.disabled = busy;
}

function appendLog(text, tone = "info") {
  const item = createElement("p");
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

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character] || character;
  });
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

function sanitizeUrl(url) {
  const trimmed = String(url || "").trim();
  if (!trimmed) {
    return "";
  }

  if (typeof window === "undefined") {
    return "";
  }

  try {
    const parsed = new URL(trimmed, window.location.origin);
    if (["http:", "https:", "mailto:"].includes(parsed.protocol)) {
      return parsed.href;
    }
  } catch (error) {
    return "";
  }

  return "";
}

function createHtmlTokenStore() {
  const tokens = [];

  return {
    stash(html) {
      const token = `\u0000HTML${tokens.length}\u0000`;
      tokens.push(html);
      return token;
    },
    restore(text) {
      return tokens.reduce(
        (result, html, index) => result.replaceAll(`\u0000HTML${index}\u0000`, html),
        text
      );
    },
  };
}

function renderInlineMarkdown(text) {
  let value = String(text || "");
  const tokenStore = createHtmlTokenStore();

  value = value.replace(/`([^`]+)`/g, (_, code) => tokenStore.stash(`<code>${escapeHtml(code)}</code>`));
  value = value.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, rawDestination) => {
    const match = String(rawDestination).trim().match(/^(\S+?)(?:\s+["'][^"']*["'])?$/);
    const safeHref = sanitizeUrl(match ? match[1] : rawDestination);
    if (!safeHref) {
      return label;
    }
    return tokenStore.stash(
      `<a href="${escapeAttribute(safeHref)}" target="_blank" rel="noreferrer noopener">${escapeHtml(label)}</a>`
    );
  });

  value = escapeHtml(value);
  value = value.replace(/\*\*([\s\S]+?)\*\*/g, "<strong>$1</strong>");
  value = value.replace(/__([\s\S]+?)__/g, "<strong>$1</strong>");
  value = value.replace(/~~([\s\S]+?)~~/g, "<del>$1</del>");
  value = value.replace(/(^|[^\*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  value = value.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");

  return tokenStore.restore(value);
}

function isHorizontalRule(line) {
  return /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line);
}

function isUnorderedListLine(line) {
  return /^\s*[-+*]\s+/.test(line);
}

function isOrderedListLine(line) {
  return /^\s*\d+\.\s+/.test(line);
}

function isListLine(line) {
  return isUnorderedListLine(line) || isOrderedListLine(line);
}

function isTableSeparatorLine(line) {
  return /^\s*\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(line);
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderParagraph(lines) {
  return `<p>${lines.map((line) => renderInlineMarkdown(line)).join("<br>")}</p>`;
}

function isSpecialBlockStart(lines, index) {
  const line = lines[index];
  if (!line || !line.trim()) {
    return true;
  }

  return (
    /^(```|~~~)/.test(line) ||
    /^(#{1,6})\s+/.test(line) ||
    isHorizontalRule(line) ||
    /^>\s?/.test(line) ||
    isListLine(line) ||
    (index + 1 < lines.length && line.includes("|") && isTableSeparatorLine(lines[index + 1]))
  );
}

function renderList(lines, startIndex) {
  const ordered = isOrderedListLine(lines[startIndex]);
  const tag = ordered ? "ol" : "ul";
  const items = [];
  let index = startIndex;

  while (index < lines.length) {
    const line = lines[index];
    const markerMatch = ordered
      ? line.match(/^\s*\d+\.\s+(.*)$/)
      : line.match(/^\s*[-+*]\s+(.*)$/);

    if (!markerMatch) {
      break;
    }

    const itemLines = [markerMatch[1]];
    index += 1;

    while (index < lines.length) {
      const continuationLine = lines[index];
      if (!continuationLine.trim()) {
        if (
          index + 1 < lines.length &&
          (ordered ? isOrderedListLine(lines[index + 1]) : isUnorderedListLine(lines[index + 1]))
        ) {
          index += 1;
        }
        break;
      }

      if (isListLine(continuationLine) || isSpecialBlockStart(lines, index)) {
        break;
      }

      itemLines.push(continuationLine.trim());
      index += 1;
    }

    items.push(`<li>${renderParagraph(itemLines)}</li>`);
  }

  return {
    html: `<${tag}>${items.join("")}</${tag}>`,
    nextIndex: index,
  };
}

function renderTable(lines, startIndex) {
  const headerCells = splitTableRow(lines[startIndex]);
  const alignCells = splitTableRow(lines[startIndex + 1]);
  const bodyRows = [];
  let index = startIndex + 2;

  while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
    bodyRows.push(splitTableRow(lines[index]));
    index += 1;
  }

  const alignments = alignCells.map((cell) => {
    const trimmed = cell.trim();
    if (trimmed.startsWith(":") && trimmed.endsWith(":")) {
      return "center";
    }
    if (trimmed.endsWith(":")) {
      return "right";
    }
    return "left";
  });

  const headerHtml = headerCells
    .map((cell, cellIndex) => `<th style="text-align:${alignments[cellIndex] || "left"}">${renderInlineMarkdown(cell)}</th>`)
    .join("");
  const bodyHtml = bodyRows
    .map((row) => {
      const cells = headerCells.map(
        (_, cellIndex) =>
          `<td style="text-align:${alignments[cellIndex] || "left"}">${renderInlineMarkdown(row[cellIndex] || "")}</td>`
      );
      return `<tr>${cells.join("")}</tr>`;
    })
    .join("");

  return {
    html: `<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`,
    nextIndex: index,
  };
}

function renderMarkdown(markdownText) {
  const text = String(markdownText || "").replace(/\r\n?/g, "\n");
  if (!text.trim()) {
    return "";
  }

  const lines = text.split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fenceMatch = line.match(/^(```|~~~)\s*([a-zA-Z0-9_-]+)?\s*$/);
    if (fenceMatch) {
      const fence = fenceMatch[1];
      const language = fenceMatch[2] || "";
      const codeLines = [];
      index += 1;

      while (index < lines.length && !new RegExp(`^${fence}\\s*$`).test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }

      if (index < lines.length) {
        index += 1;
      }

      const languageClass = language ? ` class="language-${escapeAttribute(language)}"` : "";
      blocks.push(`<pre><code${languageClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (isHorizontalRule(line)) {
      blocks.push("<hr>");
      index += 1;
      continue;
    }

    if (index + 1 < lines.length && line.includes("|") && isTableSeparatorLine(lines[index + 1])) {
      const table = renderTable(lines, index);
      blocks.push(table.html);
      index = table.nextIndex;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    if (isListLine(line)) {
      const list = renderList(lines, index);
      blocks.push(list.html);
      index = list.nextIndex;
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && lines[index].trim() && !isSpecialBlockStart(lines, index)) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(renderParagraph(paragraphLines));
  }

  return blocks.join("");
}

function parseJsonText(rawText) {
  try {
    return rawText ? JSON.parse(rawText) : {};
  } catch (error) {
    return null;
  }
}

function buildMetaText(data) {
  const model = data?.model || "No model";
  const sourceCount = data?.source_count || 0;
  const documentCount = Array.isArray(data?.referenced_documents) ? data.referenced_documents.length : 0;
  const conversationTitle = String(data?.conversation_title || "").trim();
  const conversationSuffix = conversationTitle ? ` · ${conversationTitle}` : "";
  if (sourceCount > 0) {
    return `${model} · ${documentCount} docs · ${sourceCount} sources${conversationSuffix}`;
  }
  if (Array.isArray(data?.llm_messages) && data.llm_messages.length > 0) {
    return `${model} · direct LLM${conversationSuffix}`;
  }
  return `${model} · 0 sources${conversationSuffix}`;
}

function renderSources(sources) {
  sourcesOutput.innerHTML = "";

  if (!Array.isArray(sources) || sources.length === 0) {
    const empty = createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No sources returned.";
    sourcesOutput.appendChild(empty);
    return;
  }

  sources.forEach((source, index) => {
    const card = createElement("article");
    card.className = "source-card";

    const title = createElement("h3");
    const referenceIndex = Number(source.reference_index || 0);
    const titlePrefix = referenceIndex > 0 ? `[${referenceIndex}] ` : "";
    title.textContent = `${titlePrefix}${source.document_keyword || `Snippet ${index + 1}`}`;

    const body = createElement("pre");
    body.textContent = source.content || "";

    card.appendChild(title);
    card.appendChild(body);
    sourcesOutput.appendChild(card);
  });
}

function renderReferencedDocuments(documents) {
  referencedDocumentsOutput.innerHTML = "";

  if (!Array.isArray(documents) || documents.length === 0) {
    const empty = createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No referenced documents returned.";
    referencedDocumentsOutput.appendChild(empty);
    return;
  }

  documents.forEach((document) => {
    const card = createElement("article");
    card.className = "source-card";

    const title = createElement("h3");
    const documentIndex = Number(document.index || 0);
    const titlePrefix = documentIndex > 0 ? `[${documentIndex}] ` : "";
    title.textContent = `${titlePrefix}${document.document_name || "Unnamed document"}`;

    const body = createElement("pre");
    body.textContent = [
      `dataset_id: ${document.dataset_id || ""}`,
      `document_id: ${document.document_id || ""}`,
    ].join("\n");

    card.appendChild(title);
    card.appendChild(body);
    referencedDocumentsOutput.appendChild(card);
  });
}

function renderHistoryMessages(messages) {
  const list = createElement("div");
  list.className = "history-messages";

  if (!Array.isArray(messages) || messages.length === 0) {
    const empty = createElement("p");
    empty.className = "history-message-empty";
    empty.textContent = "No retained messages.";
    list.appendChild(empty);
    return list;
  }

  messages.forEach((message) => {
    const item = createElement("p");
    item.className = `history-message history-message-${message.role === "assistant" ? "assistant" : "user"}`;
    const role = message.role === "assistant" ? "Assistant" : "User";
    item.textContent = `${role}: ${String(message.content || "").trim()}`;
    list.appendChild(item);
  });

  return list;
}

function renderConversationHistory(conversations) {
  historyOutput.innerHTML = "";

  if (!Array.isArray(conversations) || conversations.length === 0) {
    const empty = createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No conversations found for this user.";
    historyOutput.appendChild(empty);
    return;
  }

  conversations.forEach((conversation) => {
    const card = createElement("article");
    card.className = "history-card";

    const header = createElement("div");
    header.className = "history-card-head";

    const titleWrap = createElement("div");
    const title = createElement("h3");
    title.textContent = conversation.conversation_title || "Untitled conversation";
    const meta = createElement("p");
    meta.textContent = [
      conversation.conversation_id || "",
      conversation.updated_at ? `updated ${conversation.updated_at}` : "",
    ].filter(Boolean).join(" · ");
    titleWrap.appendChild(title);
    titleWrap.appendChild(meta);

    const continueButton = createElement("button");
    continueButton.type = "button";
    continueButton.className = "secondary-button history-continue-button";
    continueButton.textContent = "Continue";
    continueButton.addEventListener("click", () => {
      conversationIdInput.value = conversation.conversation_id || "";
      metaText.textContent = conversation.conversation_title || "Conversation selected";
      appendLog(`Selected conversation ${conversation.conversation_id || ""}.`, "info");
    });

    header.appendChild(titleWrap);
    header.appendChild(continueButton);
    card.appendChild(header);

    if (conversation.history_summary) {
      const summary = createElement("p");
      summary.className = "history-summary";
      summary.textContent = `Summary: ${conversation.history_summary}`;
      card.appendChild(summary);
    }

    card.appendChild(renderHistoryMessages(conversation.history_messages));
    historyOutput.appendChild(card);
  });
}

function hasDatasetIds() {
  return parseCsv(datasetIdsInput.value || "").length > 0;
}

function getPromptMode() {
  return hasDatasetIds() ? "retrieval" : "direct";
}

function getPromptValues() {
  return {
    system_prompt: systemPromptInput.value || "",
    user_prompt_template: userPromptTemplateInput.value || "",
  };
}

function arePromptTemplatesEqual(left, right) {
  if (!left || !right) {
    return false;
  }

  return (
    String(left.system_prompt || "") === String(right.system_prompt || "") &&
    String(left.user_prompt_template || "") === String(right.user_prompt_template || "")
  );
}

function getActiveDefaultPromptTemplates() {
  if (!defaultPromptTemplates) {
    return null;
  }
  return defaultPromptTemplates[getPromptMode()] || defaultPromptTemplates.retrieval || null;
}

function applyPromptTemplates(templates) {
  if (!templates) {
    return;
  }

  systemPromptInput.value = templates.system_prompt || "";
  userPromptTemplateInput.value = templates.user_prompt_template || "";
}

function renderAnswerMarkdown(markdown, scrollToBottom = false) {
  answerMarkdown = String(markdown || "");
  answerOutput.innerHTML = renderMarkdown(answerMarkdown);
  if (!answerOutput.innerHTML) {
    answerOutput.textContent = "";
  }
  if (scrollToBottom) {
    answerOutput.scrollTop = answerOutput.scrollHeight;
  }
}

function renderAnswerText(text) {
  answerMarkdown = "";
  answerOutput.textContent = text;
}

function resetAnswerPanels() {
  renderAnswerText("");
  responseOutput.textContent = "{}";
  renderSources([]);
  renderReferencedDocuments([]);
  renderLlmMessages([]);
}

function applyAnswerPayload(data) {
  renderAnswerMarkdown(data.answer || "");
  renderSources(data.sources || []);
  renderReferencedDocuments(data.referenced_documents || []);
  renderLlmMessages(data.llm_messages);
  metaText.textContent = buildMetaText(data);
  if (data.conversation_id) {
    conversationIdInput.value = data.conversation_id;
  }
}

function appendAnswerDelta(delta) {
  renderAnswerMarkdown(answerMarkdown + String(delta || ""), true);
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

    const retrievalDefaults = {
      system_prompt: payload.data?.system_prompt || "",
      user_prompt_template: payload.data?.user_prompt_template || "",
    };
    defaultPromptTemplates = {
      retrieval: retrievalDefaults,
      direct: payload.data?.direct_answer_defaults || retrievalDefaults,
    };
    currentPromptMode = getPromptMode();
    applyPromptTemplates(getActiveDefaultPromptTemplates());
    promptStatusText.textContent =
      currentPromptMode === "retrieval"
        ? "Using knowledge-base prompt defaults."
        : "Using direct-LLM prompt defaults.";
    appendLog("Loaded prompt templates.", "success");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to load prompt templates.";
    promptStatusText.textContent = "Prompt template load failed. Blank fields will use backend defaults.";
    appendLog(message, "error");
  }
}

function processStreamLine(line, state) {
  if (!line.trim()) {
    return;
  }

  let event;
  try {
    event = JSON.parse(line);
  } catch (error) {
    throw new Error("Streaming response contained invalid JSON.");
  }

  if (event.type === "context") {
    renderSources(event.data?.sources || []);
    renderReferencedDocuments(event.data?.referenced_documents || []);
    renderLlmMessages(event.data?.llm_messages);
    metaText.textContent = buildMetaText(event.data);
    if (event.data?.conversation_id) {
      conversationIdInput.value = event.data.conversation_id;
    }
    appendLog("Prepared the answer context and started streaming.", "info");
    return;
  }

  if (event.type === "answer_delta") {
    appendAnswerDelta(event.delta || "");
    return;
  }

  if (event.type === "done") {
    state.finalData = event.data || {};
    applyAnswerPayload(state.finalData);
    renderJson(responseOutput, { code: 0, data: state.finalData });
    return;
  }

  if (event.type === "error") {
    const streamError = new Error(event.message || "Streaming request failed.");
    streamError.keepCurrentOutput = true;
    throw streamError;
  }
}

async function sendStreamingRequest(payload, path) {
  renderJson(requestOutput, payload);
  appendLog(`Sending streaming QA request to ${path}.`);

  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const rawText = await response.text();
    const data = parseJsonText(rawText);
    if (data) {
      renderJson(responseOutput, data);
    } else {
      responseOutput.textContent = rawText || "<empty response>";
    }

    const detail = data?.detail || rawText || `Request failed with status ${response.status}.`;
    throw new Error(detail);
  }

  const state = { finalData: null };
  const processChunkText = (chunkText, buffer) => {
    const combined = buffer + chunkText;
    const lines = combined.split(/\r?\n/);
    const remainder = lines.pop() || "";
    lines.forEach((line) => processStreamLine(line, state));
    return remainder;
  };

  if (!response.body) {
    let buffer = "";
    buffer = processChunkText(await response.text(), buffer);
    if (buffer.trim()) {
      processStreamLine(buffer, state);
    }
  } else {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      buffer = processChunkText(decoder.decode(value || new Uint8Array(), { stream: !done }), buffer);
      if (done) {
        break;
      }
    }

    const tail = decoder.decode();
    buffer = processChunkText(tail, buffer);
    if (buffer.trim()) {
      processStreamLine(buffer, state);
    }
  }

  if (!state.finalData) {
    throw new Error("Streaming response ended before completion.");
  }

  return { code: 0, data: state.finalData };
}

async function loadConversationHistory() {
  const userId = String(userIdInput.value || "").trim();
  if (!userId) {
    setHistoryStatus("Enter a user ID before loading history.");
    appendLog("User ID is required to load conversation history.", "error");
    return;
  }

  setHistoryStatus("Loading conversation history...", true);
  try {
    const query = new URLSearchParams({
      user_id: userId,
      page: "1",
      page_size: "20",
    });
    const response = await fetch(`/api/v1/qa/conversations?${query.toString()}`);
    const rawText = await response.text();
    const payload = rawText ? JSON.parse(rawText) : {};

    if (!response.ok) {
      throw new Error(payload.detail || `History request failed with status ${response.status}.`);
    }

    const conversations = payload.data?.conversations || [];
    renderConversationHistory(conversations);
    setHistoryStatus(`${payload.data?.total || 0} conversations loaded for ${payload.data?.user_id || userId}.`);
    appendLog("Loaded conversation history.", "success");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to load conversation history.";
    renderConversationHistory([]);
    setHistoryStatus("Conversation history load failed.");
    appendLog(message, "error");
  } finally {
    loadHistoryButton.disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const formData = new FormData(form);
  const payload = {
    question: String(formData.get("question") || "").trim(),
  };

  const userId = String(formData.get("user_id") || "").trim();
  const conversationId = String(formData.get("conversation_id") || "").trim();
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
    renderAnswerText("Question is required.");
    return;
  }

  try {
    const metadataCondition = parseOptionalJson(
      String(formData.get("metadata_condition") || ""),
      "Metadata condition"
    );

    if (userId) {
      payload.user_id = userId;
    }
    if (userId && conversationId) {
      payload.conversation_id = conversationId;
    }
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
    renderAnswerText(message);
    metaText.textContent = "Fix form input";
    return;
  }

  setStatus("Running...", true);
  metaText.textContent = datasetIds.length > 0 ? "Retrieving knowledge snippets..." : "Sending question to the LLM...";
  resetAnswerPanels();

  try {
    const endpoint = userId ? "/api/v1/qa/conversations/answer/stream" : "/api/v1/qa/answer/stream";
    const response = await sendStreamingRequest(payload, endpoint);
    const data = response.data || {};
    applyAnswerPayload(data);
    appendLog("Received streamed QA response.", "success");
    if (userId) {
      await loadConversationHistory();
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error.";
    if (!(error instanceof Error && error.keepCurrentOutput)) {
      renderAnswerText(message);
      renderSources([]);
      renderReferencedDocuments([]);
      renderLlmMessages([]);
    }
    metaText.textContent = error instanceof Error && error.keepCurrentOutput ? "Stream interrupted" : "Request failed";
    appendLog(message, "error");
  } finally {
    setStatus("Ready", false);
  }
});

loadHistoryButton.addEventListener("click", () => {
  loadConversationHistory();
});

resetPromptsButton.addEventListener("click", () => {
  const activeTemplates = getActiveDefaultPromptTemplates();
  if (!activeTemplates) {
    promptStatusText.textContent = "Default prompt templates are not loaded yet.";
    appendLog("Default prompt templates are not loaded yet.", "info");
    return;
  }

  applyPromptTemplates(activeTemplates);
  promptStatusText.textContent = "Prompt templates reset to backend defaults.";
  appendLog("Prompt templates reset to defaults.", "success");
});

datasetIdsInput.addEventListener("input", () => {
  if (!defaultPromptTemplates) {
    return;
  }

  const nextPromptMode = getPromptMode();
  if (nextPromptMode === currentPromptMode) {
    return;
  }

  const currentValues = getPromptValues();
  const previousDefaults = defaultPromptTemplates[currentPromptMode];
  currentPromptMode = nextPromptMode;

  if (arePromptTemplatesEqual(currentValues, previousDefaults)) {
    applyPromptTemplates(getActiveDefaultPromptTemplates());
    promptStatusText.textContent =
      currentPromptMode === "retrieval"
        ? "Switched to knowledge-base prompt defaults."
        : "Switched to direct-LLM prompt defaults.";
    appendLog("Prompt defaults updated to match the current QA mode.", "info");
    return;
  }

  promptStatusText.textContent =
    currentPromptMode === "retrieval"
      ? "Dataset IDs detected. Keeping your custom prompt edits."
      : "Direct LLM mode detected. Keeping your custom prompt edits.";
});

if (doc && typeof doc.createElement === "function" && typeof fetch === "function") {
  loadPromptTemplates();
}

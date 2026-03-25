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
  return `${data?.model || "No model"} · ${data?.source_count || 0} sources`;
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
  renderLlmMessages([]);
}

function applyAnswerPayload(data) {
  renderAnswerMarkdown(data.answer || "");
  renderSources(data.sources || []);
  renderLlmMessages(data.llm_messages);
  metaText.textContent = buildMetaText(data);
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
  let data = parseJsonText(rawText);
  if (data) {
    renderJson(responseOutput, data);
  } else {
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
    renderLlmMessages(event.data?.llm_messages);
    metaText.textContent = buildMetaText(event.data);
    appendLog("Retrieved sources and started streaming the answer.", "info");
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

async function sendStreamingRequest(payload) {
  renderJson(requestOutput, payload);
  appendLog("Sending streaming QA request.");

  const response = await fetch("/api/v1/qa/answer/stream", {
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
    renderAnswerText("Question is required.");
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
    renderAnswerText(message);
    metaText.textContent = "Fix form input";
    return;
  }

  setStatus("Running...", true);
  metaText.textContent = "Retrieving knowledge snippets...";
  resetAnswerPanels();

  try {
    const response = await sendStreamingRequest(payload);
    const data = response.data || {};
    applyAnswerPayload(data);
    appendLog("Received streamed QA response.", "success");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error.";
    if (!(error instanceof Error && error.keepCurrentOutput)) {
      renderAnswerText(message);
      renderSources([]);
      renderLlmMessages([]);
    }
    metaText.textContent = error instanceof Error && error.keepCurrentOutput ? "Stream interrupted" : "Request failed";
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

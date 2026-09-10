const chatWindow = document.getElementById("chat-window");
const emptyState = document.getElementById("empty-state");
const loadingIndicator = document.getElementById("loading-indicator");
const errorBanner = document.getElementById("error-banner");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const stopButton = document.getElementById("stop-button");
const newChatButton = document.getElementById("new-chat-button");
const modeButtons = Array.from(document.querySelectorAll(".mode-option"));
const modelSelect = document.getElementById("model-select");

// เก็บ AbortController ของ request ที่กำลังทำงานอยู่ ไว้ให้ปุ่ม "หยุด" เรียกยกเลิกได้
// (นี่คือส่วนที่เติมให้ตรงกับ Usability Heuristic "User Control & Freedom" ที่สอนในสไลด์)
let activeAbortController = null;

// marked (แปลง Markdown) และ DOMPurify (กรอง HTML อันตราย) โหลดมาจาก CDN ถ้าเครื่องที่
// สาธิตไม่มีอินเทอร์เน็ต — ซึ่งเป็นกรณีปกติเวลารันโมเดลในเครื่องด้วย LM Studio — สคริปต์
// สองตัวนี้จะโหลดไม่ได้ ต้องถอยไปแสดงเป็นข้อความธรรมดาแทน ไม่ใช่ปล่อยให้ทั้งคำตอบหายไป
const hasMarkdownLibs = () =>
  typeof marked !== "undefined" && typeof DOMPurify !== "undefined";

function renderMarkdown(rawText) {
  if (!hasMarkdownLibs()) return null;
  try {
    return DOMPurify.sanitize(marked.parse(rawText, { breaks: true }));
  } catch (err) {
    console.warn("[chat] แปลง Markdown ไม่สำเร็จ จะแสดงเป็นข้อความธรรมดาแทน:", err);
    return null;
  }
}

// เขียนข้อความลงบับเบิล: ถ้าแปลง Markdown ได้ก็ใส่เป็น HTML ที่กรองแล้ว
// ถ้าไม่ได้ก็ใส่เป็น textContent ซึ่งปลอดภัยเสมอ (เบราว์เซอร์ไม่ตีความเป็น HTML)
function setBubbleContent(bubble, text) {
  const html = renderMarkdown(text);
  if (html === null) {
    bubble.textContent = text;
  } else {
    bubble.innerHTML = html;
  }
}

function hideEmptyState() {
  emptyState.classList.add("hidden");
}

function showEmptyState() {
  emptyState.classList.remove("hidden");
}

function formatTime(date) {
  return date.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
}

// ไอคอน avatar เป็น SVG แทนตัวอักษรย่อ เพื่อเลี่ยงปัญหาความกว้างตัวอักษรไทย
// ("คุณ" ยาวกว่า "AI" มาก) ล้นวงกลม avatar ขนาดคงที่บนบางฟอนต์/เบราว์เซอร์
const AVATAR_ICON = {
  ai: '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 2a1 1 0 0 1 1 1v1.06A8.004 8.004 0 0 1 20 12v1h1a1 1 0 1 1 0 2h-1.08A8.003 8.003 0 0 1 13 21.94V23a1 1 0 1 1-2 0v-1.06A8.003 8.003 0 0 1 4.08 15H3a1 1 0 1 1 0-2h1v-1a8.004 8.004 0 0 1 7-7.94V3a1 1 0 0 1 1-1Zm0 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12Zm-2.25 4.5a1.25 1.25 0 1 1 0 2.5 1.25 1.25 0 0 1 0-2.5Zm4.5 0a1.25 1.25 0 1 1 0 2.5 1.25 1.25 0 0 1 0-2.5Z" fill="currentColor"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 2c-4.14 0-7.5 2.61-7.5 5.83 0 .34.28.67.62.67h13.76c.34 0 .62-.33.62-.67C19.5 16.61 16.14 14 12 14Z" fill="currentColor"/></svg>',
};

// สร้างโครง <div class="message ..."><avatar><message-body><bubble/time></message-body></div>
// ใช้ร่วมกันทั้งฝั่ง user และ ai เพื่อไม่ให้โครง DOM เพี้ยนกันระหว่างสองฝั่ง
function createMessageRow(role) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.innerHTML = AVATAR_ICON[role] || AVATAR_ICON.ai;
  avatar.setAttribute("aria-hidden", "true");

  const body = document.createElement("div");
  body.className = "message-body";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const time = document.createElement("div");
  time.className = "message-time";
  time.textContent = formatTime(new Date());

  body.appendChild(bubble);
  body.appendChild(time);
  wrapper.appendChild(avatar);
  wrapper.appendChild(body);

  chatWindow.insertBefore(wrapper, loadingIndicator);
  scrollToBottom();

  return { wrapper, bubble, body };
}

function appendUserMessage(text) {
  hideEmptyState();
  const { bubble } = createMessageRow("user");
  bubble.textContent = text;
  scrollToBottom();
}

function createAiBubble() {
  const { wrapper, bubble, body } = createMessageRow("ai");
  return { wrapper, bubble, body };
}

// ---------- แหล่งอ้างอิงจากฐานความรู้ (RAG) ----------
// แสดงว่าคำตอบนี้ประกอบขึ้นจากเอกสารฉบับไหนบ้าง เป็นเงื่อนไขสำคัญของระบบตอบคำถาม
// แบบ RAG: ผู้ใช้ต้องตรวจสอบย้อนกลับไปยังต้นทางได้ ไม่ใช่เชื่อคำตอบของโมเดลอย่างเดียว
function renderSources(bodyEl, sources) {
  if (!Array.isArray(sources) || sources.length === 0) return;

  const details = document.createElement("details");
  details.className = "sources";

  const summary = document.createElement("summary");
  summary.className = "sources-summary";
  summary.textContent = `อ้างอิงจากเอกสาร ${sources.length} รายการ`;
  details.appendChild(summary);

  const list = document.createElement("ol");
  list.className = "sources-list";

  sources.forEach((src) => {
    const item = document.createElement("li");
    item.className = "source-item";

    const head = document.createElement("div");
    head.className = "source-head";

    const title = document.createElement("span");
    title.className = "source-title";
    // ใช้ textContent ตลอด ไม่ใช้ innerHTML เพราะข้อความส่วนนี้มาจากไฟล์เอกสาร
    // ไม่ควรถูกตีความเป็น HTML
    title.textContent = src.heading || src.doc_id;
    head.appendChild(title);

    if (src.system) {
      const badge = document.createElement("span");
      badge.className = "source-system";
      badge.textContent = src.system;
      head.appendChild(badge);
    }

    const score = document.createElement("span");
    score.className = "source-score";
    score.textContent = typeof src.score === "number" ? src.score.toFixed(2) : "";
    score.title = "ค่าความคล้าย (cosine similarity) ระหว่างคำถามกับข้อความส่วนนี้";
    head.appendChild(score);

    const path = document.createElement("div");
    path.className = "source-path";
    path.textContent = src.doc_id || "";

    const excerpt = document.createElement("p");
    excerpt.className = "source-excerpt";
    excerpt.textContent = src.excerpt || "";

    item.appendChild(head);
    item.appendChild(path);
    item.appendChild(excerpt);
    list.appendChild(item);
  });

  details.appendChild(list);
  bodyEl.appendChild(details);
}

// อ่านสถานะฐานความรู้ครั้งเดียวตอนเปิดหน้า แล้วขึ้นป้ายบอกให้ชัดว่าตอนนี้ตอบจากเอกสาร
// หรือตอบจากความรู้ของโมเดลล้วนๆ (สำคัญตอนสาธิต เพราะสองโหมดนี้คุณภาพคำตอบต่างกันมาก)
async function loadRagStatus() {
  const badge = document.getElementById("rag-badge");
  if (!badge) return;
  try {
    const response = await fetch("/api/v1/rag/status");
    const data = await response.json();
    if (!data.enabled) {
      badge.textContent = "ปิดใช้ฐานความรู้";
      badge.className = "rag-badge off";
    } else if (data.ready) {
      badge.textContent = `ฐานความรู้: ${data.n_chunks} ส่วน / ${data.n_systems} ระบบ`;
      badge.className = "rag-badge on";
      badge.title = [
        `Hybrid search: ${data.hybrid ? "เปิด" : "ปิด"}`,
        `Reranker: ${data.reranker ? "เปิด" : "ปิด"}`,
        `top-k: ${data.top_k}`,
        `prompt: ${data.prompt_variant}`,
      ].join(" · ");
    } else {
      badge.textContent = "กำลังโหลดฐานความรู้...";
      badge.className = "rag-badge loading";
      if (data.error) {
        badge.textContent = "โหลดฐานความรู้ไม่สำเร็จ";
        badge.className = "rag-badge off";
        badge.title = data.error;
      } else {
        // ยังโหลดอยู่ในเธรดพื้นหลัง ถามซ้ำอีกรอบในอีก 5 วินาที
        setTimeout(loadRagStatus, 5000);
      }
    }
  } catch (err) {
    console.warn("[chat] อ่านสถานะ RAG ไม่ได้:", err);
    badge.classList.add("hidden");
  }
}

// ---------- ตัวเลือกโมเดล (ออฟไลน์ / ออนไลน์) ----------
// เก็บสถานะไว้ฝั่งหน้าเว็บแล้วส่งไปพร้อมทุกคำถาม เซิร์ฟเวอร์ไม่ได้จำว่าใครเลือกอะไร
// จึงเปลี่ยนโมเดลกลางบทสนทนาได้ทันที และเปิดสองแท็บเทียบคนละโมเดลพร้อมกันได้
const MODE_STORAGE_KEY = "chatbot.llm.mode";
const MODEL_STORAGE_KEY_PREFIX = "chatbot.llm.model.";

let currentMode = null;
let providers = [];

// localStorage ถูกปิดได้ในบางเบราว์เซอร์/โหมดส่วนตัว และการเรียกจะ throw ไม่ใช่คืน null
// จึงหุ้ม try/catch ไว้ทั้งอ่านและเขียน — จำค่าไม่ได้ก็แค่ต้องเลือกใหม่ ไม่ควรทำหน้าพัง
function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch (err) {
    return null;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    /* จำค่าไม่ได้ก็ไม่เป็นไร */
  }
}

function currentModel() {
  return modelSelect.value || null;
}

function setModelOptions(models, { selected } = {}) {
  modelSelect.innerHTML = "";
  modelSelect.classList.remove("error");
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.id;
    modelSelect.appendChild(opt);
  }
  const wanted = models.some((m) => m.id === selected) ? selected : models[0]?.id;
  if (wanted) modelSelect.value = wanted;
  modelSelect.disabled = models.length === 0;
}

function setModelPlaceholder(text, { isError = false } = {}) {
  modelSelect.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = text;
  modelSelect.appendChild(opt);
  modelSelect.disabled = true;
  modelSelect.classList.toggle("error", isError);
}

async function loadModelsForMode(mode) {
  setModelPlaceholder("กำลังโหลดรายชื่อโมเดล...");
  let data;
  try {
    const response = await fetch(`/api/v1/models?mode=${encodeURIComponent(mode)}`);
    data = await response.json();
  } catch (err) {
    console.error("[chat] ดึงรายชื่อโมเดลไม่สำเร็จ:", err);
    setModelPlaceholder("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้", { isError: true });
    return;
  }

  if (data.error || !Array.isArray(data.models) || data.models.length === 0) {
    // กรณีที่เจอบ่อยที่สุดคือเลือกออฟไลน์ไว้แต่ยังไม่ได้เปิด LM Studio — บอกให้ชัด
    // ว่าต้องไปทำอะไร แทนที่จะปล่อยให้ผู้ใช้ไปเจอ error ตอนกดส่งคำถาม
    const fallback =
      mode === "offline"
        ? "เชื่อมต่อ LM Studio ไม่ได้ — เปิดโปรแกรมแล้วเริ่ม Local Server ก่อน"
        : "ดึงรายชื่อโมเดลไม่สำเร็จ";
    setModelPlaceholder(mode === "offline" ? "ยังไม่ได้เปิด LM Studio" : "ไม่มีโมเดล", {
      isError: true,
    });
    showNotice(data.message || fallback);
    return;
  }

  const provider = providers.find((p) => p.mode === mode);
  const remembered = readStored(MODEL_STORAGE_KEY_PREFIX + mode) || provider?.default_model;
  setModelOptions(data.models, { selected: remembered });
  clearError();
}

async function selectMode(mode, { persist = true } = {}) {
  const provider = providers.find((p) => p.mode === mode);
  if (!provider || !provider.configured) return;

  currentMode = mode;
  modeButtons.forEach((btn) => {
    btn.setAttribute("aria-checked", String(btn.dataset.mode === mode));
  });
  if (persist) writeStored(MODE_STORAGE_KEY, mode);
  await loadModelsForMode(mode);
}

async function initModelPicker() {
  try {
    const response = await fetch("/api/v1/providers");
    providers = (await response.json()).providers || [];
  } catch (err) {
    console.error("[chat] ดึงรายชื่อโหมดไม่สำเร็จ:", err);
    setModelPlaceholder("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้", { isError: true });
    return;
  }

  modeButtons.forEach((btn) => {
    const provider = providers.find((p) => p.mode === btn.dataset.mode);
    if (!provider) return;
    btn.title = provider.configured
      ? provider.description
      : `ยังไม่ได้ตั้งค่าโหมดนี้ — ตั้ง ${btn.dataset.mode.toUpperCase()}_BASE_URL ในไฟล์ .env`;
    btn.disabled = !provider.configured;
    btn.addEventListener("click", () => selectMode(btn.dataset.mode));
  });

  // ใช้โหมดที่ผู้ใช้เลือกไว้ครั้งก่อนถ้ายังตั้งค่าอยู่ ไม่งั้นใช้ค่าเริ่มต้นจากเซิร์ฟเวอร์
  const remembered = readStored(MODE_STORAGE_KEY);
  const usable = providers.filter((p) => p.configured);
  const chosen =
    usable.find((p) => p.mode === remembered) ||
    usable.find((p) => p.is_default) ||
    usable[0];

  if (!chosen) {
    setModelPlaceholder("ยังไม่ได้ตั้งค่าผู้ให้บริการโมเดล", { isError: true });
    showError("ยังไม่ได้ตั้งค่าผู้ให้บริการโมเดลเลย กรุณาตั้ง OFFLINE_BASE_URL หรือ ONLINE_BASE_URL ในไฟล์ .env");
    return;
  }
  await selectMode(chosen.mode, { persist: false });
}

modelSelect.addEventListener("change", () => {
  if (currentMode && modelSelect.value) {
    writeStored(MODEL_STORAGE_KEY_PREFIX + currentMode, modelSelect.value);
  }
});

function scrollToBottom() {
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function showError(message) {
  errorBanner.classList.remove("notice");
  errorBanner.textContent = message;
  errorBanner.classList.remove("hidden");
  console.error("[chat] error:", message);
}

function showNotice(message) {
  errorBanner.classList.add("notice");
  errorBanner.textContent = message;
  errorBanner.classList.remove("hidden");
}

function clearError() {
  errorBanner.classList.add("hidden");
  errorBanner.classList.remove("notice");
  errorBanner.textContent = "";
}

function setBusy(isBusy) {
  messageInput.disabled = isBusy;
  // ตอนกำลังตอบ: สลับปุ่ม "ส่ง" เป็นปุ่ม "หยุด" แทนที่จะปิดการใช้งานเฉยๆ
  // ผู้ใช้จึงมีทางเลือกที่จะยกเลิกการรอได้เสมอ ไม่ใช่แค่ถูกบล็อกอย่างเดียว
  sendButton.classList.toggle("hidden", isBusy);
  stopButton.classList.toggle("hidden", !isBusy);
  loadingIndicator.classList.toggle("hidden", !isBusy);
}

async function sendMessage(message) {
  clearError();
  appendUserMessage(message);
  setBusy(true);

  activeAbortController = new AbortController();
  const { wrapper: aiWrapper, bubble: aiBubble, body: aiBody } = createAiBubble();
  let accumulatedText = "";
  let receivedDone = false;
  let wasAborted = false;

  try {
    const response = await fetch("/api/v1/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode: currentMode, model: currentModel() }),
      signal: activeAbortController.signal,
    });

    if (!response.ok || !response.body) {
      throw new Error(`เซิร์ฟเวอร์ตอบกลับด้วยสถานะ ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();

      for (const rawEvent of events) {
        const dataLine = rawEvent
          .split("\n")
          .find((line) => line.startsWith("data:"));
        if (!dataLine) continue;

        let payload;
        try {
          payload = JSON.parse(dataLine.slice("data:".length).trim());
        } catch (err) {
          console.warn("[chat] ข้าม event ที่ parse ไม่ได้:", rawEvent);
          continue;
        }

        if (Array.isArray(payload.sources)) {
          renderSources(aiBody, payload.sources);
          scrollToBottom();
        } else if (payload.delta) {
          accumulatedText += payload.delta;
          setBubbleContent(aiBubble, accumulatedText);
          scrollToBottom();
        } else if (payload.error) {
          showError(payload.message || "เกิดข้อผิดพลาดที่ไม่ทราบสาเหตุ");
        } else if (payload.done) {
          receivedDone = true;
          console.log("[chat] stream done");
        }
      }
    }

    if (!receivedDone) {
      showError("การเชื่อมต่อถูกตัดกลางทาง กรุณาลองใหม่อีกครั้ง");
    }

    if (!accumulatedText) {
      aiWrapper.remove();
    }
  } catch (err) {
    if (err.name === "AbortError") {
      // ผู้ใช้กด "หยุด" เอง ไม่ถือเป็น error ของระบบ — เก็บข้อความบางส่วนที่ได้มาแล้วไว้
      // (ไม่ลบทิ้ง) แล้วแจ้งเป็น notice เฉยๆ แทน error banner สีแดง
      wasAborted = true;
      console.log("[chat] ผู้ใช้กดหยุดการตอบกลับ");
      showNotice("หยุดการตอบกลับแล้ว");
      if (!accumulatedText) {
        aiWrapper.remove();
      }
    } else {
      console.error("[chat] sendMessage failed:", err);
      showError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ กรุณาตรวจสอบว่า backend และผู้ให้บริการ AI เปิดอยู่");
      if (!accumulatedText) {
        aiWrapper.remove();
      }
    }
  } finally {
    activeAbortController = null;
    setBusy(false);
    messageInput.focus();
  }
}

async function resetConversation() {
  newChatButton.disabled = true;
  try {
    const response = await fetch("/api/v1/chat/reset", { method: "POST" });
    if (!response.ok) {
      throw new Error(`เซิร์ฟเวอร์ตอบกลับด้วยสถานะ ${response.status}`);
    }
    // ล้างเฉพาะบับเบิลข้อความ ไม่แตะ empty-state / loading-indicator ที่เป็นโครงถาวร
    chatWindow.querySelectorAll(".message").forEach((el) => el.remove());
    clearError();
    showEmptyState();
    messageInput.value = "";
    messageInput.focus();
  } catch (err) {
    console.error("[chat] resetConversation failed:", err);
    showError("เริ่มบทสนทนาใหม่ไม่สำเร็จ กรุณาลองอีกครั้ง");
  } finally {
    newChatButton.disabled = false;
  }
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  messageInput.value = "";
  sendMessage(message);
});

stopButton.addEventListener("click", () => {
  activeAbortController?.abort();
});

newChatButton.addEventListener("click", () => {
  resetConversation();
});

loadRagStatus();
initModelPicker();

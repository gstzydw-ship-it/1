const heroBadges = document.querySelector("#hero-badges");
const heroNote = document.querySelector("#hero-note");
const statusGrid = document.querySelector("#status-grid");
const recentVideos = document.querySelector("#recent-videos");
const taskGlance = document.querySelector("#task-glance");
const commandList = document.querySelector("#command-list");
const outputLog = document.querySelector("#output-log");
const conversationFeed = document.querySelector("#conversation-feed");
const liveStatus = document.querySelector("#live-status");
const refreshButton = document.querySelector("#refresh-button");
const dashboardButton = document.querySelector("#dashboard-button");
const copyDashboardButton = document.querySelector("#copy-dashboard-button");
const dashboardHint = document.querySelector("#dashboard-hint");
const agentForm = document.querySelector("#agent-form");
const agentMessage = document.querySelector("#agent-message");
const agentSubmit = document.querySelector("#agent-submit");
const agentStatusLine = document.querySelector("#agent-status-line");

const appState = {
  latestStatus: null,
  watchedTaskId: null,
  optimisticTask: null,
  pollTimer: null,
};

const actionTitles = {
  doctor: "项目体检",
  test_asset_planner: "测试素材规划",
  test_prompt_composer: "测试提示词组装",
  open_outputs: "打开输出目录",
  openclaw_status: "查看 OpenClaw 状态",
  open_dashboard: "打开官方页",
  copy_dashboard_url: "复制令牌链接",
  agent_prompt: "Agent 回复",
};

const taskStatusLabel = {
  queued: "已提交",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isTerminalTask(task) {
  return task?.status === "succeeded" || task?.status === "failed";
}

function summarizeText(text, limit = 180) {
  const cleaned = String(text || "").replace(/\s+/g, " ").trim();
  if (cleaned.length <= limit) {
    return cleaned;
  }
  return `${cleaned.slice(0, limit - 3).trimEnd()}...`;
}

function formatSeconds(value) {
  const total = Number(value) || 0;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (!minutes) {
    return `${seconds} 秒`;
  }
  if (minutes < 60) {
    return `${minutes} 分 ${seconds} 秒`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

function taskTone(task) {
  if (!task) {
    return "neutral";
  }
  if (task.status === "failed") {
    return "warn";
  }
  if (task.status === "succeeded") {
    return "ok";
  }
  if (task.status === "running" || task.status === "queued") {
    return "running";
  }
  return "neutral";
}

function formatLogPayload(payload) {
  if (typeof payload === "string") {
    return payload;
  }

  if (payload?.task) {
    const task = payload.task;
    const sections = [
      `任务编号:\n${task.id || "-"}`,
      `当前状态:\n${task.status_label || task.status || "-"}`,
      `任务内容:\n${task.message || ""}`,
    ];
    if (task.stage) {
      sections.push(`当前阶段:\n${task.stage}`);
    }
    if (task.reply_text) {
      sections.push(`最终回复:\n${task.reply_text}`);
    } else if (task.summary) {
      sections.push(`当前摘要:\n${task.summary}`);
    }
    if (task.ok === false && task.stderr) {
      sections.push(`附加信息:\n${task.stderr}`);
    }
    return sections.join("\n\n");
  }

  const sections = [];
  if (payload?.message) {
    sections.push(payload.message);
  }
  if (payload?.command?.length) {
    sections.push(`执行命令:\n${payload.command.join(" ")}`);
  }
  if (payload?.stdout?.trim()) {
    sections.push(`标准输出:\n${payload.stdout.trim()}`);
  }
  if (payload?.stderr?.trim()) {
    sections.push(`标准错误:\n${payload.stderr.trim()}`);
  }
  if (!sections.length) {
    sections.push(JSON.stringify(payload, null, 2));
  }
  return sections.join("\n\n");
}

function setOutput(title, payload) {
  outputLog.textContent = `${title}\n\n${formatLogPayload(payload)}`.trim();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error || `请求失败 (${response.status})`);
  }
  return payload;
}

async function fetchStatus() {
  return fetchJson("/api/status", { method: "GET" });
}

async function fetchTask(taskId) {
  return fetchJson(`/api/task/${encodeURIComponent(taskId)}`, { method: "GET" });
}

function normalizeTask(task, fallbackId) {
  const status =
    task?.status ||
    (task?.ok === false ? "failed" : task?.ok === true ? "succeeded" : "queued");

  const replyText = String(task?.reply_text || task?.reply_preview || task?.summary || "").trim();

  return {
    id: task?.id || fallbackId,
    message: String(task?.message || "").trim(),
    status,
    status_label: task?.status_label || taskStatusLabel[status] || "未知",
    created_at: task?.created_at || "",
    started_at: task?.started_at || "",
    ended_at: task?.ended_at || "",
    stage: task?.stage || "",
    summary: String(task?.summary || "").trim(),
    reply_text: replyText,
    reply_preview: String(task?.reply_preview || "").trim(),
    ok: task?.ok,
    returncode: task?.returncode,
    elapsed_seconds: Number(task?.elapsed_seconds) || 0,
  };
}

function getTimelineTasks(data) {
  const ordered = [];
  const indexById = new Map();
  const recent = [...(data?.recent_tasks || [])].reverse();

  recent.forEach((task, index) => {
    const normalized = normalizeTask(task, `recent-${index}`);
    indexById.set(normalized.id, ordered.length);
    ordered.push(normalized);
  });

  (data?.active_tasks || []).forEach((task, index) => {
    const normalized = normalizeTask(task, `active-${index}`);
    if (indexById.has(normalized.id)) {
      ordered[indexById.get(normalized.id)] = normalized;
      return;
    }
    indexById.set(normalized.id, ordered.length);
    ordered.push(normalized);
  });

  if (appState.optimisticTask) {
    const optimistic = normalizeTask(appState.optimisticTask, appState.optimisticTask.id || "optimistic");
    if (indexById.has(optimistic.id)) {
      const current = ordered[indexById.get(optimistic.id)];
      if (!isTerminalTask(current)) {
        ordered[indexById.get(optimistic.id)] = optimistic;
      }
    } else {
      ordered.push(optimistic);
    }
  }

  return ordered.sort((left, right) => {
    const leftKey = left.created_at || left.started_at || left.ended_at || left.id;
    const rightKey = right.created_at || right.started_at || right.ended_at || right.id;
    return leftKey.localeCompare(rightKey);
  });
}

function getCurrentTask(data) {
  const activeTasks = data?.active_tasks || [];
  const watched = activeTasks.find((task) => task.id === appState.watchedTaskId);
  const source = watched || activeTasks[0] || data?.latest_reply_task || null;
  return source ? normalizeTask(source, "latest") : null;
}

function renderHero(data) {
  const tasks = getTimelineTasks(data);
  const latest = tasks.at(-1);

  const badges = [
    { label: "OpenClaw", value: data.openclaw.installed ? data.openclaw.version : "未安装" },
    { label: "Agent", value: data.openclaw.agent_id },
    { label: "网关", value: data.openclaw.gateway_running ? "运行中" : "未运行" },
    { label: "最近状态", value: latest?.status_label || "等待会话" },
  ];

  heroBadges.innerHTML = badges
    .map(
      (badge) => `
        <span class="badge">
          <span class="badge-label">${escapeHtml(badge.label)}</span>
          <span class="badge-value">${escapeHtml(badge.value)}</span>
        </span>
      `,
    )
    .join("");

  if (data.openclaw.gateway_running) {
    heroNote.textContent = "网关正常运行。你现在可以像聊天一样连续给 OpenClaw 下发任务，回复会直接留在右侧会话里。";
    dashboardButton.disabled = false;
    copyDashboardButton.disabled = false;
    dashboardHint.textContent = "官方 Dashboard 仍可能断线。日常用这页就够了，需要时再复制令牌链接。";
    return;
  }

  heroNote.textContent = "网关当前未运行。先点右侧刷新或检查状态，确认服务起来后再发任务。";
  dashboardButton.disabled = true;
  copyDashboardButton.disabled = true;
  dashboardHint.textContent = "网关未运行时，不建议尝试打开官方 Dashboard。";
}

function renderStatusCards(data) {
  const dirtyCount = Array.isArray(data.git.status) ? data.git.status.length : 0;
  const cards = [
    {
      label: "连接",
      value: data.openclaw.gateway_running ? "运行中" : "未运行",
      meta: data.openclaw.gateway_running ? "本地对话台可用" : "先刷新或重启服务",
      tone: data.openclaw.gateway_running ? "ok" : "warn",
    },
    {
      label: "官方页",
      value: data.openclaw.dashboard_direct_accessible ? "可直连" : "不稳定",
      meta: data.openclaw.dashboard_direct_accessible ? "能打开，但仍建议用本地页" : "容易断线，仅作辅助入口",
      tone: data.openclaw.dashboard_direct_accessible ? "neutral" : "warn",
    },
    {
      label: "工作区",
      value: ".openclaw-workspace",
      meta: summarizeText(data.workspace_path, 56),
      tone: "neutral",
    },
    {
      label: "Git",
      value: data.git.branch,
      meta: dirtyCount ? `有 ${dirtyCount} 个未提交变更` : "工作区干净",
      tone: dirtyCount ? "warn" : "ok",
    },
  ];

  statusGrid.innerHTML = cards
    .map(
      (card) => `
        <article class="status-card status-${card.tone}">
          <p class="status-label">${escapeHtml(card.label)}</p>
          <p class="status-value">${escapeHtml(card.value)}</p>
          <p class="status-meta">${escapeHtml(card.meta)}</p>
        </article>
      `,
    )
    .join("");
}

function renderVideos(items) {
  if (!items?.length) {
    recentVideos.innerHTML = `
      <div class="mini-item empty">
        <div class="mini-title">还没有新视频</div>
        <div class="mini-meta">等工作流跑完后，这里会更新最近输出。</div>
      </div>
    `;
    return;
  }

  recentVideos.innerHTML = items
    .slice(0, 4)
    .map(
      (item) => `
        <article class="mini-item">
          <div class="mini-title">${escapeHtml(item.name)}</div>
          <div class="mini-meta">${escapeHtml(item.modified)} · ${escapeHtml(item.size_mb)} MB</div>
        </article>
      `,
    )
    .join("");
}

function renderTaskGlance(tasks) {
  if (!tasks.length) {
    taskGlance.innerHTML = `
      <div class="mini-item empty">
        <div class="mini-title">还没有会话记录</div>
        <div class="mini-meta">右侧发出第一条任务后，这里会显示最近几条摘要。</div>
      </div>
    `;
    return;
  }

  taskGlance.innerHTML = [...tasks]
    .slice(-4)
    .reverse()
    .map(
      (task) => `
        <article class="mini-item">
          <div class="mini-row">
            <div class="mini-title">${escapeHtml(summarizeText(task.message, 40))}</div>
            <span class="mini-pill tone-${taskTone(task)}">${escapeHtml(task.status_label)}</span>
          </div>
          <div class="mini-meta">${escapeHtml(task.ended_at || task.created_at || "")}</div>
        </article>
      `,
    )
    .join("");
}

function buildAssistantBody(task) {
  if (task.status === "failed") {
    return task.summary || "任务失败了，但没有拿到更详细的信息。";
  }
  if (task.status === "queued" || task.status === "running") {
    return `${task.stage || "OpenClaw 正在思考中。"} 已运行 ${formatSeconds(task.elapsed_seconds)}。`;
  }
  return task.reply_text || task.summary || "任务完成了，但没有捕获到回复正文。";
}

function renderConversation(tasks) {
  if (!tasks.length) {
    conversationFeed.innerHTML = `
      <article class="message-row assistant">
        <div class="message-avatar">OC</div>
        <div class="message-card">
          <div class="message-meta">
            <span class="speaker">OpenClaw</span>
            <span class="message-time">准备就绪</span>
          </div>
          <p class="message-text">直接在底部输入框里说你想让它做什么。发出任务后，这里会像聊天一样显示提问、处理中和最终回复。</p>
        </div>
      </article>
    `;
    return;
  }

  const rows = [];
  for (const task of tasks) {
    rows.push(`
      <article class="message-row user">
        <div class="message-avatar">你</div>
        <div class="message-card user-card">
          <div class="message-meta">
            <span class="speaker">你</span>
            <span class="message-time">${escapeHtml(task.created_at || "刚刚")}</span>
          </div>
          <p class="message-text">${escapeHtml(task.message || "")}</p>
          <div class="message-tools">
            <button class="tiny-button" type="button" data-fill-task="${encodeURIComponent(task.message || "")}">放回输入框</button>
            <button class="tiny-button ghost" type="button" data-rerun-task="${encodeURIComponent(task.message || "")}">重发这条</button>
          </div>
        </div>
      </article>
    `);

    rows.push(`
      <article class="message-row assistant">
        <div class="message-avatar">OC</div>
        <div class="message-card assistant-card tone-${taskTone(task)}">
          <div class="message-meta">
            <span class="speaker">OpenClaw</span>
            <span class="message-time">${escapeHtml(task.ended_at || task.created_at || "处理中")}</span>
          </div>
          <div class="assistant-head">
            <span class="message-pill tone-${taskTone(task)}">${escapeHtml(task.status_label)}</span>
            <span class="assistant-stage">${escapeHtml(task.stage || "")}</span>
          </div>
          ${
            task.status === "queued" || task.status === "running"
              ? `
                <div class="thinking-block">
                  <span class="thinking-dot"></span>
                  <span class="thinking-dot"></span>
                  <span class="thinking-dot"></span>
                </div>
              `
              : ""
          }
          <p class="message-text">${escapeHtml(buildAssistantBody(task))}</p>
        </div>
      </article>
    `);
  }

  conversationFeed.innerHTML = rows.join("");

  for (const button of conversationFeed.querySelectorAll("[data-fill-task]")) {
    button.addEventListener("click", () => {
      agentMessage.value = decodeURIComponent(button.dataset.fillTask || "");
      agentMessage.focus();
    });
  }

  for (const button of conversationFeed.querySelectorAll("[data-rerun-task]")) {
    button.addEventListener("click", async () => {
      const message = decodeURIComponent(button.dataset.rerunTask || "");
      await sendAgentMessage(message);
    });
  }

  conversationFeed.scrollTop = conversationFeed.scrollHeight;
}

function renderLiveStatus(task) {
  if (!task || !task.id) {
    liveStatus.innerHTML = `
      <div class="live-strip idle">
        <span class="live-label">等待任务</span>
        <span class="live-copy">右下角发出一条消息后，这里会显示实时状态。</span>
      </div>
    `;
    return;
  }

  liveStatus.innerHTML = `
    <div class="live-strip tone-${taskTone(task)}">
      <span class="live-label">${escapeHtml(task.status_label)}</span>
      <span class="live-copy">任务 ${escapeHtml(task.id)} · ${escapeHtml(task.stage || "等待状态更新")} · ${escapeHtml(formatSeconds(task.elapsed_seconds))}</span>
    </div>
  `;
}

function renderCommands(items) {
  commandList.innerHTML = (items || [])
    .map(
      (item) => `
        <div class="command-item">
          <div class="command-text">${escapeHtml(item)}</div>
          <button class="tiny-button ghost" type="button" data-copy="${escapeHtml(item)}">复制</button>
        </div>
      `,
    )
    .join("");

  for (const button of commandList.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      button.textContent = "已复制";
      window.setTimeout(() => {
        button.textContent = "复制";
      }, 1200);
    });
  }
}

function updateAgentStatusLine(task) {
  if (!task || !task.id) {
    agentStatusLine.textContent = "提交后会像聊天一样显示“你发了什么”和“OpenClaw 回了什么”。";
    return;
  }

  agentStatusLine.textContent = `任务 ${task.id} · ${task.status_label} · ${task.stage || "等待状态更新"}`;
}

function renderAll(data, options = {}) {
  const preserveOutput = Boolean(options.preserveOutput);
  const timelineTasks = getTimelineTasks(data);
  const currentTask = getCurrentTask(data);

  renderHero(data);
  renderStatusCards(data);
  renderVideos(data.recent_videos || []);
  renderTaskGlance(timelineTasks);
  renderConversation(timelineTasks);
  renderLiveStatus(currentTask);
  renderCommands(data.commands || []);
  updateAgentStatusLine(currentTask);

  if (!preserveOutput) {
    setOutput("状态总览", data);
  }
}

function clearPollTimer() {
  if (appState.pollTimer) {
    window.clearTimeout(appState.pollTimer);
    appState.pollTimer = null;
  }
}

function scheduleTaskPoll(taskId, delay = 1400) {
  clearPollTimer();
  appState.pollTimer = window.setTimeout(() => pollTask(taskId), delay);
}

async function pollTask(taskId) {
  if (!taskId) {
    return;
  }

  try {
    const payload = await fetchTask(taskId);
    const task = normalizeTask(payload.task, taskId);
    appState.optimisticTask = task;
    appState.watchedTaskId = task.id;

    if (appState.latestStatus) {
      const merged = {
        ...appState.latestStatus,
        active_tasks: isTerminalTask(task) ? [] : [task],
        latest_reply_task: isTerminalTask(task) ? task : appState.latestStatus.latest_reply_task,
      };
      renderAll(merged, { preserveOutput: true });
    }

    setOutput(isTerminalTask(task) ? "Agent 回复" : "任务进行中", { task });

    if (isTerminalTask(task)) {
      appState.optimisticTask = task;
      appState.watchedTaskId = null;
      clearPollTimer();
      await refresh({ preserveOutput: true });
      return;
    }

    scheduleTaskPoll(task.id);
  } catch (error) {
    setOutput("任务轮询失败", String(error));
    clearPollTimer();
  }
}

async function refresh(options = {}) {
  const preserveOutput = Boolean(options.preserveOutput);
  refreshButton.disabled = true;
  refreshButton.textContent = "刷新中...";

  try {
    const data = await fetchStatus();
    appState.latestStatus = data;

    if (data.active_tasks?.length) {
      const activeTask = data.active_tasks.find((item) => item.id === appState.watchedTaskId) || data.active_tasks[0];
      appState.watchedTaskId = activeTask?.id || null;
      scheduleTaskPoll(appState.watchedTaskId, 1200);
    } else {
      clearPollTimer();
    }

    renderAll(data, { preserveOutput });
  } catch (error) {
    setOutput("状态加载失败", String(error));
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新";
  }
}

async function runAction(action) {
  const title = actionTitles[action] || action;
  setOutput(`正在执行：${title}`, { action });
  try {
    const payload = await fetchJson("/api/action", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    setOutput(title, payload);
    await refresh({ preserveOutput: true });
  } catch (error) {
    setOutput(`${title} 失败`, String(error));
  }
}

async function sendAgentMessage(message) {
  const text = String(message || "").trim();
  if (!text) {
    agentMessage.focus();
    return;
  }

  agentSubmit.disabled = true;
  agentSubmit.textContent = "发送中...";
  setOutput("任务提交中", { message: text });

  try {
    const payload = await fetchJson("/api/agent", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    const task = normalizeTask(payload.task, `pending-${Date.now()}`);
    appState.optimisticTask = task;
    appState.watchedTaskId = task.id;

    if (appState.latestStatus) {
      const snapshot = {
        ...appState.latestStatus,
        active_tasks: [task, ...(appState.latestStatus.active_tasks || [])],
      };
      renderAll(snapshot, { preserveOutput: true });
    }

    setOutput("任务已提交", { task });
    agentSubmit.disabled = false;
    agentSubmit.textContent = "发送任务";
    scheduleTaskPoll(task.id, 900);
  } catch (error) {
    agentSubmit.disabled = false;
    agentSubmit.textContent = "发送任务";
    setOutput("任务提交失败", String(error));
  }
}

for (const button of document.querySelectorAll(".action")) {
  button.addEventListener("click", () => runAction(button.dataset.action));
}

for (const chip of document.querySelectorAll("[data-template]")) {
  chip.addEventListener("click", () => {
    agentMessage.value = chip.dataset.template || "";
    agentMessage.focus();
  });
}

dashboardButton.addEventListener("click", async () => {
  if (dashboardButton.disabled) {
    setOutput("官方 Dashboard", "当前网关未运行，先刷新状态并确认 OpenClaw 服务正常。");
    return;
  }
  await runAction("open_dashboard");
});

copyDashboardButton.addEventListener("click", async () => {
  if (copyDashboardButton.disabled) {
    setOutput("复制令牌链接", "当前网关未运行，暂时不建议同步 Dashboard 链接。");
    return;
  }
  await runAction("copy_dashboard_url");
});

refreshButton.addEventListener("click", () => refresh());

agentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendAgentMessage(agentMessage.value);
});

refresh();

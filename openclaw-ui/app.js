const heroBadges = document.querySelector("#hero-badges");
const heroNote = document.querySelector("#hero-note");
const statusGrid = document.querySelector("#status-grid");
const currentTask = document.querySelector("#current-task");
const latestReply = document.querySelector("#latest-reply");
const recentTasks = document.querySelector("#recent-tasks");
const recentVideos = document.querySelector("#recent-videos");
const commandList = document.querySelector("#command-list");
const outputLog = document.querySelector("#output-log");
const refreshButton = document.querySelector("#refresh-button");
const dashboardButton = document.querySelector("#dashboard-button");
const copyDashboardButton = document.querySelector("#copy-dashboard-button");
const dashboardHint = document.querySelector("#dashboard-hint");
const agentForm = document.querySelector("#agent-form");
const agentMessage = document.querySelector("#agent-message");
const agentSubmit = document.querySelector("#agent-submit");
const agentStatusLine = document.querySelector("#agent-status-line");

const appState = {
  watchedTaskId: null,
  pollTimer: null,
  latestStatus: null,
};

const actionTitles = {
  doctor: "项目体检",
  test_asset_planner: "测试素材规划",
  test_prompt_composer: "测试提示词组装",
  open_outputs: "打开视频输出目录",
  openclaw_status: "查看 OpenClaw 状态",
  open_dashboard: "辅助打开官方 Dashboard",
  copy_dashboard_url: "复制带令牌链接",
  agent_prompt: "Agent 回复",
};

const taskStatusTone = {
  queued: "neutral",
  running: "running",
  succeeded: "ok",
  failed: "warn",
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

function summarizeForUi(text, limit = 220) {
  const cleaned = String(text || "").replace(/\s+/g, " ").trim();
  if (cleaned.length <= limit) {
    return cleaned;
  }
  return `${cleaned.slice(0, limit - 3).trimEnd()}...`;
}

function pickReplyText(task) {
  if (!task) {
    return "";
  }
  return String(task.reply_text || task.reply_preview || task.summary || "").trim();
}

function taskToneClass(task) {
  return `tone-${taskStatusTone[task?.status] || "neutral"}`;
}

function renderEmptyBlock(target, title, detail) {
  target.innerHTML = `
    <div class="empty-state block-empty">
      <div class="empty-title">${escapeHtml(title)}</div>
      <div class="empty-detail">${escapeHtml(detail)}</div>
    </div>
  `;
}

function formatLogPayload(payload) {
  if (typeof payload === "string") {
    return payload;
  }

  if (payload?.task) {
    const task = payload.task;
    const sections = [
      `任务编号:\n${task.id}`,
      `当前状态:\n${task.status_label || task.status}`,
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

function renderHero(data) {
  const badges = [
    { label: "OpenClaw", value: data.openclaw.installed ? data.openclaw.version : "未安装" },
    { label: "Agent", value: data.openclaw.agent_id },
    { label: "网关", value: data.openclaw.gateway_running ? "运行中" : "未运行" },
    { label: "主入口", value: "本地任务中心" },
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
    heroNote.textContent = "OpenClaw 网关正常运行。你现在可以在下面直接发任务，页面会显示状态变化和最终回复。";
    dashboardButton.disabled = false;
    copyDashboardButton.disabled = false;
    dashboardHint.textContent = "官方 Dashboard 可能断线或报 token 错误。日常发任务请优先使用本地任务中心。";
    return;
  }

  heroNote.textContent = "网关当前未运行。先点“查看 OpenClaw 状态”或“刷新状态”，确认服务起来后再发任务。";
  dashboardButton.disabled = true;
  copyDashboardButton.disabled = true;
  dashboardHint.textContent = "网关未运行时，不建议尝试打开官方 Dashboard。";
}

function renderStatuses(data) {
  const dirtyCount = Array.isArray(data.git.status) ? data.git.status.length : 0;
  const cards = [
    {
      label: "OpenClaw 版本",
      value: data.openclaw.installed ? data.openclaw.version : "未安装",
      meta: data.openclaw.installed ? `Agent：${data.openclaw.agent_id}` : "WSL 中尚未检测到 OpenClaw",
      tone: data.openclaw.installed ? "ok" : "warn",
    },
    {
      label: "网关状态",
      value: data.openclaw.gateway_running ? "运行中" : "未运行",
      meta: data.openclaw.gateway_running ? "可以直接下发任务" : "先检查 OpenClaw 状态",
      tone: data.openclaw.gateway_running ? "ok" : "warn",
    },
    {
      label: "官方 Dashboard",
      value: data.openclaw.dashboard_direct_accessible ? "可直连" : "不稳定",
      meta: data.openclaw.dashboard_direct_accessible
        ? `${data.openclaw.dashboard_url} | 仍建议优先用本地任务中心`
        : "这台机器上官方页面 websocket 不稳定，建议把它当辅助入口。",
      tone: data.openclaw.dashboard_direct_accessible ? "neutral" : "warn",
    },
    {
      label: "工作区",
      value: ".openclaw-workspace",
      meta: data.workspace_path,
      tone: "neutral",
    },
    {
      label: "Git 分支",
      value: data.git.branch,
      meta: `备份分支：${data.git.backup_branch} | 未提交文件：${dirtyCount}`,
      tone: dirtyCount ? "warn" : "ok",
    },
  ];

  statusGrid.innerHTML = cards
    .map(
      (card) => `
        <article class="status-card status-${escapeHtml(card.tone)}">
          <p class="status-label">${escapeHtml(card.label)}</p>
          <p class="status-value">${escapeHtml(card.value)}</p>
          <p class="status-meta">${escapeHtml(card.meta)}</p>
        </article>
      `,
    )
    .join("");
}

function renderCurrentTask(task) {
  if (!task) {
    renderEmptyBlock(currentTask, "当前没有任务在跑", "发布一个任务后，这里会实时显示任务编号、阶段、耗时和当前状态。");
    return;
  }

  const replyHint = task.reply_text
    ? `<div class="task-snippet"><strong>回复预览：</strong>${escapeHtml(summarizeForUi(task.reply_text, 260))}</div>`
    : "";

  currentTask.innerHTML = `
    <article class="live-card ${taskToneClass(task)}">
      <div class="live-head">
        <div>
          <p class="live-kicker">任务编号</p>
          <h3>${escapeHtml(task.id)}</h3>
        </div>
        <span class="status-pill ${taskToneClass(task)}">${escapeHtml(task.status_label || task.status)}</span>
      </div>
      <p class="live-message">${escapeHtml(task.message || "")}</p>
      <div class="meta-grid">
        <div class="meta-block">
          <span class="meta-label">提交时间</span>
          <span class="meta-value">${escapeHtml(task.created_at || "-")}</span>
        </div>
        <div class="meta-block">
          <span class="meta-label">已运行</span>
          <span class="meta-value">${escapeHtml(formatSeconds(task.elapsed_seconds))}</span>
        </div>
      </div>
      <div class="stage-box">
        <span class="meta-label">当前阶段</span>
        <p>${escapeHtml(task.stage || "等待状态更新")}</p>
      </div>
      ${replyHint}
    </article>
  `;
}

function renderLatestReply(task) {
  if (!task || !pickReplyText(task)) {
    renderEmptyBlock(latestReply, "还没有可展示的回复", "等任务结束后，最终答复会直接出现在这里。");
    return;
  }

  latestReply.innerHTML = `
    <article class="reply-card ${taskToneClass(task)}">
      <div class="reply-head">
        <div>
          <p class="live-kicker">来自任务</p>
          <h3>${escapeHtml(task.id || "最近一次任务")}</h3>
        </div>
        <span class="status-pill ${taskToneClass(task)}">${escapeHtml(task.status_label || task.status || "已完成")}</span>
      </div>
      <p class="reply-meta">任务内容：${escapeHtml(summarizeForUi(task.message || "", 120))}</p>
      <pre class="reply-pre">${escapeHtml(pickReplyText(task))}</pre>
    </article>
  `;
}

function renderTasks(items) {
  if (!items?.length) {
    renderEmptyBlock(recentTasks, "还没有历史任务", "在上面的输入框里发出第一条任务后，这里会自动记录时间、状态和回复摘要。");
    return;
  }

  recentTasks.innerHTML = items
    .map(
      (item) => `
        <article class="task-item ${item.ok ? "task-ok" : "task-warn"}">
          <div class="task-head">
            <p class="task-time">${escapeHtml(item.ended_at || item.created_at || "")}</p>
            <span class="task-badge">${escapeHtml(item.status_label || (item.ok ? "已完成" : "失败"))}</span>
          </div>
          <p class="task-message">${escapeHtml(item.message || "")}</p>
          <p class="task-summary">${escapeHtml(item.summary || "暂无摘要")}</p>
          ${item.reply_text ? `<pre class="task-reply-preview">${escapeHtml(summarizeForUi(item.reply_text, 340))}</pre>` : ""}
          <div class="task-actions">
            <button class="mini-button" type="button" data-fill-task="${encodeURIComponent(item.message || "")}">回填到输入框</button>
            <button class="mini-button ghost-mini" type="button" data-rerun-task="${encodeURIComponent(item.message || "")}">立即重发</button>
            ${
              item.reply_text
                ? `<button class="mini-button ghost-mini" type="button" data-show-reply="${encodeURIComponent(item.id || "")}">查看完整回复</button>`
                : ""
            }
          </div>
        </article>
      `,
    )
    .join("");

  for (const button of recentTasks.querySelectorAll("[data-fill-task]")) {
    button.addEventListener("click", () => {
      agentMessage.value = decodeURIComponent(button.dataset.fillTask || "");
      agentMessage.focus();
      agentMessage.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  for (const button of recentTasks.querySelectorAll("[data-rerun-task]")) {
    button.addEventListener("click", async () => {
      const message = decodeURIComponent(button.dataset.rerunTask || "");
      await sendAgentMessage(message);
    });
  }

  for (const button of recentTasks.querySelectorAll("[data-show-reply]")) {
    button.addEventListener("click", () => {
      const taskId = decodeURIComponent(button.dataset.showReply || "");
      const selected = items.find((item) => item.id === taskId);
      if (selected) {
        renderLatestReply(selected);
        setOutput("历史任务回复", { task: selected });
        latestReply.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
  }
}

function renderVideos(items) {
  if (!items?.length) {
    renderEmptyBlock(recentVideos, "暂时还没有视频输出", "先跑工作流，生成完成后再刷新这里。");
    return;
  }

  recentVideos.innerHTML = items
    .map(
      (item) => `
        <div class="video-item">
          <div class="video-name">${escapeHtml(item.name)}</div>
          <div class="video-meta">${escapeHtml(item.modified)} | ${escapeHtml(item.size_mb)} MB</div>
          <div class="video-meta">${escapeHtml(item.path)}</div>
        </div>
      `,
    )
    .join("");
}

function renderCommands(items) {
  commandList.innerHTML = (items || [])
    .map(
      (item) => `
        <div class="command-item">
          <div class="command-row">
            <div class="command-text">${escapeHtml(item)}</div>
            <button class="mini-button" data-copy="${escapeHtml(item)}">复制</button>
          </div>
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
  if (!task) {
    agentStatusLine.textContent = "建议把“做什么、读哪里、输出什么”写清楚。提交后会立刻显示“已提交 / 运行中 / 已完成”。";
    return;
  }

  const bits = [
    `任务 ${task.id}`,
    `${task.status_label || task.status}`,
    task.stage || "",
  ].filter(Boolean);
  agentStatusLine.textContent = bits.join(" | ");
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
    const task = payload.task;
    appState.watchedTaskId = task.id;
    renderCurrentTask(task);
    updateAgentStatusLine(task);

    if (isTerminalTask(task)) {
      renderLatestReply(task);
      setOutput("Agent 回复", { task });
      await refresh({ preserveOutput: true });
      clearPollTimer();
      appState.watchedTaskId = null;
      return;
    }

    setOutput("任务进行中", { task });
    scheduleTaskPoll(taskId);
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
    renderHero(data);
    renderStatuses(data);
    renderTasks(data.recent_tasks);
    renderVideos(data.recent_videos);
    renderCommands(data.commands);

    const active = data.active_tasks?.find((item) => item.id === appState.watchedTaskId) || data.active_tasks?.[0] || null;
    const currentDisplayTask = active || data.latest_reply_task || null;
    renderCurrentTask(currentDisplayTask);
    renderLatestReply(data.latest_reply_task);
    updateAgentStatusLine(currentDisplayTask);

    if (active && !isTerminalTask(active)) {
      appState.watchedTaskId = active.id;
      scheduleTaskPoll(active.id, 1000);
    }

    if (!preserveOutput) {
      setOutput("状态总览", data);
    }
  } catch (error) {
    setOutput("状态加载失败", String(error));
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新状态";
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
  agentSubmit.textContent = "任务已提交...";
  setOutput("任务提交中", { message: text });

  try {
    const payload = await fetchJson("/api/agent", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    const task = payload.task;
    appState.watchedTaskId = task.id;
    renderCurrentTask(task);
    updateAgentStatusLine(task);
    setOutput("任务已提交", { task });
    await refresh({ preserveOutput: true });
    scheduleTaskPoll(task.id, 900);
    agentSubmit.disabled = false;
    agentSubmit.textContent = "发送到 video-agent-system";
  } catch (error) {
    agentSubmit.disabled = false;
    agentSubmit.textContent = "发送到 video-agent-system";
    setOutput("任务提交失败", String(error));
  }
}

for (const button of document.querySelectorAll(".action")) {
  button.addEventListener("click", () => runAction(button.dataset.action));
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
    setOutput("复制带令牌链接", "当前网关未运行，暂时不建议同步 Dashboard 链接。");
    return;
  }
  await runAction("copy_dashboard_url");
});

agentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendAgentMessage(agentMessage.value);
});

refreshButton.addEventListener("click", () => refresh());

refresh();

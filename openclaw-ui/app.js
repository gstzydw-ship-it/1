const statusGrid = document.querySelector("#status-grid");
const recentVideos = document.querySelector("#recent-videos");
const commandList = document.querySelector("#command-list");
const outputLog = document.querySelector("#output-log");
const refreshButton = document.querySelector("#refresh-button");
const dashboardLink = document.querySelector("#dashboard-link");
const agentForm = document.querySelector("#agent-form");
const agentMessage = document.querySelector("#agent-message");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setOutput(title, payload) {
  const body = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  outputLog.textContent = `${title}\n\n${body}`.trim();
}

async function fetchStatus() {
  const response = await fetch("/api/status");
  if (!response.ok) {
    throw new Error(`Failed to load status (${response.status})`);
  }
  return response.json();
}

function renderStatuses(data) {
  const dirtyCount = Array.isArray(data.git.status) ? data.git.status.length : 0;
  const cards = [
    {
      label: "OpenClaw",
      value: data.openclaw.installed ? data.openclaw.version : "未安装",
      meta: data.openclaw.installed ? `Agent：${data.openclaw.agent_id}` : "WSL 中的 OpenClaw 尚未就绪",
    },
    {
      label: "网关",
      value: data.openclaw.gateway_reachable ? "在线" : "离线",
      meta: data.openclaw.dashboard_url,
    },
    {
      label: "工作区",
      value: ".openclaw-workspace",
      meta: data.workspace_path,
    },
    {
      label: "Git",
      value: data.git.branch,
      meta: `备份分支：${data.git.backup_branch} | 未提交文件：${dirtyCount}`,
    },
  ];

  statusGrid.innerHTML = cards
    .map(
      (card) => `
        <article class="status-card">
          <p class="status-label">${escapeHtml(card.label)}</p>
          <p class="status-value">${escapeHtml(card.value)}</p>
          <p class="status-meta">${escapeHtml(card.meta)}</p>
        </article>
      `,
    )
    .join("");
}

function renderVideos(items) {
  if (!items.length) {
    recentVideos.innerHTML = `
      <div class="video-item">
        <div class="video-name">暂时还没有视频输出</div>
        <div class="video-meta">先跑工作流，再回来刷新这里。</div>
      </div>
    `;
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
  commandList.innerHTML = items
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
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "已复制";
      window.setTimeout(() => {
        button.textContent = "复制";
      }, 1200);
    });
  }
}

async function refresh() {
  refreshButton.disabled = true;
  refreshButton.textContent = "刷新中...";
  try {
    const data = await fetchStatus();
    dashboardLink.href = data.openclaw.dashboard_url;
    renderStatuses(data);
    renderVideos(data.recent_videos);
    renderCommands(data.commands);
    setOutput("状态", data);
  } catch (error) {
    setOutput("状态加载失败", String(error));
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新状态";
  }
}

async function runAction(action) {
  setOutput("正在执行", { action });
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const payload = await response.json();
  setOutput(payload.title || action, payload);
}

for (const button of document.querySelectorAll(".action")) {
  button.addEventListener("click", () => runAction(button.dataset.action));
}

agentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = agentMessage.value.trim();
  if (!message) {
    return;
  }
  setOutput("Agent 请求", { message });
  const response = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const payload = await response.json();
  setOutput(payload.title || "Agent 回复", payload);
});

refreshButton.addEventListener("click", refresh);
refresh();

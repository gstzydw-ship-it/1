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
      value: data.openclaw.installed ? data.openclaw.version : "missing",
      meta: data.openclaw.installed ? `Agent: ${data.openclaw.agent_id}` : "WSL install not ready",
    },
    {
      label: "Gateway",
      value: data.openclaw.gateway_reachable ? "online" : "offline",
      meta: data.openclaw.dashboard_url,
    },
    {
      label: "Workspace",
      value: ".openclaw-workspace",
      meta: data.workspace_path,
    },
    {
      label: "Git",
      value: data.git.branch,
      meta: `Backup: ${data.git.backup_branch} | Dirty files: ${dirtyCount}`,
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
        <div class="video-name">No recent videos yet</div>
        <div class="video-meta">Run the pipeline and refresh.</div>
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
            <button class="mini-button" data-copy="${escapeHtml(item)}">Copy</button>
          </div>
        </div>
      `,
    )
    .join("");

  for (const button of commandList.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Copied";
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, 1200);
    });
  }
}

async function refresh() {
  refreshButton.disabled = true;
  refreshButton.textContent = "Refreshing...";
  try {
    const data = await fetchStatus();
    dashboardLink.href = data.openclaw.dashboard_url;
    renderStatuses(data);
    renderVideos(data.recent_videos);
    renderCommands(data.commands);
    setOutput("STATUS", data);
  } catch (error) {
    setOutput("STATUS ERROR", String(error));
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "Refresh Status";
  }
}

async function runAction(action) {
  setOutput("RUNNING", { action });
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
  setOutput("AGENT REQUEST", { message });
  const response = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const payload = await response.json();
  setOutput(payload.title || "Agent Reply", payload);
});

refreshButton.addEventListener("click", refresh);
refresh();

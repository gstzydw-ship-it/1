const heroBadges = document.querySelector("#hero-badges");
const heroNote = document.querySelector("#hero-note");
const statusGrid = document.querySelector("#status-grid");
const recentVideos = document.querySelector("#recent-videos");
const commandList = document.querySelector("#command-list");
const outputLog = document.querySelector("#output-log");
const refreshButton = document.querySelector("#refresh-button");
const dashboardButton = document.querySelector("#dashboard-button");
const copyDashboardButton = document.querySelector("#copy-dashboard-button");
const dashboardHint = document.querySelector("#dashboard-hint");
const agentForm = document.querySelector("#agent-form");
const agentMessage = document.querySelector("#agent-message");

const actionTitles = {
  doctor: "项目体检",
  test_asset_planner: "测试素材规划",
  test_prompt_composer: "测试提示词组装",
  open_outputs: "打开视频输出目录",
  openclaw_status: "查看 OpenClaw 状态",
  open_dashboard: "官方 Dashboard",
  copy_dashboard_url: "复制带令牌链接",
  agent_prompt: "Agent 回复",
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatLogPayload(payload) {
  if (typeof payload === "string") {
    return payload;
  }

  if (payload?.action === "agent_prompt" && Array.isArray(payload.payload?.payloads)) {
    const texts = payload.payload.payloads
      .map((item) => item?.text?.trim())
      .filter(Boolean);
    const sections = [];

    if (texts.length) {
      sections.push(`回复内容：\n${texts.join("\n\n")}`);
    }
    if (payload.command?.length) {
      sections.push(`执行命令：\n${payload.command.join(" ")}`);
    }
    if (payload.stderr?.trim()) {
      sections.push(`附加信息：\n${payload.stderr.trim()}`);
    }
    return sections.join("\n\n");
  }

  const sections = [];
  if (payload?.action === "open_dashboard") {
    sections.push(
      payload.ok
        ? "已尝试在默认浏览器中用当前令牌打开官方 Dashboard，并把链接复制到了剪贴板。"
        : "官方 Dashboard 打开失败。先点“复制带令牌链接”，再用新的链接重开页面。",
    );
  } else if (payload?.action === "copy_dashboard_url") {
    sections.push(
      payload.ok
        ? "已复制带当前令牌的 Dashboard 链接。若页面提示 token mismatch，请用这条新链接重新打开。"
        : "复制 Dashboard 链接失败，请先检查 OpenClaw 状态。",
    );
  } else if (payload?.action === "open_outputs") {
    sections.push("已在资源管理器中打开视频输出目录。");
  } else if (payload?.message) {
    sections.push(payload.message);
  }

  if (payload?.command?.length) {
    sections.push(`执行命令：\n${payload.command.join(" ")}`);
  }
  if (payload?.stdout?.trim()) {
    sections.push(`标准输出：\n${payload.stdout.trim()}`);
  }
  if (payload?.stderr?.trim()) {
    sections.push(`标准错误：\n${payload.stderr.trim()}`);
  }

  if (!sections.length) {
    sections.push(JSON.stringify(payload, null, 2));
  }

  return sections.join("\n\n");
}

function setOutput(title, payload) {
  outputLog.textContent = `${title}\n\n${formatLogPayload(payload)}`.trim();
}

async function fetchStatus() {
  const response = await fetch("/api/status");
  if (!response.ok) {
    throw new Error(`状态加载失败（${response.status}）`);
  }
  return response.json();
}

function renderHero(data) {
  const badges = [
    { label: "OpenClaw", value: data.openclaw.installed ? data.openclaw.version : "未安装" },
    { label: "Agent", value: data.openclaw.agent_id },
    { label: "网关", value: data.openclaw.gateway_running ? "运行中" : "未运行" },
    { label: "Dashboard", value: data.openclaw.dashboard_direct_accessible ? "可直连" : "需重连" },
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
    dashboardButton.disabled = false;
    copyDashboardButton.disabled = false;

    if (data.openclaw.dashboard_direct_accessible) {
      dashboardButton.textContent = "打开官方 Dashboard";
      dashboardHint.textContent = "如果页面仍提示 token mismatch，先点“复制带令牌链接”，再用新链接重新进入。";
      heroNote.textContent = "OpenClaw 网关正常运行。日常操作优先用这个本地控制台，官方 Dashboard 主要用于补充查看。";
      return;
    }

    dashboardButton.textContent = "重新打开官方 Dashboard";
    dashboardHint.textContent = "当前 Windows 侧直连检测不稳定，但你仍可以重开页面；如果报 token mismatch，优先复制带令牌链接。";
    heroNote.textContent = "这不是你操作错了。现在最稳的修法是复制带令牌链接重新进入，或继续直接用本地控制台。";
    return;
  }

  dashboardButton.disabled = true;
  copyDashboardButton.disabled = true;
  dashboardButton.textContent = "官方 Dashboard 暂不可用";
  dashboardHint.textContent = "OpenClaw 网关当前没有运行，先刷新状态或检查服务。";
  heroNote.textContent = "先确认 OpenClaw 网关是否启动，再进行 Agent 调用或 Dashboard 修复。";
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
      label: "网关进程",
      value: data.openclaw.gateway_running ? "运行中" : "未运行",
      meta: data.openclaw.gateway_running ? "WSL 内部已监听 OpenClaw 端口" : "请先运行状态检查确认服务",
      tone: data.openclaw.gateway_running ? "ok" : "warn",
    },
    {
      label: "Dashboard 连接",
      value: data.openclaw.dashboard_direct_accessible ? "可打开" : "建议重连",
      meta: data.openclaw.dashboard_direct_accessible
        ? `${data.openclaw.dashboard_url} | 若提示 token mismatch，可复制带令牌链接重开`
        : "当前 Windows 侧直连不稳定；如果页面已经打开却报 token mismatch，请复制带令牌链接重新进入。",
      tone: data.openclaw.gateway_running ? (data.openclaw.dashboard_direct_accessible ? "ok" : "warn") : "warn",
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

function renderVideos(items) {
  if (!items.length) {
    recentVideos.innerHTML = `
      <div class="video-item empty-state">
        <div class="video-name">暂时还没有视频输出</div>
        <div class="video-meta">先跑工作流，生成完成后再刷新这里。</div>
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
    renderHero(data);
    renderStatuses(data);
    renderVideos(data.recent_videos);
    renderCommands(data.commands);
    setOutput("状态总览", data);
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
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const payload = await response.json();
  setOutput(title, payload);
  if (action === "open_dashboard" || action === "copy_dashboard_url") {
    await refresh();
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
  setOutput(actionTitles.agent_prompt, payload);
});

refreshButton.addEventListener("click", refresh);
refresh();

"use strict";
const $ = id => document.getElementById(id);
const desktop = window.modelBridgeDesktop;
if (desktop) document.documentElement.classList.add('desktop');
const fragment = new URLSearchParams(location.hash.slice(1));
let token = fragment.get("token") || sessionStorage.getItem("bridge-console-token");
if (fragment.has("token")) {
  sessionStorage.setItem("bridge-console-token", token);
  history.replaceState(null, "", location.pathname);
}
const protocolNames = { responses: "OpenAI Responses", chat_completions: "Chat Completions", anthropic: "Anthropic Messages" };
const panels = {
  connection: ["模型连接", "选择服务，连接你的模型。"],
  claude: ["接入 Claude", "将已验证的连接导入 Claude 桌面端。"],
  help: ["状态与帮助", "查看本机服务和客户端请求。"]
};
let state, baseline, hydrated = false, online = false, operation = "", refreshing = false, generation = 0;
let activePanel = "connection", progressTimer, startedAt, toastTimer, cancelling = false;
const defaultModelNote = "可直接填写模型 ID，无需先获取列表。";

function draft() {
  return { model: $("model").value.trim(), connection: {
    id: $("provider").value, name: $("connection-name").value.trim(), base_url: $("base-url").value.trim(),
    protocol: $("protocol").value, auth: $("auth").value
  } };
}
function savedDraft(data) {
  const connection = data.connection || data.presets[0];
  return { model: data.model || connection.model || "", connection: {
    id: connection.id, name: connection.name, base_url: connection.base_url, protocol: connection.protocol, auth: connection.auth
  } };
}
function isDirty() { return hydrated && (JSON.stringify(draft()) !== JSON.stringify(baseline) || !!$("key").value); }
function updateControls() {
  const locked = !!operation || !online || state?.desktop_capabilities?.edit === false;
  document.querySelectorAll("#setup-form input, #setup-form select, #setup-form button").forEach(el => el.disabled = locked);
  $("connect").disabled = locked;
  $("cancel-verify").hidden = operation !== "verify";
  $("cancel-verify").disabled = cancelling;
  $("cancel-verify").textContent = cancelling ? "正在取消…" : "取消验证";
  $("refresh").disabled = !!operation || refreshing;
  $("download-config").disabled = !online || !state?.configured || operation === "verify";
  $("goto-claude").disabled = !state?.configured || operation === "verify";
  $("diagnostics").disabled = !online;
  if (desktop) $("service-stop").disabled = !online || !!operation;
  $("active-caption").textContent = !online && state ? "离线 · 显示上次状态" : "一次启用一个模型";
  if (!online && state?.configured) {
    $("active-badge").textContent = "上次保存";
    $("active-badge").classList.remove("good");
  }
  $("setup-form").setAttribute("aria-busy", String(!!operation));
  $("connect-label").textContent = operation === "verify" ? "正在验证…" : "验证并保存";
  $("load-models").textContent = operation === "models" ? "获取中…" : "获取列表";
  $("verify-spinner").hidden = !operation;
  updateAuth();
  updateDraftState();
  desktop?.workspaceState({ dirty: isDirty(), busy: !!operation });
}
function updateAuth() {
  const native = $("protocol").value === "anthropic";
  $("auth").options[1].disabled = !native;
  if (!native) $("auth").value = "bearer";
}
function updateDraftState() {
  const dirty = isDirty();
  $("draft-state").textContent = operation === "verify" ? "验证中" : dirty ? "未验证的更改" : state?.configured ? "已保存" : "待验证";
  $("draft-state").classList.toggle("dirty", dirty && operation !== "verify");
  $("reset-draft").disabled = !dirty || !!operation || !online;
  $("export-draft-note").hidden = !dirty || !state?.configured;
  try { $("endpoint-summary").textContent = new URL($("base-url").value).host; }
  catch { $("endpoint-summary").textContent = "服务地址与接口"; }
  desktop?.workspaceState({ dirty, busy: !!operation });
}
function clearResults() {
  $("feedback").hidden = true;
  $("checks").hidden = true;
  $("success-next").hidden = true;
}
function hideKey() {
  $("key").type = "password";
  $("show-key").setAttribute("aria-pressed", "false");
  $("show-key").setAttribute("aria-label", "显示 Key");
  $("show-key").title = "显示 Key";
}
function resetKey() {
  $("key").value = "";
  $("key").setCustomValidity("");
  hideKey();
  $("model-options").replaceChildren();
  $("model-note").textContent = defaultModelNote;
  clearResults();
}
function setConnection(connection, model = "") {
  $("provider").value = connection.id;
  $("connection-name").value = connection.name;
  $("base-url").value = connection.base_url;
  $("protocol").value = connection.protocol;
  $("auth").value = connection.auth;
  $("model").value = model;
  $("provider-note").textContent = state.presets.find(p => p.id === connection.id)?.note || "填写兼容服务的连接信息。";
  $("connection-settings").open = connection.id === "custom";
  updateAuth();
  updateDraftState();
}
function feedback(message, kind = "", reveal = true) {
  $("feedback").textContent = message;
  $("feedback").className = "notice " + kind;
  $("feedback").hidden = false;
  if (reveal && activePanel === "connection") $("feedback").scrollIntoView({ block: "nearest" });
}
function showPanel(id, focus = false) {
  activePanel = panels[id] ? id : "connection";
  for (const name of Object.keys(panels)) $(name).hidden = name !== activePanel;
  document.querySelectorAll("nav a").forEach(link => {
    const selected = link.dataset.panel === activePanel;
    link.classList.toggle("selected", selected);
    if (selected) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
  [$("page-title").textContent, $("page-description").textContent] = panels[activePanel];
  document.title = "ModelBridge · " + panels[activePanel][0];
  if (focus) { $("page-title").tabIndex = -1; $("page-title").focus({ preventScroll: true }); }
  if (activePanel !== "connection") hideKey();
}
function go(id) {
  if (location.hash !== "#" + id) location.hash = id;
  showPanel(id, true);
}
function timestamp(value) { return new Date(value).toLocaleString("zh-CN", { hour12: false }); }
function render(data) {
  state = data;
  online = true;
  baseline = savedDraft(data);
  if (!hydrated) {
    $("provider").replaceChildren(...data.presets.map(p => new Option(p.name, p.id)));
    setConnection(baseline.connection, baseline.model);
    hydrated = true;
  }
  $("version").textContent = desktop ? "客户端 " + desktop.version + " · 后台 " + data.version : "v" + data.version + " · 预览版";
  $("service-pill").classList.remove("offline");
  $("service-pill").lastElementChild.textContent = "服务运行中";
  $("service-state").textContent = "运行中";
  $("current-model").textContent = data.model || "尚未配置连接";
  $("current-provider").textContent = data.connection?.name || "选择下方服务开始设置";
  $("current-protocol").textContent = protocolNames[data.connection?.protocol] || "—";
  $("active-badge").textContent = data.configured ? "已启用" : "未配置";
  $("active-badge").classList.toggle("good", !!data.configured);
  $("verification-time").textContent = data.verified_at ? "上次验证于 " + timestamp(data.verified_at) : "验证通过后，连接会显示在这里。";
  $("claude-state").textContent = data.requests.active ? "正在处理客户端请求" : data.requests.completed ? "已收到客户端成功请求" : "等待首次成功请求";
  $("client-dot").classList.toggle("good", !!data.requests.completed);
  $("request-count").textContent = data.requests.completed;
  $("failed-count").textContent = data.requests.failed;
  $("last-success").textContent = data.requests.last_success ? timestamp(data.requests.last_success) : "暂无客户端请求";
  $("gateway-url").textContent = $("manual-url").textContent = data.gateway_url;
  $("route").textContent = data.route;
  $("label-name").textContent = data.display_name || "先完成连接验证";
  $("claude-detected").textContent = data.claude_installed ? "已检测到安装" : "尚未检测到安装";
  $("export-model").textContent = data.model || "先完成模型连接";
  $("export-description").textContent = data.configured ? data.connection.name + " · 导出此已保存的连接" : "返回连接设置，验证通过后即可导出。";
  $("key").placeholder = data.configured ? "重新填写 Key 以验证或修改连接" : "输入此服务的 API Key";
  $("fatal").hidden = !data.config_error;
  if (data.config_error) $("fatal").textContent = "本地配置无法读取。请重新填写服务信息并验证；原文件未被删除。";
  $("desktop-notice").textContent = data.desktop_notice || '';
  $("desktop-notice").hidden = !data.desktop_notice;
  updateControls();
}
async function api(path, body, timeout = 30000) {
  if (desktop) {
    const result = await desktop.request(path, body);
    if (!result.ok) { const error = new Error(result.message); error.code = result.code; error.unconfirmed = result.unconfirmed; throw error; }
    return result.data;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(path, {
      signal: controller.signal,
      method: body === undefined ? "GET" : "POST",
      headers: { Authorization: "Bearer " + (token || ""), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    let data;
    try { data = await response.json(); }
    catch { const error = new Error("无法读取服务响应，请刷新连接状态。"); error.unconfirmed = true; throw error; }
    if (!response.ok) { const error = new Error(data.message || "请求失败，请稍后重试。"); error.code = data.code; throw error; }
    return data;
  } catch (cause) {
    if (response && cause.name !== "AbortError") throw cause;
    const error = new Error(controller.signal.aborted ? "等待服务响应超时，请刷新连接状态。" : "本地服务未响应，请重新打开 ModelBridge。");
    error.unconfirmed = true;
    throw error;
  } finally { clearTimeout(timer); }
}
async function refresh() {
  if (operation || refreshing) return;
  if (!token && !desktop) {
    online = false;
    $("fatal").textContent = "请双击“打开 ModelBridge.command”进入控制台。直接打开地址不会携带本机管理凭据。";
    $("fatal").hidden = false;
    $("service-pill").classList.add("offline");
    $("service-pill").lastElementChild.textContent = "需要重新打开";
    updateControls();
    return;
  }
  refreshing = true;
  const requestGeneration = generation;
  updateControls();
  try { const data = await api("/api/status"); if (requestGeneration === generation) render(data); }
  catch (error) {
    if (requestGeneration === generation) {
      online = false;
      $("service-pill").classList.add("offline");
      $("service-pill").lastElementChild.textContent = "服务不可用";
      $("service-state").textContent = "连接不可用";
      $("fatal").textContent = error.message;
      $("fatal").hidden = false;
    }
  } finally { refreshing = false; updateControls(); }
}
function begin(kind) {
  operation = kind;
  cancelling = false;
  generation += 1;
  startedAt = Date.now();
  hideKey();
  clearResults();
  updateControls();
  const tick = () => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    $("save-hint").textContent = kind === "verify" ? "验证中 · 已用 " + seconds + " 秒，最多 180 秒；当前连接仍可使用。" : "正在向所选服务获取模型列表…";
  };
  tick();
  progressTimer = setInterval(tick, 1000);
}
function finish() {
  clearInterval(progressTimer);
  operation = "";
  $("save-hint").textContent = "验证将发送 3 次简短请求，使用少量额度。";
  updateControls();
}
function validate(ids) {
  const key = $("key").value.trim();
  $("key").setCustomValidity(key && (key.length < 8 || /\s/.test(key)) ? "请输入至少 8 位、不含空格的 API Key。" : "");
  for (const id of ids) {
    if (!$(id).checkValidity()) {
      if ($("connection-settings").contains($(id))) $("connection-settings").open = true;
      $(id).reportValidity();
      return false;
    }
  }
  return true;
}
$("provider").addEventListener("change", () => {
  resetKey();
  const preset = state.presets.find(p => p.id === $("provider").value);
  setConnection(preset, preset.model || "");
  updateControls();
});
["base-url", "protocol", "auth"].forEach(id => $(id).addEventListener("input", () => { resetKey(); updateAuth(); updateDraftState(); }));
$("setup-form").addEventListener("input", () => { clearResults(); updateDraftState(); });
$("key").addEventListener("input", () => {
  $("key").setCustomValidity("");
  $("model-options").replaceChildren();
  $("model-note").textContent = defaultModelNote;
});
$("show-key").addEventListener("click", () => {
  if ($("key").type === "text") { hideKey(); return; }
  $("key").type = "text";
  $("show-key").setAttribute("aria-label", "隐藏 Key");
  $("show-key").setAttribute("aria-pressed", "true");
  $("show-key").title = "隐藏 Key";
});
$("reset-draft").addEventListener("click", () => {
  resetKey(); setConnection(baseline.connection, baseline.model); updateControls(); toast("已还原连接设置");
});
$("load-models").addEventListener("click", async () => {
  if (operation || !online || state?.desktop_capabilities?.edit === false || !validate(["connection-name", "base-url", "key"])) return;
  const body = { ...draft(), key: $("key").value.trim() };
  begin("models");
  feedback("正在从所选服务获取可用模型…", "pending");
  try {
    const data = await api("/api/models", body);
    $("model-options").replaceChildren(...data.models.map(model => new Option(model, model)));
    if (!$("model").value) $("model").value = data.models[0] || "";
    $("model-note").textContent = "已获取 " + data.models.length + " 个模型。点击输入框选择，也可手动填写。";
    feedback("模型列表已更新。选择模型后，点击“验证并保存”。");
  } catch (error) {
    feedback(error.message + " 也可以直接输入模型 ID 并验证。", "error");
  } finally { finish(); $("model").focus(); }
});
$("setup-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (operation || !online || state?.desktop_capabilities?.edit === false || !validate(["connection-name", "base-url", "key", "model"])) return;
  const body = { ...draft(), key: $("key").value.trim() };
  begin("verify");
  feedback("正在验证文字回复、流式工具调用和结果回传，通常需要 15–60 秒。", "pending");
  let uncertain = false;
  try {
    const data = await api("/api/connect", body, 195000);
    render(data);
    resetKey();
    setConnection(data.connection, data.model);
    $("checks").hidden = false;
    $("success-next").hidden = false;
    feedback("三项验证通过，连接已保存。下一步：在 Claude 中导入配置并填写同一个 Key。");
    toast("连接已保存");
  } catch (error) {
    uncertain = !!error.unconfirmed;
    feedback(uncertain ? "连接中断，保存结果尚未确认。正在重新检查服务状态，请勿重复提交。" : error.message + (state?.configured ? " 当前连接保持不变。" : " 连接尚未保存。"), error.code === "cancelled" ? "" : "error");
  } finally { finish(); if (uncertain) await refresh(); }
});
$("cancel-verify").addEventListener("click", async () => {
  if (operation !== "verify" || cancelling) return;
  cancelling = true;
  updateControls();
  try { await api("/api/connect/cancel", {}, 10000); }
  catch (error) { toast(error.message + " 验证会在时限内结束。"); }
  finally { cancelling = false; updateControls(); }
});
$("setup-form").addEventListener("invalid", event => {
  if ($("connection-settings").contains(event.target)) $("connection-settings").open = true;
}, true);
document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && activePanel === "connection" && !$("connect").disabled) {
    event.preventDefault(); $("setup-form").requestSubmit();
  }
  if (event.key === "Escape") hideKey();
});
window.addEventListener("beforeunload", event => {
  if (!desktop && (isDirty() || operation)) { event.preventDefault(); event.returnValue = ""; }
});
function download(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("download-config").addEventListener("click", async () => {
  $("download-config").disabled = true;
  try {
    if (desktop) { const result = await desktop.exportJSON('config'); if (!result.ok) throw new Error(result.message); if (result.canceled) return; }
    else download("modelbridge-claude-config.json", await api("/api/claude-config"));
    toast("配置已导出，请在 Claude 中填入同一个服务商 Key");
  }
  catch (error) { toast(error.message); }
  finally { updateControls(); }
});
$("diagnostics").addEventListener("click", async () => {
  $("diagnostics").disabled = true;
  try {
    if (desktop) { const result = await desktop.exportJSON('diagnostics'); if (!result.ok) throw new Error(result.message); if (result.canceled) return; }
    else download("modelbridge-diagnostics.json", await api("/api/diagnostics"));
    toast("已导出诊断摘要");
  }
  catch (error) { toast(error.message); }
  finally { updateControls(); }
});
function toast(message) {
  $("toast").textContent = message; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").hidden = true, 4000);
}
document.querySelectorAll("[data-copy]").forEach(button => button.addEventListener("click", async () => {
  try {
    const text = $(button.dataset.copy).textContent;
    if (desktop) { if (!await desktop.copyText(text)) throw new Error('copy'); }
    else await navigator.clipboard.writeText(text);
    toast("已复制");
  }
  catch { toast("无法访问剪贴板，请手动选择并复制该字段。"); }
}));
document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => go(button.dataset.go)));
document.querySelectorAll("nav a").forEach(link => link.addEventListener("click", event => { event.preventDefault(); go(link.dataset.panel); }));
window.addEventListener("hashchange", () => showPanel(location.hash.slice(1)));
$("refresh").addEventListener("click", async () => { await refresh(); if (online) toast("状态已更新"); });
desktop?.onNavigate(panel => { if (panel === 'refresh') refresh(); else go(panel); });
showPanel(location.hash.slice(1));
updateControls();
refresh();
setInterval(() => { if (!document.hidden && !operation) refresh(); }, 10000);

let serviceSetupState;
function renderSetup(value) {
  serviceSetupState = value;
  const working = value.phase === 'working', checking = value.phase === 'checking', ready = value.phase === 'ready';
  const action = ready ? '开始连接模型' : value.action === 'repair' ? '检查并修复服务' : value.phase === 'resume' ? '启动已有服务' : value.phase === 'upgrade' ? '升级后台服务' : value.phase === 'failed' ? '重新检查并重试' : '准备后台服务';
  $('desktop-service-actions').hidden = false;
  $('service-setup-banner').hidden = ready;
  $('service-setup-summary').textContent = value.detail;
  $('service-setup-open').textContent = working ? '查看进度' : checking ? '正在检查…' : action;
  $('setup-title').textContent = ready ? '环境检查通过' : checking ? '检查本机环境' : working ? '正在准备 ModelBridge' : value.phase === 'failed' ? '需要处理后台服务' : value.phase === 'resume' ? '启动已有后台服务' : value.phase === 'upgrade' ? '升级后台服务' : '检查完成，准备安装';
  $('setup-detail').textContent = value.detail;
  $('setup-python-check').textContent = value.checks?.python || '已有环境直接复用，仅在缺失时下载';
  $('setup-service-check').textContent = value.checks?.service || '确认版本，按需安装或升级';
  $('setup-connection-check').textContent = value.checks?.connection || '服务已运行时直接使用';
  $('setup-network').textContent = value.network || '检查完成后，仅在缺少环境或组件时联网下载。';
  $('setup-spinner').hidden = !working && !checking;
  $('setup-install').disabled = working || checking || value.phase === 'unavailable';
  $('setup-install').textContent = working ? '正在处理…' : checking ? '正在检查…' : action;
  $('setup-close').textContent = working ? '后台继续安装' : ready ? '关闭' : '稍后设置';
  $('service-stop').disabled = working || !online;
  document.querySelectorAll('.setup-steps li').forEach(el => {
    const step = Number(el.dataset.step);
    el.classList.toggle('current', working && step === value.step);
    el.classList.toggle('done', ready || working && step < value.step);
    el.querySelector('span').textContent = ready || working && step < value.step ? '✓' : String(step);
  });
  if (working) { online = false; updateControls(); }
}
if (desktop) {
  const openSetup = () => { if (!$('desktop-setup').open) $('desktop-setup').showModal(); };
  const checkAndOpen = async () => {
    renderSetup({ phase: 'checking', detail: '检查已有环境和后台状态，不下载文件…' }); openSetup();
    renderSetup(await desktop.setupState());
  };
  $('service-setup-open').addEventListener('click', checkAndOpen);
  $('service-manage').addEventListener('click', checkAndOpen);
  $('setup-close').addEventListener('click', () => $('desktop-setup').close());
  $('setup-install').addEventListener('click', async () => {
    if (serviceSetupState?.phase === 'ready') { $('desktop-setup').close(); await refresh(); go('connection'); return; }
    $('setup-install').disabled = true;
    try { renderSetup(await desktop.installService()); }
    catch { renderSetup({ phase: 'failed', detail: '无法开始安装，请重新打开客户端后重试。' }); }
    await refresh();
  });
  $('service-stop').addEventListener('click', async () => {
    const result = await desktop.stopService();
    if (!result.ok) toast(result.message);
    else if (!result.canceled) { await refresh(); renderSetup(await desktop.setupState()); }
  });
  desktop.onSetupState(value => { renderSetup(value); if (value.phase === 'ready') refresh(); });
  desktop.setupState().then(value => { renderSetup(value); if (['missing','resume','failed','working'].includes(value.phase)) openSetup(); });
}

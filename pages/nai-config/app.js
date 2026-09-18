// NovelAI 配置管理面板（AstrBot 插件 Pages）
//
// 运行环境：AstrBot Dashboard 把这个页面放在一个受限 iframe 里，
// 页面和后端之间只能通过 window.AstrBotPluginPage（bridge SDK）通信，
// 不能直接 fetch("/api/...")——那样带不上登录态，会 401。
//
// bridge 的返回值规则（写在这里免得以后忘）：
//   - 后端 json_response({...}) 普通对象   → await 直接拿到这个对象
//   - 后端 error_response("...") 或 HTTP 出错 → await 抛 Error，message 是后端给的文案
// 所以所有接口调用都包在 try/catch 里，错误统一走 toast。

const bridge = window.AstrBotPluginPage;

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

// 常用质量词 / 负向词快捷芯片。点一下追加到对应文本框末尾，已存在的不重复加。
// 质量词来自 NovelAI 官方文档（V4.5 / V5 推荐），不是随手写的。
const ARTIST_QUICK_TAGS = [
  "best quality",
  "amazing quality",
  "very aesthetic",
  "masterpiece",
  "absurdres",
  "no text",
  "year 2025",
  "full body",
  "cowboy shot",
  "looking at viewer",
];

const NEGATIVE_QUICK_TAGS = [
  "lowres",
  "worst quality",
  "bad quality",
  "bad anatomy",
  "bad hands",
  "extra digits",
  "jpeg artifacts",
  "signature",
  "watermark",
  "text",
  "blurry",
];

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const state = {
  defaults: { artist: "", negative: "" },
  savedArtist: "",
  savedNegative: "",
  defaultPreset: "", // 配置里的「默认预设」名，空串 = 未设置
  customPresets: [],
  builtinPresets: [],
  editingName: null, // null = 新增；否则为正在编辑的预设名
};

const $ = (id) => document.getElementById(id);

const els = {
  statusChip: $("status-chip"),
  errorBanner: $("error-banner"),
  reloadBtn: $("reload-btn"),

  artistInput: $("artist-input"),
  artistCount: $("artist-count"),
  artistTags: $("artist-tags"),
  artistDirty: $("artist-dirty"),
  artistQuick: $("artist-quick"),
  resetArtist: $("reset-artist"),
  saveArtist: $("save-artist"),
  defaultPresetStatus: $("default-preset-status"),
  defaultPresetName: $("default-preset-name"),

  negativeInput: $("negative-input"),
  negativeCount: $("negative-count"),
  negativeTags: $("negative-tags"),
  negativeDirty: $("negative-dirty"),
  negativeQuick: $("negative-quick"),
  resetNegative: $("reset-negative"),
  saveNegative: $("save-negative"),

  previewPrompt: $("preview-prompt"),
  previewPreset: $("preview-preset"),
  previewBtn: $("preview-btn"),
  previewResult: $("preview-result"),
  previewFinal: $("preview-final"),
  previewNegative: $("preview-negative"),
  previewNotes: $("preview-notes"),

  customList: $("custom-presets-list"),
  customCount: $("custom-count"),
  noCustom: $("no-custom-presets"),
  builtinList: $("builtin-presets-list"),
  builtinCount: $("builtin-count"),
  addPresetBtn: $("add-preset-btn"),

  modal: $("preset-modal"),
  modalTitle: $("modal-title"),
  modalError: $("modal-error"),
  closeModal: $("close-modal"),
  cancelModal: $("cancel-modal"),
  savePreset: $("save-preset"),
  presetName: $("preset-name"),
  presetArtist: $("preset-artist"),
  presetPositive: $("preset-positive"),
  presetNegative: $("preset-negative"),
  presetDesc: $("preset-desc"),

  toast: $("toast"),
};

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function countTags(text) {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean).length;
}

function truncate(str, max) {
  if (!str) return "";
  return str.length <= max ? str : str.slice(0, max) + "…";
}

let toastTimer = null;
function showToast(message, type = "info") {
  els.toast.textContent = message;
  els.toast.className = `toast toast-${type}`;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, type === "error" ? 5000 : 2500);
}

function showBanner(message) {
  els.errorBanner.textContent = message;
  els.errorBanner.hidden = false;
}

function hideBanner() {
  els.errorBanner.hidden = true;
}

function setStatus(text, ok) {
  els.statusChip.textContent = text;
  els.statusChip.className = `chip ${ok ? "chip-ok" : "chip-bad"}`;
}

function setBusy(btn, busy, busyText) {
  if (busy) {
    btn.dataset.label = btn.textContent;
    btn.textContent = busyText || "处理中…";
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.label || btn.textContent;
    btn.disabled = false;
  }
}

// 把标签追加到文本框末尾（已存在则跳过），并触发 input 事件刷新计数
function appendTag(textarea, tag) {
  const existing = textarea.value
    .split(",")
    .map((s) => s.trim().toLowerCase());
  if (existing.includes(tag.toLowerCase())) {
    showToast(`「${tag}」已经在里面了`, "info");
    return;
  }
  const base = textarea.value.trim();
  textarea.value = base ? `${base.replace(/,\s*$/, "")}, ${tag}` : tag;
  textarea.dispatchEvent(new Event("input"));
  textarea.focus();
}

function renderQuickTags(container, tags, textarea) {
  tags.forEach((tag) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "quick-chip";
    chip.textContent = tag;
    chip.title = `追加「${tag}」`;
    chip.addEventListener("click", () => appendTag(textarea, tag));
    container.appendChild(chip);
  });
}

// ---------------------------------------------------------------------------
// 画师串 / 负向词
// ---------------------------------------------------------------------------

function refreshArtistMeta() {
  const v = els.artistInput.value;
  els.artistCount.textContent = v.length;
  els.artistTags.textContent = countTags(v);
  els.artistDirty.hidden = v.trim() === state.savedArtist.trim();
}

function refreshNegativeMeta() {
  const v = els.negativeInput.value;
  els.negativeCount.textContent = v.length;
  els.negativeTags.textContent = countTags(v);
  els.negativeDirty.hidden = v.trim() === state.savedNegative.trim();
}

// 默认预设状态行：让人一眼知道每次生图实际套的是哪个预设
function renderDefaultPresetStatus() {
  const name = state.defaultPreset || "";
  els.defaultPresetName.textContent = name || "未设置";
  els.defaultPresetStatus.classList.toggle("is-set", Boolean(name));
}

async function saveArtist() {
  setBusy(els.saveArtist, true, "保存中…");
  try {
    const res = await bridge.apiPost("config/artist", {
      value: els.artistInput.value,
    });
    state.savedArtist = res.artist ?? els.artistInput.value.trim();
    els.artistInput.value = state.savedArtist;
    refreshArtistMeta();
    showToast("画师串已保存，立即生效", "success");
  } catch (err) {
    showToast(`保存失败：${err.message}`, "error");
  } finally {
    setBusy(els.saveArtist, false);
  }
}

async function saveNegative() {
  setBusy(els.saveNegative, true, "保存中…");
  try {
    const res = await bridge.apiPost("config/negative", {
      value: els.negativeInput.value,
    });
    state.savedNegative = res.negative ?? els.negativeInput.value.trim();
    els.negativeInput.value = state.savedNegative;
    refreshNegativeMeta();
    showToast("负向词已保存，立即生效", "success");
  } catch (err) {
    showToast(`保存失败：${err.message}`, "error");
  } finally {
    setBusy(els.saveNegative, false);
  }
}

function resetArtist() {
  if (!confirm("把画师串恢复成插件默认值？（还没点保存前不会真的生效）")) return;
  els.artistInput.value = state.defaults.artist;
  refreshArtistMeta();
  showToast("已填入默认画师串，记得点「保存」", "info");
}

function resetNegative() {
  if (!confirm("把负向词恢复成插件默认值？（还没点保存前不会真的生效）")) return;
  els.negativeInput.value = state.defaults.negative;
  refreshNegativeMeta();
  showToast("已填入默认负向词，记得点「保存」", "info");
}

// ---------------------------------------------------------------------------
// 拼接预览
// ---------------------------------------------------------------------------

async function runPreview() {
  const prompt = els.previewPrompt.value.trim();
  if (!prompt) {
    showToast("先输入一段正向词再预览", "info");
    els.previewPrompt.focus();
    return;
  }
  setBusy(els.previewBtn, true, "计算中…");
  try {
    const presetName = els.previewPreset.value;
    const res = await bridge.apiPost("preview", {
      prompt,
      artist: els.artistInput.value,
      negative: els.negativeInput.value,
      preset: presetName || "",
    });
    els.previewFinal.textContent = res.final_prompt || "（空）";
    els.previewNegative.textContent = res.final_negative || "（空）";

    const notes = [];
    if (res.preset_applied) {
      const p = findPreset(presetName);
      const parts = [];
      if (p?.artist) parts.push("画师串替代了全局画师串");
      if (p?.positive) parts.push(`正向词 <code>${escapeHtml(truncate(p.positive, 60))}</code> 追加到了提示词后面`);
      if (p?.negative) parts.push(`负向词 <code>${escapeHtml(truncate(p.negative, 60))}</code> 追加到了负向词后面`);
      notes.push(`已模拟 <code>/nai -p ${escapeHtml(presetName)}</code>：${parts.join("；") || "预设为空"}。`);
    }
    if (res.moved_negative) {
      notes.push(`画师串里的负权重 <code>${escapeHtml(res.moved_negative)}</code> 已自动挪到负向词。`);
    }
    if (res.composition_added) {
      notes.push("正向词和画师串都没写构图/景别，已自动补 <code>full body, standing</code>（可在配置页关闭「自动补构图标签」）。");
    }
    if (!notes.length) notes.push("没有额外处理：画师串直接拼在最前面。");
    els.previewNotes.innerHTML = notes.map((n) => `<div class="note">• ${n}</div>`).join("");
    els.previewResult.hidden = false;
  } catch (err) {
    showToast(`预览失败：${err.message}`, "error");
  } finally {
    setBusy(els.previewBtn, false);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function findPreset(name) {
  return (
    state.customPresets.find((p) => p.name === name) ||
    state.builtinPresets.find((p) => p.name === name) ||
    null
  );
}

// 预览卡片里的「模拟预设」下拉框，每次预设列表变化后重建
function renderPreviewPresetOptions() {
  const current = els.previewPreset.value;
  els.previewPreset.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "不用预设（用上面两个框）";
  els.previewPreset.appendChild(none);

  const addGroup = (label, list) => {
    if (!list.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    list.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = p.name;
      group.appendChild(opt);
    });
    els.previewPreset.appendChild(group);
  };
  addGroup("自定义预设", state.customPresets);
  addGroup("内置预设", state.builtinPresets);

  // 保住用户之前选的（如果它还在）
  if (current && findPreset(current)) els.previewPreset.value = current;
}

// ---------------------------------------------------------------------------
// 预设
// ---------------------------------------------------------------------------

// 预设卡片里的一段内容（画师串 / 正向词 / 负向词），空的不渲染
function createPresetField(label, value) {
  const wrap = document.createElement("div");
  wrap.className = "preset-field";
  const lab = document.createElement("span");
  lab.className = "preset-field-label";
  lab.textContent = label;
  wrap.appendChild(lab);
  const val = document.createElement("div");
  val.className = "preset-field-value mono";
  val.textContent = truncate(value, 160);
  val.title = value;
  wrap.appendChild(val);
  return wrap;
}

function createPresetCard(preset, { builtin }) {
  const card = document.createElement("div");
  card.className = `preset-card${builtin ? " preset-card-builtin" : ""}`;

  const isDefault = Boolean(state.defaultPreset) && state.defaultPreset === preset.name;
  if (isDefault) {
    card.classList.add("preset-card-default");
  }

  const header = document.createElement("div");
  header.className = "preset-card-header";

  const nameWrap = document.createElement("div");
  nameWrap.className = "preset-name-wrap";
  const name = document.createElement("strong");
  name.textContent = preset.name;
  nameWrap.appendChild(name);
  if (builtin) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "内置";
    nameWrap.appendChild(badge);
  }
  if (isDefault) {
    const badge = document.createElement("span");
    badge.className = "badge badge-default";
    badge.textContent = "默认";
    badge.title = "每次生图会自动套用这个预设";
    nameWrap.appendChild(badge);
  }
  // 小标记：这个预设带了哪几段，一眼看出来
  if (preset.positive) {
    const b = document.createElement("span");
    b.className = "badge badge-soft";
    b.textContent = "+正向";
    b.title = "附带正向词";
    nameWrap.appendChild(b);
  }
  if (preset.negative) {
    const b = document.createElement("span");
    b.className = "badge badge-soft";
    b.textContent = "+负向";
    b.title = "附带负向词";
    nameWrap.appendChild(b);
  }
  header.appendChild(nameWrap);

  const actions = document.createElement("div");
  actions.className = "preset-card-actions";

  // 三段任意一段非空就值得设成默认 —— v1.4.2 起允许「只有正向/负向词、
  // 画师串为空」的预设，旧代码用 `if (preset.artist)` 会让这类预设连按钮都没有
  if (preset.artist || preset.positive || preset.negative) {
    const useBtn = document.createElement("button");
    useBtn.type = "button";
    useBtn.className = "btn btn-ghost btn-xs";
    useBtn.textContent = isDefault ? "取消默认" : "设为默认风格";
    useBtn.title = isDefault
      ? "取消默认：之后生图不再自动套用这个预设"
      : "设为默认：之后每次生图自动套用它的画师串 + 附带正向词 + 附带负向词";
    useBtn.addEventListener("click", () =>
      setDefaultPreset(isDefault ? "" : preset.name)
    );
    actions.appendChild(useBtn);
  }

  const previewBtn = document.createElement("button");
  previewBtn.type = "button";
  previewBtn.className = "btn btn-ghost btn-xs";
  previewBtn.textContent = "预览";
  previewBtn.title = "在上面的「拼接预览」里模拟这个预设";
  previewBtn.addEventListener("click", () => {
    els.previewPreset.value = preset.name;
    els.previewPrompt.scrollIntoView({ behavior: "smooth", block: "center" });
    if (els.previewPrompt.value.trim()) {
      runPreview();
    } else {
      els.previewPrompt.focus();
      showToast("输入一段正向词后点「预览」", "info");
    }
  });
  actions.appendChild(previewBtn);

  if (builtin) {
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn btn-ghost btn-xs";
    copyBtn.textContent = "复制为新预设";
    copyBtn.addEventListener("click", () =>
      openModal(
        { name: "", artist: preset.artist || "", positive: "", negative: "", desc: `基于 ${preset.name}` },
        null
      )
    );
    actions.appendChild(copyBtn);
  } else {
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn btn-ghost btn-xs";
    editBtn.textContent = "编辑";
    editBtn.addEventListener("click", () => openModal(preset, preset.name));
    actions.appendChild(editBtn);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-ghost btn-xs btn-danger";
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", () => deletePreset(preset.name));
    actions.appendChild(delBtn);
  }
  header.appendChild(actions);
  card.appendChild(header);

  const body = document.createElement("div");
  body.className = "preset-card-body";
  if (preset.desc) {
    const desc = document.createElement("div");
    desc.className = "preset-desc";
    desc.textContent = preset.desc;
    body.appendChild(desc);
  }
  if (preset.artist) {
    body.appendChild(createPresetField("画师串", preset.artist));
  } else {
    const none = document.createElement("div");
    none.className = "preset-field-value muted";
    none.textContent = "（不带画师串，沿用全局画师串）";
    body.appendChild(none);
  }
  if (preset.positive) body.appendChild(createPresetField("附带正向词", preset.positive));
  if (preset.negative) body.appendChild(createPresetField("附带负向词", preset.negative));
  card.appendChild(body);

  return card;
}

function renderPresets() {
  els.customList.innerHTML = "";
  els.builtinList.innerHTML = "";

  els.customCount.textContent = state.customPresets.length;
  els.builtinCount.textContent = state.builtinPresets.length;
  els.noCustom.hidden = state.customPresets.length > 0;

  state.customPresets.forEach((p) =>
    els.customList.appendChild(createPresetCard(p, { builtin: false }))
  );
  state.builtinPresets.forEach((p) =>
    els.builtinList.appendChild(createPresetCard(p, { builtin: true }))
  );
  renderPreviewPresetOptions();
}

// 设为 / 取消默认预设。name 为空 = 取消。
// 成功后就地更新 state 并重绘，不再整页拉配置（省一次往返，且不会打断用户）。
async function setDefaultPreset(name) {
  try {
    const res = await bridge.apiPost("preset/default", { name });
    state.defaultPreset = res.default_preset || "";
    renderDefaultPresetStatus();
    renderPresets();
    showToast(
      state.defaultPreset
        ? `已把「${state.defaultPreset}」设为默认风格，之后每次生图自动套用它的三段`
        : "已取消默认预设",
      "success"
    );
  } catch (err) {
    showToast(`设置失败：${err.message}`, "error");
  }
}

function openModal(preset, editingName) {
  state.editingName = editingName;
  els.modalTitle.textContent = editingName ? `编辑预设「${editingName}」` : "新增预设";
  els.presetName.value = preset?.name || "";
  els.presetName.disabled = Boolean(editingName); // 改名 = 删了重建，这里不支持
  els.presetArtist.value = preset?.artist || "";
  els.presetPositive.value = preset?.positive || "";
  els.presetNegative.value = preset?.negative || "";
  els.presetDesc.value = preset?.desc || "";
  els.modalError.hidden = true;
  els.modal.hidden = false;
  (editingName ? els.presetArtist : els.presetName).focus();
}

function closeModal() {
  els.modal.hidden = true;
  state.editingName = null;
}

function showModalError(msg) {
  els.modalError.textContent = msg;
  els.modalError.hidden = false;
}

async function submitPreset() {
  const name = els.presetName.value.trim();
  const artist = els.presetArtist.value.trim();
  const positive = els.presetPositive.value.trim();
  const negative = els.presetNegative.value.trim();
  const desc = els.presetDesc.value.trim();

  // 前端先拦一遍常见错误，省一次请求；后端还会再校验一次
  if (!name) return showModalError("预设名不能为空");
  if (/\s/.test(name)) return showModalError("预设名不能含空格（/nai -p 是靠空格切参数的）");
  if (!artist && !positive && !negative) {
    return showModalError("画师串、正向词、负向词至少填一项");
  }
  if (!state.editingName && state.builtinPresets.some((p) => p.name === name)) {
    return showModalError(`「${name}」是内置预设名，换一个吧`);
  }
  if (!state.editingName && state.customPresets.some((p) => p.name === name)) {
    return showModalError(`「${name}」已存在，请到列表里点「编辑」`);
  }

  setBusy(els.savePreset, true, "保存中…");
  try {
    const res = await bridge.apiPost("presets", {
      action: state.editingName ? "update" : "add",
      data: { name, artist, positive, negative, desc },
    });
    state.customPresets = res.custom_presets || [];
    renderPresets();
    closeModal();
    showToast(`预设「${name}」已${state.editingName ? "更新" : "添加"}，现在就能用 /nai -p ${name}`, "success");
  } catch (err) {
    showModalError(err.message);
  } finally {
    setBusy(els.savePreset, false);
  }
}

async function deletePreset(name) {
  if (!confirm(`确定删除预设「${name}」？\n聊天里 /nai -p ${name} 会立刻失效。`)) return;
  try {
    const res = await bridge.apiPost("presets", { action: "delete", data: { name } });
    state.customPresets = res.custom_presets || [];
    renderPresets();
    showToast(`预设「${name}」已删除`, "success");
  } catch (err) {
    showToast(`删除失败：${err.message}`, "error");
  }
}

// ---------------------------------------------------------------------------
// 加载 / 初始化
// ---------------------------------------------------------------------------

async function loadConfig() {
  setStatus("读取中…", true);
  try {
    const res = await bridge.apiGet("config");
    state.defaults.artist = res.defaults?.artist || "";
    state.defaults.negative = res.defaults?.negative || "";
    state.savedArtist = res.artist || "";
    state.savedNegative = res.negative || "";
    state.defaultPreset = res.default_preset || "";
    state.customPresets = res.custom_presets || [];
    state.builtinPresets = res.builtin_presets || [];

    els.artistInput.value = state.savedArtist;
    els.negativeInput.value = state.savedNegative;
    refreshArtistMeta();
    refreshNegativeMeta();
    renderDefaultPresetStatus();
    renderPresets();
    hideBanner();
    setStatus(`已连接 · v${res.version || "?"}`, true);
  } catch (err) {
    setStatus("连接失败", false);
    showBanner(
      `读取配置失败：${err.message}。` +
        "常见原因：插件没启用、AstrBot 版本低于 4.26.0、或者刚改完插件还没重载。"
    );
  }
}

function applyTheme(context) {
  // bridge SDK 自己也会维护 data-theme；这里再设一次是为了在 SDK 行为变化时兜底
  const isDark = Boolean(context?.isDark);
  document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
}

function applyI18n() {
  const title = bridge.t("pages.nai-config.title", "NovelAI 配置管理");
  document.title = title;
  els.statusChip.setAttribute("aria-label", title);
}

function bindEvents() {
  els.artistInput.addEventListener("input", refreshArtistMeta);
  els.negativeInput.addEventListener("input", refreshNegativeMeta);
  els.saveArtist.addEventListener("click", saveArtist);
  els.saveNegative.addEventListener("click", saveNegative);
  els.resetArtist.addEventListener("click", resetArtist);
  els.resetNegative.addEventListener("click", resetNegative);
  els.reloadBtn.addEventListener("click", loadConfig);

  els.previewBtn.addEventListener("click", runPreview);
  els.previewPrompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runPreview();
  });

  els.addPresetBtn.addEventListener("click", () => openModal(null, null));
  els.closeModal.addEventListener("click", closeModal);
  els.cancelModal.addEventListener("click", closeModal);
  els.savePreset.addEventListener("click", submitPreset);
  els.modal.addEventListener("click", (e) => {
    if (e.target === els.modal) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.modal.hidden) closeModal();
  });

  // Ctrl/Cmd + S 保存当前焦点所在的框，顺手
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "s") return;
    if (document.activeElement === els.artistInput) {
      e.preventDefault();
      saveArtist();
    } else if (document.activeElement === els.negativeInput) {
      e.preventDefault();
      saveNegative();
    }
  });

  // 有未保存改动时离开页面提示一下
  window.addEventListener("beforeunload", (e) => {
    if (!els.artistDirty.hidden || !els.negativeDirty.hidden) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
}

async function main() {
  if (!bridge) {
    setStatus("bridge 不可用", false);
    showBanner(
      "没有检测到 AstrBot 插件页面 bridge。这个页面必须从 AstrBot Dashboard 的插件详情页打开，直接访问文件不行。"
    );
    return;
  }

  renderQuickTags(els.artistQuick, ARTIST_QUICK_TAGS, els.artistInput);
  renderQuickTags(els.negativeQuick, NEGATIVE_QUICK_TAGS, els.negativeInput);
  bindEvents();

  const context = await bridge.ready();
  applyTheme(context);
  applyI18n();
  bridge.onContext((ctx) => {
    applyTheme(ctx);
    applyI18n();
  });

  await loadConfig();
}

main().catch((err) => {
  setStatus("初始化失败", false);
  showBanner(`页面初始化失败：${err.message}`);
});

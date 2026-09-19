// NovelAI 预设管理面板前端脚本 (AstrBot Plugin Page)

(function () {
  let bridge = window.AstrBotPluginPage;

  async function resolveBridge(maxRetries = 25, delayMs = 100) {
    if (bridge && typeof bridge.apiGet === "function") return bridge;
    for (let i = 0; i < maxRetries; i++) {
      const b = window.AstrBotPluginPage || (window.parent && window.parent.AstrBotPluginPage);
      if (b && typeof b.apiGet === "function") {
        bridge = b;
        return b;
      }
      await new Promise((r) => setTimeout(r, delayMs));
    }
    return null;
  }

  // 状态
  const state = {
    presets: [],
    defaultPreset: "",
    filter: "all",
    search: "",
    editingName: null, // null 表示新建，string 表示正在编辑已有预设
  };

  // DOM 元素引用
  const el = {
    statusChip: document.getElementById("status-chip"),
    reloadBtn: document.getElementById("reload-btn"),
    errorBanner: document.getElementById("error-banner"),
    defaultBanner: document.getElementById("default-preset-banner"),
    currentDefaultName: document.getElementById("current-default-name"),
    currentDefaultTip: document.getElementById("current-default-tip"),
    clearDefaultBtn: document.getElementById("clear-default-btn"),
    presetList: document.getElementById("preset-list"),
    btnCreatePreset: document.getElementById("btn-create-preset"),
    presetSearch: document.getElementById("preset-search"),
    filterChips: document.querySelectorAll(".filter-chip"),

    // 预览
    previewPrompt: document.getElementById("preview-prompt"),
    previewPresetSelect: document.getElementById("preview-preset-select"),
    previewArtistVal: document.getElementById("preview-artist-val"),
    previewTagVal: document.getElementById("preview-tag-val"),
    previewNegVal: document.getElementById("preview-neg-val"),

    // 直译 (v0.3.0)
    translateEnabled: document.getElementById("translate-enabled"),
    translateProvider: document.getElementById("translate-provider"),
    translateProviderHint: document.getElementById("translate-provider-hint"),
    translatePrompt: document.getElementById("translate-prompt"),
    translateSave: document.getElementById("translate-save"),
    translateRestore: document.getElementById("translate-restore"),
    translateRefreshModels: document.getElementById("translate-refresh-models"),
    translateTestInput: document.getElementById("translate-test-input"),
    translateTest: document.getElementById("translate-test"),
    translateTestResult: document.getElementById("translate-test-result"),

    // 弹窗
    modal: document.getElementById("preset-modal"),
    modalTitle: document.getElementById("modal-title"),
    modalCloseBtn: document.getElementById("modal-close-btn"),
    modalCancelBtn: document.getElementById("modal-cancel-btn"),
    modalSaveBtn: document.getElementById("modal-save-btn"),
    modalName: document.getElementById("modal-preset-name"),
    modalDesc: document.getElementById("modal-preset-desc"),
    modalArtist: document.getElementById("modal-preset-artist"),
    modalPositive: document.getElementById("modal-preset-positive"),
    modalNegative: document.getElementById("modal-preset-negative"),
    modalSetDefault: document.getElementById("modal-set-default"),

    toastContainer: document.getElementById("toast-container"),
  };

  // -------------------------------------------------------------------------
  // 工具函数
  // -------------------------------------------------------------------------

  function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    el.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(-10px)";
      toast.style.transition = "all 0.2s ease";
      setTimeout(() => toast.remove(), 200);
    }, 2800);
  }

  function appendTagToTextarea(textarea, tag) {
    let current = textarea.value.trim();
    if (!current) {
      textarea.value = tag;
      return;
    }
    const tags = current.split(",").map((s) => s.trim().toLowerCase());
    if (tags.includes(tag.toLowerCase())) {
      showToast(`标签 "${tag}" 已存在`, "warning");
      return;
    }
    textarea.value = current.replace(/,\s*$/, "") + ", " + tag;
  }

  // -------------------------------------------------------------------------
  // 数据加载与渲染
  // -------------------------------------------------------------------------

  async function loadConfig() {
    if (!bridge || typeof bridge.apiGet !== "function") {
      bridge = await resolveBridge(10, 80);
    }
    if (!bridge || typeof bridge.apiGet !== "function") {
      el.statusChip.textContent = "未连接 Bridge";
      el.statusChip.className = "chip chip-error";
      el.errorBanner.hidden = false;
      el.errorBanner.textContent = "无法与 AstrBot 建立通信，请在 AstrBot 面板中打开此页面。";
      return;
    }

    try {
      el.statusChip.textContent = "读取中…";
      const data = await bridge.apiGet("config");
      state.presets = data.presets || [];
      state.defaultPreset = data.default_preset || "";

      el.statusChip.textContent = `已连接 (v${data.version || "0.0.1"})`;
      el.statusChip.className = "chip chip-connected";
      el.errorBanner.hidden = true;

      renderDefaultPresetBanner();
      renderPresetList();
      renderPreviewSelect();
      updatePreview();
      fillTranslateForm(data);
      await loadTranslateModels();
    } catch (err) {
      console.error("加载配置失败:", err);
      el.statusChip.textContent = "读取失败";
      el.statusChip.className = "chip chip-error";
      el.errorBanner.hidden = false;
      el.errorBanner.textContent = `读取插件配置失败: ${err.message || err}`;
    }
  }

  function renderDefaultPresetBanner() {
    if (state.defaultPreset) {
      el.currentDefaultName.textContent = `【${state.defaultPreset}】`;
      el.currentDefaultTip.textContent = "（普通生图未指定 -p 时自动套用此画风）";
      el.clearDefaultBtn.style.display = "inline-flex";
    } else {
      el.currentDefaultName.textContent = "未设置";
      el.currentDefaultTip.textContent = "（生图未指定 -p 时使用常规默认画师串）";
      el.clearDefaultBtn.style.display = "none";
    }
  }

  function renderPresetList() {
    const query = state.search.trim().toLowerCase();
    const filtered = state.presets.filter((p) => {
      // 类别筛选
      if (state.filter === "builtin" && !p.is_builtin) return false;
      if (state.filter === "custom" && p.is_builtin) return false;
      // 关键字搜索
      if (query) {
        const text = [p.name, p.desc, p.artist, p.positive, p.negative].join(" ").toLowerCase();
        if (!text.includes(query)) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      el.presetList.innerHTML = `<div class="loading-placeholder">无匹配的预设</div>`;
      return;
    }

    el.presetList.innerHTML = filtered
      .map((p) => {
        const isDefault = p.name === state.defaultPreset;
        const defaultBadge = isDefault ? `<span class="badge badge-default">★ 默认预设</span>` : "";
        const typeBadge = p.is_builtin
          ? `<span class="badge badge-builtin">官方内置</span>`
          : `<span class="badge badge-custom">自定义</span>`;

        return `
          <div class="preset-card ${isDefault ? "is-default" : ""}" data-name="${escapeHtml(p.name)}">
            <div class="preset-card-head">
              <div class="preset-card-title">
                <h3>${escapeHtml(p.name)}</h3>
                ${defaultBadge}
                ${typeBadge}
              </div>
            </div>
            ${p.desc ? `<div class="preset-card-desc">${escapeHtml(p.desc)}</div>` : ""}

            <div class="preset-card-body">
              <div class="preset-segment">
                <div class="segment-label">🎨 质量词 / 画师串 (artist)</div>
                <div class="segment-text ${!p.artist ? "empty" : ""}" title="${escapeHtml(p.artist || "（留空）")}">
                  ${escapeHtml(p.artist || "（留空）")}
                </div>
              </div>

              <div class="preset-segment">
                <div class="segment-label">➕ 附带正向词 (positive)</div>
                <div class="segment-text ${!p.positive ? "empty" : ""}" title="${escapeHtml(p.positive || "（无）")}">
                  ${escapeHtml(p.positive || "（无）")}
                </div>
              </div>

              <div class="preset-segment">
                <div class="segment-label">➖ 附带负向词 (negative)</div>
                <div class="segment-text ${!p.negative ? "empty" : ""}" title="${escapeHtml(p.negative || "（无）")}">
                  ${escapeHtml(p.negative || "（无）")}
                </div>
              </div>
            </div>

            <div class="preset-card-footer">
              <div class="footer-actions-left">
                ${
                  isDefault
                    ? `<button class="btn btn-secondary btn-sm btn-clear-def" data-name="${escapeHtml(p.name)}">取消默认</button>`
                    : `<button class="btn btn-gold btn-sm btn-set-def" data-name="${escapeHtml(p.name)}">★ 设为默认</button>`
                }
              </div>
              <div class="footer-actions-right">
                ${
                  p.is_builtin
                    ? `<button class="btn btn-secondary btn-sm btn-copy-preset" data-name="${escapeHtml(p.name)}" title="基于此预设复制新建">复制新建</button>`
                    : `
                      <button class="btn btn-secondary btn-sm btn-edit-preset" data-name="${escapeHtml(p.name)}">编辑</button>
                      <button class="btn btn-danger btn-sm btn-del-preset" data-name="${escapeHtml(p.name)}">删除</button>
                    `
                }
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderPreviewSelect() {
    const currentVal = el.previewPresetSelect.value;
    el.previewPresetSelect.innerHTML = state.presets
      .map((p) => {
        const isDef = p.name === state.defaultPreset ? " (默认)" : "";
        return `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}${isDef}</option>`;
      })
      .join("");

    // 优先保持当前选择，没有的话默认选当前默认预设或第一项
    if (currentVal && state.presets.some((p) => p.name === currentVal)) {
      el.previewPresetSelect.value = currentVal;
    } else if (state.defaultPreset) {
      el.previewPresetSelect.value = state.defaultPreset;
    }
  }

  async function updatePreview() {
    if (!bridge || !bridge.apiPost) return;
    const prompt = el.previewPrompt.value.trim();
    const preset = el.previewPresetSelect.value;
    if (!preset) return;

    try {
      const res = await bridge.apiPost("preview", {
        prompt: prompt || "1girl",
        preset: preset,
      });

      el.previewArtistVal.textContent = res.artist || "（未设置画师串）";
      el.previewTagVal.textContent = res.tag || prompt || "（空）";
      el.previewNegVal.textContent = res.negative || "（空）";
    } catch (err) {
      console.error("生成预览失败:", err);
    }
  }

  // -------------------------------------------------------------------------
  // 提示词直译 (v0.3.0)
  // -------------------------------------------------------------------------

  function fillTranslateForm(data) {
    const t = (data && data.translate) || {};
    state.translateProviderId = t.provider_id || "";
    state.translateDefaultPrompt = (data && data.translate_default_prompt) || "";
    el.translateEnabled.checked = !!t.enabled;
    el.translatePrompt.value = t.system_prompt || state.translateDefaultPrompt;
    // provider 下拉框在 loadTranslateModels 中根据 state.translateProviderId 回填
  }

  async function loadTranslateModels() {
    if (!bridge || typeof bridge.apiGet !== "function") return;
    const select = el.translateProvider;
    const savedVal = state.translateProviderId || (select.value || "");
    select.disabled = true;
    select.innerHTML = `<option value="">加载中…</option>`;

    try {
      const data = await bridge.apiGet("translate/models");
      const models = (data && data.models) || [];
      const available = !data || data.available !== false;

      if (!available) {
        select.innerHTML = `<option value="">（当前 AstrBot 不支持读取模型）</option>`;
        select.disabled = true;
        el.translateProviderHint.textContent =
          "无法读取 AstrBot 模型列表（需 AstrBot >= 4.26.0）。可手动保存后由后端解析。";
        return;
      }

      let html = `<option value="">（不启用 / 未选择）</option>`;
      models.forEach((m) => {
        const label = m.name && m.name !== m.id ? `${m.name} (${m.type || "chat"})` : m.id;
        html += `<option value="${escapeHtml(m.id)}">${escapeHtml(label)}</option>`;
      });
      // 已保存的模型已失效：补一个占位项，提醒用户重新选择
      if (savedVal && !models.some((m) => m.id === savedVal)) {
        html += `<option value="${escapeHtml(savedVal)}">（已失效）${escapeHtml(savedVal)}</option>`;
      }
      select.innerHTML = html;
      select.disabled = false;

      if (savedVal && Array.from(select.options).some((o) => o.value === savedVal)) {
        select.value = savedVal;
      }

      el.translateProviderHint.textContent = models.length
        ? `共 ${models.length} 个可用对话模型。只读取配置，绝不修改你的对话模型设置。`
        : "未发现可用的对话模型，请先在 AstrBot 中配置对话模型。";
    } catch (err) {
      console.error("加载直译模型失败:", err);
      select.innerHTML = `<option value="">（读取失败）</option>`;
      select.disabled = true;
      el.translateProviderHint.textContent = `读取模型列表失败: ${err.message || err}`;
    }
  }

  async function saveTranslate() {
    if (!bridge || !bridge.apiPost) return;
    try {
      el.translateSave.disabled = true;
      const payload = {
        enabled: el.translateEnabled.checked,
        provider_id: el.translateProvider.value,
        system_prompt: el.translatePrompt.value,
      };
      const res = await bridge.apiPost("translate", payload);
      if (res && res.translate) {
        state.translateProviderId = res.translate.provider_id || "";
      }
      showToast("直译设置已保存");
    } catch (err) {
      showToast(`保存直译设置失败: ${err.message || err}`, "error");
    } finally {
      el.translateSave.disabled = false;
    }
  }

  function restoreDefaultTranslatePrompt() {
    if (state.translateDefaultPrompt) {
      el.translatePrompt.value = state.translateDefaultPrompt;
      showToast("已恢复默认系统提示词（记得点「保存直译设置」）");
    } else {
      showToast("未获取到默认提示词", "warning");
    }
  }

  async function testTranslate() {
    if (!bridge || !bridge.apiPost) return;
    const text = el.translateTestInput.value.trim();
    if (!text) {
      showToast("请输入要试译的中文提示词", "error");
      return;
    }

    const btn = el.translateTest;
    const box = el.translateTestResult;
    const oldLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "翻译中…";
    box.hidden = false;
    box.className = "translate-test-result mono";
    box.textContent = "请求直译模型中…";

    try {
      const res = await bridge.apiPost("translate/test", {
        text,
        provider_id: el.translateProvider.value,
        system_prompt: el.translatePrompt.value,
      });

      if (res && res.error) {
        box.className = "translate-test-result mono is-error";
        box.textContent = `试译失败: ${res.error}`;
      } else if (res && res.translated) {
        box.className = "translate-test-result mono is-success";
        box.textContent = res.text;
      } else if (res && res.note) {
        box.className = "translate-test-result mono is-warning";
        box.textContent = res.note;
      } else {
        box.className = "translate-test-result mono";
        box.textContent = "原文不含中文，无需直译（生图时将直接使用原文）。";
      }
    } catch (err) {
      box.className = "translate-test-result mono is-error";
      box.textContent = `试译失败: ${err.message || err}`;
    } finally {
      btn.disabled = false;
      btn.textContent = oldLabel;
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // -------------------------------------------------------------------------
  // 预设操作（设默认、增、删、改）
  // -------------------------------------------------------------------------

  async function setDefaultPreset(name) {
    try {
      await bridge.apiPost("preset/default", { name });
      state.defaultPreset = name;
      showToast(name ? `已将预设「${name}」设为默认` : "已取消默认预设");
      renderDefaultPresetBanner();
      renderPresetList();
      renderPreviewSelect();
      updatePreview();
    } catch (err) {
      showToast(`设置默认预设失败: ${err.message || err}`, "error");
    }
  }

  async function deletePreset(name) {
    if (!confirm(`确定要删除自定义预设「${name}」吗？此操作不可恢复。`)) {
      return;
    }
    try {
      await bridge.apiPost("presets", {
        action: "delete",
        data: { name },
      });
      showToast(`预设「${name}」已删除`);
      await loadConfig();
    } catch (err) {
      showToast(`删除预设失败: ${err.message || err}`, "error");
    }
  }

  function openModalForCreate(copyFrom = null) {
    state.editingName = null;
    el.modalTitle.textContent = copyFrom ? `复制预设 (${copyFrom.name})` : "新建预设";
    el.modalName.value = copyFrom ? `${copyFrom.name}_副本` : "";
    el.modalName.disabled = false;
    el.modalDesc.value = copyFrom ? copyFrom.desc || "" : "";
    el.modalArtist.value = copyFrom ? copyFrom.artist || "" : "";
    el.modalPositive.value = copyFrom ? copyFrom.positive || "" : "";
    el.modalNegative.value = copyFrom ? copyFrom.negative || "" : "";
    el.modalSetDefault.checked = false;
    el.modal.hidden = false;
    el.modalName.focus();
  }

  function openModalForEdit(preset) {
    state.editingName = preset.name;
    el.modalTitle.textContent = `编辑预设「${preset.name}」`;
    el.modalName.value = preset.name;
    el.modalName.disabled = true; // 名称作为键不可改
    el.modalDesc.value = preset.desc || "";
    el.modalArtist.value = preset.artist || "";
    el.modalPositive.value = preset.positive || "";
    el.modalNegative.value = preset.negative || "";
    el.modalSetDefault.checked = preset.name === state.defaultPreset;
    el.modal.hidden = false;
  }

  function closeModal() {
    el.modal.hidden = true;
  }

  async function saveModalPreset() {
    const name = el.modalName.value.trim();
    const desc = el.modalDesc.value.trim();
    const artist = el.modalArtist.value.trim();
    const positive = el.modalPositive.value.trim();
    const negative = el.modalNegative.value.trim();
    const isSetDefault = el.modalSetDefault.checked;

    if (!name) {
      showToast("预设名称不能为空", "error");
      el.modalName.focus();
      return;
    }
    if (name.includes(" ")) {
      showToast("预设名称不能包含空格", "error");
      el.modalName.focus();
      return;
    }
    if (!state.editingName && state.presets.some((p) => p.name === name)) {
      showToast(`已存在名为「${name}」的预设，请使用其它名称`, "error");
      el.modalName.focus();
      return;
    }
    if (!artist && !positive && !negative) {
      showToast("画师串、正向词、负向词三者至少需填写一项", "error");
      return;
    }

    const action = state.editingName ? "update" : "add";
    try {
      await bridge.apiPost("presets", {
        action,
        data: { name, desc, artist, positive, negative },
      });

      if (isSetDefault && state.defaultPreset !== name) {
        await bridge.apiPost("preset/default", { name });
        state.defaultPreset = name;
      } else if (!isSetDefault && state.defaultPreset === name) {
        await bridge.apiPost("preset/default", { name: "" });
        state.defaultPreset = "";
      }

      showToast(`预设「${name}」保存成功！`);
      closeModal();
      await loadConfig();
    } catch (err) {
      showToast(`保存预设失败: ${err.message || err}`, "error");
    }
  }

  // -------------------------------------------------------------------------
  // 事件绑定
  // -------------------------------------------------------------------------

  el.reloadBtn.addEventListener("click", loadConfig);

  el.clearDefaultBtn.addEventListener("click", () => setDefaultPreset(""));

  el.btnCreatePreset.addEventListener("click", () => openModalForCreate());

  el.modalCloseBtn.addEventListener("click", closeModal);
  el.modalCancelBtn.addEventListener("click", closeModal);
  el.modalSaveBtn.addEventListener("click", saveModalPreset);

  // 点击遮罩外部关闭
  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal) closeModal();
  });

  // 快捷标签芯片点击追加
  document.getElementById("artist-quick-chips").addEventListener("click", (e) => {
    if (e.target.dataset.tag) appendTagToTextarea(el.modalArtist, e.target.dataset.tag);
  });
  document.getElementById("pos-quick-chips").addEventListener("click", (e) => {
    if (e.target.dataset.tag) appendTagToTextarea(el.modalPositive, e.target.dataset.tag);
  });
  document.getElementById("neg-quick-chips").addEventListener("click", (e) => {
    if (e.target.dataset.tag) appendTagToTextarea(el.modalNegative, e.target.dataset.tag);
  });

  // 搜索和分类过滤
  el.presetSearch.addEventListener("input", (e) => {
    state.search = e.target.value;
    renderPresetList();
  });

  el.filterChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      el.filterChips.forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.filter = chip.dataset.filter;
      renderPresetList();
    });
  });

  // 预设卡片列表事件代理
  el.presetList.addEventListener("click", (e) => {
    const target = e.target;
    const name = target.dataset.name;
    if (!name) return;

    if (target.classList.contains("btn-set-def")) {
      setDefaultPreset(name);
    } else if (target.classList.contains("btn-clear-def")) {
      setDefaultPreset("");
    } else if (target.classList.contains("btn-del-preset")) {
      deletePreset(name);
    } else if (target.classList.contains("btn-edit-preset")) {
      const preset = state.presets.find((p) => p.name === name);
      if (preset) openModalForEdit(preset);
    } else if (target.classList.contains("btn-copy-preset")) {
      const preset = state.presets.find((p) => p.name === name);
      if (preset) openModalForCreate(preset);
    }
  });

  // 预览更新
  el.previewPrompt.addEventListener("input", updatePreview);
  el.previewPresetSelect.addEventListener("change", updatePreview);

  // 直译设置
  el.translateSave.addEventListener("click", saveTranslate);
  el.translateRestore.addEventListener("click", restoreDefaultTranslatePrompt);
  el.translateRefreshModels.addEventListener("click", loadTranslateModels);
  el.translateTest.addEventListener("click", testTranslate);

  // 初始化流程：先解析 bridge，等待 ready 并监听主题，再加载配置
  async function init() {
    el.statusChip.textContent = "连接中…";
    const b = await resolveBridge();
    if (b) {
      if (typeof b.ready === "function") {
        try {
          const ctx = await b.ready();
          if (ctx && ctx.isDark !== undefined) {
            document.documentElement.setAttribute("data-theme", ctx.isDark ? "dark" : "light");
          }
        } catch (e) {
          console.warn("[Nai WebUI] bridge.ready() error:", e);
        }
      }
      if (typeof b.onContext === "function") {
        b.onContext((ctx) => {
          if (ctx && ctx.isDark !== undefined) {
            document.documentElement.setAttribute("data-theme", ctx.isDark ? "dark" : "light");
          }
        });
      }
    }
    await loadConfig();
  }

  init();
})();

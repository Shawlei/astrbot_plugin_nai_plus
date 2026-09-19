"""
Nai2API AstrBot 生图插件 (astrbot_plugin_nai_plus)

通过 Nai2API 网关调用 NovelAI 生成图片。
支持 /nai 指令、LLM 工具调用，并提供独立的 WebUI 预设管理面板。
"""

import json as _json
import re
import time
from pathlib import Path

import mcp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Node, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.image_manager import ImageManager
from .core.nai2api_client import (
    Nai2ApiClient,
    DEFAULT_ARTIST,
    DEFAULT_NEGATIVE,
    MODEL_V5_FULL,
    MODEL_V5_CURATED,
    V5_MODELS,
    is_v5_model,
    resolve_model_alias,
)
from .core.preset_manager import (
    BUILTIN_PRESETS,
    PresetManager,
    ensure_composition,
    merge_tags,
    split_negative_weights,
)
from .core.translate_client import (
    DEFAULT_TRANSLATE_SYSTEM_PROMPT,
    PromptTranslator,
)

PLUGIN_NAME = "astrbot_plugin_nai_plus"

# 版本号回退常量（正常情况下从 metadata.yaml 读取，见 _read_plugin_version）
_PLUGIN_VERSION_FALLBACK = "0.3.4"

# 匹配「唤醒前缀 + nai 指令名」的头部，用于从原始消息还原完整参数、以及展示真实前缀。
# prefix 允许最多 3 个非字母数字且非空白字符（如 '#'、'/'、'！' 等）。
_CMD_HEAD_RE = re.compile(r"^(?P<prefix>[^\w\s]{0,3})nai\b\s*", re.IGNORECASE)

# 解析用户输入中的尺寸前缀、-m/--model、-p/--preset、--artist、--negative、--seed 与 --no-preset 参数
_SIZE_PATTERN = re.compile(
    r'^(2K竖图|2K横图|2K方图|4K竖图|4K横图|4K方图|竖图|横图|方图)\s+',
    re.IGNORECASE,
)
_MODEL_PATTERN = re.compile(r'(?:-m|--model)\s+(\S+)', re.IGNORECASE)
_PRESET_PATTERN = re.compile(r'(?:-p|--preset)\s+(\S+)', re.IGNORECASE)
_SEED_PATTERN = re.compile(r'--seed\s+(\d+)', re.IGNORECASE)
_ARTIST_PATTERN = re.compile(r'--artist\s+(.+?)(?=\s+(?:--negative|-p|--preset|-m|--model|--seed|--no-preset)\s+|$)', re.DOTALL)
_NEGATIVE_PATTERN = re.compile(r'--negative\s+(.+?)(?=\s+(?:--artist|-p|--preset|-m|--model|--seed|--no-preset)\s+|$)', re.DOTALL)
_NO_PRESET_PATTERN = re.compile(r'--no-preset\b', re.IGNORECASE)


def _read_plugin_version() -> str:
    """从 metadata.yaml 读取插件版本号，读取失败时回退到常量。

    为什么：用户每次排查问题都要靠「猜版本」，把版本号读出来存进内存，
    便于日志与 /nai version 指令一眼定位。读取逻辑与 WebUI 面板保持一致。
    """
    meta_path = Path(__file__).parent / "metadata.yaml"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*version:\s*['\"]?([^'\"\s]+)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return _PLUGIN_VERSION_FALLBACK


def _recover_command_text(event: AstrMessageEvent) -> str | None:
    """从原始消息文本还原「指令名之后」的完整参数（兜底）。

    为什么：AstrBot 的 CommandFilter 在参数带默认值时会把它当普通 str，
    只把第一个词传进来（详见 nai_cmd 的说明）。这里直接读原始消息文本，
    把 `#nai` / `/nai` 之后的全部内容还原出来，即使框架行为再变也不会丢参数。
    """
    try:
        raw = event.get_message_str()
    except Exception:
        raw = getattr(event, "message_str", "") or ""
    if not raw:
        return None
    raw = re.sub(r"\s+", " ", str(raw)).strip()
    m = _CMD_HEAD_RE.match(raw)
    if not m:
        return None
    return raw[m.end():].strip()


def _cmd_head(event: AstrMessageEvent) -> str:
    """返回用户实际使用的「唤醒前缀 + 指令名」，如 '#nai'；取不到时回退 '/nai'。

    仅用于展示文案（帮助/引导），不能用于下发给 NovelAI 的提示词。
    """
    try:
        raw = event.get_message_str()
    except Exception:
        raw = getattr(event, "message_str", "") or ""
    if raw:
        m = _CMD_HEAD_RE.match(str(raw).strip())
        if m:
            return f"{m.group('prefix')}nai"
    return "/nai"


def _model_display_name(model: str) -> str:
    """把模型标识转成人类可读名称。

    注意：不能用 `"5-full" in model` 判断，因为 "nai-diffusion-4-5-full" 也包含子串
    "5-full"，会把 V4.5 误判为 V5。这里统一走 resolve_model_alias 精确匹配。
    """
    resolved = resolve_model_alias(model) or model
    if resolved == MODEL_V5_FULL:
        return "V5 Full (nai-diffusion-5-full)"
    if resolved == MODEL_V5_CURATED:
        return "V5 Curated (nai-diffusion-5-curated)"
    return model


def _clean_param_val(val: str | None) -> str | None:
    """清理用户可能误输入的尖括号、书名号、引号及末尾逗号等标点。"""
    if not val:
        return None
    val = val.strip()
    # 移除外层包裹符号: < >, 《 》, 【 】, ", '
    for l_bracket, r_bracket in (("<", ">"), ("《", "》"), ("【", "】"), ('"', '"'), ("'", "'")):
        if val.startswith(l_bracket) and val.endswith(r_bracket):
            val = val[len(l_bracket):-len(r_bracket)].strip()
    # 移除末尾逗号或分号（例如 `-p 动漫风, 1girl` 或 `-m 5, 1girl`）
    val = val.rstrip(",，.。;；")
    return val.strip() or None


def _parse_nai_command(text: str) -> tuple[str | None, str, str | None, str | None, str | None, int | None, bool, str | None]:
    """
    解析 /nai 指令的参数。

    格式: /nai [尺寸] <提示词> [-m <模型>] [-p <预设>] [--artist <质量前缀>] [--negative <负面提示词>] [--seed <种子>] [--no-preset]

    Returns:
        (size, prompt, preset_name, artist, negative, seed, no_preset, model)
    """
    text = text.strip()
    size = None

    # 提取尺寸前缀
    m = _SIZE_PATTERN.match(text)
    if m:
        size = m.group(1)
        text = text[m.end():]

    # 提取 --no-preset
    no_preset = False
    m = _NO_PRESET_PATTERN.search(text)
    if m:
        no_preset = True
        text = text[:m.start()] + text[m.end():]

    # 提取 -m / --model
    model = None
    m = _MODEL_PATTERN.search(text)
    if m:
        raw_m = _clean_param_val(m.group(1))
        model = resolve_model_alias(raw_m) if raw_m else None
        text = text[:m.start()] + text[m.end():]

    # 提取预设名
    preset_name = None
    m = _PRESET_PATTERN.search(text)
    if m:
        preset_name = _clean_param_val(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 提取种子
    seed = None
    m = _SEED_PATTERN.search(text)
    if m:
        seed = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 提取负面提示词
    negative = None
    m = _NEGATIVE_PATTERN.search(text)
    if m:
        negative = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    # 提取质量前缀/画师串
    artist = None
    m = _ARTIST_PATTERN.search(text)
    if m:
        artist = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    prompt = text.strip()
    return size, prompt, preset_name, artist, negative, seed, no_preset, model


class Nai2ApiPlugin(Star):
    """Nai2API 生图插件"""

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        # 版本号只在启动时读取一次，供日志、WebUI 与 /nai version 指令复用
        self._plugin_version = _read_plugin_version()

        api_url = str(config.get("api_url", "https://nai.sta1n.cn")).strip()
        token = str(config.get("token", "")).strip()
        timeout = int(config.get("timeout", 120))
        max_cached = int(config.get("max_cached_images", 50))

        self.client = Nai2ApiClient(
            api_url=api_url,
            token=token,
            default_size=str(config.get("default_size", "竖图")),
            default_model=str(config.get("default_model", "nai-diffusion-4-5-full")),
            default_steps=int(config.get("default_steps", 28)),
            default_scale=int(config.get("default_scale", 6)),
            default_cfg=float(config.get("default_cfg", 0)),
            default_sampler=str(config.get("default_sampler", "k_dpmpp_2m_sde")),
            default_negative=str(config.get("default_negative", DEFAULT_NEGATIVE)),
            default_artist=str(config.get("default_artist", DEFAULT_ARTIST)),
            default_noise_schedule=str(config.get("default_noise_schedule", "karras")),
            allow_2k=bool(config.get("allow_2k", True)),
            allow_4k=bool(config.get("allow_4k", True)),
            timeout=timeout,
        )

        self.imgr = ImageManager(
            data_dir=self.data_dir,
            max_cached=max_cached,
            timeout=timeout,
        )

        self.presets = PresetManager(self.data_dir, config.get("custom_presets", []))
        self._default_preset = str(config.get("default_preset", "") or "").strip()

        self._llm_tool_enabled = bool(config.get("llm_tool_enabled", True))
        self._show_image_info = bool(config.get("show_image_info", True))

        # v0.3.0 中文提示词直译器：独立于聊天主模型，只做读操作（绝不 set_using_provider）
        self.translator = PromptTranslator(context, self._translate_cfg())

        self._fix_llm_tool_schemas()
        self._register_webui_apis()
        # 启动日志带版本号：用户排查问题时不用再「猜版本」
        logger.info("[Nai2API] Nai2API 生图插件 (Plus) 已加载，版本 v%s", self._plugin_version)

    def _fix_llm_tool_schemas(self):
        """修复 LLM 工具的 JSON Schema，添加 required 字段以兼容 Gemini 等模型。"""
        try:
            from astrbot.core.provider.register import llm_tools
        except ImportError:
            return

        tool_required_map = {
            "nai_generate": ["prompt"],
            "nai_get_balance": ["detail"],
            "nai_list_presets": ["preset_name"],
            "nai_save_preset": ["name", "artist"],
            "nai_update_preset": ["name", "artist"],
            "nai_delete_preset": ["name"],
        }

        for tool_name, required_params in tool_required_map.items():
            tool = llm_tools.get_func(tool_name)
            if tool is None:
                continue
            if "required" not in tool.parameters:
                tool.parameters["required"] = list(required_params)

    async def _save_plugin_config(self, patch: dict) -> bool:
        """保存配置项到插件配置文件中。"""
        cfg = self.config
        save_async = getattr(cfg, "save_config_async", None)
        if callable(save_async):
            ok = await save_async(patch)
            return ok is not False
        save_sync = getattr(cfg, "save_config", None)
        if callable(save_sync):
            save_sync(patch)
            return True
        if isinstance(cfg, dict):
            cfg.update(patch)
        return False

    async def _persist_presets_to_config(self) -> None:
        """把自定义预设同步回写到 AstrBot 配置项中。"""
        template_list = self.presets.export_template_list()
        await self._save_plugin_config({"custom_presets": template_list})

    def _translate_cfg(self, patch: dict | None = None) -> dict:
        """从当前配置提取直译相关配置（供 PromptTranslator 使用）。

        patch 用于叠加「尚未落盘」的改动（如 WebUI 保存时）。
        """
        cfg = self.config

        def _get(key: str, default):
            try:
                return cfg.get(key, default)
            except Exception:
                return default

        out = {
            "translate_enabled": _get("translate_enabled", False),
            "translate_provider_id": _get("translate_provider_id", ""),
            "translate_system_prompt": _get("translate_system_prompt", ""),
            "translate_timeout": _get("translate_timeout", None),
        }
        if patch:
            out.update(patch)
        return out

    async def _translate_prompt(self, prompt: str) -> tuple[str, str, str | None]:
        """对用户提示词做中文直译，返回 (生效提示词, 信息标签, 失败说明)。

        铁律：直译失败**绝不静默**——失败时回退原文，并通过 translate_note 明确告知原因。
        成功时返回「 | 直译」信息标签用于图片信息行；未触发时标签为空串。
        """
        translator = getattr(self, "translator", None)
        if translator is None:
            return prompt, "", None
        try:
            result = await translator.translate(prompt)
        except Exception as e:
            # translate() 内部已兜底，这里再加一层保险：绝不让直译拖垮生图主链路
            logger.error("[Nai2API] 直译调用异常: %s", e, exc_info=True)
            return prompt, "", f"直译失败（{type(e).__name__}），本次用原文生图"

        if result.translated:
            logger.info("[Nai2API] 提示词直译成功: %r -> %r", prompt, result.text)
            return result.text, " | 直译", None
        if result.note:
            logger.warning("[Nai2API] 提示词直译未生效: %s（输入 %r）", result.note, prompt)
            return prompt, "", result.note
        return prompt, "", None

    def _register_webui_apis(self) -> None:
        """注册 AstrBot 插件独立 WebUI 预设管理面板的后端 API。"""
        try:
            from astrbot.api.web import error_response, json_response, request
        except ImportError:
            logger.info(
                "[Nai WebUI] 当前 AstrBot 版本没有 astrbot.api.web（需 >= 4.26.0），"
                "WebUI 预设管理面板不可用，指令功能不受影响"
            )
            return

        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            logger.info("[Nai WebUI] 当前 AstrBot 不支持 register_web_api，跳过面板注册")
            return

        def _plugin_version() -> str:
            # 复用启动时读取的版本号（见 _read_plugin_version），避免重复读文件
            return self._plugin_version

        # ---- API Handlers --------------------------------------------------

        async def _get_payload(*args) -> dict:
            """安全获取请求体 JSON 数据，兼容不同框架或单测调用形式。"""
            if args and hasattr(args[0], "json"):
                fn = args[0].json
                if callable(fn):
                    res = fn()
                    import asyncio as _aio
                    if _aio.iscoroutine(res):
                        return await res or {}
                    return res or {}
                return args[0].json or {}
            try:
                return await request.json(default={}) or {}
            except Exception:
                return {}

        async def api_get_config(*args, **kwargs):
            """GET config - 获取当前预设配置"""
            try:
                def_preset = str(self.config.get("default_preset", "") or "")
                return json_response({
                    "default_preset": def_preset,
                    "presets": self.presets.export_for_webui(def_preset),
                    "default_artist": str(self.config.get("default_artist", "") or ""),
                    "default_negative": str(self.config.get("default_negative", "") or ""),
                    "translate": {
                        "enabled": bool(self.config.get("translate_enabled", False)),
                        "provider_id": str(self.config.get("translate_provider_id", "") or ""),
                        "system_prompt": (
                            str(self.config.get("translate_system_prompt", "") or "")
                            or DEFAULT_TRANSLATE_SYSTEM_PROMPT
                        ),
                    },
                    "translate_default_prompt": DEFAULT_TRANSLATE_SYSTEM_PROMPT,
                    "version": _plugin_version(),
                })
            except Exception as e:
                logger.error("[Nai WebUI] 读取预设配置失败: %s", e, exc_info=True)
                return error_response(f"读取配置失败: {e}", status_code=500)

        async def api_set_default_preset(*args, **kwargs):
            """POST preset/default - 设置或取消默认预设 {"name": "..."}"""
            try:
                payload = await _get_payload(*args)
                name = str(payload.get("name") or payload.get("preset") or "").strip()
                if name and self.presets.get_entry(name) is None:
                    return error_response(f"预设「{name}」不存在，无法设为默认", status_code=400)

                await self._save_plugin_config({"default_preset": name})
                self._default_preset = name
                return json_response({"ok": True, "saved": True, "default_preset": name})
            except Exception as e:
                logger.error("[Nai WebUI] 设置默认预设失败: %s", e, exc_info=True)
                return error_response(f"保存失败: {e}", status_code=500)

        async def api_presets(*args, **kwargs):
            """POST presets - 增删改预设 {"action": "add|update|delete", "data": {...}}"""
            try:
                payload = await _get_payload(*args)
                action = str(payload.get("action", "") or "").strip().lower()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

                name = str(data.get("name", "") or "").strip()
                artist = str(data.get("artist", "") or "").strip()
                positive = str(data.get("positive", "") or "").strip()
                negative = str(data.get("negative", "") or "").strip()
                desc = str(data.get("desc", "") or "").strip()

                if action == "delete":
                    if not name:
                        return error_response("预设名不能为空", status_code=400)
                    if self.presets.is_builtin(name) and name not in self.presets.list_custom():
                        return error_response("官方内置预设不能删除", status_code=400)
                    if not self.presets.delete(name):
                        return error_response(f"预设「{name}」不存在", status_code=404)
                    if self._default_preset == name:
                        self._default_preset = ""
                        await self._save_plugin_config({"default_preset": ""})

                elif action in ("add", "update"):
                    if not name:
                        return error_response("预设名不能为空", status_code=400)
                    if " " in name:
                        return error_response("预设名不能包含空格", status_code=400)
                    if not (artist or positive or negative):
                        return error_response("画师串、正向词、负向词至少需填写一项", status_code=400)
                    if action == "add" and (name in BUILTIN_PRESETS or name in self.presets.list_custom()):
                        return error_response(f"预设「{name}」已存在，请使用其它名称或点击编辑", status_code=409)

                    self.presets.save(name, artist, desc, positive=positive, negative=negative)
                else:
                    return error_response(f"未知操作: {action}", status_code=400)

                await self._persist_presets_to_config()
                return json_response({
                    "ok": True,
                    "saved": True,
                    "presets": self.presets.export_for_webui(self._default_preset),
                })
            except Exception as e:
                logger.error("[Nai WebUI] 预设操作失败: %s", e, exc_info=True)
                return error_response(f"操作失败: {e}", status_code=500)

        async def api_preview(*args, **kwargs):
            """POST preview - 实时模拟发给 NovelAI 的参数 {"prompt": "...", "preset": "..."}"""
            try:
                payload = await _get_payload(*args)
                prompt = str(payload.get("prompt", "") or "").strip() or "1girl"
                preset_name = str(payload.get("preset", "") or "").strip() or None

                entry = self.presets.get_entry(preset_name) if preset_name else None
                preset_artist = entry.get("artist", "") if entry else ""
                preset_pos = entry.get("positive", "") if entry else ""
                preset_neg = entry.get("negative", "") if entry else ""

                final_artist = preset_artist or self.client.default_artist or ""
                clean_artist, extracted_neg = split_negative_weights(final_artist)
                final_artist = clean_artist

                final_tag = merge_tags(prompt, preset_pos) if preset_pos else prompt
                final_tag, _ = ensure_composition(final_tag, final_artist)

                final_neg = merge_tags(self.client.default_negative or "", preset_neg) if preset_neg else (self.client.default_negative or "")
                if extracted_neg:
                    final_neg = merge_tags(final_neg, extracted_neg)

                return json_response({
                    "ok": True,
                    "artist": final_artist,
                    "tag": final_tag,
                    "negative": final_neg,
                })
            except Exception as e:
                logger.error("[Nai WebUI] 预览失败: %s", e, exc_info=True)
                return error_response(f"预览失败: {e}", status_code=500)

        async def api_translate_models(*args, **kwargs):
            """GET translate/models - 列出可用作直译的对话模型（只读）"""
            try:
                models = self.translator.list_models()
                return json_response({
                    "available": self.context is not None,
                    "models": models,
                })
            except Exception as e:
                logger.error("[Nai WebUI] 获取直译模型列表失败: %s", e, exc_info=True)
                return json_response({"available": False, "models": [], "error": str(e)})

        async def api_translate_save(*args, **kwargs):
            """POST translate - 保存直译配置 {enabled, provider_id, system_prompt}"""
            try:
                payload = await _get_payload(*args)
                enabled = bool(payload.get("enabled", False))
                provider_id = str(payload.get("provider_id", "") or "").strip()
                system_prompt = str(payload.get("system_prompt", "") or "").strip()
                if not system_prompt:
                    system_prompt = DEFAULT_TRANSLATE_SYSTEM_PROMPT

                patch = {
                    "translate_enabled": enabled,
                    "translate_provider_id": provider_id,
                    "translate_system_prompt": system_prompt,
                }
                await self._save_plugin_config(patch)
                self.translator.reload(self._translate_cfg(patch))
                return json_response({
                    "ok": True,
                    "saved": True,
                    "translate": {
                        "enabled": enabled,
                        "provider_id": provider_id,
                        "system_prompt": system_prompt,
                    },
                })
            except Exception as e:
                logger.error("[Nai WebUI] 保存直译配置失败: %s", e, exc_info=True)
                return error_response(f"保存失败: {e}", status_code=500)

        async def api_translate_test(*args, **kwargs):
            """POST translate/test - 用表单未保存的值试译，不做任何持久化/内存改动"""
            try:
                payload = await _get_payload(*args)
                text = str(payload.get("text", "") or "")
                provider_id = str(payload.get("provider_id", "") or "").strip()
                system_prompt = str(payload.get("system_prompt", "") or "").strip()
                if not system_prompt:
                    system_prompt = DEFAULT_TRANSLATE_SYSTEM_PROMPT

                # 临时 translator：强制启用，绝不改动内存中的正式配置（无持久化）
                temp_cfg = {
                    "translate_enabled": True,
                    "translate_provider_id": provider_id,
                    "translate_system_prompt": system_prompt,
                    "translate_timeout": self._translate_cfg().get("translate_timeout"),
                }
                temp = PromptTranslator(self.context, temp_cfg)
                result = await temp.translate(text)
                return json_response({
                    "ok": True,
                    "input": text,
                    "translated": result.translated,
                    "text": result.text,
                    "note": result.note,
                })
            except Exception as e:
                logger.error("[Nai WebUI] 试译失败: %s", e, exc_info=True)
                return error_response(f"试译失败: {e}", status_code=500)

        endpoint_specs = [
            ("config", api_get_config, ["GET"], "NovelAI 面板：获取预设配置"),
            ("preset/default", api_set_default_preset, ["POST"], "NovelAI 面板：设置或取消默认预设"),
            ("presets", api_presets, ["POST"], "NovelAI 面板：预设增删改操作"),
            ("preview", api_preview, ["POST"], "NovelAI 面板：提示词与预设拼接预览"),
            ("translate/models", api_translate_models, ["GET"], "NovelAI 面板：列出可用直译模型"),
            ("translate", api_translate_save, ["POST"], "NovelAI 面板：保存直译配置"),
            ("translate/test", api_translate_test, ["POST"], "NovelAI 面板：试译（不保存）"),
        ]
        registered_count = 0
        for endpoint, handler, methods, desc in endpoint_specs:
            for path in (f"/{PLUGIN_NAME}/{endpoint}", endpoint):
                try:
                    register(path, handler, methods, desc)
                    registered_count += 1
                except Exception as e:
                    logger.debug("[Nai WebUI] 注册路由 %s 跳过: %s", path, e)
        logger.info("[Nai WebUI] 独立预设管理面板 API 已注册（%d 个路由项）", registered_count)

    async def terminate(self):
        """插件卸载时清理资源"""
        await self.client.close()
        await self.imgr.close()

    def _resolve_artist(self, preset_name: str | None, artist: str | None) -> str | None:
        """解析 artist：预设优先，--artist 覆盖预设"""
        if artist is not None:
            return artist
        if preset_name is not None:
            resolved = self.presets.get(preset_name)
            if resolved is not None:
                return resolved
        return None

    def _forward_result(self, event: AstrMessageEvent, title: str, content: str):
        """将查询结果以合并转发消息形式发送，不占用聊天空间"""
        node = Node(
            uin=int(event.get_sender_id()) if event.get_sender_id().isdigit() else 0,
            name=event.message_obj.sender.nickname if hasattr(event.message_obj.sender, 'nickname') else "查询结果",
            content=[Plain(f"{title}\n\n{content}")]
        )
        return event.chain_result([node])

    async def _send_image_with_info(
        self,
        event: AstrMessageEvent,
        image_path: Path,
        preset_name: str | None,
        elapsed: float,
        model: str | None = None,
        translate_tag: str = "",
        translate_note: str | None = None,
    ):
        """发送图片+信息标签。

        translate_tag: 直译成功时附加的简短标签（如「 | 直译」），未触发为空串。
        translate_note: 直译失败说明；非空时另起一行展示（铁律：失败绝不静默）。
        """
        await event.send(event.image_result(str(image_path)))
        if self._show_image_info:
            model_tag = ""
            if model:
                if is_v5_model(model):
                    model_tag = " | V5-Curated" if "curated" in model else " | V5"
                else:
                    model_tag = f" | {model.replace('nai-diffusion-', '')}"
            elif is_v5_model(self.client.default_model):
                model_tag = " | V5-Curated" if "curated" in self.client.default_model else " | V5"
            info_text = f"{preset_name or '默认'}{model_tag}{translate_tag or ''} | 耗时{int(elapsed)}秒"
            if translate_note:
                info_text += f"\n{translate_note}"
            await event.send(event.plain_result(info_text))

    async def _do_generate(
        self,
        prompt: str,
        size: str | None = None,
        artist: str | None = None,
        negative: str | None = None,
        seed: int | None = None,
        model: str | None = None,
    ) -> Path:
        """执行生图并返回本地图片路径"""
        image_bytes = await self.client.generate(
            prompt, size=size, artist=artist, negative=negative, seed=seed, model=model
        )
        return await self.imgr.save_image(image_bytes)

    # -----------------------------------------------------------------------
    # /nai 指令分发
    # -----------------------------------------------------------------------

    # ⚠️ v0.2.2 重要踩坑记录：`args: GreedyStr` 绝对不能写成 `args: GreedyStr = ""`！
    #   AstrBot 的 `astrbot/core/star/filter/command.py::CommandFilter.init_handler_md()` 中，
    #   参数「带默认值」时写入 handler_params 的是「默认值本身」而非注解类型：
    #       if v.default == inspect.Parameter.empty:
    #           self.handler_params[k] = v.annotation   # 无默认值 -> 存 GreedyStr 类型
    #       else:
    #           self.handler_params[k] = v.default      # 有默认值 -> 存 '' 这个普通字符串
    #   随后 `validate_and_convert_params()` 用 `is_greedy = param_type_or_default_val is GreedyStr`
    #   判断：拿到的是 ''（普通 str）→ is_greedy=False → 走 isinstance(..., str) 分支只取第一个 token。
    #   后果：`#nai -m 5 1girl` 的 args 只剩 '-m'，提示词变成 '-m'、模型退回默认值。
    #   用户历史上所有「-p / -m 命令没用、风格不生效」的投诉均源于此，故此处刻意不写默认值。
    #   空参场景安全：AstrBot 在 message_str == 'nai' 时传空串，isinstance("", str) 为真，
    #   下面的 `text = args.strip()` 照常工作。
    @filter.command("nai")
    async def nai_cmd(self, event: AstrMessageEvent, args: GreedyStr):
        """/nai 指令入口"""
        text = args.strip() if isinstance(args, str) else ""

        # v0.2.2 第二道保险：若框架仍把 GreedyStr 当普通 str 只传了第一个词，
        # 就从原始消息还原完整参数（谁更长用谁）。正常情况下两者一致 → 无副作用；
        # 异常时留 warning 日志，禁止静默。
        recovered = _recover_command_text(event)
        if recovered is not None and len(recovered) > len(text):
            logger.warning(
                "[Nai2API] 指令参数疑似被框架截断（收到 %r），已从原始消息还原为 %r",
                text, recovered,
            )
            text = recovered

        # 子指令分发
        if text.startswith("presets") or text.startswith("预设"):
            sub = text[len("presets"):].strip() if text.startswith("presets") else text[len("预设"):].strip()
            return self._handle_presets(event, sub)
        elif text.startswith("default") or text.startswith("默认"):
            sub = text[len("default"):].strip() if text.startswith("default") else text[len("默认"):].strip()
            return await self._handle_default_preset_cmd(event, sub)
        elif text.startswith("model") or text.startswith("模型"):
            sub = text[len("model"):].strip() if text.startswith("model") else text[len("模型"):].strip()
            return await self._handle_model_cmd(event, sub)
        elif text.startswith("-m ") or text.startswith("--model "):
            # 用户可能把 -m 当成单独切换模型的命令（如 /nai -m 5）
            sub_raw = text[len("-m "):].strip() if text.startswith("-m ") else text[len("--model "):].strip()
            parts = sub_raw.split(None, 1)
            if len(parts) == 1:
                return await self._handle_model_cmd(event, parts[0])
            return await self._handle_generate(event, text)
        elif text.startswith("-p ") or text.startswith("--preset "):
            # 用户可能把 -p 当成单独切换预设的命令（如 /nai -p 动漫风）
            sub_raw = text[len("-p "):].strip() if text.startswith("-p ") else text[len("--preset "):].strip()
            parts = sub_raw.split(None, 1)
            if len(parts) == 1:
                return await self._handle_default_preset_cmd(event, parts[0])
            return await self._handle_generate(event, text)
        elif text.startswith("save") or text.startswith("保存"):
            sub = text[len("save"):].strip() if text.startswith("save") else text[len("保存"):].strip()
            return await self._handle_save_preset(event, sub)
        elif text.startswith("update") or text.startswith("修改"):
            sub = text[len("update"):].strip() if text.startswith("update") else text[len("修改"):].strip()
            return await self._handle_update_preset(event, sub)
        elif text.startswith("del") or text.startswith("删除"):
            sub = text[len("del"):].strip() if text.startswith("del") else text[len("删除"):].strip()
            return await self._handle_del_preset(event, sub)
        elif text.startswith("balance") or text.startswith("余额") or text.startswith("点数") or text.startswith("次数"):
            return await self._handle_balance(event)
        elif text.startswith("version") or text.startswith("版本"):
            return await self._handle_version(event)

        # 生图指令
        return await self._handle_generate(event, text)

    async def _handle_generate(self, event: AstrMessageEvent, args: str):
        """文生图核心逻辑"""
        if not args:
            # P1-5：帮助文案里的前缀按用户实际唤醒前缀动态适配（如 #nai），
            # 采用整体 .replace 而非逐个 f-string 插值，避免漏改。
            help_text = (
                "Nai2API 生图插件 (Plus)\n"
                "用法:\n"
                "  /nai [尺寸] <提示词> [-m 模型] [-p 预设] [--artist 画师串] [--negative 负面] [--seed 种子] [--no-preset]\n\n"
                "示例:\n"
                "  /nai 1girl, silver hair, blue eyes\n"
                "  /nai 竖图 1girl, white dress\n"
                "  /nai -m 5 1girl, silver hair              (使用 V5 模型)\n"
                "  /nai -m 5c 1girl, blue eyes               (使用 V5 Curated 模型)\n"
                "  /nai -p 动漫风 1girl, silver hair\n"
                "  /nai 2K竖图 -p GalGame风 1girl\n"
                "  /nai 1girl --artist best quality, absurdres\n"
                "  /nai 1girl --negative bad anatomy, bad hands\n"
                "  /nai 1girl --no-preset\n\n"
                "预设管理:\n"
                "  /nai presets                     查看所有预设\n"
                "  /nai presets <预设名>             查看单个预设详情\n"
                "  /nai default <预设名>             设置默认画风预设\n"
                "  /nai default 取消                 取消默认预设\n"
                "  /nai save <名称> <质量前缀>        保存自定义预设\n"
                "  /nai update <名称> <新的质量前缀>  修改预设\n"
                "  /nai del <名称>                   删除自定义预设\n"
                "  /nai balance                     查询剩余点数\n"
                "  /nai version                     查看插件版本与当前配置"
            )
            return event.plain_result(help_text.replace("/nai", _cmd_head(event)))

        size, prompt, preset_name, artist, negative, seed, no_preset, model = _parse_nai_command(args)

        # 预设名可能是用户手输（如「韩漫风」），尽早解析为真实全名，供引导与生图统一使用
        if preset_name:
            resolved_input_preset = self.presets.resolve(preset_name)
            if resolved_input_preset and resolved_input_preset != preset_name:
                logger.info("[Nai2API] 预设名 '%s' 已按别名解析为 '%s'", preset_name, resolved_input_preset)
                preset_name = resolved_input_preset

        if not prompt:
            # 智能拦截与引导：用户可能只输入了参数而漏掉了提示词
            if preset_name and not model:
                entry = self.presets.get_entry(preset_name)
                if entry is not None:
                    return event.plain_result(
                        (
                            f"已识别到预设【{preset_name}】。\n"
                            f"• 若要以此预设单次生图，请在后面加上提示词，例如：\n"
                            f"  /nai -p {preset_name} 1girl, silver hair\n"
                            f"• 若要将此预设设为默认（后续生图免写 -p），请发送：\n"
                            f"  /nai default {preset_name}"
                        ).replace("/nai", _cmd_head(event))
                    )
                else:
                    return event.plain_result(
                        f"预设 '{preset_name}' 不存在，可用预设：{self._available_preset_names()}"
                    )
            elif model and not preset_name:
                model_name = _model_display_name(model)
                return event.plain_result(
                    (
                        f"已识别到模型【{model_name}】。\n"
                        f"• 若要以此模型生图，请在后面加上提示词，例如：\n"
                        f"  /nai -m 5 1girl, silver hair\n"
                        f"• 若要将全局默认模型切换为该模型，请发送：\n"
                        f"  /nai model 5"
                    ).replace("/nai", _cmd_head(event))
                )
            elif preset_name and model:
                return event.plain_result(
                    (
                        f"已识别到模型与预设，但未提供图片提示词。\n"
                        f"生图示例：/nai -m 5 -p {preset_name} 1girl, white dress"
                    ).replace("/nai", _cmd_head(event))
                )
            return event.plain_result(
                f"提示词不能为空。请在指令中输入图片描述，例如：{_cmd_head(event)} 1girl, silver hair"
            )

        # 确定生效预设：--no-preset 强制不使用任何预设；否则优先指令 -p，其次默认预设
        effective_preset = None if no_preset else (preset_name or self._default_preset or None)

        # P1-4：默认预设名也可能是用户手输的别名，统一解析一次（只映射，不新建/覆盖预设）
        if effective_preset:
            resolved_effective = self.presets.resolve(effective_preset)
            if resolved_effective and resolved_effective != effective_preset:
                logger.info("[Nai2API] 预设名 '%s' 已按别名解析为 '%s'", effective_preset, resolved_effective)
                effective_preset = resolved_effective

        preset_entry = None
        if effective_preset:
            preset_entry = self.presets.get_entry(effective_preset)
            if preset_entry is None and artist is None:
                return event.plain_result(
                    f"预设 '{effective_preset}' 不存在，可用预设：{self._available_preset_names()}"
                )

        preset_artist = preset_entry.get("artist", "") if preset_entry else ""
        preset_pos = preset_entry.get("positive", "") if preset_entry else ""
        preset_neg = preset_entry.get("negative", "") if preset_entry else ""

        # 1. 解析画师串/质量词：--artist 覆盖预设
        final_artist = artist if artist is not None else (preset_artist if effective_preset else self.client.default_artist)
        clean_artist, extracted_neg = split_negative_weights(final_artist or "")
        final_artist = clean_artist

        # 1.5 中文提示词直译（v0.3.0）：用户提示词含中文时先经独立 LLM 直译为英文标签
        #     直译失败会回退原文，并通过 translate_note 在图片信息下方说明原因（绝不静默）
        prompt, translate_tag, translate_note = await self._translate_prompt(prompt)

        # 2. 解析正向提示词：预设正向词追加到用户提示词后
        final_prompt = merge_tags(prompt, preset_pos) if (effective_preset and preset_pos) else prompt
        final_prompt, _ = ensure_composition(final_prompt, final_artist or "")

        # 3. 解析负向提示词：全局默认 + 预设负向词 + 画师串中提取出的负权重
        base_neg = negative if negative is not None else self.client.default_negative
        final_negative = merge_tags(base_neg or "", preset_neg) if (effective_preset and preset_neg) else (base_neg or "")
        if extracted_neg:
            final_negative = merge_tags(final_negative, extracted_neg)

        try:
            start = time.time()
            image_path = await self._do_generate(
                final_prompt, size=size, artist=final_artist, negative=final_negative, seed=seed, model=model
            )
            elapsed = time.time() - start
            display_preset = effective_preset or "默认"
            await self._send_image_with_info(
                event, image_path, display_preset, elapsed, model=model,
                translate_tag=translate_tag, translate_note=translate_note,
            )
            return None
        except Exception as e:
            logger.error("[Nai2API] 生图失败: %s", e)
            if self._show_image_info:
                elapsed = time.time() - start
                reason = str(e)[:100] if str(e) else "未知错误"
                model_tag = f" | {model}" if model else ""
                info_text = f"{effective_preset or '默认'}{model_tag} | 耗时{int(elapsed)}秒\n失败原因：{reason}"
                if translate_note:
                    info_text += f"\n{translate_note}"
                return event.plain_result(info_text)
            return event.plain_result(f"生图失败: {e}")

    def _available_preset_names(self) -> str:
        """返回顿号分隔的可用预设名，用于报错时直接告知用户（省去再发一条指令）。"""
        names = list(self.presets.list_all().keys())
        return "、".join(names) if names else "（暂无预设）"

    async def _handle_version(self, event: AstrMessageEvent):
        """查询插件版本与当前运行配置（P0-3：方便用户一眼定位版本）。"""
        model_disp = _model_display_name(self.client.default_model)
        preset_disp = self._default_preset or "（未设置，使用全局默认画师串）"
        lines = [
            "Nai2API 生图插件 (Plus)",
            f"插件版本: v{self._plugin_version}",
            f"当前默认模型: {model_disp}",
            f"当前默认预设: {preset_disp}",
            "",
            "提示: 指令前缀以你配置的唤醒前缀为准（如 #nai 或 /nai）。",
        ]
        return event.plain_result("\n".join(lines))

    async def _handle_default_preset_cmd(self, event: AstrMessageEvent, sub: str):
        """设置或查询默认预设"""
        sub = _clean_param_val(sub) or ""
        if not sub:
            if self._default_preset:
                return event.plain_result(f"当前默认预设: 【{self._default_preset}】（每次生图未加 -p 时自动套用）")
            return event.plain_result("当前未设置默认预设。用法: /nai default 预设名 或在 WebUI 面板中点击「设为默认」")

        if sub in ("cancel", "clear", "none", "取消", "关闭"):
            await self._save_plugin_config({"default_preset": ""})
            self._default_preset = ""
            return event.plain_result("已取消默认预设，恢复常规生图配置。")

        # P1-4：把用户手输的别名（如「韩漫风」）解析为真实全名
        resolved = self.presets.resolve(sub)
        if resolved and resolved != sub:
            logger.info("[Nai2API] 默认预设名 '%s' 已按别名解析为 '%s'", sub, resolved)
            sub = resolved

        if self.presets.get_entry(sub) is None:
            return event.plain_result(f"预设 '{sub}' 不存在，可用预设：{self._available_preset_names()}")

        await self._save_plugin_config({"default_preset": sub})
        self._default_preset = sub
        return event.plain_result(f"已将【{sub}】设为默认预设！每次生图未加 -p 时将自动套用其风格。")

    async def _handle_model_cmd(self, event: AstrMessageEvent, sub: str):
        """查询或切换默认生图模型"""
        sub = _clean_param_val(sub) or ""
        if not sub:
            model_disp = _model_display_name(self.client.default_model)
            lines = [
                f"当前默认模型: 【{model_disp}】",
                "",
                "常用模型切换命令:",
                "  /nai model 5        → 切换为 NovelAI V5 Full (5点/张，高精二次元)",
                "  /nai model 5c       → 切换为 NovelAI V5 Curated (5点/张，纯净版)",
                "  /nai model 4.5      → 切换为 NovelAI V4.5 (1点/张，经典版)",
                "  /nai model 4        → 切换为 NovelAI V4 (1点/张)",
                "  /nai model 3        → 切换为 NovelAI V3 (1点/张)",
                "",
                "单次临时生图示例: /nai -m 5 1girl, silver hair"
            ]
            return event.plain_result("\n".join(lines).replace("/nai", _cmd_head(event)))

        resolved = resolve_model_alias(sub)
        if not resolved:
            return event.plain_result(f"未知模型标识 '{sub}'，常用可选: 5 (V5), 5c (V5 Curated), 4.5, 4, 3 等")

        await self._save_plugin_config({"default_model": resolved})
        self.client.default_model = resolved
        model_disp = _model_display_name(resolved)
        return event.plain_result(f"已将默认生图模型切换为: 【{model_disp}】！后续生图未加 -m 时将默认使用该模型。")

    async def _handle_balance(self, event: AstrMessageEvent):
        """查询 Nai2API 余额"""
        try:
            data = await self.client.get_balance()
            balance = data.get("balance", 0)
            enabled = data.get("enabled", True)
            note = data.get("note", "")

            balance_int = int(balance)
            status = "正常" if enabled else "已禁用"
            lines = [
                f"剩余点数: {balance_int} 点",
                f"账号状态: {status}",
            ]
            if note:
                lines.append(f"备注: {note}")
            lines.append("---")
            lines.append("预计可生成:")
            lines.append(f"  V4.5 普通尺寸: ~{balance_int} 张 (1点/张)")
            lines.append(f"  V5 普通尺寸: ~{balance_int // 5} 张 (5点/张)")
            lines.append(f"  2K尺寸: ~{balance_int // 15} 张 (15点/张)")
            lines.append(f"  4K尺寸: ~{balance_int // 25} 张 (25点/张)")

            return self._forward_result(event, "Nai2API 余额查询", "\n".join(lines))
        except Exception as e:
            logger.error("[Nai2API] 查询余额失败: %s", e)
            return event.plain_result(f"查询余额失败: {e}")

    def _handle_presets(self, event: AstrMessageEvent, preset_name: str = ""):
        """查看可用预设列表或单个预设详情"""
        all_presets = self.presets.list_all()

        if preset_name:
            # P1-4：兼容用户手输别名（如「韩漫风」→「韩漫小清新风」）
            preset_name = _clean_param_val(preset_name) or preset_name
            resolved = self.presets.resolve(preset_name)
            if resolved and resolved != preset_name:
                logger.info("[Nai2API] 预设名 '%s' 已按别名解析为 '%s'", preset_name, resolved)
                preset_name = resolved

            if preset_name in all_presets:
                info = all_presets[preset_name]
                builtin_tag = " [官方内置]" if self.presets.is_builtin(preset_name) else " [自定义]"
                default_tag = " [★ 默认预设]" if preset_name == self._default_preset else ""
                desc = info.get("desc", "")
                artist_val = info.get("artist", "")
                pos_val = info.get("positive", "") or "（无）"
                neg_val = info.get("negative", "") or "（无）"
                return self._forward_result(
                    event,
                    f"预设 '{preset_name}'{builtin_tag}{default_tag}",
                    f"描述: {desc}\n\n质量前缀 (artist):\n{artist_val}\n\n附带正向词 (positive):\n{pos_val}\n\n附带负向词 (negative):\n{neg_val}"
                )
            else:
                return event.plain_result(f"预设 '{preset_name}' 不存在，可用预设：{self._available_preset_names()}")

        if not all_presets:
            return event.plain_result("暂无预设")

        lines = []
        if self._default_preset:
            lines.append(f"★ 当前默认预设：【{self._default_preset}】\n")

        for name, info in all_presets.items():
            builtin_tag = " [内置]" if self.presets.is_builtin(name) else ""
            default_tag = " [★ 默认]" if name == self._default_preset else ""
            desc = info.get("desc", "")
            artist_val = info.get("artist", "")
            lines.append(f"• {name}{builtin_tag}{default_tag} - {desc}")
            if artist_val:
                preview_text = artist_val[:60] + ("..." if len(artist_val) > 60 else "")
                lines.append(f"  画师串: {preview_text}")

        lines.append("\n使用: /nai -p <预设名> <提示词>")
        lines.append("设默认: /nai default <预设名>")
        lines.append("更多管理请在 AstrBot 插件页面打开【NovelAI 预设管理】WebUI 面板")
        return self._forward_result(event, "可用预设列表", "\n".join(lines).replace("/nai", _cmd_head(event)))

    async def _handle_save_preset(self, event: AstrMessageEvent, args: str):
        """保存自定义预设"""
        args = args.strip()
        parts = args.split(None, 1)
        if len(parts) < 2:
            return event.plain_result("用法: /nai save <名称> <质量前缀>\n示例: /nai save 我的预设 best quality, absurdres, detailed")

        name, artist = parts[0].strip(), parts[1].strip()
        if not name or not artist:
            return event.plain_result("名称和质量前缀不能为空")
        if self.presets.is_builtin(name):
            return event.plain_result(f"'{name}' 是内置预设，不能覆盖")

        is_overwrite = self.presets.get(name) is not None
        self.presets.save(name, artist)
        await self._persist_presets_to_config()
        action = "已更新" if is_overwrite else "已保存"
        return event.plain_result(f"{action}预设 '{name}': {artist}")

    async def _handle_del_preset(self, event: AstrMessageEvent, args: str):
        """删除自定义预设"""
        name = args.strip()
        if not name:
            return event.plain_result("用法: /nai del <名称>")

        name = _clean_param_val(name) or name
        # P1-4：兼容用户手输别名，先解析为真实全名再删除
        resolved = self.presets.resolve(name)
        if resolved and resolved != name:
            logger.info("[Nai2API] 待删除预设名 '%s' 已按别名解析为 '%s'", name, resolved)
            name = resolved

        if self.presets.is_builtin(name):
            return event.plain_result(f"'{name}' 是内置预设，无法删除")

        if self.presets.delete(name):
            if self._default_preset == name:
                self._default_preset = ""
                await self._save_plugin_config({"default_preset": ""})
            await self._persist_presets_to_config()
            return event.plain_result(f"已删除预设 '{name}'")
        else:
            return event.plain_result(f"预设 '{name}' 不存在，可用预设：{self._available_preset_names()}")

    async def _handle_update_preset(self, event: AstrMessageEvent, args: str):
        """修改自定义预设"""
        args = args.strip()
        parts = args.split(None, 1)
        if len(parts) < 2:
            return event.plain_result("用法: /nai update <名称> <新的质量前缀>\n示例: /nai update 我的预设 best quality, masterpiece")

        name, artist = parts[0].strip(), parts[1].strip()
        if not name or not artist:
            return event.plain_result("名称和质量前缀不能为空")

        name = _clean_param_val(name) or name
        # P1-4：兼容用户手输别名，先解析为真实全名再修改
        resolved = self.presets.resolve(name)
        if resolved and resolved != name:
            logger.info("[Nai2API] 待修改预设名 '%s' 已按别名解析为 '%s'", name, resolved)
            name = resolved

        if self.presets.is_builtin(name):
            return event.plain_result(f"'{name}' 是内置预设，无法修改")

        if self.presets.update(name, artist=artist):
            await self._persist_presets_to_config()
            return event.plain_result(f"已修改预设 '{name}': {artist}")
        else:
            return event.plain_result(f"预设 '{name}' 不存在，请先用 {_cmd_head(event)} save 保存新预设")

    # -----------------------------------------------------------------------
    # LLM 工具调用
    # -----------------------------------------------------------------------

    @filter.llm_tool(name="nai_generate")
    async def nai_generate_tool(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str = "",
        artist: str = "",
        negative: str = "",
        preset: str = "",
        seed: str = "0",
        model: str = "",
    ):
        """使用 NovelAI 生成图片。

        Args:
            prompt(string): 图片提示词，例如 "1girl, silver hair, blue eyes"
            size(string): 图片尺寸，可选 "竖图"、"横图"、"方图"、"2K竖图" 等，留空使用默认
            artist(string): 质量前缀或画师串，例如 "best quality, absurdres"，留空使用默认或预设
            negative(string): 负面提示词，留空使用默认
            preset(string): 预设名称，例如 "动漫风"、"GalGame风"，留空使用默认预设
            seed(string): 随机种子，数字字符串，"0" 表示自动随机
            model(string): 模型名称或简写，如 "5"、"5-curated"、"4.5"，留空使用默认模型
        """
        if not self._llm_tool_enabled:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="生图功能已被管理员禁用")]
            )

        if not prompt.strip():
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="提示词不能为空")]
            )

        # 解析模型参数
        final_model = resolve_model_alias(model.strip()) if model and model.strip() else None

        # 确定预设
        effective_preset = preset.strip() or self._default_preset or None
        # P1-4：LLM 传入的预设名也可能是别名（如「韩漫风」），统一解析
        if effective_preset:
            resolved_preset = self.presets.resolve(effective_preset)
            if resolved_preset and resolved_preset != effective_preset:
                logger.info("[Nai2API] LLM 工具预设名 '%s' 已按别名解析为 '%s'", effective_preset, resolved_preset)
                effective_preset = resolved_preset
        preset_entry = self.presets.get_entry(effective_preset) if effective_preset else None

        preset_artist = preset_entry.get("artist", "") if preset_entry else ""
        preset_pos = preset_entry.get("positive", "") if preset_entry else ""
        preset_neg = preset_entry.get("negative", "") if preset_entry else ""

        # 质量前缀
        final_artist = artist.strip() if artist.strip() else (preset_artist if effective_preset else self.client.default_artist)
        clean_artist, extracted_neg = split_negative_weights(final_artist or "")
        final_artist = clean_artist

        # v0.3.0 中文提示词直译：LLM 也可能传入中文提示词，同样走直译（失败回退原文）
        translated_prompt, translate_tag, translate_note = await self._translate_prompt(prompt.strip())

        # 正向词
        final_prompt = merge_tags(translated_prompt, preset_pos) if (effective_preset and preset_pos) else translated_prompt
        final_prompt, _ = ensure_composition(final_prompt, final_artist or "")

        # 负向词
        base_neg = negative.strip() if negative.strip() else self.client.default_negative
        final_negative = merge_tags(base_neg or "", preset_neg) if (effective_preset and preset_neg) else (base_neg or "")
        if extracted_neg:
            final_negative = merge_tags(final_negative, extracted_neg)

        try:
            seed_int = 0
            if seed and seed.strip():
                try:
                    seed_int = int(seed)
                except ValueError:
                    seed_int = 0
            final_seed = seed_int if seed_int else None

            start = time.time()
            image_path = await self._do_generate(
                final_prompt,
                size=size.strip() or None,
                artist=final_artist,
                negative=final_negative,
                seed=final_seed,
                model=final_model,
            )
            elapsed = time.time() - start

            display_preset = effective_preset or "默认"
            await self._send_image_with_info(
                event, image_path, display_preset, elapsed, model=final_model,
                translate_tag=translate_tag, translate_note=translate_note,
            )

            result_text = f"图片已生成并发送给用户。提示词: {final_prompt[:100]}"
            if translate_note:
                result_text += f"\n（{translate_note}）"
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(
                    type="text",
                    text=result_text
                )]
            )
        except Exception as e:
            logger.error("[Nai2API] LLM 工具生图失败: %s", e)
            if self._show_image_info:
                elapsed = time.time() - start
                reason = str(e)[:30] if str(e) else "未知错误"
                info_text = f"{effective_preset or '默认'} | 耗时{int(elapsed)}秒\n失败原因：{reason}"
                await event.send(event.plain_result(info_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"生图失败: {e}")]
            )

    @filter.llm_tool(name="nai_get_balance")
    async def nai_get_balance_tool(self, event: AstrMessageEvent, detail: str):
        """查询 Nai2API 账户余额和剩余点数。"""
        try:
            data = await self.client.get_balance()
            balance = data.get("balance", 0)
            enabled = data.get("enabled", True)
            note = data.get("note", "")

            balance_int = int(balance)
            status = "正常" if enabled else "已禁用"
            lines = [
                f"剩余点数: {balance_int} 点",
                f"账号状态: {status}",
            ]
            if note:
                lines.append(f"备注: {note}")
            lines.append("---")
            lines.append("预计可生成:")
            lines.append(f"  V4.5 普通尺寸: ~{balance_int} 张 (1点/张)")
            lines.append(f"  V5 普通尺寸: ~{balance_int // 5} 张 (5点/张)")
            lines.append(f"  2K尺寸: ~{balance_int // 15} 张 (15点/张)")
            lines.append(f"  4K尺寸: ~{balance_int // 25} 张 (25点/张)")

            result_text = "\n".join(lines)
            await event.send(self._forward_result(event, "Nai2API 余额查询", result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )
        except Exception as e:
            logger.error("[Nai2API] LLM 查询余额失败: %s", e)
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"查询余额失败: {e}")]
            )

    @filter.llm_tool(name="nai_list_presets")
    async def nai_list_presets_tool(self, event: AstrMessageEvent, preset_name: str):
        """列出所有可用预设，或查看单个预设详情。"""
        all_presets = self.presets.list_all()

        if preset_name and preset_name not in ("all", "全部"):
            # P1-4：兼容 LLM 传入手输别名（如「韩漫风」）
            resolved = self.presets.resolve(preset_name)
            if resolved and resolved != preset_name:
                logger.info("[Nai2API] LLM 查询预设名 '%s' 已按别名解析为 '%s'", preset_name, resolved)
                preset_name = resolved
            if preset_name in all_presets:
                info = all_presets[preset_name]
                desc = info.get("desc", "")
                artist_val = info.get("artist", "")
                pos_val = info.get("positive", "") or "（无）"
                neg_val = info.get("negative", "") or "（无）"
                is_def = " (默认预设)" if preset_name == self._default_preset else ""
                result_text = f"预设 '{preset_name}'{is_def}\n描述: {desc}\n质量前缀: {artist_val}\n正向词: {pos_val}\n负向词: {neg_val}"
            else:
                result_text = f"预设 '{preset_name}' 不存在，可用预设：{self._available_preset_names()}"
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )

        lines = []
        if self._default_preset:
            lines.append(f"当前默认预设: 【{self._default_preset}】")
        for name, info in all_presets.items():
            is_def = " ★" if name == self._default_preset else ""
            lines.append(f"- {name}{is_def}: {info.get('desc', '')}")
        result_text = "\n".join(lines)
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=result_text)]
        )

    @filter.llm_tool(name="nai_save_preset")
    async def nai_save_preset_tool(self, event: AstrMessageEvent, name: str, artist: str):
        """保存自定义预设。"""
        if self.presets.is_builtin(name):
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"'{name}' 是内置预设，无法覆盖")]
            )
        self.presets.save(name, artist)
        await self._persist_presets_to_config()
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=f"已保存自定义预设 '{name}': {artist}")]
        )

    @filter.llm_tool(name="nai_update_preset")
    async def nai_update_preset_tool(self, event: AstrMessageEvent, name: str, artist: str):
        """修改自定义预设。"""
        if self.presets.is_builtin(name):
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"'{name}' 是内置预设，无法修改")]
            )
        if self.presets.update(name, artist=artist):
            await self._persist_presets_to_config()
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"已修改自定义预设 '{name}': {artist}")]
            )
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=f"预设 '{name}' 不存在，请先使用 nai_save_preset 保存")]
        )

    @filter.llm_tool(name="nai_delete_preset")
    async def nai_delete_preset_tool(self, event: AstrMessageEvent, name: str):
        """删除自定义预设。"""
        if self.presets.is_builtin(name):
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"'{name}' 是内置预设，无法删除")]
            )
        if self.presets.delete(name):
            if self._default_preset == name:
                self._default_preset = ""
                await self._save_plugin_config({"default_preset": ""})
            await self._persist_presets_to_config()
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"已删除自定义预设 '{name}'")]
            )
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=f"预设 '{name}' 不存在")]
        )

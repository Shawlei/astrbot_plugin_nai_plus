"""
中文提示词 LLM 直译（v0.3.0）

设计要点（唯一真源）：
    - 直译所用的对话模型**完全独立于**聊天主模型，本模块只做「读」操作：
      `context.get_all_providers()` / `context.get_provider_by_id(id)` / `provider.text_chat(...)`。
      **绝不**调用任何写操作（如 `set_using_provider`），绝不修改用户的对话模型设置。
    - 仅当提示词含中文（CJK）时才触发直译；纯英文/符号提示词原样透传，**不会**调用任何 LLM。
    - 铁律：直译失败**绝不静默**。失败时回退原文生图，并通过 `TranslateResult.note`
      明确告知失败原因，由上层展示给用户。

⚠️ `DEFAULT_TRANSLATE_SYSTEM_PROMPT` 必须与 `_conf_schema.json` 里
   `translate_system_prompt.default` 的默认值**逐字节一致**：用户没改过配置时读取的是
   schema 那份。改这里请同步改 schema，并跑 `tests/test_webui_and_presets.py`
   里的 `TestPromptTranslation::test_default_prompt_matches_schema` 校验。
"""

import asyncio
import re
from dataclasses import dataclass

from astrbot.api import logger

# 默认直译超时（秒）。翻译比生图快得多，30s 足够；可用配置 translate_timeout 覆盖。
DEFAULT_TRANSLATE_TIMEOUT = 30.0

# 默认系统提示词。
# 设计取舍（v0.3.1）：本字段在 WebUI 里是**给用户看、给用户改**的输入框，而本插件面向
# 零基础中文用户，因此**规则条文用中文书写**，示例保留「中文输入 → 英文标签输出」的对照形式；
# 英文标签、权重语法、`artist:name` 一律原样保留不翻译。
# 强调「作品名/游戏名/动漫名 + 人物角色名」如何落到 Danbooru 消歧标签，并重申
# 「绝不臆造细节」「不确定时不加括号、不编造作品名」「主语数量必填」「权重语法原样保留」等铁律。
DEFAULT_TRANSLATE_SYSTEM_PROMPT = (
    "你是 NovelAI 生图专用的提示词翻译器。把用户的中文描述翻译成英文 Danbooru 标签，只输出标签本身。\n"
    "\n"
    "规则：\n"
    "1. 只输出英文标签，用英文逗号分隔；不要任何解释、标题、编号、引号、代码块或多余句子。\n"
    "2. 用标签短语，不要整句：写 long hair，不要写 she has long hair。\n"
    "3. 输入里本来就有的英文标签要原样保留，不要改写、重排或增删。\n"
    "4. 原样保留 NovelAI 权重语法，绝不修改：`1.2::tag::`、`{{tag}}`、`[tag]`、`-2::tag::`、`\\n20::tag::`。\n"
    "5. 原样保留画师标签，例如 `artist:name`。\n"
    "6. 尽量保持原有顺序。\n"
    "\n"
    "角色名与作品名（最重要）：\n"
    "7. 用户提到某个已知游戏 / 动漫 / 漫画 / 插画作品里的角色时，输出精确的 Danbooru 角色标签，"
    "格式为 `角色名_(作品名)`，全部小写、空格用下划线。示例：原神 雷电将军 -> raiden_shogun_(genshin_impact)；"
    "崩坏3 琪亚娜 -> kiana_kaslana_(honkai_impact_3rd)；鸣潮 达妮娅 -> dania_(wuthering_waves)。\n"
    "8. 单独出现的作品名 / 系列名，翻译成它的通用英文标签（例如 原神 -> genshin_impact，"
    "崩坏3 -> honkai_impact_3rd）。\n"
    "9. 如果不知道某角色的准确消歧写法：该角色只输出「小写 + 下划线」的罗马字名字作为标签，"
    "不要加括号、不要编造作品名、也不要凭猜测描述长相；其余描述（服装、动作、场景等）照常翻译，"
    "绝不要因为一个角色不认识就丢掉后面的内容。\n"
    "10. 不确定角色属于哪个作品时，宁可不加括号，也绝不编造作品名。\n"
    "11. 除非用户明确说了，否则绝不编造细节：不要凭空添加发色、瞳色、发型、体型、服装、姿势或表情。"
    "这条规则优先级最高。\n"
    "12. 用户只描述一个人时，必须在最前面加 `1girl`（或 `1boy`）；性别未知用 `solo`。"
    "只有用户明确要求多个人、或场景里没有人物时才省略。\n"
    "13. 不要输出两个意思相同的标签，只保留最准确的一个：泳装 -> swimsuit（不要 swimsuit + bikini）；"
    "女孩 -> 1girl（不要 1girl + female）。\n"
    "14. 把中文量词翻译成标签：双马尾 -> twintails；两把刀 -> holding two swords。\n"
    "15. 忽略尺寸、分辨率等与画面无关的指令。\n"
    "16. 拿不准时就不要加标签。宁少勿滥：准确的标签少一点，远好过瞎猜一堆。\n"
    "\n"
    "示例：\n"
    "输入：白发少女站在樱花树下，回头微笑\n"
    "输出：1girl, white hair, standing, cherry blossoms, tree, looking back, smile\n"
    "\n"
    "输入：原神 雷电将军 紫色长发 和服\n"
    "输出：1girl, raiden_shogun_(genshin_impact), purple hair, long hair, japanese clothes\n"
    "\n"
    "输入：战双 比安卡 泳装\n"
    "输出：1girl, bianca_(punishing:_gray_raven), swimsuit\n"
    "\n"
    "输入：鸣潮 达妮娅 泳装\n"
    "输出：1girl, dania_(wuthering_waves), swimsuit\n"
    "\n"
    "输入：赛博朋克风格的城市夜景，霓虹灯，雨\n"
    "输出：cyberpunk, city, night, neon lights, rain\n"
    "\n"
    "输入：{{1girl}} 1.3::silver hair:: [detailed]\n"
    "输出：{{1girl}}, 1.3::silver hair::, [detailed]\n"
    "\n"
    "输入：1girl, silver hair, looking at viewer\n"
    "输出：1girl, silver hair, looking at viewer\n"
    "\n"
    "只输出一行标签，不要任何解释。"
)

# 匹配 CJK 字符：CJK 扩展 A + 基本汉字 + 兼容表意文字 + 日文假名（平/片假名）。
# 只要命中其中之一，就认为提示词「含中文」，需要直译。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]")


class TranslateError(RuntimeError):
    """直译过程中可预期的失败（如找不到 provider）。"""


@dataclass
class TranslateResult:
    """直译结果。

    Attributes:
        text: 最终应送入生图链路的文本（成功为英文标签，失败/未触发为原文）。
        translated: 是否真的发生了直译（True 表示 text 是英文标签）。
        note: 失败说明；未触发或成功时为 None。失败说明必须非空（铁律：绝不静默）。
    """

    text: str
    translated: bool
    note: str | None = None


def has_cjk(text: str) -> bool:
    """判断文本是否包含中日文字符（用于决定是否需要直译）。"""
    if not text:
        return False
    return _CJK_RE.search(text) is not None


def _is_chat_provider(provider) -> bool:
    """粗略判断一个 provider 是不是可对话的文本模型。

    AstrBot 里可能还有 tts / stt / embedding 之类的 provider，它们不能拿来翻译。
    判断不出来时（老版本没 meta()）就当作可用，宁可多试一个也不要漏掉。
    """
    getter = getattr(provider, "meta", None)
    if not callable(getter):
        return True
    try:
        meta = getter()
    except Exception:
        return True
    if meta is None:
        return True

    ptype = str(getattr(meta, "type", "") or "").strip().lower()

    # 明确排除的非对话类型。注意不能简单用 "text" 做包含匹配 ——
    # "text_to_speech" 里也含 "text"，会把 tts 放进来。
    if any(k in ptype for k in ("speech", "tts", "stt", "audio", "image", "embed", "rerank")):
        return False

    if ptype:
        # 只放行明确是对话/文本生成的类型
        if not any(k in ptype for k in ("chat", "llm", "completion", "text")):
            return False

    # 兜底：必须真的能对话才行
    return callable(getattr(provider, "text_chat", None))


def _normalize_tag_key(tag: str) -> str:
    """标签归一化：小写 + 下划线视为空格 + 空格收敛。

    为什么下划线和空格要等价：词库里 `amiya_(arknights)`（Danbooru 原生写法）
    和 `arona (blue archive)`（NovelAI 推荐写法）并存，两种写法指的是同一个标签。
    """
    return re.sub(r"\s+", " ", (tag or "").strip().lower().replace("_", " "))


def _clean_llm_output(text: str) -> str:
    """清洗模型返回的文本，只做「去噪」不做「语义改写」。

    模型经常自作主张加上 markdown 代码块、引号、或 "Sure, here are the tags:"
    这类前缀，这些都会污染提示词，必须先洗掉。**绝不**改动标签本身的语义与顺序，
    尤其不能破坏 `1.5::a, b::` 这种内部含逗号的权重分组。
    """
    if not text:
        return ""

    result = text.strip()

    # 1. 去掉 ``` 代码块包裹（含语言标注，如 ```text）
    if "```" in result:
        first = result.find("```")
        nl = result.find("\n", first)
        if nl != -1:
            content_start = nl + 1
            last = result.rfind("```")
            if last > content_start:
                result = result[content_start:last]
            else:
                result = result[content_start:]
        else:
            # 形如 ```1girl, smile``` 的单行情况
            last = result.rfind("```")
            result = result[first + 3:last] if last > first + 3 else result[first + 3:]
        result = result.strip()

    # 2. 去掉成对的首尾引号（中英文皆可）
    if len(result) >= 2 and result[0] == result[-1] and result[0] in "\"'“”‘’《》「」":
        result = result[1:-1].strip()

    # 3. 去掉常见的解释性前缀（大小写不敏感；中英文冒号都处理）
    lowered = result.lower()
    for prefix in (
        "output:", "输出:", "输出：", "tags:", "标签:", "标签：",
        "result:", "结果:", "结果：", "translation:", "翻译:", "翻译：",
    ):
        if lowered.startswith(prefix):
            result = result[len(prefix):].strip()
            lowered = result.lower()

    # 4. 多行只保留像标签的行（含逗号者优先），取第一行
    if "\n" in result:
        lines = [ln.strip() for ln in result.split("\n") if ln.strip()]
        tagged = [ln for ln in lines if "," in ln]
        result = tagged[0] if tagged else (lines[0] if lines else result)

    # 5. 收敛空白、合并连续逗号（不拆分类单个逗号，避免破坏权重分组）
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"(?:\s*,\s*){2,}", ", ", result)
    result = result.strip().strip(",").strip()
    result = re.sub(r"\s+", " ", result)
    return result


def _validate_result(cleaned: str, original: str) -> str | None:
    """校验清洗后的直译结果，返回失败原因；通过则返回 None。"""
    if not cleaned:
        return "返回为空"
    # 只含空白/标点（没有任何字母、数字或汉字）
    if re.search(r"\w", cleaned) is None:
        return "返回为空"
    # 仍然含中文，说明模型没翻干净
    if has_cjk(cleaned):
        return "结果仍含中文"
    # 异常膨胀（正常情况下英文标签比中文原文长，但不至于长这么多）
    if len(cleaned) > len(original) * 8 + 200:
        return "结果异常过长"
    return None


class PromptTranslator:
    """中文提示词直译器（独立于聊天主模型，只读调用）。

    典型用法::

        translator = PromptTranslator(context, config)
        result = await translator.translate("原神 雷电将军 泳装")
        if result.translated:
            prompt = result.text
        else:
            prompt = result.text  # 原文，且 result.note 已说明失败原因
    """

    def __init__(self, context, config: dict):
        self.context = context
        self.enabled: bool = False
        self.provider_id: str = ""
        self.system_prompt: str = DEFAULT_TRANSLATE_SYSTEM_PROMPT
        self.timeout: float = DEFAULT_TRANSLATE_TIMEOUT
        self.reload(config)

    def reload(self, config: dict | None) -> None:
        """从配置读取直译相关设置。"""
        cfg = config or {}
        self.enabled = bool(cfg.get("translate_enabled", False))
        self.provider_id = str(cfg.get("translate_provider_id", "") or "").strip()
        prompt = str(cfg.get("translate_system_prompt", "") or "").strip()
        self.system_prompt = prompt or DEFAULT_TRANSLATE_SYSTEM_PROMPT
        try:
            self.timeout = float(cfg.get("translate_timeout", DEFAULT_TRANSLATE_TIMEOUT))
        except (TypeError, ValueError):
            self.timeout = DEFAULT_TRANSLATE_TIMEOUT
        if self.timeout <= 0:
            self.timeout = DEFAULT_TRANSLATE_TIMEOUT

    def list_models(self) -> list[dict[str, str]]:
        """列出可用作直译的对话模型，返回 [{"id","name","type"}]，出错返回空列表。

        只读取 AstrBot 已配置的 provider，不做任何写操作。
        """
        if self.context is None:
            return []

        try:
            all_providers = self.context.get_all_providers() or []
        except Exception as e:
            logger.warning("[Translate] 获取 AstrBot 模型列表失败: %s", e)
            return []

        models: list[dict[str, str]] = []
        for p in all_providers:
            if not _is_chat_provider(p):
                continue

            pid = getattr(p, "provider_id", None)
            name = None
            ptype = ""
            getter = getattr(p, "meta", None)
            if callable(getter):
                try:
                    meta = getter()
                except Exception:
                    meta = None
                if meta is not None:
                    if not pid:
                        pid = getattr(meta, "id", None)
                    name = getattr(meta, "name", None) or getattr(meta, "display_name", None)
                    ptype = str(getattr(meta, "type", "") or "")

            if not pid:
                continue
            models.append({
                "id": str(pid),
                "name": str(name or pid),
                "type": ptype,
            })
        return models

    def _resolve_provider(self):
        """按配置解析直译 provider，解析失败返回 None。"""
        if self.context is None:
            return None
        pid = (self.provider_id or "").strip()
        if not pid:
            return None
        try:
            provider = self.context.get_provider_by_id(pid)
        except Exception as e:
            logger.warning("[Translate] 获取 provider '%s' 失败: %s", pid, e)
            return None
        if provider is None:
            return None
        if not callable(getattr(provider, "text_chat", None)):
            return None
        return provider

    async def translate(self, text: str) -> TranslateResult:
        """把中文提示词直译为英文 Danbooru 标签。

        决策顺序（严格）：
            1. 空输入 → 原样返回，不触发；
            2. 未启用 → 原样返回，不触发；
            3. 不含中文 → 原样返回，不调用 LLM；
            4. provider 解析失败 → 原文 + 明确失败说明；
            5. 调用 provider.text_chat（带超时）；
            6. 清洗 + 校验；失败 → 原文 + 明确失败说明；成功 → 英文标签。
        """
        # 1. 空输入
        if not text or not text.strip():
            return TranslateResult(text=text, translated=False, note=None)

        # 2. 未启用
        if not self.enabled:
            return TranslateResult(text=text, translated=False, note=None)

        # 3. 不含中文：纯英文/符号直接透传，绝不调用 LLM
        if not has_cjk(text):
            return TranslateResult(text=text, translated=False, note=None)

        # 4. 解析 provider
        provider = self._resolve_provider()
        if provider is None:
            return TranslateResult(
                text=text,
                translated=False,
                note="直译模型未配置或已失效，本次用原文生图（请在 WebUI「提示词直译」中设置）",
            )

        # 5. 调用（带超时）
        try:
            resp = await asyncio.wait_for(
                provider.text_chat(prompt=text, system_prompt=self.system_prompt),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return TranslateResult(
                text=text,
                translated=False,
                note=f"直译失败（超时 {int(self.timeout)}s），本次用原文生图",
            )
        except TranslateError as e:
            return TranslateResult(
                text=text,
                translated=False,
                note=f"直译失败（{e}），本次用原文生图",
            )
        except Exception as e:
            return TranslateResult(
                text=text,
                translated=False,
                note=f"直译失败（{type(e).__name__}: {str(e)[:80]}），本次用原文生图",
            )

        # 兼容不同版本的返回结构
        completion = getattr(resp, "completion_text", None)
        if completion is None and hasattr(resp, "result"):
            completion = getattr(resp.result, "completion_text", None)
        raw = str(completion or "")

        # 6. 清洗 + 校验
        cleaned = _clean_llm_output(raw)
        reason = _validate_result(cleaned, text)
        if reason:
            return TranslateResult(
                text=text,
                translated=False,
                note=f"直译失败（{reason}），本次用原文生图",
            )

        return TranslateResult(text=cleaned, translated=True, note=None)

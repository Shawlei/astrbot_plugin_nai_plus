"""
提示词直译模块

把用户输入（中文或英文）翻译成 NovelAI 能用的英文标签。

支持两种后端（通过配置切换）：
1. "astrbot" —— 调用 AstrBot 已配置的模型（可轮询多个）
2. "openai"  —— 调用插件自己配的 OpenAI 兼容接口（可轮询多个）

两者都支持轮询：某个模型调用失败时自动换下一个，全部失败才报错。
"""

import asyncio
import json
from typing import Any

import aiohttp

from astrbot.api import logger


# 系统提示词
# 关键约束（这几条是踩坑总结，别随意改）：
# 1. 只输出标签，不要解释、不要 markdown、不要引号包裹
# 2. NovelAI 的权重语法（1.2::xxx::、{}、[]）很脆弱，必须原样保留
# 3. 逗号是标签分隔符，标签本身一般不含逗号
SYSTEM_PROMPT = (
    "You are a prompt translator for NovelAI image generation.\n"
    "Translate the user's description into English Danbooru-style tags.\n\n"
    "Rules:\n"
    "1. Output ONLY the tags, separated by commas. No explanation, no markdown, "
    "no quotes, no code fences, no extra sentences.\n"
    "2. Use tag keywords, not full sentences. "
    "Write 'long hair' not 'she has long hair'.\n"
    "3. If the input is already English tags, keep them and only normalize/complete them. "
    "Do NOT rewrite or reorder existing tags unnecessarily.\n"
    "4. PRESERVE any NovelAI weight syntax exactly as-is, never modify it: "
    "`1.2::tag::`, `{{tag}}`, `[tag]`, `-2::tag::`, `\\n20::tag::`.\n"
    "5. Preserve artist tags such as `artist:name`, `dino_(dinoartforame)` unchanged.\n"
    "6. Keep the original tag order as much as possible; quality tags stay where they are.\n"
    "7. For a single character, start with `1girl` or `1boy` when applicable.\n"
    "8. Never output Chinese characters in the result.\n"
    "9. If the user asks for a size or other non-visual instruction, ignore it.\n\n"
    "Output the translated tags in a single line and nothing else."
)


class TranslateError(RuntimeError):
    """翻译全部失败时抛出"""


def _clean_result(text: str) -> str:
    """清理模型返回的文本。

    模型经常会自作主张加上 markdown 代码块、引号、或者"Sure, here are the tags:"
    这类前缀，这些都会污染提示词，必须先洗掉。
    """
    if not text:
        return ""

    result = text.strip()

    # 去掉 ``` 代码块包裹
    if result.startswith("```"):
        # 去掉第一行（可能是 ``` 或 ```text）
        lines = result.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # 去掉结尾的 ```
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        result = "\n".join(lines).strip()

    # 去掉成对的首尾引号
    if len(result) >= 2 and result[0] == result[-1] and result[0] in "\"'“”‘’":
        result = result[1:-1].strip()

    # 去掉常见的解释性前缀
    for prefix in (
        "Tags:", "tags:", "Result:", "result:",
        "Here are the tags:", "Here is the translation:",
        "翻译：", "标签：", "结果：",
    ):
        if result.startswith(prefix):
            result = result[len(prefix):].strip()

    # 多行只取第一行（正常情况下应该只有一行）
    if "\n" in result:
        lines = [ln.strip() for ln in result.split("\n") if ln.strip()]
        # 过滤掉像是解释性文字的短句
        candidates = [ln for ln in lines if "," in ln or " " not in ln.strip()]
        result = candidates[0] if candidates else (lines[0] if lines else result)

    # 去掉首尾多余的逗号
    result = result.strip().strip(",").strip()

    return result


class TranslateManager:
    """提示词翻译管理器"""

    def __init__(self, config: dict, context: Any = None):
        """
        Args:
            config: 插件配置字典
            context: AstrBot 的 Context 对象（astrbot 模式需要）
        """
        self.context = context

        self.enabled = bool(config.get("translate_enabled", True))
        self.mode = str(config.get("translate_mode", "astrbot")).strip().lower()

        # astrbot 模式：可轮询多个 provider id；留空则自动使用 AstrBot 当前默认模型
        self.provider_ids = _split_list(config.get("translate_provider_ids", ""))
        self._auto_provider = not self.provider_ids
        # openai 模式：可轮询多个地址/key/模型
        self.openai_models = _parse_openai_models(config.get("translate_openai_models", ""))

        self.system_prompt = str(config.get("translate_system_prompt", "")).strip() or SYSTEM_PROMPT
        self.timeout = int(config.get("translate_timeout", 60))

        self._preferred_index = 0  # 上次成功的模型下标

        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=float(self.timeout) + 10, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def translate(self, text: str) -> str:
        """把用户输入翻译成英文标签。

        全部模型都失败时抛出 TranslateError，由调用方决定怎么处理。
        """
        text = (text or "").strip()
        if not text:
            return ""

        if self.mode == "openai":
            candidates = self.openai_models
        else:
            candidates = self._resolve_astrbot_candidates()

        if not candidates:
            if self.mode == "openai":
                raise TranslateError(
                    "翻译已开启（OpenAI 模式）但没有配置任何模型，"
                    "请到插件配置里填写「翻译模型（OpenAI 模式）」"
                )
            raise TranslateError(
                "翻译已开启（AstrBot 模式）但 AstrBot 里没有可用的模型，"
                "请先在 AstrBot 里配置模型，或在插件配置里填写「翻译模型（AstrBot 模式）」"
            )

        # 从上一次成功的模型开始试，成功就把它记住
        order = _rotate(range(len(candidates)), self._preferred_index)

        errors: list[str] = []
        for idx in order:
            try:
                if self.mode == "openai":
                    raw = await self._translate_via_openai(candidates[idx], text)
                else:
                    raw = await self._translate_via_astrbot(candidates[idx], text)

                result = _clean_result(raw)
                if not result:
                    raise TranslateError("模型返回了空内容")

                if idx != self._preferred_index:
                    logger.info(
                        "[Translate] 模型 #%d 翻译成功，切换为首选", idx + 1
                    )
                self._preferred_index = idx
                return result

            except Exception as e:
                desc = _describe_candidate(self.mode, candidates[idx])
                errors.append(f"{desc}: {e}")
                logger.warning("[Translate] %s 翻译失败，尝试下一个: %s", desc, e)

        raise TranslateError("所有翻译模型都失败了 → " + "；".join(errors))

    def _resolve_astrbot_candidates(self) -> list[Any]:
        """解析 astrbot 模式下要尝试的 provider 列表。

        配置了 translate_provider_ids 就按配置来；
        没配置就自动抓取 AstrBot 里所有已启用的对话模型（安装插件后免配置即可用）。
        """
        if self.provider_ids:
            return list(self.provider_ids)
        if self.context is None:
            return []

        providers: list[Any] = []
        try:
            all_providers = self.context.get_all_providers()
        except Exception as e:
            logger.warning("[Translate] 获取 AstrBot 模型列表失败: %s", e)
            return []

        for p in all_providers or []:
            pid = getattr(p, "provider_id", None)
            if not pid:
                meta = None
                getter = getattr(p, "meta", None)
                if callable(getter):
                    try:
                        meta = getter()
                    except Exception:
                        meta = None
                pid = getattr(meta, "id", None)
            if not pid:
                continue
            if _is_chat_provider(p):
                providers.append(str(pid))

        if providers:
            logger.info(
                "[Translate] 未指定翻译模型，自动使用 AstrBot 已配置模型: %s",
                ", ".join(providers),
            )
        return providers

    async def _translate_via_astrbot(self, provider_id: str, text: str) -> str:
        """通过 AstrBot 已配置的 provider 翻译"""
        if self.context is None:
            raise TranslateError("没有拿到 AstrBot Context，无法调用框架模型")

        provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            raise TranslateError(f"找不到 provider '{provider_id}'（可能已被删除）")

        resp = await provider.text_chat(
            prompt=text,
            system_prompt=self.system_prompt,
        )
        # 不同版本返回字段可能有差异，做个兼容
        completion = getattr(resp, "completion_text", None)
        if completion is None and hasattr(resp, "result"):
            completion = getattr(resp.result, "completion_text", None)
        return str(completion or "")

    async def _translate_via_openai(self, item: dict, text: str) -> str:
        """通过自定义 OpenAI 兼容接口翻译"""
        base_url = str(item.get("base_url", "")).rstrip("/")
        api_key = str(item.get("api_key", ""))
        model = str(item.get("model", ""))

        if not base_url or not model:
            raise TranslateError("OpenAI 接口的 base_url 或 model 没填")

        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        session = await self._get_session()
        async with session.post(url, json=payload, headers=headers) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise TranslateError(f"HTTP {resp.status}: {body[:150]}")

            try:
                data = json.loads(body)
            except Exception:
                raise TranslateError(f"返回的不是合法 JSON: {body[:150]}")

            choices = data.get("choices") or []
            if not choices:
                raise TranslateError(f"返回里没有 choices: {body[:150]}")

            message = choices[0].get("message") or {}
            return str(message.get("content") or "")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _is_chat_provider(provider: Any) -> bool:
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


def _split_list(value: Any) -> list[str]:
    """把配置里的字符串或列表切成去重的字符串列表（支持中英文逗号、分号、换行）"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value]
    else:
        text = str(value).replace("，", ",").replace("；", ",").replace(";", ",")
        raw = text.replace("\n", ",").split(",")

    seen: list[str] = []
    for item in raw:
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def _parse_openai_models(value: Any) -> list[dict]:
    """解析 OpenAI 兼容模型配置。

    支持两种写法：

    1. 简洁写法（每个模型一行，用 | 分隔 base_url|api_key|model）：
       https://api.openai.com/v1|sk-xxx|gpt-4o-mini
       https://other.com/v1|sk-yyy|qwen-plus

    2. JSON 写法：
       [{"base_url": "...", "api_key": "...", "model": "..."}, ...]
    """
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            data = json.loads(value)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except Exception as e:
            logger.warning("[Translate] OpenAI 配置 JSON 解析失败，按行解析: %s", e)

    lines = []
    if isinstance(value, (list, tuple)):
        lines = [str(v) for v in value]
    else:
        lines = str(value or "").replace("\r\n", "\n").split("\n")

    result: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            logger.warning(
                "[Translate] 这一行格式不对（需要 base_url|api_key|model）: %s", line
            )
            continue
        result.append({"base_url": parts[0], "api_key": parts[1], "model": parts[2]})
    return result


def _rotate(items: Any, start: int) -> list:
    """把序列旋转成「从 start 开始」的顺序"""
    seq = list(items)
    if not seq:
        return []
    start = max(0, min(start, len(seq) - 1))
    return seq[start:] + seq[:start]


def _describe_candidate(mode: str, item: Any) -> str:
    """生成用于日志的候选模型描述"""
    if mode == "openai" and isinstance(item, dict):
        return f"{item.get('model', '?')}@{item.get('base_url', '?')}"
    return str(item)

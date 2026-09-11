"""
提示词直译模块

把用户输入（中文或英文）翻译成 NovelAI 能用的英文标签。

特性：
1. 角色名查表加速：集成 CharacterManager，优先精准匹配热门角色官方 Tag，支持 0 延迟秒回
2. Few-shot 系统提示词：内置 6 个生动精确的输入输出示例，严格保持权重语法与标签结构
3. 中文残留检测与重试：校验模型输出，遇中文自动重试（最多1次）并执行兜底清洗
4. 双后端支持：
   - "astrbot" —— 复用 AstrBot 已配置的模型（可勾选多个做轮询）
   - "openai"  —— 调用插件自己配的 OpenAI 兼容接口
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import aiohttp

from .character_manager import CharacterManager

logger = logging.getLogger("astrbot")


# 系统提示词
# 关键约束与 Few-shot 示例
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
    "8. Never output Chinese characters in the result. All tags must be English Danbooru tags.\n"
    "9. If the user asks for a size or other non-visual instruction, ignore it.\n\n"
    "Examples:\n"
    "Input: 站在樱花树下的微笑女孩，微风吹拂长发，阳光洒落\n"
    "Output: 1girl, smiling, standing, sakura tree, cherry blossoms, falling petals, long hair, blowing hair, sunlight, dappled sunlight, outdoors\n\n"
    "Input: 赛博朋克夜景街道，雨水倒影，霓虹灯光，湿润的地面\n"
    "Output: cyberpunk, night, street, city, neon lights, glowing, wet clothes, puddle, reflection, dark ambient\n\n"
    "Input: 1.2::blue eyes::, 穿着白色连衣裙, 露肩, {{masterpiece}}\n"
    "Output: 1.2::blue eyes::, 1girl, white dress, bare shoulders, {{masterpiece}}\n\n"
    "Input: artist:wanke, 趴在床上的猫耳少女，慵懒表情，午后阳光\n"
    "Output: artist:wanke, 1girl, cat ears, lying on bed, lazy expression, looking at viewer, afternoon, sunbeam\n\n"
    "Input: -2::umbrella::, [black jacket], 雨中奔跑，动感姿态\n"
    "Output: -2::umbrella::, [black jacket], 1girl, running in rain, rain, dynamic angle, motion blur\n\n"
    "Input: 银发红瞳的吸血鬼少女，哥特洋装，红色满月背景\n"
    "Output: 1girl, silver hair, red eyes, vampire, gothic dress, full moon, red moon, night sky\n\n"
    "Output the translated tags in a single line and nothing else."
)


class TranslateError(RuntimeError):
    """翻译全部失败时抛出"""


def _has_chinese(text: str) -> bool:
    """检查文本是否包含中文字符"""
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fa5]", text))


# 成对引号映射表（支持英文及全角中文引号）
PAIRED_QUOTES = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
    "《": "》",
}


def _clean_result(text: str) -> str:
    """清理模型返回的文本。

    清洗 markdown 代码块、引号、或者解释性前缀。
    """
    if not text:
        return ""

    result = text.strip()

    # 去掉 ``` 代码块包裹
    if result.startswith("```"):
        lines = result.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        result = "\n".join(lines).strip()

    # 去掉成对的首尾引号（支持英文引号及中文全角引号，循环剥离多层引号）
    while (
        len(result) >= 2
        and result[0] in PAIRED_QUOTES
        and result[-1] == PAIRED_QUOTES[result[0]]
    ):
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
        candidates = [ln for ln in lines if "," in ln or " " not in ln.strip()]
        result = candidates[0] if candidates else (lines[0] if lines else result)

    # 去掉首尾多余的逗号
    result = result.strip().strip(",").strip()

    return result


def _filter_chinese_residue(text: str) -> str:
    """剔除翻译结果中的残留中文字符和含中文的无效标签"""
    if not text:
        return ""
    tags = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
    cleaned_tags: list[str] = []
    for tag in tags:
        if not re.search(r"[\u4e00-\u9fa5]", tag):
            # 过滤纯标点、空括号等无效残留（必须包含字母或数字）
            cleaned = tag.strip(" -_:;,")
            cleaned = re.sub(r"[\(（]\s*[\)）]", "", cleaned).strip(" -_:;,")
            if cleaned and any(c.isalnum() for c in cleaned):
                cleaned_tags.append(cleaned)
        else:
            # 尝试去除 (中文说明)
            cleaned = re.sub(r"[\(（][^\)）]*[\u4e00-\u9fa5]+[^\)）]*[\)）]", "", tag)
            # 移除剩余汉字
            cleaned = re.sub(r"[\u4e00-\u9fa5]+", "", cleaned).strip(" -_:;,")
            # 去除残留的空括号
            cleaned = re.sub(r"[\(（]\s*[\)）]", "", cleaned).strip(" -_:;,")
            # 去除未闭合或多余的单层包裹括号
            if cleaned.startswith("(") and not cleaned.endswith(")"):
                cleaned = cleaned.lstrip("(").strip()
            elif cleaned.endswith(")") and not cleaned.startswith("("):
                cleaned = cleaned.rstrip(")").strip()
            elif cleaned.startswith("(") and cleaned.endswith(")") and not cleaned.endswith("))"):
                inner = cleaned[1:-1].strip()
                if "(" not in inner and ")" not in inner:
                    cleaned = inner

            if cleaned and any(c.isalnum() for c in cleaned):
                cleaned_tags.append(cleaned)
    return ", ".join(cleaned_tags)


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

        # 角色名查表配置
        self.char_mapping_enabled = bool(config.get("char_mapping_enabled", True))
        custom_char_file = str(
            config.get("custom_characters_file", "data/custom_characters.json") or ""
        ).strip()
        if custom_char_file:
            p = Path(custom_char_file)
            if not p.is_absolute():
                base_dir = Path(__file__).resolve().parent.parent
                custom_char_path = base_dir / p
            else:
                custom_char_path = p
        else:
            custom_char_path = None

        self.char_manager = CharacterManager(custom_path=custom_char_path)

        # astrbot 模式：可轮询多个 provider id；留空则自动使用 AstrBot 当前默认模型
        self.provider_ids = _split_list(config.get("translate_provider_ids", ""))
        self._auto_provider = not self.provider_ids

        # openai 模式：单独一个端点（地址 + 密钥 + 模型）
        self.openai_endpoint = {
            "base_url": str(config.get("translate_openai_base_url", "") or "").strip(),
            "api_key": str(config.get("translate_openai_api_key", "") or "").strip(),
            "model": str(config.get("translate_openai_model", "") or "").strip(),
        }

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

        流程：
        1. 角色名查表前置提取（若开启）：
           - 纯角色名：直接返回官方 Danbooru Tag，0 延迟秒回
           - 角色名 + 描述：剩余描述给大模型翻译，角色 Tag 置于最前并去重
        2. 多模型轮询与容灾
        3. 中文残留自动检测与二次重试
        4. 兜底中文字符清洗过滤
        """
        text = (text or "").strip()
        if not text:
            return ""

        char_tags: list[str] = []
        text_to_translate = text

        # 1. 角色名查表提取
        if self.char_mapping_enabled and self.char_manager:
            remaining, extracted_tags = self.char_manager.extract_and_replace(text)
            char_tags = extracted_tags
            text_to_translate = remaining

        # 若命中角色且无剩余描述：直接返回角色 Tag，0 耗时 100% 准确
        if char_tags and not text_to_translate:
            logger.info("[Translate] 角色名命中且无剩余描述，直出官方 Tag: %s", ", ".join(char_tags))
            return ", ".join(char_tags)

        # 2. 准备翻译模型候选
        if self.mode == "openai":
            candidates = [self.openai_endpoint] if self._openai_ready() else []
        else:
            candidates = self._resolve_astrbot_candidates()

        if not candidates:
            if self.mode == "openai":
                missing = [
                    label
                    for key, label in (
                        ("base_url", "接口地址"),
                        ("model", "直译模型"),
                    )
                    if not self.openai_endpoint.get(key)
                ]
                raise TranslateError(
                    "翻译已开启（OpenAI 模式）但还没配置：" + "、".join(missing) +
                    "。请到插件配置的「OpenAI 接口地址」「直译模型」里填写"
                )
            raise TranslateError(
                "翻译已开启（AstrBot 模式）但 AstrBot 里没有可用的模型，"
                "请先在 AstrBot 里配置模型，或在插件配置里填写「翻译模型（AstrBot 模式）」"
            )

        order = _rotate(range(len(candidates)), self._preferred_index)

        errors: list[str] = []
        translated_result: str = ""

        for idx in order:
            try:
                translated_result = await self._translate_candidate(
                    candidates[idx], text_to_translate
                )
                if not translated_result:
                    raise TranslateError("模型返回了空内容")

                if idx != self._preferred_index:
                    logger.info("[Translate] 模型 #%d 翻译成功，切换为首选", idx + 1)
                self._preferred_index = idx
                break

            except Exception as e:
                desc = _describe_candidate(self.mode, candidates[idx])
                errors.append(f"{desc}: {e}")
                logger.warning("[Translate] %s 翻译失败，尝试下一个: %s", desc, e)

        if not translated_result:
            raise TranslateError("所有翻译模型都失败了 → " + "；".join(errors))

        # 3. 合并角色 Tag 与模型输出的英文标签
        if char_tags:
            trans_tags = [t.strip() for t in translated_result.split(",") if t.strip()]
            merged = list(char_tags)
            for t in trans_tags:
                if t not in merged:
                    merged.append(t)
            return ", ".join(merged)

        return translated_result

    async def _translate_candidate(self, candidate: Any, text: str) -> str:
        """调用单个候选模型进行翻译，包含中文残留校验与最多 1 次重试"""
        if self.mode == "openai":
            raw = await self._translate_via_openai(candidate, text, self.system_prompt)
        else:
            raw = await self._translate_via_astrbot(candidate, text, self.system_prompt)

        result = _clean_result(raw)

        # 中文残留校验与重试（最多 1 次）
        if _has_chinese(result):
            logger.warning("[Translate] 模型输出包含中文残留，触发防残留重试: %s", result[:60])
            retry_prompt = (
                f"{text}\n\n"
                "[CRITICAL: The previous translation contained Chinese characters. "
                "You MUST output ONLY English Danbooru tags separated by commas. "
                "NO Chinese characters are allowed under any circumstances.]"
            )
            try:
                if self.mode == "openai":
                    raw_retry = await self._translate_via_openai(
                        candidate, retry_prompt, self.system_prompt
                    )
                else:
                    raw_retry = await self._translate_via_astrbot(
                        candidate, retry_prompt, self.system_prompt
                    )
                result_retry = _clean_result(raw_retry)
                if result_retry:
                    result = result_retry
            except Exception as e:
                logger.warning("[Translate] 中文残留重试失败: %s，使用当前结果继续清洗", e)

            # 若重试后依然有残留中文，进行正则清洗与过滤
            if _has_chinese(result):
                logger.warning("[Translate] 重试后仍有中文字符，执行清洗过滤: %s", result[:60])
                result = _filter_chinese_residue(result)

        return result

    def _openai_ready(self) -> bool:
        """OpenAI 模式是否已经填够信息（地址 + 模型）"""
        return bool(self.openai_endpoint.get("base_url") and self.openai_endpoint.get("model"))

    def _resolve_astrbot_candidates(self) -> list[Any]:
        """解析 astrbot 模式下要尝试的 provider 列表"""
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

    async def _translate_via_astrbot(
        self, provider_id: str, text: str, system_prompt: str | None = None
    ) -> str:
        """通过 AstrBot 已配置的 provider 翻译"""
        if self.context is None:
            raise TranslateError("没有拿到 AstrBot Context，无法调用框架模型")

        provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            raise TranslateError(f"找不到 provider '{provider_id}'（可能已被删除）")

        sys_prompt = system_prompt or self.system_prompt
        resp = await provider.text_chat(
            prompt=text,
            system_prompt=sys_prompt,
        )
        completion = getattr(resp, "completion_text", None)
        if completion is None and hasattr(resp, "result"):
            completion = getattr(resp.result, "completion_text", None)
        return str(completion or "")

    async def _translate_via_openai(
        self, item: dict, text: str, system_prompt: str | None = None
    ) -> str:
        """通过自定义 OpenAI 兼容接口翻译"""
        base_url = _normalize_base_url(item.get("base_url", ""))
        api_key = str(item.get("api_key", ""))
        model = str(item.get("model", ""))

        if not base_url or not model:
            raise TranslateError("OpenAI 接口的地址或模型没填")

        url = f"{base_url}/chat/completions"
        sys_prompt = system_prompt or self.system_prompt
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_prompt},
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
    """粗略判断一个 provider 是不是可对话的文本模型"""
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

    if any(k in ptype for k in ("speech", "tts", "stt", "audio", "image", "embed", "rerank")):
        return False

    if ptype:
        if not any(k in ptype for k in ("chat", "llm", "completion", "text")):
            return False

    return callable(getattr(provider, "text_chat", None))


def _split_list(value: Any) -> list[str]:
    """把配置里的字符串或列表切成去重的字符串列表"""
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


def _normalize_base_url(base_url: str) -> str:
    """把用户填的地址规整成可以直接拼 /chat/completions 的形式"""
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return ""
    for tail in ("/chat/completions", "/completions", "/models"):
        if url.endswith(tail):
            url = url[: -len(tail)].rstrip("/")
    if not url.endswith("/v1") and "/v1/" not in url + "/":
        parsed_tail = url.split("//")[-1]
        has_path = "/" in parsed_tail
        if not has_path:
            url = f"{url}/v1"
    return url


def fetch_openai_models(
    base_url: str = "",
    api_key: str = "",
    timeout: float = 10.0,
) -> list[str]:
    """请求 `{base_url}/models` 并返回模型 id 列表"""
    url = _normalize_base_url(base_url)
    if not url:
        logger.warning("[Translate] 未填写接口地址，无法拉取模型列表")
        return []

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async def _do_fetch() -> Any:
        client_timeout = aiohttp.ClientTimeout(total=timeout, connect=min(8.0, timeout))
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(f"{url}/models", headers=headers) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {body[:150]}")
                return json.loads(body)

    try:
        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        if has_loop:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                data = pool.submit(lambda: asyncio.run(_do_fetch())).result(timeout + 5)
        else:
            data = asyncio.run(_do_fetch())

    except Exception as e:
        logger.warning("[Translate] 拉取模型列表失败: %s", e)
        return []

    items = data.get("data") if isinstance(data, dict) else None
    models: list[str] = []
    if isinstance(items, list):
        for item in items:
            mid = item.get("id") if isinstance(item, dict) else item
            mid = str(mid or "").strip()
            if mid and mid not in models:
                models.append(mid)
    models.sort()
    if models:
        logger.info("[Translate] 从 %s 拉取到 %d 个模型", url, len(models))
    return models


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
        return f"{item.get('model', '?')}@{_normalize_base_url(item.get('base_url', '?'))}"
    return str(item)

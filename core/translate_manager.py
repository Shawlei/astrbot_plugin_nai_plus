"""
提示词直译模块

把用户输入（中文或英文）翻译成 NovelAI 能用的英文标签。

支持两种后端（通过配置切换）：
1. "astrbot" —— 复用 AstrBot 已配置的模型（可勾选多个做轮询）
2. "openai"  —— 调用插件自己配的 OpenAI 兼容接口

两者都支持轮询：某个模型调用失败时自动换下一个，全部失败才报错。
"""

import asyncio
import json
import re
from typing import Any

import aiohttp

from astrbot.api import logger

from .prompt_dict import (
    PromptDictionary,
    apply_dictionary,
    has_cjk,
)


# 系统提示词
# 关键约束（这几条是踩坑总结，别随意改）：
# 1. 只输出标签，不要解释、不要 markdown、不要引号包裹
# 2. NovelAI 的权重语法（1.2::xxx::、{}、[]）很脆弱，必须原样保留
# 3. 逗号是标签分隔符，标签本身一般不含逗号
#
# 关于示例（few-shot）：只有规则没有例子时模型会「飘」—— 典型表现是把描写性
# 长句直译成英文句子、给标签加上冠词和 be 动词、或者把中文语序硬搬成英文。
# 下面这几组示例是按实际翻车类型挑的，每条都对应一类错误，不是为了凑数：
#   - 长句 → 标签（模型最容易犯的错）
#   - 角色名（必须落到 Danbooru 官方标签，不能音译）
#   - 权重语法（极易被模型「顺手规范化」掉）
#   - 已英文输入（模型容易自作主张重写）
#   - 抽象氛围（考的是从描述里提取标签的能力）
#
# ⚠️ v1.4.4 修改记录（别改回去）：
#   第 7 条原先是 "start with `1girl` or `1boy` **when applicable**"，太软。
#   线上日志实证：`#nai 原神神里绫华穿着泳衣在沙滩玩耍` 被翻成
#   `kamisato_ayaka_(genshin_impact), swimsuit, beach, playing` —— 人数标签整个没了。
#   人数标签是 NovelAI 最重要的主体标签（没有它模型会自己猜是一人还是多人，
#   画面主体经常跑偏），所以改成 MUST 硬约束并明确要求「放最前面」。
#
#   同时示例里有两处自相矛盾必须一并修掉，否则规则和示例打架、模型听示例的：
#     a) `战双 比安卡 泳装` / `鸣潮 达妮娅 泳装` 的 Output 自己就没带 1girl —— 补上
#     b) 赛博朋克城市示例的 Output 里有个 `1girl`，可输入压根没提人 —— 删掉
#        （它直接违反第 11 条「不许脑补」）
#   `原神 雷电将军...` 示例里的 1girl 也从末尾挪到了最前，和新规则保持一致。
#
# ⚠️ 这份文本和 `_conf_schema.json` 里 `translate_system_prompt.default` 的默认值
#    必须**逐字节一致**：用户没改过配置时读取的是 schema 那份，只改这里等于没改。
#    改完请跑 `tests/test_webui_api.py`（第 11 节有 schema 一致性断言）校验。
SYSTEM_PROMPT = (
    "You are a prompt translator for NovelAI image generation.\n"
    "Translate the user's description into English Danbooru-style tags.\n\n"
    "Rules:\n"
    "1. Output ONLY the tags, separated by commas. No explanation, no markdown, "
    "no quotes, no code fences, no extra sentences.\n"
    "2. Use tag keywords, not full sentences. "
    "Write 'long hair' not 'she has long hair'.\n"
    "3. If the input is already English tags, keep them VERBATIM. "
    "Do not rewrite, reorder, or add anything to them.\n"
    "4. PRESERVE any NovelAI weight syntax exactly as-is, never modify it: "
    "`1.2::tag::`, `{{tag}}`, `[tag]`, `-2::tag::`, `\\n20::tag::`.\n"
    "5. Preserve artist tags such as `artist:name`, `dino_(dinoartforame)` unchanged.\n"
    "6. Keep the original tag order as much as possible; quality tags stay where they are.\n"
    "7. SUBJECT COUNT IS MANDATORY. If the picture has a single person (the user named "
    "one character, or described one person), the output MUST include `1girl` or `1boy` "
    "as the FIRST tag (use `solo` if the gender is unknown). Only omit it when the user "
    "explicitly asks for multiple people or a scene with no people at all.\n"
    "8. Never output Chinese characters in the result.\n"
    "9. If the user asks for a size or other non-visual instruction, ignore it.\n"
    "10. Convert Chinese counters and quantifiers into tags: 双马尾 → twintails, "
    "两把刀 → holding two swords.\n"
    "11. NEVER invent details the user did not mention. This is the MOST IMPORTANT rule. "
    "Do NOT add hair color, eye color, hairstyle, body type, clothing, or pose unless the "
    "user explicitly said it. If the user names a character you do not recognize, output "
    "ONLY that character's name as a tag — do NOT guess how the character looks.\n"
    "12. Do NOT output two tags that mean the same thing. Pick the single most accurate "
    "one and drop the rest: 泳装 → swimsuit (NOT swimsuit + bikini), 女孩 → 1girl "
    "(NOT 1girl + female). Near-synonyms stack as extra weight and distort the image.\n"
    "13. When in doubt, do NOT add a tag. Too few accurate tags is far better than "
    "extra guessed tags.\n\n"
    "Examples:\n"
    "Input: 白发少女站在樱花树下，回头微笑\n"
    "Output: 1girl, white hair, standing, cherry blossoms, tree, looking back, smile\n\n"
    "Input: 原神 雷电将军 紫色长发 和服\n"
    "Output: 1girl, raiden shogun, purple hair, long hair, japanese clothes\n\n"
    "Input: {{1girl}} 1.3::silver hair:: [detailed]\n"
    "Output: {{1girl}}, 1.3::silver hair::, [detailed]\n\n"
    "Input: 1girl, silver hair, looking at viewer\n"
    "Output: 1girl, silver hair, looking at viewer\n\n"
    "Input: 赛博朋克风格的城市夜景，霓虹灯，雨，氛围感\n"
    "Output: cyberpunk, city, night, neon lights, rain, cinematic lighting, "
    "depth of field\n\n"
    "Input: 一个女孩抱着猫坐在床上，猫是橘色的\n"
    "Output: 1girl, holding cat, cat, orange cat, sitting, on bed, indoors\n\n"
    "Input: 战双 比安卡 泳装\n"
    "Output: 1girl, bianca_(punishing:_gray_raven), swimsuit\n\n"
    "Input: 动漫风 熟女 泳装\n"
    "Output: 1girl, mature female, swimsuit, anime style\n\n"
    "Input: 鸣潮 达妮娅 泳装\n"
    "Output: 1girl, dania_(wuthering_waves), swimsuit\n\n"
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


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _extract_cjk(text: str) -> str:
    """把文本里残留的中文片段抠出来，用于报错提示和重翻指令"""
    runs = _CJK_RUN_RE.findall(text or "")
    # 去重但保持出现顺序
    seen, out = set(), []
    for r in runs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return "、".join(out[:8])


def _dedup_tags(tags: str) -> str:
    """标签去重（保留首次出现的顺序）

    词库直译和模型翻译拼在一起时很容易出现重复 —— 比如词库把「白发」翻成
    `white hair`，模型又自己补了一个 `white hair`。重复标签在 NovelAI 里
    等于加权，会让画面过拟合，所以这里按小写去重。
    """
    parts = [p.strip() for p in (tags or "").split(",")]
    seen, out = set(), []
    for p in parts:
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return ", ".join(out)


# 近义标签组：同一组内只保留一个（保留先出现的那个）。
# 为什么需要：模型经常把「泳装」翻成 `swimsuit, bikini`，把「女孩」翻成
# `1girl, female`。在 NovelAI 里近义标签叠加 = 给这个概念额外加权，会让画面
# 在这个概念上过拟合；更麻烦的是 swimsuit 和 bikini 是**不同款式**，
# 同时写会让模型在两者之间摇摆，画出来的衣服经常不伦不类。
# 注意：只处理「确实等价」的，不做语义相近但不同的合并（如 skirt/dress 不并）。
#
# 另外：组内的每一项必须是**单个标签**，不能写 `"1girl, solo"` 这种跨标签的串。
# `_collapse_synonyms` 是按逗号切分后**逐段**比对的，任何一段都不可能等于
# `'1girl, solo'`（那是两个标签），所以那种写法是永远命中不了的死条目 ——
# 曾经真的这么写过，v1.6.0 删掉了。想表达「有 solo 就够了」请分别列 `solo`。
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("swimsuit", "bikini", "one-piece swimsuit", "school swimsuit"),
    ("1girl", "female", "woman", "girl"),
    ("1boy", "male", "man", "boy"),
)


def _collapse_synonyms(tags: str) -> str:
    """同一近义组内只保留第一个出现的标签，其余丢弃。"""
    parts = [p.strip() for p in (tags or "").split(",") if p.strip()]
    lowered = [p.lower() for p in parts]
    drop: set[int] = set()
    for group in _SYNONYM_GROUPS:
        hit_idx = [i for i, t in enumerate(lowered) if t in group and i not in drop]
        # 第一个保留，后面的丢掉
        for i in hit_idx[1:]:
            drop.add(i)
    return ", ".join(p for i, p in enumerate(parts) if i not in drop)


# 质量标签：命中这些的标签会被自动加权重。
# 为什么只给质量词加，不给普通标签加：
#   给「泳装」这类内容标签加权重会让该概念过拟合，挤掉画面其他部分
#   （这正是之前画不对的原因之一）；而质量词加权重是纯收益 ——
#   它提升整体画面精度，不与具体内容争抢表现力。
# 注意：如果标签**已经带权重语法**（1.2::xxx::），一律跳过，绝不覆盖用户意图。
_QUALITY_TAGS: frozenset[str] = frozenset({
    "best quality", "masterpiece", "absurdres", "highres",
    "very aesthetic", "aesthetic", "detailed", "ultra detailed",
    "highly detailed", "extremely detailed", "no text", "textless",
    "official art", "beautiful", "amazing quality", "high quality",
})

# 自动加权的系数。1.2 是社区常用值：明显强于裸标签，又不至于压过画师串。
_QUALITY_WEIGHT = "1.2"


# ---------------------------------------------------------------------------
# 人数标签兜底（v1.4.4 新增）
# ---------------------------------------------------------------------------

# 已经表达了「画面里有几个人」的标签。只要结果里出现任意一个，就不再补 solo。
# 为什么要把 2girls 这类也列进去：模型按第 7 条规则输出后，如果用户明确要了
# 多人，我们不希望再插一个自相矛盾的 solo。
_SUBJECT_COUNT_TAGS: frozenset[str] = frozenset({
    "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls", "multiple girls",
    "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys", "multiple boys",
    "1other", "2others", "multiple others",
    "solo", "solo focus",
    "no humans",
})

# 上面那个集合只是「快速路径」，只覆盖规范写法。它挡不住用户 / 模型写的变体：
# `7girls`（集合里没有 7 这个数）、`1girls`、`1 girl`（带空格）、`6+ girls`、
# `multiple  girls`…… 这些一旦漏掉，就会被误补一个自相矛盾的 `solo`。
# 线上复现：`esc('7girls, kamisato ayaka')` → `'solo, 7girls, kamisato ayaka'`。
# 所以 v1.6.0 再加一层「按写法识别」的兜底。
#
# 输入到这里的标签已经被 `_normalize_tag_key` 归一化过（小写 + 下划线转空格 +
# 空白收敛），所以这些正则只按**单空格**写就行。
_SUBJECT_COUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d+\s*girls?$"),                      # 1girl / 7girls / 1 girl
    re.compile(r"^\d+\s*boys?$"),                       # 1boy / 7boys / 1 boy
    re.compile(r"^\d+\s*others?$"),                     # 1other / 2others
    re.compile(r"^\d+\+\s*(?:girls|boys|others)$"),     # 6+ girls / 6+girls
    re.compile(r"^multiple\s+(?:girls|boys|others)$"),  # multiple girls / multiple  girls
    re.compile(r"^no\s+humans?$"),                      # no humans / no human
    re.compile(r"^solo(?:\s+focus)?$"),                 # solo / solo focus
)


def _is_subject_count_tag(normalized: str) -> bool:
    """判断一个**已归一化**的标签是不是「人数标签」。

    先查集合（快路径），命中不了再走正则兜底（覆盖非规范写法）。
    这样即使以后 NovelAI / 用户引入新的写法（`8girls`、`3+ boys`），也只需
    往 `_SUBJECT_COUNT_PATTERNS` 里加一条，不用维护穷举列表。
    """
    if not normalized:
        return False
    if normalized in _SUBJECT_COUNT_TAGS:
        return True
    return any(p.match(normalized) for p in _SUBJECT_COUNT_PATTERNS)

# 形如 `xxx_(yyy)` / `xxx (yyy)` 的消歧写法里，括号里是作品名的情况：
# `fate_(series)` 这种是**作品**标签，不能算作「画里有一个人」。
# 另外 `(character)` 也排除 —— 它是 Danbooru 用来区分「角色 vs 同名作品」的
# 标记（如 `inuyasha_(character)`），不代表有几个人。
_GENERIC_PAREN_SUFFIXES: frozenset[str] = frozenset({"series", "character"})

# 拆权重/括号语法用的正则：`20::`、`1.2::` 这类前缀
_WEIGHT_PREFIX_RE = re.compile(r"^[\d.]+\s*::\s*")


def _strip_tag_syntax(tag: str) -> str:
    """把一个标签剥成「裸标签」，用于判断它到底是不是人数标签 / 角色标签。

    NovelAI 的几个权重写法都要能剥掉，否则 `{{1girl}}` 会被当成一个陌生的
    标签，兜底逻辑就会误判成「结果里没有人数标签」而再插一个 solo：
        `{{1girl}}`        → `1girl`
        `[detailed]`       → `detailed`
        `1.2::1girl::`     → `1girl`
        `\\n20::1girl`      → `1girl`
    """
    t = (tag or "").strip()
    if not t:
        return ""

    # 权重语法：`1.2::tag::` / `\n20::tag::` → 取中间那段
    if "::" in t:
        parts = [p for p in t.split("::") if p.strip()]
        if len(parts) >= 2:
            t = parts[1].strip()

    t = _WEIGHT_PREFIX_RE.sub("", t).strip()

    # 剥掉任意层数的 {} / [] 包裹（`{{tag}}` 会剥两轮）
    prev = None
    while prev != t:
        prev = t
        if len(t) >= 2 and (
            (t.startswith("{{") and t.endswith("}}"))
            or (t.startswith("[[") and t.endswith("]]"))
        ):
            t = t[1:-1].strip()
        elif len(t) >= 2 and t[0] in "[{" and t[-1] in "]}":
            t = t[1:-1].strip()

    return t.strip()


def _normalize_tag_key(tag: str) -> str:
    """标签归一化：小写 + 下划线视为空格 + 空格收敛。

    为什么下划线和空格要等价：词库里 `amiya_(arknights)`（Danbooru 原生写法）
    和 `arona (blue archive)`（NovelAI 推荐写法）并存，两种写法指的是同一个
    标签。判定角色时不做归一化就会漏判。
    """
    return re.sub(r"\s+", " ", (tag or "").strip().lower().replace("_", " "))


def _looks_like_character_tag(tag: str) -> bool:
    """靠「消歧括号」猜一个标签是不是角色。

    词库里没覆盖的角色（用户自己写的、模型猜的）只能靠写法判断：Danbooru 的
    角色标签在重名时会带作品名括号（`bianca_(pgr)`、`dino_(dinoartforame)`）。

    注意排除两类，它们虽然有括号但不是角色：
        - `fate_(series)` / `inuyasha_(character)` —— 括号里是 series/character
          这类通用后缀，不是作品名
        - `artist:xxx_(yyy)` —— 画师标签
    """
    t = _normalize_tag_key(_strip_tag_syntax(tag))
    if not t or t.startswith("artist:"):
        return False

    m = re.search(r"[\(\[]([^\)\]]*)[\)\]]", t)
    if not m:
        return False

    inner = m.group(1).strip()
    # `(series)` / `(character)` 这类通用后缀不算作品名，也就不算角色
    if inner in _GENERIC_PAREN_SUFFIXES:
        return False
    return bool(inner)


def _ensure_subject_count(tags: str, character_tags: set[str] | None) -> str:
    """兜底补一个人数标签。

    为什么需要这个兜底（v1.4.4）：
        人数标签是 NovelAI 最重要的主体标签。没有它，模型得自己猜画面里是
        一个人还是几个人，主体经常跑偏。而翻译模型经常把 `1girl` 忘掉 ——
        线上实证：`原神神里绫华穿着泳衣在沙滩玩耍` 被翻成
        `kamisato_ayaka_(genshin_impact), swimsuit, beach, playing`，
        人数标签整个消失了。提示词层面已经改成硬约束（第 7 条），
        但模型不听话是常态，所以代码层再兜一道。

    判定规则：
        1. 已经有任何人数标签（`_is_subject_count_tag`，集合快速路径 +
           `_SUBJECT_COUNT_PATTERNS` 写法兜底）→ 原样返回，不碰
        2. 数一数有几个「角色标签」：
           - 命中 `character_tags`（词库里的角色名，已排除作品名）
           - 或者长得像消歧标签 `xxx_(yyy)`（词库没覆盖的角色）
        3. **恰好 1 个** → 在最前面插入 `solo`

    为什么补 `solo` 而不是 `1girl`：
        词库只存了角色名的英文标签，**没有性别信息**（`kamisato ayaka` 是女的，
        但代码无从得知）。猜错性别比不写更糟 —— `1girl` + 一个男性角色会让
        模型在两者间摇摆。`solo` 只表达「画面里只有一个人」，不分性别，
        任何情况下都不会翻车。

    为什么 0 个和 ≥2 个都不动：
        0 个 = 没有识别出角色，可能是纯风景，补 solo 是错的；
        ≥2 个 = 多人场景，人数和性别都不确定，宁缺勿错（补 `1girl` 可能把
        两个人画成一个）。

    Args:
        tags: 待处理的标签串
        character_tags: 词库里的角色标签集合（归一化后的形式）。
            传 None 或空集合时只靠括号写法判断。

    Returns:
        处理后的标签串（未命中任何条件时原样返回）
    """
    if not tags:
        return tags

    parts = [p.strip() for p in tags.split(",") if p.strip()]
    if not parts:
        return tags

    normalized = [_normalize_tag_key(_strip_tag_syntax(p)) for p in parts]

    # 规则 1：已有人数标签 → 交给模型/用户，不插手
    if any(_is_subject_count_tag(n) for n in normalized):
        return tags

    # 规则 2：数角色标签
    known = character_tags or set()
    count = 0
    for n in normalized:
        if not n:
            continue
        if n in known or _looks_like_character_tag(n):
            count += 1

    # 规则 3：恰好一个角色 → 补 solo
    if count == 1:
        return ", ".join(["solo"] + parts)

    return tags


def _wrap_quality_tags(tags: str, weight: str = _QUALITY_WEIGHT) -> str:
    """给未被加权过的质量标签套上权重语法。

    只处理「裸质量词」：已经有 `::` 权重、或已被 `{{}}`/`[]` 包裹的跳过，
    避免出现 `1.2::1.3::masterpiece::::` 这种嵌套垃圾。
    """
    if not tags:
        return tags
    out: list[str] = []
    for raw in tags.split(","):
        tag = raw.strip()
        if not tag:
            continue
        low = tag.lower()
        # 已带权重语法 / 已用花括号或方括号包裹 → 尊重原样
        if "::" in tag or tag.startswith(("{", "[")):
            out.append(tag)
            continue
        if low in _QUALITY_TAGS:
            out.append(f"{weight}::{tag}::")
        else:
            out.append(tag)
    return ", ".join(out)


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

        # openai 模式：单独一个端点（地址 + 密钥 + 模型）
        # 以前是一个多行文本框，每行 base_url|api_key|model，填起来很痛苦，
        # 现在拆成配置面板上三个独立输入框，模型还能从接口拉取。
        self.openai_endpoint = {
            "base_url": str(config.get("translate_openai_base_url", "") or "").strip(),
            "api_key": str(config.get("translate_openai_api_key", "") or "").strip(),
            "model": str(config.get("translate_openai_model", "") or "").strip(),
        }

        self.system_prompt = str(config.get("translate_system_prompt", "")).strip() or SYSTEM_PROMPT
        self.timeout = int(config.get("translate_timeout", 60))

        # 词库直译（不走模型）
        self.dictionary = PromptDictionary(
            user_path=str(config.get("translate_user_dict_path", "") or ""),
            enabled=bool(config.get("translate_dictionary_enabled", True)),
            mode=str(config.get("translate_dictionary_match", "segment") or "segment"),
        )

        # 结果校验（检查残留中文）
        self.verify_enabled = bool(config.get("translate_verify_enabled", True))
        self.verify_retry = max(0, int(config.get("translate_verify_retry", 1)))
        self.verify_on_fail = str(config.get("translate_verify_on_fail", "warn")).strip().lower()

        # 质量词自动加权（best quality / masterpiece 等 → 1.2::xxx::）
        self.quality_weight_enabled = bool(config.get("translate_quality_weight", True))
        _qw = str(config.get("translate_quality_weight_value", _QUALITY_WEIGHT)).strip() or _QUALITY_WEIGHT
        try:
            float(_qw)
            self.quality_weight_value = _qw
        except ValueError:
            logger.warning("[Translate] 质量权重值 %r 不是数字，回退默认 %s", _qw, _QUALITY_WEIGHT)
            self.quality_weight_value = _QUALITY_WEIGHT

        # 最近一次翻译的统计，供日志/调试查看
        self.last_stats: dict[str, Any] = {}

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

        整个流程分三段，越靠前越省：

        1. **词库直译** —— 命中就完成，不调模型。全部命中时直接返回
        2. **模型翻译** —— 只把词库没覆盖到的部分交给模型
        3. **结果校验** —— 检查有没有残留中文，有就重翻一次

        全部模型都失败时抛出 TranslateError，由调用方决定怎么处理。
        """
        text = (text or "").strip()
        if not text:
            return ""

        self.last_stats = {}

        # 容错底线：词库对象为空时不能让整个流程炸掉。
        # `self.dictionary` 在 `__init__` 里必然被赋成 PromptDictionary，理论上
        # 不会为 None；但一旦为 None，旧代码会在 apply_dictionary 里抛
        # AttributeError（`dictionary.enabled`），把生图整个打断。
        # 项目原则：代价只能是「该功能不生效」，绝不能是插件报错 / 生图失败。
        # 这里记一条日志、原样返回输入，让调用方继续走后面的流程。
        if self.dictionary is None:
            logger.warning(
                "[Translate] 词库未初始化（dictionary=None），本次跳过直译、原样返回"
            )
            self.last_stats = {"dict_unavailable": True}
            return text

        # ---- 第一段：词库直译 ----
        merged, remaining, hits, misses = apply_dictionary(text, self.dictionary)
        self.last_stats.update(
            {"dict_hits": hits, "dict_misses": misses, "dict_bypassed": False}
        )

        if hits and not remaining:
            # 全部命中 —— 完全不需要模型，这也是「减少 LLM 调用」的核心收益
            self.last_stats["dict_bypassed"] = True
            logger.info(
                "[Translate] 词库全命中（%d 条），跳过模型：%s",
                len(hits), "、".join(hits[:8]),
            )
            # v1.4.4 之前这里直接 `return merged`，跳过了近义折叠 / 补 solo /
            # 质量词加权 —— 结果就是词库全命中的输入反而少了一层处理。
            # 两条返回路径现在统一走 _finalize()。
            return self._finalize(merged)

        if hits:
            logger.info(
                "[Translate] 词库命中 %d 条，剩余交给模型：%s",
                len(hits), remaining[:80],
            )

        # 交给模型的是「未命中的部分」，已命中的部分等模型翻完再拼回去
        to_translate = remaining or text

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

        translated = await self._translate_with_candidates(candidates, to_translate)

        # ---- 第三段：结果校验 ----
        translated = await self._verify_and_fix(translated, candidates, to_translate)

        # 已命中的部分要拼回去：顺序上保持「词库结果在前」，
        # 因为用户输入的往往是「白发 双马尾」这类属性词在前、描述在后
        if hits and translated:
            final = _dedup_tags(f"{merged}, {translated}")
        elif hits:
            final = merged
        else:
            final = translated

        return self._finalize(final)

    def _finalize(self, tags: str) -> str:
        """两条返回路径（词库全命中 / 走了模型）共用的收尾。

        顺序有讲究，别乱换：
            1. 折叠近义标签（swimsuit+bikini → swimsuit）—— 先做，否则模型补出的
               近义词会和词库结果一起被后面的步骤当成两个概念
            2. 补人数标签（v1.4.4）—— 放在折叠之后，因为折叠可能把
               `1girl, female` 收成 `1girl`，这时就不该再补 solo；
               又必须在质量词加权之前，否则 `1.2::1girl::` 已经被包了权重，
               虽然 _strip_tag_syntax 能剥，但少一层依赖更稳
            3. 质量词自动加权 —— 最后一步，去重和折叠都做完后再套权重，
               避免出现 `1.2::best quality::` 与 `best quality` 同时存在
        """
        final = _collapse_synonyms(tags)

        final = _ensure_subject_count(final, self._character_tags())

        if self.quality_weight_enabled:
            final = _wrap_quality_tags(final, self.quality_weight_value)

        return _clean_result(final)

    def _character_tags(self) -> set[str]:
        """拿词库里的角色标签集合，任何异常都退成空集合。

        self.dictionary 可能是 None（测试里手动置空）、可能 disabled、
        也可能是老版本没有 character_tags 属性 —— 都不能让翻译炸掉。
        退成空集合时 _ensure_subject_count 只靠括号写法判断，功能降级但可用。
        """
        d = getattr(self, "dictionary", None)
        if d is None or not getattr(d, "enabled", False):
            return set()
        tags = getattr(d, "character_tags", None)
        return tags if isinstance(tags, set) else set()

    async def _translate_with_candidates(self, candidates: list[Any], text: str) -> str:
        """带轮询地调用模型翻译（某个失败自动换下一个）"""
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
                    logger.info("[Translate] 模型 #%d 翻译成功，切换为首选", idx + 1)
                self._preferred_index = idx
                return result

            except Exception as e:
                desc = _describe_candidate(self.mode, candidates[idx])
                errors.append(f"{desc}: {e}")
                logger.warning("[Translate] %s 翻译失败，尝试下一个: %s", desc, e)

        raise TranslateError("所有翻译模型都失败了 → " + "；".join(errors))

    async def _verify_and_fix(
        self, result: str, candidates: list[Any], original: str
    ) -> str:
        """校验翻译结果里有没有残留中文，有就重翻

        为什么单独做这一步：模型「翻一半就交卷」是很常见的行为 ——
        尤其是输入里的中文专有名词，模型会直接原样抄回来。这种结果
        NovelAI 完全无法理解（它只认英文标签），但界面上看起来「翻过了」，
        所以必须在送出去之前拦一道。

        Returns:
            修正后的结果。重翻后仍有中文时按 verify_on_fail 决定是原样返回
            （由上层提示）还是抛错。
        """
        if not self.verify_enabled or not result:
            return result

        if not has_cjk(result):
            self.last_stats["verify"] = "ok"
            return result

        leftovers = _extract_cjk(result)
        self.last_stats["verify"] = "cjk-left"
        self.last_stats["leftover"] = leftovers

        if self.verify_retry <= 0:
            logger.warning("[Translate] 结果里残留中文（未开启重翻）：%s", leftovers)
            return result

        # 重翻时把「有中文没翻掉」这件事明确说给模型，比原样再问一次有效得多
        retry_input = (
            f"{original}\n\n"
            f"NOTE: your previous answer still contained Chinese characters "
            f"({leftovers}). Translate EVERYTHING into English tags. "
            f"Never output any Chinese character."
        )

        for attempt in range(self.verify_retry):
            try:
                retried = await self._translate_with_candidates(candidates, retry_input)
            except TranslateError as e:
                logger.warning("[Translate] 重翻失败（第 %d 次）：%s", attempt + 1, e)
                break

            if not has_cjk(retried):
                logger.info("[Translate] 重翻成功，已消除残留中文：%s", leftovers)
                self.last_stats["verify"] = "fixed"
                return retried

            leftovers = _extract_cjk(retried)
            retry_input = retry_input.replace(leftovers, "").strip()
            logger.warning(
                "[Translate] 重翻后仍有中文（第 %d 次）：%s", attempt + 1, leftovers
            )

        self.last_stats["verify"] = "failed"
        if self.verify_on_fail == "abort":
            raise TranslateError(
                f"翻译结果里仍有中文标签「{leftovers}」，已按你的设置终止生图。"
                f"可以把这些词加进自定义词库，或换个直译模型再试"
            )
        return result

    def _openai_ready(self) -> bool:
        """OpenAI 模式是否已经填够信息（地址 + 模型）"""
        return bool(self.openai_endpoint.get("base_url") and self.openai_endpoint.get("model"))

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
        base_url = _normalize_base_url(item.get("base_url", ""))
        api_key = str(item.get("api_key", ""))
        model = str(item.get("model", ""))

        if not base_url or not model:
            raise TranslateError("OpenAI 接口的地址或模型没填")

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
    """兼容旧版配置：把多行 `base_url|api_key|model` 解析成端点列表。

    仅用于测试/兼容历史数据。新版配置已经拆成三个独立输入框，
    解析工作由 TranslateManager.__init__ 直接读字段完成。
    """
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            data = json.loads(value)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except Exception as e:
            logger.warning("[Translate] OpenAI 配置 JSON 解析失败，按行解析: %s", e)

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


# ---------------------------------------------------------------------------
# 拉取 OpenAI 兼容接口的模型列表
#
# 配置面板上「直译模型」旁边有个「获取模型列表」按钮，点了以后刷新配置页，
# AstrBot 会去读这里返回的列表 —— 所以这里必须是个同步函数（配置面板是同步渲染的）。
# 同步函数里不能 await，所以直接读配置缓存 + 用子进程跑一段异步请求。
# ---------------------------------------------------------------------------

def _normalize_base_url(base_url: str) -> str:
    """把用户填的地址规整成可以直接拼 /chat/completions 的形式。

    常见写法都能吃：
        https://api.openai.com            → https://api.openai.com/v1
        https://api.openai.com/v1/        → https://api.openai.com/v1
        https://api.openai.com/v1/models  → https://api.openai.com/v1   （去掉误填的尾巴）
    """
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return ""
    for tail in ("/chat/completions", "/completions", "/models"):
        if url.endswith(tail):
            url = url[: -len(tail)].rstrip("/")
    # 本机 / 局域网地址通常走 /v1；显式带了版本号或路径的不动
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
    """请求 `{base_url}/models` 并返回模型 id 列表。

    同步函数（AstrBot 的配置面板就要求同步返回），内部用工作线程跑 aiohttp，
    避免和已在运行的事件循环冲突（aiohttp 的 asyncio.run 会直接报错）。

    任何失败都只打日志并返回空列表 —— 配置面板拉不到列表不该让整个页面挂掉，
    用户仍然可以手动填模型名。
    """
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
            # 已经有事件循环在跑（AstrBot 是异步框架），另起线程跑
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

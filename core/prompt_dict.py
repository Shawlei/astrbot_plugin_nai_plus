"""
词库直译（不走模型）

为什么要有这个东西：翻译模型每次调用都要花时间，而且角色名这种专有名词
模型经常翻车 —— 「雷电将军」被翻成 `raiden shogun`（对）还是 `thunder general`
（错）全看运气。但它其实是个**确定性**问题：改名有官方 Danbooru 标签，
查表就是 100% 正确，还不用等网络。

所以流程变成：

    用户输入 → 词库切分/替换 → 剩下没命中的部分才交给模型

两个好处：
1. **准确率**：命中词库的部分永远不会错
2. **省钱省时**：命中率足够高时，大量请求根本不需要调用模型
   （纯标签型输入，比如「白发 双马尾 微笑」，可以完全不出网）

## 两种匹配模式

- `segment`（默认，推荐）：按逗号/空格/标点把输入切成词，逐词查表。
  不会误伤 —— 「黑」不会去替换「黑猫」里的一部分。
- `substring`：在整句里做子串替换。能命中「白发少女站在樱花树下」这种
  连写句子，但短词有误替换风险（所以按词长从长到短替换，降低风险）。

## 词库格式

支持 `.json` 和 `.txt` 两种。JSON 用于结构化（带分类，方便维护）：

    {
      "characters": {"雷电将军": "raiden shogun"},
      "hair": {"白发": "white hair"},
      "pose": {"回头": "looking back"}
    }

TXT 用于快速追加，每行 `中文=英文` 或 `中文,英文`，`#` 开头是注释：

    雷电将军=raiden shogun
    白发=white hair

内置词库在 `core/prompt_dict/` 下，按主题分文件。用户词库与内置词库合并，
**用户的优先级更高**（方便覆盖内置里写不对的条目）。
"""

import json
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

# 内置词库目录
_BUILTIN_DICT_DIR = Path(__file__).resolve().parent / "prompt_dict"


# ---------------------------------------------------------------------------
# 词库加载
# ---------------------------------------------------------------------------


def _load_json_dict(path: Path) -> dict[str, str]:
    """加载 JSON 词库。支持两种结构：
    - 扁平：{"中文": "english"}
    - 分组：{"characters": {"中文": "english"}, ...}
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[PromptDict] 加载 JSON 词库失败 %s: %s", path, e)
        return {}

    if not isinstance(data, dict):
        logger.warning("[PromptDict] %s 顶层不是对象，已跳过", path)
        return {}

    out: dict[str, str] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue  # _comment 之类的说明字段
        if isinstance(value, dict):
            # 分组结构，展开一层
            for k, v in value.items():
                # 组内同样要跳过 _comment / _note 之类的说明字段，
                # 否则它们会被当成真实词条加载进去
                if k.startswith("_"):
                    continue
                if isinstance(v, str) and k:
                    out[k] = v
        elif isinstance(value, str):
            out[key] = value
    return out


def _load_txt_dict(path: Path) -> dict[str, str]:
    """加载 TXT 词库，每行 `中文=英文` 或 `中文,英文`"""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[PromptDict] 加载 TXT 词库失败 %s: %s", path, e)
        return {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # 英文里可能有逗号（多标签），所以优先按 = 切；没有 = 才按第一个逗号切
        if "=" in line:
            zh, _, en = line.partition("=")
        elif "," in line:
            zh, _, en = line.partition(",")
        else:
            continue
        zh, en = zh.strip(), en.strip()
        if zh and en:
            out[zh] = en
    return out


def load_dict_file(path: Path) -> dict[str, str]:
    """按扩展名加载一个词库文件"""
    if not path.exists() or not path.is_file():
        return {}
    if path.suffix.lower() == ".json":
        return _load_json_dict(path)
    return _load_txt_dict(path)


def load_builtin_dict() -> dict[str, str]:
    """加载内置词库目录下的所有文件（按文件名排序，保证结果稳定）"""
    merged: dict[str, str] = {}
    if not _BUILTIN_DICT_DIR.is_dir():
        logger.warning("[PromptDict] 内置词库目录不存在: %s", _BUILTIN_DICT_DIR)
        return merged

    for path in sorted(_BUILTIN_DICT_DIR.iterdir()):
        if path.suffix.lower() not in (".json", ".txt"):
            continue
        part = load_dict_file(path)
        if part:
            merged.update(part)
            logger.debug("[PromptDict] 载入内置词库 %s（%d 条）", path.name, len(part))
    return merged


# ---------------------------------------------------------------------------
# 角色标签集合（v1.4.4）
# ---------------------------------------------------------------------------

# 只有 characters.json 里的值才算「角色」。三个 json 合并进 entries 后来源就丢了，
# 所以这里单独再读一次 characters.json。
_CHARACTER_DICT_FILE = "characters.json"


def _normalize_tag_key(tag: str) -> str:
    """小写 + 下划线视为空格 + 空格收敛。和 translate_manager 里的同名函数
    保持一致（两边都要能对上同一个 key，故意不互相 import 以免循环依赖）。"""
    return re.sub(r"\s+", " ", (tag or "").strip().lower().replace("_", " "))


def _is_series_tag(en: str, norm: str, paren_series: set[str]) -> bool:
    """判断 characters.json 里的一条**值**是不是「作品名」而非「角色名」。

    为什么要区分：characters.json 每个分组开头都放了作品名本身（`原神` →
    `genshin impact`、`鸣潮` → `wuthering_waves`），它们和角色名混在一起。
    `_ensure_subject_count` 要靠这个集合数「画里有几个角色」，作品名混进去
    会把 `原神 神里绫华` 数成 2 个角色，导致该补 solo 时不补。

    三条判据（任一命中即是作品名）：
        1. 值本身以 `(series)` 结尾：`fate_(series)`、`nier (series)`
        2. 值（归一化后）出现在其他值的消歧括号里：`wuthering waves` 出现在
           `denia_(wuthering_waves)` 的括号里 → 它是作品名。这条能自动覆盖
           词库里大部分作品名，以后加新分组不用改代码
        3. 显式列表 `_EXTRA_SERIES_TAGS`：没有任何角色用它做消歧后缀的作品名
           （`genshin impact` 的角色标签都是裸名 `kamisato ayaka`，不带括号）
    """
    if norm.endswith("(series)"):
        return True
    if norm in paren_series:
        return True
    if norm in _EXTRA_SERIES_TAGS:
        return True
    return False


# 没法从括号自动派生、只能手写的作品名 / 非角色词条。
# 判断依据：该分组的角色标签都是裸名（不带作品消歧括号），或者这条根本不是人。
# 加分组时如果角色都是裸名，记得把作品名补到这里 —— 否则 `原神 XX` 会被数成
# 两个角色。测试 `test_prompt_dict_quality.py` 会检查 `genshin impact` 不在集合里。
_EXTRA_SERIES_TAGS: frozenset[str] = frozenset({
    # 作品名（分组首条）
    "genshin impact", "zenless zone zero", "fate/grand order", "honkai impact 3rd",
    "bocchi the rock!", "hololive", "nijisanji", "virtual youtuber", "vshojo",
    "punishing:gray raven", "gensokyo", "luofu",
    # anime_classic 分组里的作品名（角色多为裸名）
    "sousou no frieren", "mahou shoujo madoka magica", "k-on!", "shingeki no kyojin",
    "kimetsu no yaiba", "jujutsu kaisen", "chainsaw man", "spy x family",
    "violet evergarden", "kimi no na wa", "tenki no ko", "detective conan",
    "bishoujo senshi sailor moon", "neon genesis evangelion", "code geass",
    "steins;gate", "toaru kagaku no railgun", "sword art online",
    "re:zero kara hajimeru isekai seikatsu", "kono subarashii sekai ni shukufuku wo!",
    "hyouka", "yahari ore no seishun love comedy wa machigatteiru",
    "seishun buta yarou wa bunny girl senpai no yume wo minai",
    "saenai heroine no sodatekata", "mahou shoujo lyrical nanoha",
    "toaru majutsu no index", "mahou shoujo", "date a live", "strike witches",
    "lucky star", "chuunibyou demo koi ga shitai!", "dantalian no shoka",
    "kuroshitsuji", "tokyo ghoul", "one punch man", "gintama", "d.gray-man",
    "kuroko no basket", "tennis no oujisama", "slam dunk", "one piece", "bleach",
    "dragon ball", "dragon ball z", "dragon ball super", "marvel (comics)",
    # game_others
    "nier:automata", "final fantasy", "overwatch",
    # 非角色的杂项（种族 / 道具 / 效果 / 画风）—— 它们出现在 characters.json
    # 只是因为归到了作品分组下，但不是「一个人」
    "ajin", "saiyan", "super saiyan", "martial arts uniform", "kamehameha",
    "aura", "golden aura", "power level", "manhwa", "anime", "comic",
})


def load_character_tags() -> set[str]:
    """从 characters.json 提取「角色标签」集合（归一化后的值），排除作品名。

    只读内置词库，不含用户自定义词库 —— 用户词库没有分文件的来源信息，
    分不清哪条是角色哪条是场景，全塞进来会污染判定。
    """
    path = _BUILTIN_DICT_DIR / _CHARACTER_DICT_FILE
    if not path.is_file():
        return set()

    values = [en for en in load_dict_file(path).values() if isinstance(en, str) and en.strip()]
    if not values:
        return set()

    # 先扫一遍所有括号内容，收集「被拿来做消歧后缀的作品名」
    paren_series: set[str] = set()
    for en in values:
        for m in re.finditer(r"[\(\[]([^\)\]]+)[\)\]]", en):
            paren_series.add(_normalize_tag_key(m.group(1)))

    out: set[str] = set()
    for en in values:
        norm = _normalize_tag_key(en)
        if not norm or _is_series_tag(en, norm, paren_series):
            continue
        out.add(norm)
    return out


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------

# 切分时要丢掉的标点。
#
# 空格 `\s` 也是分隔符，这是给**中文输入**设计的（「白发 双马尾 微笑」）。
# 但它有个副作用：英文多词标签（`white hair`）会被切成 `white` + `hair`，
# 拼回去变成 `white, hair` —— 在 NovelAI 里这是两个概念，不是「白色的头发」。
# 所以英文片段在切分后还要走一遍 `_reglue_ascii()` 把它们重新粘回去，
# 见下方。不能干脆去掉 `\s`，否则「白发 双马尾」会整体查不到。
_SPLIT_RE = re.compile(r"[,，、;；\s]+")

# 只按「明确的分隔符」切，不按空格切 —— 用于判断哪些片段原本是同一个逗号项
_HARD_SPLIT_RE = re.compile(r"[,，、;；]+")

# 不应被当作「可翻译词」的片段：NovelAI 权重语法、画师标签、纯英文标签
# 这些要么不能被替换（权重要原样保留），要么本来就是英文（不用查表）
_PROTECTED_RE = re.compile(
    r"""
    \{[^{}]*\}            # {{tag}} 双花括号
    | \[[^\[\]]*\]        # [tag] 方括号
    | \d+(?:\.\d+)?::[^:]+::   # 1.2::tag:: 权重语法
    | artist:[^\s,]+      # artist:xxx 画师标签
    """,
    re.VERBOSE,
)

# 判断一段文本里有没有中文（词库只索引中文；纯英文片段不用查表）
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 口语填充词 —— 用户天然会说「画一个泳装爱蜜莉雅」「来张白发少女」。
# 这些前缀对出图没有任何作用，但会让「画一个泳装爱蜜莉雅」整体查不到词库
# （词库里只有「泳装」「爱蜜莉雅」），白白多调一次模型、还容易被翻歪。
# 这里在切分前先剥掉。
#
# 只在**句首**剥离（`^`），不碰句中 —— 否则「画一个」这种出现在描述中间的
# 情况可能误伤。另外要求后面跟着别的内容，避免把整句都删光。
_FILLER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"帮我|请|麻烦|给我|来一?[张个幅]|生成|画一?[张个幅]|画|来|搞一?[张个幅]|整一?[张个幅]|做一?[张个幅]"
    r"|我想要?|我要|我想|出图|产图|捏一?[张个幅]"
    r")\s*(?=\S)"
)


def strip_filler(text: str) -> str:
    """剥掉「画一个」「来张」这类口语填充前缀。

    只处理句首，且会反复剥离（「帮我画一个 XX」→「XX」）。
    剥空的极端情况返回原文，避免把输入清成空串。
    """
    if not text:
        return text
    out = text.strip()
    for _ in range(4):  # 最多剥 4 层，防止病态输入
        new = _FILLER_PREFIX_RE.sub("", out)
        if new == out:
            break
        out = new
    return out.strip() or text


def has_cjk(text: str) -> bool:
    """文本里是否含有中日韩汉字。用于判断「还有没有没翻掉的中文」"""
    return bool(_HAS_CJK_RE.search(text or ""))


def _protect(text: str) -> tuple[str, list[str]]:
    """把不能/不用替换的片段挖出来，用占位符代替。

    为什么要挖出来而不是直接跳过分词：
    权重语法 `1.3::silver hair::` 里的空格会把分词切碎，
    切碎之后 `silver`、`hair` 又可能被当成待翻译词去查表，白白增加噪音。
    """
    stash: list[str] = []

    def _take(m: re.Match) -> str:
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    return _PROTECTED_RE.sub(_take, text), stash


def _restore(text: str, stash: list[str]) -> str:
    for idx, original in enumerate(stash):
        text = text.replace(f"\x00{idx}\x00", original)
    return text


def split_segments(text: str) -> list[str]:
    """把输入切成待查表的片段。

    先保护权重语法，再按分隔符切，最后还原受保护片段。
    返回的片段保留它们之间的分隔符信息由调用方自己处理（当前用逗号 join）。

    ## 英文多词标签不切碎

    切分是两层的：先按逗号等「硬分隔符」切成大项，每个大项内部再按空格切。
    但如果一个大项**全是 ASCII 且不含中文**（`white hair`、`2b (nier:automata)`、
    `long hair`），就**不再按空格切**，整个大项作为一个片段。

    为什么：NovelAI 官方推荐的标签写法就是带空格的（`white hair`，而不是
    `white_hair`），用户直接贴一段英文 prompt 是很常见的用法。旧逻辑会把
    `1girl, long hair, blue eyes` 切成 `1girl, long, hair, blue, eyes` ——
    5 个碎片，「长发」「蓝眼」两个概念直接没了，出图完全不对。

    中文大项（`白发 双马尾`）仍按空格切，因为用户用空格分隔中文标签也很常见，
    而且中文标签本身不含空格，不存在切碎问题。

    中英混排大项（`白发 white hair`）比较少见，按空格切：中文部分会各自查表，
    英文碎片原样保留。这是可接受的折中 —— 用户如果想让英文标签保持完整，
    用逗号隔开就好，这也是 NovelAI 本来的规范写法。
    """
    protected, stash = _protect(text or "")
    out: list[str] = []
    for chunk in _HARD_SPLIT_RE.split(protected):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not _HAS_CJK_RE.search(chunk):
            # 纯英文大项：整体保留（内部多个空格收敛成一个）
            out.append(re.sub(r"\s+", " ", chunk))
            continue
        out.extend(p for p in _SPLIT_RE.split(chunk) if p.strip())
    return [_restore(p.strip(), stash) for p in out]


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


class PromptDictionary:
    """提示词词库：中文 → 英文标签的确定性映射

    设计原则是「只做加法，不做减法」—— 查不到的词原样留着交给模型，
    绝不因为查不到就把内容丢掉。
    """

    def __init__(self, user_path: str = "", enabled: bool = True, mode: str = "segment"):
        """
        Args:
            user_path: 用户自定义词库路径（可为空）
            enabled: 是否启用词库
            mode: segment（逐词精确匹配）或 substring（子串替换）
        """
        self.enabled = enabled
        self.mode = mode if mode in ("segment", "substring") else "segment"

        # 用户词库覆盖内置：先内置，后用户，后写入的赢
        self.builtin = load_builtin_dict()
        self.user = self._load_user_dict(user_path)
        self.entries: dict[str, str] = {**self.builtin, **self.user}

        # 角色标签集合（v1.4.4）：给 translate_manager 的「单角色自动补 solo」用。
        # 只来自内置 characters.json，已剔除作品名；用户词库不进这个集合。
        # 读失败就是空集合，兜底逻辑会退化成只靠括号写法判断，不会炸。
        try:
            self.character_tags: set[str] = load_character_tags()
        except Exception as e:  # noqa: BLE001
            logger.warning("[PromptDict] 角色标签集合加载失败，补 solo 只靠括号写法: %s", e)
            self.character_tags = set()

        # 纯英文键的大小写不敏感索引。词库里 `HK416`/`MEIKO` 是大写、`saber`
        # 是小写，用户怎么打都该命中。只给不含中文的键建这张表 —— 中文键没有
        # 大小写问题，全建一遍白占内存。有原样键优先，索引只做兜底。
        self._ascii_lower: dict[str, str] = {
            zh.lower(): en for zh, en in self.entries.items()
            if zh and not _HAS_CJK_RE.search(zh)
        }

        # 子串模式要按长度从长到短替换，否则「黑」会先吃掉「黑猫」的一部分
        self._by_length = sorted(self.entries.items(), key=lambda kv: -len(kv[0]))

        logger.info(
            "[PromptDict] 词库就绪：内置 %d 条 + 自定义 %d 条 = %d 条（模式 %s）",
            len(self.builtin), len(self.user), len(self.entries), self.mode,
        )

    @staticmethod
    def _load_user_dict(user_path: str) -> dict[str, str]:
        raw = (user_path or "").strip()
        if not raw:
            return {}

        path = Path(raw).expanduser()
        if not path.is_absolute():
            # 相对路径按当前工作目录算（AstrBot 的数据目录），并兜底试插件目录
            candidates = [Path.cwd() / path, Path(__file__).resolve().parent.parent / path]
            for cand in candidates:
                if cand.exists():
                    path = cand
                    break
            else:
                logger.warning(
                    "[PromptDict] 找不到自定义词库 %s（试过 %s）",
                    raw, "、".join(str(c) for c in candidates),
                )
                return {}

        loaded = load_dict_file(path)
        if loaded:
            logger.info("[PromptDict] 载入自定义词库 %s（%d 条）", path, len(loaded))
        return loaded

    # -- 查询 ---------------------------------------------------------------

    def lookup(self, term: str) -> str | None:
        """查一个词，返回英文标签；查不到返回 None

        原样键优先；不含中文的词再用小写兜底一次（`hk416` 也能命中 `HK416`）。
        """
        if not self.enabled or not term:
            return None
        key = term.strip()
        hit = self.entries.get(key)
        if hit is None and key and not _HAS_CJK_RE.search(key):
            hit = self._ascii_lower.get(key.lower())
        return hit

    def lookup_segment(self, segment: str) -> str | None:
        """查一个切分后的片段。

        对片段做归一化尝试：全角空格、首尾标点、中文引号等，
        避免「白发，」这种带尾巴的片段查不到。
        """
        term = (segment or "").strip().strip(",.，、。!！?？:：;；'\"“”‘’()（）【】[]")
        return self.lookup(term)

    def replace_substring(self, text: str) -> tuple[str, int]:
        """子串模式：在整句里做替换

        按词长从长到短替换，避免「黑」抢走「黑猫」的一部分。

        替换时两侧补**逗号**做分隔，而不是空格 —— 这里有两个原因：

        1. 不补分隔符的话，连写输入（「鸣潮达尼亚」）会变成
           `wuthering_wavesdenia_(wuthering_waves)`，两个标签粘成一坨，
           NovelAI 完全读不懂。
        2. 补空格虽然能断开，但 NovelAI 的标签分隔符是**逗号**：空格分隔的
           `wuthering_waves denia_(...)` 会被当成一个混合概念，而逗号分隔
           才是两个独立标签。多词标签（`white hair`）内部本身含空格，
           用空格做分隔符还会和它混淆。

        逗号 + 后续 `_tidy()` 收敛重复分隔符，既断得开也不会产生 `,,`。

        Returns:
            (替换后的文本, 替换次数)
        """
        if not self.enabled or not text:
            return text, 0
        out = text
        count = 0
        for zh, en in self._by_length:
            if zh in out:
                # 两侧补逗号：既隔开相邻标签，也隔开中文残留
                out = out.replace(zh, f", {en}, ")
                count += 1
        return out, count

    def __len__(self) -> int:
        return len(self.entries)

    def stats(self) -> dict[str, Any]:
        return {
            "builtin": len(self.builtin),
            "user": len(self.user),
            "total": len(self.entries),
            "mode": self.mode,
            "enabled": self.enabled,
        }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

# 中英混排时的连接符：词库替换结果和原有英文之间用逗号隔开即可，
# 这里只需要处理「替换后出现连续逗号」这类脏格式
_MULTI_COMMA_RE = re.compile(r"\s*,\s*(?:,\s*)+")

# 连接词 / 虚词（v1.4.4）。
#
# 为什么需要：用户写整句时天然会带「穿着」「在」「的」这类词，它们对出图
# **没有任何视觉含义**，但词库里不可能收录它们（`穿着` → ? 没有对应标签）。
# 结果就是「原神神里绫华穿着泳衣在沙滩玩耍」这句：原神 / 神里绫华 / 泳衣 /
# 沙滩 / 玩耍 全在词库里，却因为夹着 `穿着` 和 `在` 两个虚词导致子串兜底
# 「有中文残留 → 整段放弃」，整句白白交给模型（模型还把 1girl 弄丢了）。
#
# 只列这一小批**纯功能词**，多字优先匹配（`穿着` 先于 `穿`、`着`），
# 绝不放形容词 / 名词进来 —— 这个列表的每一个词都必须满足
# 「删掉它画面不会有任何变化」。
_CONNECTIVE_RE = re.compile(
    r"穿着|戴着|拿着|抱着|正在|一个|一位|一名|穿|戴|在|的|和|与|着|了"
)


def _strip_connectives(text: str) -> str:
    """删掉连接词。只在子串兜底的残留判定里用，不改动正常命中的路径。"""
    return _CONNECTIVE_RE.sub("", text or "")


def _tidy(tags: str) -> str:
    """清理标签串里的脏格式：连续逗号、首尾逗号、多余空格"""
    out = _MULTI_COMMA_RE.sub(", ", tags or "")
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip().strip(",").strip()


def apply_dictionary(
    text: str, dictionary: PromptDictionary
) -> tuple[str, str, list[str], list[str]]:
    """对用户输入做一次词库直译。

    Returns:
        (处理后文本, 剩余待翻译文本, 命中的词, 未命中的词)

    三种情况的返回值：

    1. **全部命中** —— 剩余待翻译文本为空，调用方可以直接用结果，**不调模型**
    2. **部分命中** —— 剩余待翻译文本非空，只需要把「未命中的词」交给模型，
       结果再与命中部分拼起来（这样模型工作量最小，也最不容易翻车）
    3. **一个都没命中** —— 原样返回，走原来的整句翻译流程
    """
    if not dictionary.enabled or not (text or "").strip():
        return text, text, [], []

    # 先剥口语前缀。注意：剥掉的部分**不再回填**到 merged ——
    # 「画一个」本来就不该出现在 prompt 里。但 remaining 要用剥离后的文本，
    # 否则这段噪音会跟着一起发给模型，模型有可能把它翻成 `draw a` 混进 tags。
    body = strip_filler(text)

    if dictionary.mode == "substring":
        replaced, _ = dictionary.replace_substring(body)
        replaced = _tidy(replaced)
        # 子串模式下无法可靠区分单期命中/未命中，如果还有中文就整句交给模型
        if has_cjk(replaced):
            return replaced, replaced, [], []
        return replaced, "", [body], []

    # --- 逐词精确匹配 ---
    segments = split_segments(body)
    if not segments:
        return text, body, [], []

    hits: list[str] = []
    misses: list[str] = []
    out_parts: list[str] = []

    for seg in segments:
        # 纯英文/纯符号片段不算「未命中」——它本来就不需要翻译，
        # 查不到就原样保留，这样才不会把大量英文输入误判成「要调模型」。
        #
        # 但**要先查一次表**再放行。词库里有一批纯英文键（`2b`、`dva`、
        # `saber`、`HK416`、`MEIKO`……），它们存在的意义是把用户随手打的
        # 简写补全成 Danbooru 的完整消歧标签（`2b` → `2b (nier:automata)`）。
        # 曾经这里是「不含中文直接跳过」，导致这 20 多条键从上线起就一次
        # 都没命中过 —— 用户打 `2b`，出图时模型只拿到孤零零的 `2b`，
        # 经常画成别的东西。大小写兜底在 `lookup()` 里做（`hk416` 也能命中
        # `HK416`），这里只管查。
        if not has_cjk(seg):
            en = dictionary.lookup_segment(seg)
            if en and en != seg:
                hits.append(seg)
                out_parts.append(en)
            else:
                out_parts.append(seg)
            continue

        en = dictionary.lookup_segment(seg)
        if en:
            hits.append(seg)
            out_parts.append(en)
        else:
            # 逐词查不到时，尝试**子串兜底**：用户经常把词连写
            # （「鸣潮达妮娅」「泳装爱蜜莉雅」），按分隔符切分后整段查不到，
            # 但里面其实包含多个词库词条，整体甩给模型很容易翻车 ——
            # 线上真实日志：「鸣潮达妮娅」被模型翻成 dania_(wuthering_waves)，
            # 正确是 denia_（拼错了）。词库明明有这角色却因「连写」没用上。
            #
            # ⚠️ 但子串替换是把双刃剑，必须**只在能完整消化整个片段时**采用。
            # 反例：「一个婴儿，超级赛亚人发型」里的 `赛亚人` 没收录，
            # 而 `亚人` → `ajin` 在词库里 —— 于是被替换成 `超级赛 ajin 发型`，
            # 用户在 prompt 里得到一段连自己都没写过的诡异内容。这类「半截
            # 命中」比不命中危险得多：不命中只是交给模型（顶多翻得一般），
            # 半截命中是**直接污染 prompt**。
            #
            # 所以这里的判据是：替换完还有中文残留 → 整个片段放弃兜底，
            # 原样交给模型处理。
            #
            # v1.4.4 放宽一档：残留如果**全部是连接词**（穿着 / 在 / 的 …），
            # 删掉之后不再有任何中文 → 也接受为命中。理由：这些词对画面
            # 没有任何贡献，删掉它们不会产生「用户没写过的内容」，也就不会
            # 触发上面说的 prompt 污染。
            #
            # 但判据必须严格到「删完连接词后一个汉字都不剩」：只要还剩一个
            # 非连接词的汉字（`超级赛`、`发型`），仍然整段放弃 —— 因为那个
            # 汉字说明这段里有我们**没理解**的内容，而半截命中比不命中危险。
            # `超级赛亚人发型` 的反例仍然成立：`亚人` → ajin 命中，但残留
            # `超级赛` / `发型` 不是连接词，照旧放弃进 misses。
            sub, n_sub = dictionary.replace_substring(seg)
            if n_sub and not has_cjk(sub):
                hits.append(seg)
                out_parts.append(_tidy(sub))
            elif n_sub and not has_cjk(_strip_connectives(sub)):
                # 残留全是连接词：剥掉后接受。注意剥的是 sub（替换后的串），
                # 这样英文标签之间的分隔逗号仍然保留，_tidy 负责收敛。
                hits.append(seg)
                out_parts.append(_tidy(_strip_connectives(sub)))
            else:
                # 未命中的部分**不能**留在 merged 里。
                #
                # 曾经的 bug：这里 append(seg) 把未翻译的中文留在了 merged，
                # 而同一段文本又通过 remaining 发给模型翻译一次 —— 导致中文
                # 既出现在「已翻译结果」里、又被翻译了一遍，最终 prompt 变成
                # `wuthering_waves, denia_(...), 泳装, swimsuit`：
                # 中文残留白占 token（NAI 根本不认识），
                # 而且模型若翻成别的写法（如 bikini）就会和原词重复加权。
                #
                # 正确做法：未命中的片段只走 remaining，交给模型翻译后再拼回来。
                # 这里只记录，不输出。
                misses.append(seg)

    merged = _tidy(", ".join(out_parts))
    remaining = ", ".join(misses)
    return merged, remaining, hits, misses


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
# 切分
# ---------------------------------------------------------------------------

# 切分时要丢掉的标点（保留逗号作为主分隔符，先统一处理）
_SPLIT_RE = re.compile(r"[,，、;；\s]+")

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
    """
    protected, stash = _protect(text or "")
    parts = [p.strip() for p in _SPLIT_RE.split(protected) if p.strip()]
    return [_restore(p, stash) for p in parts]


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
        """查一个词，返回英文标签；查不到返回 None"""
        if not self.enabled or not term:
            return None
        return self.entries.get(term.strip())

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
        # 直接原样保留，这样才不会把大量英文输入误判成「要调模型」
        if not has_cjk(seg):
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
            sub, n_sub = dictionary.replace_substring(seg)
            if n_sub and not has_cjk(sub):
                hits.append(seg)
                out_parts.append(_tidy(sub))
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


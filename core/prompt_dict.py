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

        Returns:
            (替换后的文本, 替换次数)
        """
        if not self.enabled or not text:
            return text, 0
        out = text
        count = 0
        for zh, en in self._by_length:
            if zh in out:
                out = out.replace(zh, en)
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

    if dictionary.mode == "substring":
        replaced, _ = dictionary.replace_substring(text)
        replaced = _tidy(replaced)
        # 子串模式下无法可靠区分单期命中/未命中，如果还有中文就整句交给模型
        if has_cjk(replaced):
            return replaced, replaced, [], []
        return replaced, "", [text], []

    # --- 逐词精确匹配 ---
    segments = split_segments(text)
    if not segments:
        return text, text, [], []

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
            misses.append(seg)
            out_parts.append(seg)

    merged = _tidy(", ".join(out_parts))
    remaining = ", ".join(misses)
    return merged, remaining, hits, misses

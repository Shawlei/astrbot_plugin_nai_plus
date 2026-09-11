"""
质量前缀预设管理

内置常用预设，支持用户自定义保存/删除。
"""

import json
import re
from pathlib import Path

from astrbot.api import logger


# 内置预设（来自 Nai2API 官方前端 app.js 中的 artistPresets）
#
# ⚠️ 关于负权重语法：
# Nai2API 官方预设里混着 `-2::green::`、`-4::Muscle definition, abs::` 这类
# **负权重**标签。它们被写在 artist 串（正向提示词）里，但语义上属于负面约束。
#
# 这会造成两个问题：
#   1. NovelAI 的正向 prompt 里出现负权重是畸形写法，模型对它的处理不稳定；
#      NAI 本身是支持在正向串里用负权重的，但它会干扰同一串里其他标签的
#      注意力分配。
#   2. `-2::green::` 是无差别压制绿色 —— 画粉发角色没感觉，但画**绿发角色**
#      （原神纳西妲、蔚蓝档案的绿系角色等）时会把角色本身的发色/瞳色一起压掉，
#      属于「预设和角色打架」。
#
# 所以这里把负权重统一**提取**出来，由 split_negative_weights() 交给调用方
# 拼进负面提示词，artist 串本身只保留正向内容。提取是幂等的，自定义预设
# 里如果也写了负权重，同样会被处理。
BUILTIN_PRESETS: dict[str, dict[str, str]] = {
    "2.5D唯美风": {
        "artist": "0.9::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, textless version, The image is highly intricate finished drawn. Only the character's face is in anime style, but their body is in realistic style. 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::. 1.63::photorealistic::, 1.63::photo(medium)::, \\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,, very aesthetic, masterpiece, no text, cowboy shot, looking at viewer",
        "desc": "2.5D唯美风（Nai2API 默认）",
    },
    "韩漫小清新风": {
        "artist": "[[[artist:dishwasher1910]]], {{yd_(orange_maru)}}, [artist:ciloranko], [artist:sho_(sho_lwlw)], [ningen mame], year 2024, full body, standing, looking at viewer",
        "desc": "韩漫小清新风格",
    },
    "本子动漫风": {
        "artist": "1.4::asanagi::,{{{{{artist:asanagi}}}}},1.2::xiaoluo_xl::,1.3::Artist: misaka_12003-gou::,1.2::Artist:shexyo::,0.7::Artist:b.sa_(bbbs)::,1::Artist:qiandaiyiyu::,1.05::artist:natedecock::,1.05::artist:kunaboto::,0.75::artist:kandata_nijou::,1.05::artist:zer0.zer0 ::,1.05::artist:jasony::,0.75::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, {textless version, The image is highly intricate finished drawn,write realistically,true to life}, 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::, 1.63::photorealistic::,3::age slider::,1.63::photo(medium)::, 2::best quality, absurdres, very aesthetic, detailed, masterpiece::,-4::Muscle definition, abs::, cowboy shot, looking at viewer",
        "desc": "本子动漫风格",
    },
    "GalGame风": {
        "artist": "artist:ningen_mame,, noyu_(noyu23386566),, toosaka asagi,, location,\\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,:,, very aesthetic, masterpiece, no text, full body, standing",
        "desc": "GalGame 风格",
    },
    "动漫风": {
        "artist": "artist collaboration, 0.70::artist:necomi ::, 0.80::artist:tan (tangent) ::, 1.38::artist:kanda done ::, 1.22::artist:quasarcake ::, 1.22::artist:atdan ::, 0.94::artist:fuumi (radial engine) ::, 1.70::artist:john kafka ::, 0.60::artist:meisansan ::, 0.98::artist:ogipote ::, 0.44::artist:nixeu ::, 0.74::artist:mignon ::, 0.94::artist:rangu ::, 1.18::artist:hiten (hitenkei) ::, 1.24::artist:freng ::, 0.56::artist:miwabe sakura ::, year 2024, perspective, full body, standing",
        "desc": "动漫风（旧版画师混合）",
    },
}


# ---------------------------------------------------------------------------
# 负权重提取
# ---------------------------------------------------------------------------

# NovelAI 负权重语法：`-2::tag::` / `-.5::tag::`。数值可带小数点。
_NEG_WEIGHT_RE = re.compile(r"-(\d+(?:\.\d+)?)::([^:]+)::")


def split_negative_weights(text: str) -> tuple[str, str]:
    """把正向串里的负权重标签拆出来。

    Nai2API 官方预设把 `-2::green::` 这类**负面约束**写进了 artist（正向）
    串里。语义上它们是负面提示词的内容，混在正向串里既干扰其他标签的
    注意力分配，又会导致「预设和角色打架」—— `green` 被无差别压制，
    画绿发角色时会把角色本身的发色一起压掉。

    这里把它们提取出来交给调用方拼进 negative。

    Args:
        text: 可能含负权重的正向串

    Returns:
        (清理后的正向串, 提取出的负向标签串)
        没有负权重时返回 (原文, "")

    注意：不改变权重数值。`-2::green::` 提取后变成 negative 里的
    `-2::green::`（NAI 的 negative 字段同样支持负权重语法，含义一致）。
    """
    if not text:
        return text, ""

    negs: list[str] = []

    def _take(m: re.Match) -> str:
        negs.append(f"-{m.group(1)}::{m.group(2).strip()}::")
        return ""

    cleaned = _NEG_WEIGHT_RE.sub(_take, text)
    # 清掉提取后留下的空段（`, ,` / 首尾逗号）
    cleaned = re.sub(r"\s*,\s*(?:,\s*)+", ", ", cleaned)
    cleaned = cleaned.strip().strip(",").strip()

    return cleaned, ", ".join(negs)


# ---------------------------------------------------------------------------
# 构图兜底
# ---------------------------------------------------------------------------

# 判断一段 prompt / 画师串里是否已经包含了「拍到哪」的约束。
# 关键词取 Danbooru 常用的构图/景别标签。
_COMPOSITION_KEYWORDS = (
    # 景别
    "full body", "upper body", "lower body", "cowboy shot", "portrait",
    "wide shot", "close-up", "closeup", "face focus", "half body",
    "feet out of frame", "head out of frame",
    # 视角 / 构图
    "from above", "from below", "from behind", "from side", "from front",
    "dutch angle", "three quarter view", "perspective", "profile",
    "looking at viewer", "standing", "sitting", "lying",
    # 中文（词库未命中时可能残留，一并识别避免重复补）
    "全身", "半身", "胸像", "特写", "立绘", "构图", "景别",
    "站姿", "坐姿", "躺", "远景", "中景", "广角", "俯视", "仰视", "视角",
)

# 兜底追加的构图标签。选 `full body` 是因为它是「不想要半身」时最直接的
# 诉求；`standing` 让姿势有默认值，避免模型自己挑个奇怪姿势。
DEFAULT_COMPOSITION = "full body, standing"


def has_composition(text: str) -> bool:
    """判断文本里有没有构图/景别类标签。"""
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in _COMPOSITION_KEYWORDS)


def ensure_composition(prompt: str, artist: str = "") -> tuple[str, bool]:
    """当 prompt 和画师串都没给构图约束时，补一个默认景别。

    NovelAI 在没有任何构图标签时，会退回训练数据的统计偏好 ——
    默认出 `portrait` / `upper body`。这就是「张张都是半身」的原因：
    画师串只管风格质感，用户 prompt 常只写「谁 + 穿什么」，
    「拍到哪」是空的，NAI 就自己填了半身。

    Args:
        prompt: 用户提示词（已被词库直译过）
        artist: 画师串（用于判断是否已含构图约束）

    Returns:
        (处理后的 prompt, 是否追加了兜底标签)
    """
    if has_composition(prompt) or has_composition(artist):
        return prompt, False
    sep = ", " if prompt.strip() else ""
    return f"{prompt.strip()}{sep}{DEFAULT_COMPOSITION}", True


class PresetManager:
    """预设管理器"""
    def __init__(self, data_dir: Path):
        self._preset_file = data_dir / "presets.json"
        self._custom_presets: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if self._preset_file.exists():
            try:
                with open(self._preset_file, "r", encoding="utf-8") as f:
                    self._custom_presets = json.load(f)
                logger.info("[PresetManager] 已加载 %d 个自定义预设", len(self._custom_presets))
            except Exception as e:
                logger.warning("[PresetManager] 加载预设文件失败: %s", e)
                self._custom_presets = {}

    def _save(self) -> None:
        self._preset_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._preset_file, "w", encoding="utf-8") as f:
            json.dump(self._custom_presets, f, ensure_ascii=False, indent=2)

    def get(self, name: str) -> str | None:
        """获取预设的 artist 值，返回 None 表示预设不存在"""
        # 优先查自定义预设
        if name in self._custom_presets:
            return self._custom_presets[name]["artist"]
        # 再查内置预设
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]["artist"]
        return None

    def list_all(self) -> dict[str, dict[str, str]]:
        """列出所有预设（内置 + 自定义），自定义覆盖同名内置"""
        result = dict(BUILTIN_PRESETS)
        result.update(self._custom_presets)
        return result

    def save(self, name: str, artist: str, desc: str = "") -> None:
        """保存自定义预设"""
        self._custom_presets[name] = {
            "artist": artist.strip(),
            "desc": desc.strip() or f"自定义预设",
        }
        self._save()
        logger.info("[PresetManager] 已保存预设 '%s'", name)

    def delete(self, name: str) -> bool:
        """删除自定义预设，返回是否成功"""
        if name not in self._custom_presets:
            return False
        del self._custom_presets[name]
        self._save()
        logger.info("[PresetManager] 已删除预设 '%s'", name)
        return True

    def update(self, name: str, artist: str | None = None, desc: str | None = None) -> bool:
        """修改自定义预设，返回是否成功。只传 artist 就只改 artist，只传 desc 就只改 desc。"""
        if name not in self._custom_presets:
            return False
        if artist is not None:
            self._custom_presets[name]["artist"] = artist.strip()
        if desc is not None:
            self._custom_presets[name]["desc"] = desc.strip()
        self._save()
        logger.info("[PresetManager] 已修改预设 '%s'", name)
        return True

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_PRESETS

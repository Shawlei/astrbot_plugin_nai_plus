"""
质量前缀与画风预设管理

支持官方内置预设，支持用户自定义预设的三段式配置（画师串/质量词、正向词、负向词）。
支持 WebUI 面板与指令双向同步。
"""

import json
import re
from pathlib import Path
from typing import Any

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("astrbot_plugin_nai_plus")

# ---------------------------------------------------------------------------
# 负权重与构图识别正则
# ---------------------------------------------------------------------------

_NEG_WEIGHT_RE = re.compile(r"(?<!\w)-(\d+(?:\.\d+)?)::([^:]+)::")

_COMPOSITION_KEYWORDS = (
    "full body", "upper body", "lower body", "cowboy shot", "portrait",
    "wide shot", "close-up", "closeup", "face focus", "half body",
    "feet out of frame", "head out of frame",
    "from above", "from below", "from behind", "from side", "from front",
    "dutch angle", "three quarter view", "perspective", "profile",
    "looking at viewer", "standing", "sitting", "lying",
    "全身", "半身", "胸像", "特写", "立绘", "构图", "景别",
    "站姿", "坐姿", "躺", "远景", "中景", "广角", "俯视", "仰视", "视角",
)

DEFAULT_COMPOSITION = "full body, standing"


def split_negative_weights(text: str) -> tuple[str, str]:
    """把正向画师串里的负权重标签提取出来（例如 -2::green::）。

    Returns:
        (清理后的正向串, 提取出的负向标签串)
    """
    if not text:
        return text, ""

    negs: list[str] = []

    def _take(m: re.Match) -> str:
        negs.append(f"-{m.group(1)}::{m.group(2).strip()}::")
        return ""

    cleaned = _NEG_WEIGHT_RE.sub(_take, text)
    cleaned = re.sub(r"\s*,\s*(?:,\s*)+", ", ", cleaned)
    cleaned = cleaned.strip().strip(",").strip()
    return cleaned, ", ".join(negs)


def has_composition(text: str) -> bool:
    """判断文本里是否包含构图/景别类标签。"""
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in _COMPOSITION_KEYWORDS)


def ensure_composition(prompt: str, artist: str = "") -> tuple[str, bool]:
    """当用户 prompt 和画师串都没给出景别时，自动补充默认构图标签。"""
    if has_composition(prompt) or has_composition(artist):
        return prompt, False
    sep = ", " if prompt.strip() else ""
    return f"{prompt.strip()}{sep}{DEFAULT_COMPOSITION}", True


def merge_tags(base: str, extra: str) -> str:
    """合并两段逗号分隔的标签串，跳过已存在的（大小写不敏感去重）。"""
    base = (base or "").strip().strip(",").strip()
    extra = (extra or "").strip().strip(",").strip()
    if not extra:
        return base
    if not base:
        return extra
    existing = {seg.strip().lower() for seg in base.split(",") if seg.strip()}
    add = [seg.strip() for seg in extra.split(",") if seg.strip() and seg.strip().lower() not in existing]
    if not add:
        return base
    return f"{base}, {', '.join(add)}"


# ---------------------------------------------------------------------------
# 内置预设（官方 5 大预设）
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: dict[str, dict[str, str]] = {
    "2.5D唯美风": {
        "artist": "0.9::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, textless version, The image is highly intricate finished drawn. Only the character's face is in anime style, but their body is in realistic style. 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::. 1.63::photorealistic::, 1.63::photo(medium)::, \\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,, very aesthetic, masterpiece, no text, cowboy shot, looking at viewer",
        "positive": "",
        "negative": "",
        "desc": "2.5D唯美风（半写实半动漫，质感细腻）",
    },
    "韩漫小清新风": {
        "artist": "[[[artist:dishwasher1910]]], {{yd_(orange_maru)}}, [artist:ciloranko], [artist:sho_(sho_lwlw)], [ningen mame], year 2024",
        "positive": "",
        "negative": "",
        "desc": "韩漫小清新风格（明亮干净色彩）",
    },
    "本子动漫风": {
        "artist": "1.4::asanagi::,{{{{{artist:asanagi}}}}},1.2::xiaoluo_xl::,1.3::Artist: misaka_12003-gou::,1.2::Artist:shexyo::,0.7::Artist:b.sa_(bbbs)::,1::Artist:qiandaiyiyu::,1.05::artist:natedecock::,1.05::artist:kunaboto::,0.75::artist:kandata_nijou::,1.05::artist:zer0.zer0 ::,1.05::artist:jasony::,0.75::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, {textless version, The image is highly intricate finished drawn,write realistically,true to life}, 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::, 1.63::photorealistic::,3::age slider::,1.63::photo(medium)::, 2::best quality, absurdres, very aesthetic, detailed, masterpiece::,-4::Muscle definition, abs::",
        "positive": "",
        "negative": "",
        "desc": "本子动漫风格（日系多画师混合）",
    },
    "GalGame风": {
        "artist": "artist:ningen_mame,, noyu_(noyu23386566),, toosaka asagi,, location,\\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,:,, very aesthetic, masterpiece, no text",
        "positive": "",
        "negative": "",
        "desc": "GalGame 游戏 CG 风格",
    },
    "动漫风": {
        "artist": "artist collaboration, 0.70::artist:necomi ::, 0.80::artist:tan (tangent) ::, 1.38::artist:kanda done ::, 1.22::artist:quasarcake ::, 1.22::artist:atdan ::, 0.94::artist:fuumi (radial engine) ::, 1.70::artist:john kafka ::, 0.60::artist:meisansan ::, 0.98::artist:ogipote ::, 0.44::artist:nixeu ::, 0.74::artist:mignon ::, 0.94::artist:rangu ::, 1.18::artist:hiten (hitenkei) ::, 1.24::artist:freng ::, 0.56::artist:miwabe sakura ::, year 2024, perspective",
        "positive": "",
        "negative": "",
        "desc": "经典动漫插画风（多画师混合二次元立绘）",
    },
}

PRESET_TEXT_FIELDS = ("artist", "positive", "negative")


def _normalize_entry(
    artist: str = "", desc: str = "", positive: str = "", negative: str = "",
) -> dict[str, str]:
    return {
        "artist": str(artist or "").strip(),
        "positive": str(positive or "").strip(),
        "negative": str(negative or "").strip(),
        "desc": str(desc or "").strip() or "自定义预设",
    }


def preset_has_content(entry: dict[str, Any]) -> bool:
    return any(str(entry.get(k, "") or "").strip() for k in PRESET_TEXT_FIELDS)


def parse_webui_presets(raw: Any) -> dict[str, dict[str, str]]:
    """解析 AstrBot 配置项中的自定义预设列表。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        raw = [{"name": k, **(v if isinstance(v, dict) else {"artist": str(v)})} for k, v in raw.items()]
    if not isinstance(raw, (list, tuple)):
        return {}

    result: dict[str, dict[str, str]] = {}
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name or " " in name or name in BUILTIN_PRESETS:
            continue
        entry = _normalize_entry(
            artist=item.get("artist", ""),
            desc=item.get("desc", ""),
            positive=item.get("positive", ""),
            negative=item.get("negative", ""),
        )
        if preset_has_content(entry):
            result[name] = entry
    return result


# ---------------------------------------------------------------------------
# 预设别名表（P1-4：用户习惯输入的名字 → 内置真实全名）
#
# 背景：用户常把「韩漫小清新风」简化成「韩漫风」、把「本子动漫风」说成「本子风」，
# 导致 -p 参数匹配不到真实预设而报错。此表只做「输入 → 真实全名」的映射，
# 解析不到时返回 None 由上层报错，绝不新建或覆盖任何预设（禁止静默降级）。
# ---------------------------------------------------------------------------

PRESET_ALIASES: dict[str, str] = {
    "韩漫风": "韩漫小清新风", "韩漫": "韩漫小清新风", "韩漫小清新": "韩漫小清新风",
    "小清新": "韩漫小清新风", "小清新风": "韩漫小清新风",
    "本子风": "本子动漫风", "本子": "本子动漫风", "本子动漫": "本子动漫风",
    "2.5d": "2.5D唯美风", "2.5d唯美": "2.5D唯美风", "唯美风": "2.5D唯美风",
    "唯美": "2.5D唯美风", "半写实": "2.5D唯美风",
    "galgame": "GalGame风", "galgame风": "GalGame风", "gal风": "GalGame风", "gal": "GalGame风",
    "动漫": "动漫风", "二次元": "动漫风", "动漫插画": "动漫风",
}

# 需要剥离的外层包裹符号（用户误输入的尖括号 / 书名号 / 引号等）
_PRESET_WRAPPERS = (("<", ">"), ("《", "》"), ("【", "】"), ('"', '"'), ("'", "'"))


def _norm_preset_key(name: Any) -> str:
    """归一化预设名用于比较：去全部空白 + 转小写 + 剥最外层包裹符号。"""
    if name is None:
        return ""
    key = str(name).strip()
    for l_bracket, r_bracket in _PRESET_WRAPPERS:
        if key.startswith(l_bracket) and key.endswith(r_bracket) and len(key) >= 2:
            key = key[len(l_bracket):-len(r_bracket)].strip()
    key = re.sub(r"\s+", "", key)
    return key.lower()


def resolve_preset_name(name: Any, available) -> str | None:
    """把用户输入的预设名解析为真实存在的预设名，解析不出返回 None。

    匹配顺序：
        1. 精确匹配
        2. 别名表（PRESET_ALIASES，键也做归一化比较）
        3. 忽略大小写 / 空白
        4. 唯一子串匹配（输入是真实名的子串，或真实名是输入的子串，且结果唯一）

    注意：只做「输入 → 真实全名」映射，**绝不新建或覆盖预设**。
    解析不到就返回 None，让上层显式报错，禁止静默降级。
    """
    if name is None:
        return None
    raw = str(name).strip()
    if not raw:
        return None

    avail_list = [str(a) for a in available]
    avail_set = set(avail_list)

    # 1. 精确匹配
    if raw in avail_set:
        return raw

    norm_input = _norm_preset_key(raw)
    if not norm_input:
        return None

    # 2. 别名表（别名键归一化后比较）
    for alias, target in PRESET_ALIASES.items():
        if _norm_preset_key(alias) == norm_input and target in avail_set:
            return target

    # 3. 忽略大小写 / 空白
    for a in avail_list:
        if _norm_preset_key(a) == norm_input:
            return a

    # 4. 唯一子串匹配
    substr_hits = [
        a for a in avail_list
        if norm_input in _norm_preset_key(a) or _norm_preset_key(a) in norm_input
    ]
    if len(substr_hits) == 1:
        return substr_hits[0]

    return None


class PresetManager:
    """预设管理器，支持三段式预设（画师串、正向词、负向词）与默认预设管理。"""

    def __init__(self, data_dir: Path, webui_presets: Any = None):
        self._preset_file = data_dir / "presets.json"
        self._custom_presets: dict[str, dict[str, str]] = {}
        self._webui_presets: dict[str, dict[str, str]] = parse_webui_presets(webui_presets)
        self._load_file()
        self._merge()

    def _load_file(self) -> None:
        if self._preset_file.exists():
            try:
                with open(self._preset_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._custom_presets = {
                        str(name): _normalize_entry(
                            artist=info.get("artist", "") if isinstance(info, dict) else str(info or ""),
                            desc=info.get("desc", "") if isinstance(info, dict) else "",
                            positive=info.get("positive", "") if isinstance(info, dict) else "",
                            negative=info.get("negative", "") if isinstance(info, dict) else "",
                        )
                        for name, info in data.items()
                    }
                logger.info("[PresetManager] 已加载 %d 个自定义预设（文件）", len(self._custom_presets))
            except Exception as e:
                logger.warning("[PresetManager] 加载预设文件失败: %s", e)
                self._custom_presets = {}

    def _save_file(self) -> None:
        try:
            self._preset_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._preset_file, "w", encoding="utf-8") as f:
                json.dump(self._custom_presets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("[PresetManager] 写入预设文件失败: %s", e)

    def _merge(self) -> None:
        """合并文件与配置中的预设。"""
        changed = False
        for name, info in self._custom_presets.items():
            if name in self._webui_presets or name in BUILTIN_PRESETS:
                continue
            if preset_has_content(info):
                self._webui_presets[name] = _normalize_entry(
                    artist=info.get("artist", ""),
                    desc=info.get("desc", ""),
                    positive=info.get("positive", ""),
                    negative=info.get("negative", ""),
                )
                changed = True
        self._custom_presets = dict(self._webui_presets)
        if changed:
            self._save_file()

    def reload_from_webui(self, webui_presets: Any) -> None:
        self._webui_presets = parse_webui_presets(webui_presets)
        self._merge()

    def get(self, name: str) -> str | None:
        """获取画师串，不存在返回 None。"""
        if name in self._custom_presets:
            return self._custom_presets[name].get("artist", "")
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]["artist"]
        return None

    def get_entry(self, name: str) -> dict[str, str] | None:
        """获取完整三段预设条目（artist, positive, negative, desc）。"""
        if name in self._custom_presets:
            return _normalize_entry(**self._custom_presets[name])
        if name in BUILTIN_PRESETS:
            return _normalize_entry(**BUILTIN_PRESETS[name])
        return None

    def get_positive(self, name: str) -> str:
        entry = self.get_entry(name)
        return entry["positive"] if entry else ""

    def get_negative(self, name: str) -> str:
        entry = self.get_entry(name)
        return entry["negative"] if entry else ""

    def list_all(self) -> dict[str, dict[str, str]]:
        result = {k: _normalize_entry(**v) for k, v in BUILTIN_PRESETS.items()}
        result.update(self._custom_presets)
        return result

    def resolve(self, name: str) -> str | None:
        """把用户输入的预设名解析为真实存在的预设名（含别名），解析不出返回 None。

        便捷封装，供指令与 LLM 工具统一调用；只做映射，不新建/覆盖预设。
        """
        return resolve_preset_name(name, self.list_all().keys())

    def list_custom(self) -> dict[str, dict[str, str]]:
        return dict(self._custom_presets)

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_PRESETS

    def save(
        self,
        name: str,
        artist: str,
        desc: str = "",
        *,
        positive: str = "",
        negative: str = "",
    ) -> None:
        entry = _normalize_entry(artist=artist, desc=desc, positive=positive, negative=negative)
        self._custom_presets[name] = entry
        self._webui_presets[name] = dict(entry)
        self._save_file()
        logger.info("[PresetManager] 已保存预设 '%s'", name)

    def delete(self, name: str) -> bool:
        if name not in self._custom_presets:
            return False
        self._custom_presets.pop(name, None)
        self._webui_presets.pop(name, None)
        self._save_file()
        logger.info("[PresetManager] 已删除预设 '%s'", name)
        return True

    def update(
        self,
        name: str,
        artist: str | None = None,
        desc: str | None = None,
        *,
        positive: str | None = None,
        negative: str | None = None,
    ) -> bool:
        if name not in self._custom_presets:
            return False
        cur = self._custom_presets[name]
        if artist is not None:
            cur["artist"] = artist.strip()
        if desc is not None:
            cur["desc"] = desc.strip()
        if positive is not None:
            cur["positive"] = positive.strip()
        if negative is not None:
            cur["negative"] = negative.strip()
        entry = _normalize_entry(**cur)
        self._custom_presets[name] = entry
        self._webui_presets[name] = dict(entry)
        self._save_file()
        logger.info("[PresetManager] 已更新预设 '%s'", name)
        return True

    def export_for_webui(self, default_preset: str = "") -> list[dict[str, Any]]:
        """为 WebUI 导出格式化的预设列表。"""
        out: list[dict[str, Any]] = []

        # 先排官方内置预设
        for name, info in BUILTIN_PRESETS.items():
            entry = _normalize_entry(**info)
            out.append({
                "name": name,
                "artist": entry["artist"],
                "positive": entry["positive"],
                "negative": entry["negative"],
                "desc": entry["desc"],
                "is_builtin": True,
                "is_default": (name == default_preset),
            })

        # 再排自定义预设
        for name, entry in sorted(self._custom_presets.items()):
            out.append({
                "name": name,
                "artist": entry.get("artist", ""),
                "positive": entry.get("positive", ""),
                "negative": entry.get("negative", ""),
                "desc": entry.get("desc", ""),
                "is_builtin": False,
                "is_default": (name == default_preset),
            })

        return out

    def export_template_list(self) -> list[dict[str, str]]:
        """导出为 AstrBot template_list 配置格式。"""
        out: list[dict[str, str]] = []
        for name, info in self._custom_presets.items():
            out.append({
                "__template_key": "preset",
                "name": name,
                "artist": info.get("artist", ""),
                "positive": info.get("positive", ""),
                "negative": info.get("negative", ""),
                "desc": info.get("desc", ""),
            })
        return out

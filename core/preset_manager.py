"""
质量前缀预设管理

内置常用预设，支持用户自定义保存/删除。
"""

import json
import re
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# WebUI template_list 里的条目 → 内部预设格式
# ---------------------------------------------------------------------------

def parse_webui_presets(raw: Any) -> dict[str, dict[str, str]]:
    """把 WebUI「预设管理」里的条目解析成内部格式。

    WebUI 的 template_list 每项长这样（`__template_key` 是编辑器加的标记）：
        {"__template_key": "preset", "name": "我的预设",
         "artist": "best quality, ...", "desc": "备注"}

    容错优先，和群号黑名单一个思路：解析不出来的项**跳过并记日志**，
    绝不抛异常。配置写错最多是这个预设不生效，但如果插件加载失败，
    用户连生图都用不了了。

    Args:
        raw: 配置值，正常情况下是 list[dict]，也容忍 None / 其它类型。

    Returns:
        {预设名: {"artist": ..., "desc": ...}}
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        # 兼容用户手写成 {"名字": "画师串"} 的情况
        raw = [{"name": k, "artist": v} for k, v in raw.items()]
    if not isinstance(raw, (list, tuple)):
        return {}

    result: dict[str, dict[str, str]] = {}
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            logger.debug("[PresetManager] 忽略第 %d 项：不是对象 (%r)", idx, item)
            continue

        name = str(item.get("name", "") or "").strip()
        artist = str(item.get("artist", "") or "").strip()
        desc = str(item.get("desc", "") or "").strip()

        if not name:
            logger.warning("[PresetManager] 忽略第 %d 项：预设名为空", idx)
            continue
        if " " in name:
            # 预设名带空格会让 `/nai -p 名字` 和 `--preset` 解析不出来
            logger.warning(
                "[PresetManager] 忽略预设 %r：名字不能含空格", name,
            )
            continue
        if not artist:
            logger.warning("[PresetManager] 忽略预设 %r：画师串为空", name)
            continue
        if name in BUILTIN_PRESETS:
            logger.warning(
                "[PresetManager] 忽略预设 %r：与内置预设重名（会覆盖官方效果，容易混淆）", name,
            )
            continue

        result[name] = {"artist": artist, "desc": desc or "自定义预设"}

    return result


class PresetManager:
    """预设管理器

    预设有两个来源，必须保持一致：

    1. **WebUI 配置**（`_conf_schema.json` 的 `custom_presets`，template_list）
       —— 推荐方式，增删改点几下就行，改完立即生效，不用重启
    2. **data/presets.json** —— 历史数据 / `/nai save` 指令写入的地方

    两者关系：**以 WebUI 为准**。

    - 启动时如果 presets.json 里有、WebUI 里没有的预设 → 视为「待迁移」，
      自动补进 WebUI 配置（只做一次，之后 presets.json 只作为指令写入的落盘）
    - WebUI 里删掉某个预设 → 下次同步时把 presets.json 里的同名项也删掉

    这样用户无论从哪边操作，看到的结果都一致，不会出现
    「文件里改了但界面看不到」这种迷惑情况。
    """

    def __init__(self, data_dir: Path, webui_presets: Any = None):
        self._preset_file = data_dir / "presets.json"
        self._custom_presets: dict[str, dict[str, str]] = {}
        # WebUI 配置里的预设（权威来源）
        self._webui_presets: dict[str, dict[str, str]] = parse_webui_presets(webui_presets)
        self._load_file()
        self._merge()

    # -- 文件读写 --------------------------------------------------------

    def _load_file(self) -> None:
        if self._preset_file.exists():
            try:
                with open(self._preset_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._custom_presets = data
                logger.info(
                    "[PresetManager] 已加载 %d 个自定义预设（文件）",
                    len(self._custom_presets),
                )
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
        """把两个来源合并成一份，以 WebUI 为准。

        规则：
        - WebUI 有的，覆盖文件里的同名项
        - 文件里有、WebUI 里没有的 → 迁移进 WebUI（并提示用户）
        - 两边都没有的 → 不存在
        """
        changed = False

        # 1) 文件里的旧预设，如果 WebUI 里没有，迁移过去
        for name, info in self._custom_presets.items():
            if name in self._webui_presets:
                continue
            if name in BUILTIN_PRESETS:
                continue
            artist = info.get("artist", "")
            if not artist:
                continue
            self._webui_presets[name] = {
                "artist": artist,
                "desc": info.get("desc", "") or "自定义预设",
            }
            changed = True
            logger.info(
                "[PresetManager] 已迁移预设 '%s'（文件 → WebUI 配置）", name,
            )

        if changed:
            logger.info(
                "[PresetManager] 迁移完成。建议到 WebUI 检查「预设管理」，"
                "之后的增删改都在那里做即可。"
            )

        # 2) 以 WebUI 为准，重建生效集合
        self._custom_presets = dict(self._webui_presets)
        if changed:
            self._save_file()

    def reload_from_webui(self, webui_presets: Any, *, exact: bool = False) -> None:
        """WebUI 配置变更后重新同步（例如插件重载时）。

        Args:
            webui_presets: 新的 WebUI 配置值
            exact: 是否「精确模式」。

                这个参数存在是因为**启动时和重载时的正确行为不一样**：

                - 启动（exact=False）：文件里有、WebUI 里没有的项，可能是
                  「用户还没迁移的旧数据」，也可能是「用户在 WebUI 里删掉的」。
                  两者凭配置无法区分。宁可多留一次（用户能再删），
                  也不要静默丢数据 —— 所以自动迁移进来。
                - 重载（exact=True）：此时配置是权威的，以它为准，
                  不在配置里的就删掉。用指令改过的东西已经回写进配置了，
                  不会再出现在「文件有但配置没有」的状态里。

        """
        self._webui_presets = parse_webui_presets(webui_presets)
        if exact:
            for name in list(self._custom_presets):
                if name not in self._webui_presets:
                    self._custom_presets.pop(name, None)
                    logger.info(
                        "[PresetManager] 已移除预设 '%s'（WebUI 配置里已删除）", name,
                    )
            self._merge()
        else:
            self._merge()

    # -- 查询 ------------------------------------------------------------

    def get(self, name: str) -> str | None:
        """获取预设的 artist 值，返回 None 表示预设不存在"""
        if name in self._custom_presets:
            return self._custom_presets[name].get("artist")
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]["artist"]
        return None

    def list_all(self) -> dict[str, dict[str, str]]:
        """列出所有预设（内置 + 自定义），自定义覆盖同名内置"""
        result = dict(BUILTIN_PRESETS)
        result.update(self._custom_presets)
        return result

    def list_custom(self) -> dict[str, dict[str, str]]:
        """只列自定义预设"""
        return dict(self._custom_presets)

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_PRESETS

    # -- 修改 ------------------------------------------------------------

    def save(self, name: str, artist: str, desc: str = "") -> None:
        """保存自定义预设（WebUI 和文件同时更新）"""
        entry = {
            "artist": artist.strip(),
            "desc": desc.strip() or "自定义预设",
        }
        self._custom_presets[name] = entry
        # 同步进 WebUI 侧，这样用户在配置页也能看到用指令加的预设
        self._webui_presets[name] = dict(entry)
        self._save_file()
        logger.info("[PresetManager] 已保存预设 '%s'", name)

    def delete(self, name: str) -> bool:
        """删除自定义预设，返回是否成功"""
        if name not in self._custom_presets:
            return False
        self._custom_presets.pop(name, None)
        self._webui_presets.pop(name, None)
        self._save_file()
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
        self._webui_presets[name] = dict(self._custom_presets[name])
        self._save_file()
        logger.info("[PresetManager] 已修改预设 '%s'", name)
        return True

    # -- 供 WebUI 回写 ---------------------------------------------------

    def export_for_webui(self) -> list[dict[str, str]]:
        """导成 WebUI template_list 需要的格式。

        指令（`/nai save` 等）改了预设之后，需要把它写回 AstrBot 配置，
        否则用户重载插件时 WebUI 里的旧值会把指令的改动覆盖掉。
        """
        out: list[dict[str, str]] = []
        for name, info in self._custom_presets.items():
            out.append({
                "__template_key": "preset",
                "name": name,
                "artist": info.get("artist", ""),
                "desc": info.get("desc", ""),
            })
        return out

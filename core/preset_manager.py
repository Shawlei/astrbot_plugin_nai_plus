"""
质量前缀与风格预设管理 (PresetManager)

支持预设三要素：
- artist: 质量前缀 / 画师串
- prompt: 预设自带的正向提示词（可选）
- negative: 预设特定的负面提示词（可选）

支持内置预设（出厂预设 + 用户新增内置）与用户自定义预设的分离存储与统一管理。
向下兼容历史旧格式。
"""

from collections.abc import MutableMapping
from copy import deepcopy
import json
from pathlib import Path

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger("astrbot")

# 系统出厂内置预设（硬编码定义，受保护不可修改或删除）
FACTORY_PRESETS: dict[str, dict[str, str]] = {
    "2.5D唯美风": {
        "artist": "0.9::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, textless version, The image is highly intricate finished drawn. Only the character's face is in anime style, but their body is in realistic style. 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::. 1.63::photorealistic::, 1.63::photo(medium)::, \\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,, very aesthetic, masterpiece, no text,",
        "prompt": "",
        "negative": "",
        "desc": "2.5D唯美风（Nai2API 默认）",
        "type": "builtin",
    },
    "韩漫小清新风": {
        "artist": "[[[artist:dishwasher1910]]], {{yd_(orange_maru)}}, [artist:ciloranko], [artist:sho_(sho_lwlw)], [ningen mame], year 2024,",
        "prompt": "",
        "negative": "",
        "desc": "韩漫小清新风格",
        "type": "builtin",
    },
    "本子动漫风": {
        "artist": "1.4::asanagi::,{{{{{artist:asanagi}}}}},1.2::xiaoluo_xl::,1.3::Artist: misaka_12003-gou::,1.2::Artist:shexyo::,0.7::Artist:b.sa_(bbbs)::,1::Artist:qiandaiyiyu::,1.05::artist:natedecock::,1.05::artist:kunaboto::,0.75::artist:kandata_nijou::,1.05::artist:zer0.zer0 ::,1.05::artist:jasony::,0.75::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, {textless version, The image is highly intricate finished drawn,write realistically,true to life}, 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::, 1.63::photorealistic::,3::age slider::,1.63::photo(medium)::, 2::best quality, absurdres, very aesthetic, detailed, masterpiece::,-4::Muscle definition, abs::",
        "prompt": "",
        "negative": "",
        "desc": "本子动漫风格",
        "type": "builtin",
    },
    "GalGame风": {
        "artist": "artist:ningen_mame,, noyu_(noyu23386566),, toosaka asagi,, location,\\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,:,, very aesthetic, masterpiece, no text,",
        "prompt": "",
        "negative": "",
        "desc": "GalGame 风格",
        "type": "builtin",
    },
    "动漫风": {
        "artist": "artist collaboration, 0.70::artist:necomi ::, 0.80::artist:tan (tangent) ::, 1.38::artist:kanda done ::, 1.22::artist:quasarcake ::, 1.22::artist:atdan ::, 0.94::artist:fuumi (radial engine) ::, 1.70::artist:john kafka ::, 0.60::artist:meisansan ::, 0.98::artist:ogipote ::, 0.44::artist:nixeu ::, 0.74::artist:mignon ::, 0.94::artist:rangu ::, 1.18::artist:hiten (hitenkei) ::, 1.24::artist:freng ::, 0.56::artist:miwabe sakura ::, year 2024, perspective",
        "prompt": "",
        "negative": "",
        "desc": "动漫风（旧版画师混合）",
        "type": "builtin",
    },
}

# 兼容旧代码直接引用 BUILTIN_PRESETS
BUILTIN_PRESETS = FACTORY_PRESETS


def _normalize_entry(raw: dict | str, default_type: str = "custom") -> dict[str, str]:
    """统一规范化预设数据结构，兼容纯字符串或旧版缺少字段的字典"""
    if isinstance(raw, str):
        return {
            "artist": raw.strip(),
            "prompt": "",
            "negative": "",
            "desc": f"{'内置' if default_type == 'builtin' else '自定义'}预设",
            "type": default_type,
        }
    if not isinstance(raw, dict):
        raw = {}
    entry_type = str(raw.get("type", default_type) or default_type).strip().lower()
    if entry_type not in ("builtin", "custom"):
        entry_type = default_type

    return {
        "artist": str(raw.get("artist", "") or "").strip(),
        "prompt": str(raw.get("prompt", "") or "").strip(),
        "negative": str(raw.get("negative", "") or "").strip(),
        "desc": str(raw.get("desc", "") or "").strip() or f"{'内置' if entry_type == 'builtin' else '自定义'}预设",
        "type": entry_type,
    }


def sync_panel_preset(config: MutableMapping[str, object], manager: "PresetManager") -> bool:
    """把配置面板的一次性预设输入同步到预设库。

    只有名称非空时才提交；保存成功后清空名称和三要素输入。这样面板的
    “保存配置并重载”可作为可靠确认动作，下一次重载不会再次新增同一批输入。
    保存失败时保留原输入，方便用户修正后重试。

    Args:
        config: AstrBot 配置对象或普通可变字典。
        manager: 负责分层存储的预设管理器。

    Returns:
        本次是否成功处理了一条面板预设。
    """
    name = str(config.get("preset_add_name", "") or "").strip()
    if not name:
        return False

    target_type = str(
        config.get("preset_add_target", "custom") or "custom"
    ).strip().lower()
    artist = str(config.get("preset_add_artist", "") or "").strip()
    prompt = str(config.get("preset_add_prompt", "") or "").strip()
    negative = str(config.get("preset_add_negative", "") or "").strip()

    manager.save(
        name=name,
        artist=artist,
        prompt=prompt,
        negative=negative,
        target_type=target_type,
        desc=(
            "通过配置面板添加的"
            f"{'用户内置' if target_type == 'builtin' else '自定义'}预设"
        ),
    )
    for field_name in (
        "preset_add_name",
        "preset_add_artist",
        "preset_add_prompt",
        "preset_add_negative",
    ):
        config[field_name] = ""
    return True


class PresetManager:
    """预设管理器，支持三要素（画师/正向/负面）与内置/自定义预设分层存储"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        # 持久化文件路径
        self._builtin_file = data_dir / "builtin_presets.json"
        self._custom_file = data_dir / "custom_presets.json"
        self._legacy_file = data_dir / "presets.json"

        # 内存数据存储
        self._user_builtin_presets: dict[str, dict[str, str]] = {}
        self._custom_presets: dict[str, dict[str, str]] = {}

        self._load()

    def _load(self) -> None:
        """加载用户新增的内置预设与自定义预设，并兼容历史旧文件"""
        # 1. 加载用户新增的内置预设
        if self._builtin_file.exists():
            try:
                with open(self._builtin_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._user_builtin_presets = {
                        k: _normalize_entry(v, default_type="builtin")
                        for k, v in data.items()
                        if k not in FACTORY_PRESETS
                    }
                logger.info("[PresetManager] 已加载 %d 个用户新增内置预设", len(self._user_builtin_presets))
            except Exception as e:
                logger.warning("[PresetManager] 加载内置预设文件失败: %s", e)
                self._user_builtin_presets = {}

        # 2. 加载自定义预设
        if self._custom_file.exists():
            try:
                with open(self._custom_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._custom_presets = {
                        k: _normalize_entry(v, default_type="custom")
                        for k, v in data.items()
                    }
                logger.info("[PresetManager] 已加载 %d 个自定义预设", len(self._custom_presets))
            except Exception as e:
                logger.warning("[PresetManager] 加载自定义预设文件失败: %s", e)
                self._custom_presets = {}
        elif self._legacy_file.exists():
            # 兼容旧版本 presets.json，自动读取并迁移至 custom_presets.json
            try:
                with open(self._legacy_file, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                if isinstance(legacy_data, dict):
                    self._custom_presets = {
                        k: _normalize_entry(v, default_type="custom")
                        for k, v in legacy_data.items()
                    }
                    self._save_custom()
                    logger.info("[PresetManager] 已从旧版 presets.json 自动迁移 %d 个预设到 custom_presets.json", len(self._custom_presets))
            except Exception as e:
                logger.warning("[PresetManager] 兼容加载旧版 presets.json 失败: %s", e)
                self._custom_presets = {}

    def _save_builtin(self) -> None:
        """持久化用户新增的内置预设"""
        self._builtin_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._builtin_file, "w", encoding="utf-8") as f:
            json.dump(self._user_builtin_presets, f, ensure_ascii=False, indent=2)

    def _save_custom(self) -> None:
        """持久化自定义预设"""
        self._custom_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._custom_file, "w", encoding="utf-8") as f:
            json.dump(self._custom_presets, f, ensure_ascii=False, indent=2)

    def get(self, name: str) -> dict[str, str] | None:
        """获取预设完整字典（包含 artist, prompt, negative, desc, type），未找到返回 None。
        查找优先级：自定义预设 -> 用户新增内置预设 -> 出厂内置预设
        """
        if not name:
            return None
        if name in self._custom_presets:
            return deepcopy(self._custom_presets[name])
        if name in self._user_builtin_presets:
            return deepcopy(self._user_builtin_presets[name])
        if name in FACTORY_PRESETS:
            return deepcopy(FACTORY_PRESETS[name])
        return None

    def get_artist(self, name: str) -> str | None:
        """向后兼容方法：仅获取预设的 artist 值"""
        preset = self.get(name)
        return preset.get("artist") if preset else None

    def list_builtin(self) -> dict[str, dict[str, str]]:
        """列出所有内置预设（出厂预设 + 用户新增内置预设）"""
        result = {k: deepcopy(v) for k, v in FACTORY_PRESETS.items()}
        result.update({k: deepcopy(v) for k, v in self._user_builtin_presets.items()})
        return result

    def list_custom(self) -> dict[str, dict[str, str]]:
        """列出所有自定义预设"""
        return {k: deepcopy(v) for k, v in self._custom_presets.items()}

    def list_all(self) -> dict[str, dict[str, str]]:
        """列出所有预设（内置 + 自定义，自定义覆盖同名内置）"""
        result = self.list_builtin()
        result.update(self.list_custom())
        return result

    def is_factory_builtin(self, name: str) -> bool:
        """判断是否为系统出厂内置预设（不可删除或覆盖）"""
        return name in FACTORY_PRESETS

    def is_builtin(self, name: str) -> bool:
        """判断是否为内置预设（出厂内置或用户新增内置）"""
        return name in FACTORY_PRESETS or name in self._user_builtin_presets

    def save(
        self,
        name: str,
        artist: str = "",
        prompt: str = "",
        negative: str = "",
        target_type: str = "custom",
        desc: str = "",
    ) -> None:
        """保存预设（支持内置或自定义）。
        
        出厂预设受保护，严禁被覆盖。
        """
        name = name.strip()
        if not name:
            raise ValueError("预设名称不能为空")

        if self.is_factory_builtin(name):
            raise ValueError(f"'{name}' 是系统出厂预设，禁止覆盖")

        normalized_type = str(target_type or "custom").strip().lower()
        if normalized_type not in ("builtin", "custom"):
            normalized_type = "custom"

        entry = {
            "artist": artist.strip(),
            "prompt": prompt.strip(),
            "negative": negative.strip(),
            "desc": desc.strip() or f"{'内置' if normalized_type == 'builtin' else '自定义'}预设",
            "type": normalized_type,
        }

        if normalized_type == "builtin":
            self._user_builtin_presets[name] = entry
            self._save_builtin()
            logger.info("[PresetManager] 已保存内置预设 '%s'", name)
        else:
            self._custom_presets[name] = entry
            self._save_custom()
            logger.info("[PresetManager] 已保存自定义预设 '%s'", name)

    def delete(self, name: str) -> bool:
        """删除预设。出厂预设保护不可删；用户新增内置或自定义预设可删"""
        name = name.strip()
        if not name or self.is_factory_builtin(name):
            return False

        deleted = False
        if name in self._custom_presets:
            del self._custom_presets[name]
            self._save_custom()
            deleted = True
            logger.info("[PresetManager] 已删除自定义预设 '%s'", name)

        if name in self._user_builtin_presets:
            del self._user_builtin_presets[name]
            self._save_builtin()
            deleted = True
            logger.info("[PresetManager] 已删除内置预设 '%s'", name)

        return deleted

    def update(
        self,
        name: str,
        artist: str | None = None,
        prompt: str | None = None,
        negative: str | None = None,
        desc: str | None = None,
    ) -> bool:
        """修改已有预设。出厂预设保护不可修改。"""
        name = name.strip()
        if not name or self.is_factory_builtin(name):
            return False

        target_dict = None
        save_fn = None

        if name in self._custom_presets:
            target_dict = self._custom_presets
            save_fn = self._save_custom
        elif name in self._user_builtin_presets:
            target_dict = self._user_builtin_presets
            save_fn = self._save_builtin

        if target_dict is None or save_fn is None:
            return False

        if artist is not None:
            target_dict[name]["artist"] = artist.strip()
        if prompt is not None:
            target_dict[name]["prompt"] = prompt.strip()
        if negative is not None:
            target_dict[name]["negative"] = negative.strip()
        if desc is not None:
            target_dict[name]["desc"] = desc.strip()

        save_fn()
        logger.info("[PresetManager] 已修改预设 '%s'", name)
        return True

    def format_preset_list(self, target: str = "all") -> str:
        """生成预设文本摘要，供聊天指令查看。

        Args:
            target: "all" (全部), "builtin" (仅内置), "custom" (仅自定义)
        """
        target = str(target or "all").strip().lower()
        sections: list[str] = []

        # 1. 内置预设部分
        if target in ("all", "builtin"):
            builtins = self.list_builtin()
            lines: list[str] = ["【内置预设库】"]
            if not builtins:
                lines.append("  (暂无内置预设)")
            else:
                for name, info in builtins.items():
                    tag = "[出厂内置]" if name in FACTORY_PRESETS else "[用户新增内置]"
                    desc = info.get("desc", "")
                    lines.append(f"▶ {name} {tag}{f' - {desc}' if desc else ''}")
                    lines.append(f"  • 质量前缀: {info.get('artist', '')}")
                    if info.get("prompt"):
                        lines.append(f"  • 正向词: {info.get('prompt')}")
                    if info.get("negative"):
                        lines.append(f"  • 负向词: {info.get('negative')}")
                    lines.append("")
            sections.append("\n".join(lines).rstrip())

        # 2. 自定义预设部分
        if target in ("all", "custom"):
            customs = self.list_custom()
            lines = ["【自定义预设库】"]
            if not customs:
                lines.append("  (暂无自定义预设，可在配置面板添加或使用 /nai save <名称> <质量前缀> 保存)")
            else:
                for name, info in customs.items():
                    desc = info.get("desc", "")
                    lines.append(f"▶ {name} [自定义]{f' - {desc}' if desc else ''}")
                    lines.append(f"  • 质量前缀: {info.get('artist', '')}")
                    if info.get("prompt"):
                        lines.append(f"  • 正向词: {info.get('prompt')}")
                    if info.get("negative"):
                        lines.append(f"  • 负向词: {info.get('negative')}")
                    lines.append("")
            sections.append("\n".join(lines).rstrip())

        return "\n\n".join(sections).strip()

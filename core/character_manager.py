"""
角色名映射与查表管理器

在提示词直译前，检测并提取用户输入中的角色名/别名/简称，
转换为官方 Danbooru 规范标签。

特性：
1. 长度优先匹配（长词优先，如"雷电将军"优先于"影"，避免短别名误伤长名）
2. 单字/ASCII别名安全边界校验（如"影"不误匹配"倒影/摄影"，"w"不误匹配"white"）
3. 内置海量主流二次元角色字典（崩铁、原神、绝区零、方舟、BA、赛马娘、经典ACG）
4. 支持用户自定义映射文件（data/custom_characters.json），自动合并并支持覆盖
5. 支持纯角色名输入 0 延迟秒回，支持与剩余提示词无缝分离与合并
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger("astrbot")

# 单字中文角色容易与常见词汇混淆时的前缀/后缀保护集合
_CJK_COMPOUND_CHARS = set(
    "倒阴背身摄投光黑残人暗剪缩落树幻合电重晴星夜空时放半高碧风钢提竖口手杨垂优典清哲少青中老童新令口号指除七朝转刹刀利剑真水"
)

# 常见中英文连接词（若提取后仅剩余这些词，视为纯角色输入）
_CONJUNCTION_PATTERN = re.compile(
    r"^[\s,，、;&和与跟及同以及还有andwith\/\\+＋-]*$", re.IGNORECASE
)


def _normalize_tags(raw: Any) -> list[str]:
    """将字符串或列表规范化为 Danbooru tag 列表"""
    if not raw:
        return []
    tags: list[str] = []
    if isinstance(raw, str):
        parts = raw.replace("，", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.extend(item.replace("，", ",").split(","))
            else:
                parts.append(str(item))
    else:
        parts = [str(raw)]

    for p in parts:
        cleaned = p.strip()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags


def _clean_remaining_text(text: str) -> str:
    """清理提取角色后的剩余文本，去除多余标点与空白"""
    if not text:
        return ""

    # 全角符号转半角逗号/空格
    res = text.replace("，", ", ").replace("、", ", ").replace("；", ", ").replace(";", ", ")
    # 清理多余空白
    res = re.sub(r"[ \t]+", " ", res)
    # 逗号合并整理
    res = re.sub(r"(\s*,\s*)+", ", ", res)
    # 去除首尾标点空白
    res = res.strip(" \t\r\n,;，；、")

    # 如果剩余文本只剩连接词（如"和"、"与"），视为空
    if _CONJUNCTION_PATTERN.match(res):
        return ""

    return res.strip()


class CharacterManager:
    """二次元角色名映射与查表管理器"""

    def __init__(
        self,
        default_path: Union[str, Path, None] = None,
        custom_path: Union[str, Path, None] = None,
    ):
        """
        Args:
            default_path: 默认内置角色字典路径（通常为 data/characters.json）
            custom_path: 用户自定义角色字典路径（如 data/custom_characters.json）
        """
        base_dir = Path(__file__).resolve().parent.parent
        self.default_path = (
            Path(default_path) if default_path else base_dir / "data" / "characters.json"
        )
        self.custom_path = (
            Path(custom_path)
            if custom_path
            else base_dir / "data" / "custom_characters.json"
        )

        # 核心字典：alias -> list of tags
        self._mapping: dict[str, list[str]] = {}
        # 按长度降序排列的别名列表
        self._sorted_aliases: list[str] = []

        self.load()

    def load(self) -> None:
        """从文件加载/重新加载映射字典"""
        mapping: dict[str, list[str]] = {}

        # 1. 加载默认词表
        if self.default_path.exists():
            try:
                with open(self.default_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            k_clean = str(k).strip()
                            tags = _normalize_tags(v)
                            if k_clean and tags:
                                mapping[k_clean] = tags
                        logger.info(
                            "[CharacterManager] 已加载内置角色字典: %d 个别名",
                            len(mapping),
                        )
                    else:
                        logger.warning(
                            "[CharacterManager] 默认字典格式非法（非字典对象）: %s",
                            self.default_path,
                        )
            except Exception as e:
                logger.error("[CharacterManager] 加载内置角色字典失败: %s", e)
        else:
            logger.warning("[CharacterManager] 内置角色字典未找到: %s", self.default_path)

        # 2. 加载用户自定义词表（可覆盖内置同名项）
        if self.custom_path.exists():
            try:
                with open(self.custom_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        count = 0
                        for k, v in data.items():
                            k_clean = str(k).strip()
                            tags = _normalize_tags(v)
                            if k_clean and tags:
                                mapping[k_clean] = tags
                                count += 1
                        logger.info(
                            "[CharacterManager] 已加载用户自定义角色字典: %d 个别名",
                            count,
                        )
                    else:
                        logger.warning(
                            "[CharacterManager] 自定义字典格式非法: %s",
                            self.custom_path,
                        )
            except Exception as e:
                logger.error("[CharacterManager] 加载自定义角色字典失败: %s", e)

        self._mapping = mapping
        # 按照别名长度从长到短排序（核心规则：长词优先匹配）
        self._sorted_aliases = sorted(
            self._mapping.keys(),
            key=lambda x: (-len(x), x),
        )

    def reload(self) -> None:
        """热重载词表"""
        self.load()

    def get_tag(self, name: str) -> list[str] | None:
        """精确获取某个角色名的 Tag 列表"""
        return self._mapping.get(name.strip())

    def _build_match_regex(self, alias: str) -> re.Pattern:
        """根据别名特性构建匹配正则。

        - 纯 ASCII 词（如 'miku', 'saber', 'w', '100kg'）：使用单词边界 \\b，防止子串误伤。
        - 单字中文（如 '影', '空'）：前后不能是汉字或常见复合词构词字。
        - 多字中文（如 '雷电将军', '流萤'）：直接全字匹配。
        """
        escaped = re.escape(alias)
        is_ascii = all(ord(c) < 128 for c in alias)

        if is_ascii:
            # 必须匹配完整单词
            return re.compile(rf"\b{escaped}\b", re.IGNORECASE)

        if len(alias) == 1:
            # 单字中文保护：前后不能为中文字符（除非是连接词和标点）
            # 特别防范 "倒影"、"天空"、"摄影"、"优雅"
            return re.compile(
                rf"(?<![\u4e00-\u9fa5a-zA-Z0-9_]){escaped}(?![\u4e00-\u9fa5a-zA-Z0-9_]|(?<=[和与跟及]))",
                re.IGNORECASE,
            )

        # 2个字符及以上中文/混合别名
        return re.compile(escaped, re.IGNORECASE)

    def extract_and_replace(self, text: str) -> tuple[str, list[str]]:
        """从输入文本中提取角色名并替换。

        按别名长度由长至短依次扫描，记录命中区间并替换。
        确保多角色、同一角色多种称谓不冲突。

        Returns:
            (remaining_text, character_tags)
            - remaining_text: 移除角色名并清理标点后的剩余文本（若全部为角色则为空字符串 ""）
            - character_tags: 提取到的 Danbooru 官方 Tag 列表（保持提取顺序且去重）
        """
        text_str = str(text or "").strip()
        if not text_str or not self._sorted_aliases:
            return text_str, []

        occupied_spans: list[tuple[int, int]] = []
        matched_spans: list[tuple[int, int, str]] = []  # (start, end, alias)

        def _is_overlapping(start: int, end: int) -> bool:
            for s, e in occupied_spans:
                if max(start, s) < min(end, e):
                    return True
            return False

        for alias in self._sorted_aliases:
            pattern = self._build_match_regex(alias)
            for m in pattern.finditer(text_str):
                start, end = m.start(), m.end()
                if _is_overlapping(start, end):
                    continue

                # 额外保护：如果单字中文命中，检查前后是否构成合成词
                if len(alias) == 1 and "\u4e00" <= alias <= "\u9fa5":
                    prev_char = text_str[start - 1] if start > 0 else ""
                    next_char = text_str[end] if end < len(text_str) else ""
                    if prev_char in _CJK_COMPOUND_CHARS or next_char in _CJK_COMPOUND_CHARS:
                        continue

                occupied_spans.append((start, end))
                matched_spans.append((start, end, alias))

        if not matched_spans:
            return text_str, []

        # 按在原文本中出现的位置顺序排序 spans
        matched_spans.sort(key=lambda x: x[0])

        # 按文本中出现的前后顺序整理 Tag 并去重
        matched_tags: list[str] = []
        for _, _, alias in matched_spans:
            for t in self._mapping[alias]:
                if t not in matched_tags:
                    matched_tags.append(t)

        # 逐段拼出扣除角色名后的文本
        remaining_parts: list[str] = []
        last_idx = 0
        for start, end, _ in matched_spans:
            if start > last_idx:
                remaining_parts.append(text_str[last_idx:start])
            last_idx = end
        if last_idx < len(text_str):
            remaining_parts.append(text_str[last_idx:])

        remaining_raw = " ".join(remaining_parts)
        remaining_clean = _clean_remaining_text(remaining_raw)

        return remaining_clean, matched_tags

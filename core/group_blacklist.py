"""
群聊黑名单模块

被拉黑的群，无论群里谁发什么，都不触发生图，而且**完全静默**
—— 群里不会收到任何回复（连"已被禁用"都不发）。

为什么单独抽一个模块：黑名单要在三个地方判断（LLM 请求钩子、LLM 工具入口、
命令入口），逻辑必须完全一致。写散在各处迟早会出现"命令拦了但工具没拦"
这类漏网情况。集中在一处，只有一份真相。

配置格式（_conf_schema.json 的 group_blacklist）：
    逗号 / 分号 / 顿号 / 空格 / 换行分隔，例如
        123456789, 987654321
        123456789
        987654321
    也容忍用户顺手带上常见前缀（群号一般是从客户端直接复制的）。

只做一件事：把配置里的群号解析成集合，并提供命中的判断。
"""

import re
from typing import Any

from astrbot.api import logger

# 分隔符：中英文逗号、分号、顿号、竖线、各种空白（含换行）
_SPLIT_RE = re.compile(r"[,，;；、|\s]+")

# 常见前缀，用户从客户端复制群号时容易带上
_PREFIX_RE = re.compile(r"^(?:群号|群|qq群|QQ群|gid|group)[:：]?", re.IGNORECASE)

# 群号允许的形态：纯数字，或 "平台:群号" 这种带平台前缀的写法
# （后者给多平台用户用，因为不同平台的群号可能撞号）
_ID_RE = re.compile(r"^\d+$")


def parse_group_blacklist(raw: Any) -> set[str]:
    """把配置里的群号列表解析成集合。

    容错优先：用户填的格式五花八门，解析不出来时**宁可忽略这一项**，
    也不要抛异常 —— 配置写错最多是黑名单不生效，但如果插件加载失败，
    用户连生图都用不了了。所以这里对垃圾输入一律跳过。

    Args:
        raw: 配置值。正常情况下是字符串，但也兼容用户填成列表/数字的情况。

    Returns:
        群号字符串集合。解析不出来的项已丢弃。
    """
    if raw is None:
        return set()

    # 用户可能在 WebUI 里填成了列表，或者填成了纯数字
    if isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    else:
        text = str(raw)
        if not text.strip():
            return set()
        # 先按分隔符拆
        items = _SPLIT_RE.split(text)

    result: set[str] = set()
    for item in items:
        token = item.strip()
        if not token:
            continue

        # 剥掉「群号:」这类前缀
        token = _PREFIX_RE.sub("", token).strip()
        if not token:
            continue

        # 支持 "平台:群号"，只取冒号后面那段
        if ":" in token or "：" in token:
            token = re.split(r"[:：]", token)[-1].strip()
            if not token:
                continue

        if _ID_RE.match(token):
            result.add(token)
        else:
            # 解析不了就跳过，但留个调试线索，便于用户排查为什么黑名单没生效
            logger.debug("[Nai黑名单] 忽略无法解析的群号配置项: %r", item)

    return result


class GroupBlacklist:
    """群聊黑名单。

    用法：
        bl = GroupBlacklist(config.get("group_blacklist"))
        if bl.is_blocked(event):   # 命中黑名单
            ...
    """

    def __init__(self, raw: Any = None, *, silent_log: bool = True) -> None:
        self._groups: set[str] = parse_group_blacklist(raw)
        self._silent_log = silent_log

    def __len__(self) -> int:
        return len(self._groups)

    def __bool__(self) -> bool:
        """空黑名单视为「未启用」，调用方可以据此快速跳过后续判断。"""
        return bool(self._groups)

    @property
    def groups(self) -> set[str]:
        """返回黑名单群号集合的副本，避免外部误改。"""
        return set(self._groups)

    def is_blocked_group_id(self, group_id: str | None) -> bool:
        """群号本身是否在黑名单里。

        注意：空群号（私聊）永远返回 False —— 黑名单只管群，不管私聊。
        """
        if not group_id:
            return False
        return str(group_id).strip() in self._groups

    def is_blocked(self, event: Any) -> bool:
        """当前事件是否来自被拉黑的群。

        Args:
            event: AstrMessageEvent

        Returns:
            True 表示该群已被拉黑，调用方应当静默丢弃这条消息。
        """
        # 黑名单为空时直接短路，不给正常流程增加任何开销
        if not self._groups:
            return False

        group_id = self._get_group_id(event)
        if not group_id:
            # 私聊，或者平台没提供群号 —— 都不拦
            return False

        blocked = str(group_id).strip() in self._groups
        if blocked and not self._silent_log:
            logger.info("[Nai黑名单] 已拦截来自黑名单群 %s 的生图请求", group_id)
        return blocked

    @staticmethod
    def _get_group_id(event: Any) -> str:
        """从事件里取群号，取不到就返回空串。

        用 getattr + callable 双重保护：不同平台的 event 实现不一定
        都提供 get_group_id（这是个抽象基类上的方法，实现可能缺失），
        真取不到时按「不是群」处理，而不是抛异常 —— 拦不住比报错好。
        """
        getter = getattr(event, "get_group_id", None)
        if not callable(getter):
            return ""
        try:
            value = getter()
        except Exception:
            return ""
        if value is None:
            return ""
        return str(value).strip()

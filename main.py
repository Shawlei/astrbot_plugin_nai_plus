"""
Nai2API AstrBot 生图插件（nai_plus 增强版）

通过 Nai2API 网关调用 NovelAI 生成图片。

本项目基于 helloWKQ 的原版插件二开，感谢原作者：
    https://github.com/helloWKQ/AstrBot_Nai2API
增强内容见 CHANGELOG.md 的 v1.2.0 / v1.3.0。

注意：插件名特意用了 astrbot_plugin_nai_plus（不是 astrbot_plugin_nai2api），
这样它能和原版插件同时装在同一台 AstrBot 上，不会互相顶掉。
"""

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import mcp

# 插件名。AstrBot 用这个名字区分插件，也是数据目录名。
# 改这里要注意：数据目录会跟着变，已有预设/缓存不会自动迁移。
PLUGIN_NAME = "astrbot_plugin_nai_plus"


class ImageSendError(Exception):
    """图片已经生成、保存到本地，但发到聊天平台失败。

    单独定义一个异常类型，是为了让调用方能区分「生图失败」和「发图失败」：
    前者是 NovelAI / Nai2API 的问题，后者是 QQ 客户端（NapCat 等）的问题，
    给用户的提示和排查方向完全不同。
    """

    def __init__(self, image_path: Path, cause: Exception):
        self.image_path = image_path
        self.cause = cause
        super().__init__(f"图片发送失败: {_short_err(cause)}")


def _short_err(e: BaseException, limit: int = 160) -> str:
    """把平台适配器的异常压成一行可读文本。

    NapCat 的 ActionFailed 把整个 NTQQ 事件名塞进 message 里，一行能有几百字，
    而且 str() 后带换行和 `{}`，直接发给用户或写日志都很难看。这里只保留
    第一行、截断到 limit。
    """
    text = str(e) or e.__class__.__name__
    # ActionFailed 的 repr 形如 <ActionFailed status='failed', retcode=1200, ..., message='Timeout: ...'>
    m = re.search(r"message='([^']*)'", text)
    if m:
        text = m.group(1)
    text = text.strip().splitlines()[0] if text.strip() else e.__class__.__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Node, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.group_blacklist import GroupBlacklist
from .core.image_manager import ImageManager
from .core.img2img_client import Img2ImgClient, Img2ImgError
from .core.nai2api_client import (
    Nai2ApiClient,
    DEFAULT_ARTIST,
    DEFAULT_NEGATIVE,
    COST_NORMAL_V45,
    COST_NORMAL_V5,
    COST_2K,
    COST_4K,
    is_v5_model,
    get_generation_cost,
    resolve_model_alias,
)
from .core.preset_manager import PresetManager, merge_tags
from .core.translate_manager import TranslateManager, TranslateError

# 解析用户输入中的尺寸前缀、-p/--preset、--artist 和 --negative 参数
_SIZE_PATTERN = re.compile(
    r'^(2K竖图|2K横图|2K方图|4K竖图|4K横图|4K方图|竖图|横图|方图)\s+',
    re.IGNORECASE,
)
_PRESET_PATTERN = re.compile(r'(?:-p|--preset)\s+(\S+)', re.IGNORECASE)
_MODEL_PATTERN = re.compile(r'(?:-m|--model)\s+(\S+)', re.IGNORECASE)
_SEED_PATTERN = re.compile(r'--seed\s+(\d+)', re.IGNORECASE)

# 图生图参数（相似度 / 降噪），接受 0.7 和 .7 两种写法
_STRENGTH_PATTERN = re.compile(r'--strength\s+(\d*\.?\d+)', re.IGNORECASE)
_NOISE_PATTERN = re.compile(r'--noise\s+(\d*\.?\d+)', re.IGNORECASE)
# 强制开关：不接参数的裸标志位
_FORCE_I2I_PATTERN = re.compile(r'(?:^|\s)--i2i(?=\s|$)', re.IGNORECASE)
_FORCE_T2I_PATTERN = re.compile(r'(?:^|\s)--no-i2i(?=\s|$)', re.IGNORECASE)
# --no-preset：本次不使用「默认预设」（显式 -p 仍然优先）
_NO_PRESET_PATTERN = re.compile(r'(?:^|\s)--no-preset(?=\s|$)', re.IGNORECASE)

# 「贪婪参数」的终止边界。--artist / --negative 的值会一直读到下一个参数名为止，
# 所以每新增一个带值的参数，都必须加进这个列表，否则会被前面的贪婪匹配吃掉。
#
# 踩坑记录：加了 --strength 却忘了改这里，`--artist a, b --strength 0.7`
# 会把整个 "--strength 0.7" 当成 artist 的一部分。
# 同理 --no-preset 也要加进来，否则 `--artist a, b --no-preset` 会把
# "--no-preset" 当成画师串的一部分。
_STOP_TOKENS = (
    r'--negative|--artist|-p|--preset|-m|--model|--seed|--strength|--noise|--i2i|--no-i2i|--no-preset'
)
_ARTIST_PATTERN = re.compile(
    rf'--artist\s+(.+?)(?=\s+(?:{_STOP_TOKENS})\s+|$)', re.DOTALL
)
_NEGATIVE_PATTERN = re.compile(
    rf'--negative\s+(.+?)(?=\s+(?:{_STOP_TOKENS})\s+|$)', re.DOTALL
)

# ---------------------------------------------------------------------------
# 日志用截断（v1.6.1 可观测性）
# ---------------------------------------------------------------------------
# 背景：用户反馈「画不出我要的效果」，可排查「某个标签到底哪来的」时却被日志
# 自己挡住了 —— 直译日志曾把输入与输出两段各砍到 60 字符，又**不加任何截断
# 标记**，读者根本看不出被砍过，会误把它当成全部内容。日志的职责就是让人看清
# 「到底发了什么」，绝不能静默截断。
# 这里统一用一个足够大的上限：不够长原样输出，超出才截断并显式标出「共 N 字符」。
_PROMPT_LOG_LIMIT = 1000


def _clip_for_log(text: str, limit: int = _PROMPT_LOG_LIMIT) -> str:
    """把可能过长的提示词/标签压成适合写日志的一行，**截断时必留痕**。

    和 `_short_err` 的分工：`_short_err` 是给「异常信息」防刷屏用的；这里服务
    的是「提示词内容」日志，目标是**看得清**而不是压缩，所以上限设得很大
    （默认 1000 字符），且一旦截断就附上「…（共 N 字符）」—— 让人一眼知道
    原文更长。相比之前那种不声不响的 `text[:60]`，宁可多打几百字也不能误导排查。
    """
    if not text:
        return text or ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（共 {len(text)} 字符）"


HELP_TEXT = (
    "用法: /{cmd} [尺寸] <提示词> [-p <预设>] [-m <模型>] [--artist <质量前缀>] [--negative <负面>] [--seed <种子>]\n"
    "预设: /{cmd} presets(预设) | /{cmd} save(保存) <名称> <质量前缀> | /{cmd} update(修改) <名称> <新前缀> | /{cmd} del(删除) <名称>\n"
    "默认预设: 配置项「默认预设」填了名字后，每次生图自动套用它（画师串+正向词+负向词）\n"
    "  -p <预设>   临时改用别的预设（优先于默认预设）\n"
    "  --no-preset 本次完全不用预设\n"
    "余额: /{cmd} balance(余额/点数/次数)\n"
    "词库: /{cmd} dict(词库) 查看状态 | /{cmd} dict(词库) <文本> 测试命中情况\n"
    "尺寸: 竖图|横图|方图|2K竖图|2K横图|2K方图|4K竖图|4K横图|4K方图\n"
    "模型: 5(V5) | 4.5 | 4 | 3 | furry | 2 | safe   —— 也可写全名 nai-diffusion-5-full\n\n"
    "图生图: 先发一张图，然后「回复」那张图再发本指令，会自动用它作参考图\n"
    "  --strength 0.7   相似度（越大越贴近原图）\n"
    "  --noise 0.2      降噪（越大离原图越远）\n"
    "  --i2i / --no-i2i 强制走图生图 / 强制走文生图（防止回复带图时误触发）\n\n"
    "扣点说明:\n"
    "  V4.5 普通尺寸 = 1 点    V5 普通尺寸 = 5 点\n"
    "  2K = 15 点              4K = 25 点\n"
    "  高扣点会先让你确认一次，避免误扣\n\n"
    "中文/英文提示词都会自动直译成英文标签再生成\n"
    "  先查内置词库（雷电将军、白发、樱花…约 1000 条），命中的词不调用模型\n"
    "  剩下的没命中的部分才交给翻译模型，翻完还会检查有没有残留中文\n\n"
    "示例:\n"
    "  /{cmd} 1girl, silver hair\n"
    "  /{cmd} 一个银发女孩                          (中文直接写，自动翻译)\n"
    "  /{cmd} -m 5 -p 动漫风 1girl, silver hair     (用 V5 + 动漫风预设)\n"
    "  /{cmd} -p 高质量 1girl, silver hair\n"
    "  /{cmd} 2K竖图 -p 动漫风 1girl, silver hair\n"
    "  /{cmd} 1girl --artist best quality, absurdres\n"
    "  /{cmd} 1girl --negative bad anatomy, bad hands\n"
    "  /{cmd} 1girl --seed 12345\n"
    "  /{cmd} 1girl --no-preset                        (本次不用默认预设)\n"
    "  (回复一张图) /{cmd} 换个背景 --strength 0.6      (图生图)\n"
    "  /{cmd} save 我的预设 best quality, absurdres, detailed\n"
    "  /{cmd} 保存 我的预设 best quality, absurdres, detailed\n"
    "  /{cmd} update 我的预设 best quality, masterpiece\n"
    "  /{cmd} 修改 我的预设 best quality, masterpiece\n"
    "  /{cmd} del 我的预设\n"
    "  /{cmd} 删除 我的预设\n"
    "  /{cmd} balance"
)


def _parse_nai_command(text: str) -> tuple[
    str | None, str, str | None, str | None, str | None, int | None, str | None,
    float | None, float | None, bool | None, bool,
]:
    """
    解析 /nai 指令的参数。

    格式:
        /nai [尺寸] <提示词> [-p <预设>] [-m <模型>] [--artist <质量前缀>]
             [--negative <负面提示词>] [--seed <种子>]
             [--strength <相似度>] [--noise <降噪>] [--i2i | --no-i2i]
             [--no-preset]

    参数顺序可以任意，但 --artist / --negative 的值会一直读到下一个
    "参数名"为止，所以这两个建议写在最后。

    Returns:
        (size, prompt, preset_name, artist, negative, seed, model,
         strength, noise, force_i2i, force_no_preset)

        force_i2i: True = 强制走图生图；False = 强制走文生图；None = 自动判断
                   （自动判断的规则是「回复消息里有没有图」）
        force_no_preset: True = 用户写了 --no-preset，本次不用「默认预设」。
                   注意它**不压制**显式 -p —— 显式指定的预设永远优先。
    """
    text = text.strip()
    size = None

    # 提取尺寸前缀
    m = _SIZE_PATTERN.match(text)
    if m:
        size = m.group(1)
        text = text[m.end():]

    # 提取模型（-m / --model），支持简写如 "5"、"4.5"
    model = None
    m = _MODEL_PATTERN.search(text)
    if m:
        model = resolve_model_alias(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 提取预设名
    preset_name = None
    m = _PRESET_PATTERN.search(text)
    if m:
        preset_name = m.group(1)
        text = text[:m.start()] + text[m.end():]

    # 提取种子
    seed = None
    m = _SEED_PATTERN.search(text)
    if m:
        seed = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 提取相似度 / 降噪
    strength = None
    m = _STRENGTH_PATTERN.search(text)
    if m:
        strength = _clamp01(m.group(1))
        text = text[:m.start()] + text[m.end():]

    noise = None
    m = _NOISE_PATTERN.search(text)
    if m:
        noise = _clamp01(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 提取强制开关（--no-i2i 要先判断，因为它是 --i2i 的超集）
    force_i2i = None
    m = _FORCE_T2I_PATTERN.search(text)
    if m:
        force_i2i = False
        text = text[:m.start()] + text[m.end():]
    else:
        m = _FORCE_I2I_PATTERN.search(text)
        if m:
            force_i2i = True
            text = text[:m.start()] + text[m.end():]

    # 提取 --no-preset（本次不用默认预设的裸标志位）。
    # 必须放在 --artist / --negative 之前剥掉：虽然 --no-preset 已在 _STOP_TOKENS
    # 里能挡住贪婪匹配，但标志位本身还得从 text 里删掉，否则会混进提示词。
    force_no_preset = False
    m = _NO_PRESET_PATTERN.search(text)
    if m:
        force_no_preset = True
        text = text[:m.start()] + text[m.end():]

    # 提取负面提示词
    negative = None
    m = _NEGATIVE_PATTERN.search(text)
    if m:
        negative = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    # 提取质量前缀/画师串
    artist = None
    m = _ARTIST_PATTERN.search(text)
    if m:
        artist = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    prompt = text.strip()
    return (
        size, prompt, preset_name, artist, negative, seed, model,
        strength, noise, force_i2i, force_no_preset,
    )


def _clamp01(value) -> float | None:
    """把用户输入的数值夹到 0~1。

    相似度和降噪在几乎所有渠道里的合法区间都是 0~1，
    这里提前夹一次，避免把 1.5 这种值原样发给渠道再吃一个难懂的报错。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


async def _fetch_reference_image(event: AstrMessageEvent) -> tuple[str | None, str | None]:
    """从「用户回复的那条消息」里取出参考图。

    Returns:
        (图片本地路径, 错误提示)。成功时第二个值为 None。

    为什么用回复而不是「当前消息里带图」：
    用户发图通常是一句话带图，此时那条消息本身就是图片消息，
    再让用户在同一条消息里写提示词体验很别扭；而「先发图、再回复它发指令」
    能复用历史图片（比如拿上一条生成结果继续改），更实用。
    """
    try:
        from astrbot.core.utils.quoted_message import extract_quoted_message_images
    except ImportError:
        return None, (
            "当前 AstrBot 版本不支持读取被回复消息里的图片（需要 4.16+），"
            "图生图无法使用。可以用 --no-i2i 强制走文生图"
        )

    try:
        refs = await extract_quoted_message_images(event)
    except Exception as e:
        logger.warning("[Nai2API] 读取被回复消息的图片失败: %s", e)
        return None, f"读取参考图失败：{e}"

    if not refs:
        return None, None

    # 只取第一张 —— 绝大多数图生图渠道只接受单张参考图，
    # 多张图的行为各家不一致，与其猜不如明确只用第一张
    first = refs[0]
    logger.info("[Nai2API] 从回复消息里拿到参考图: %s", str(first)[:120])

    try:
        # _materialize_image 是同步函数（内部用 urllib 做阻塞 IO），
        # 这里必须用 to_thread 丢到线程池执行：
        # 1) 直接 return _materialize_image(first) 会因为 urllib 阻塞
        #    事件循环最多 30 秒，期间机器人其他消息全部停摆；
        # 2) 写成 await _materialize_image(...) 更是错的 —— 对普通函数
        #    的返回值做 await，必然抛 TypeError（就是 v1.4.4 及以前
        #    图生图一直不可用的根因）。
        return await asyncio.to_thread(_materialize_image, first), None
    except Exception as e:
        logger.warning("[Nai2API] 参考图落地失败: %s", e)
        return None, f"参考图下载/解码失败：{e}"


def _materialize_image(ref: str) -> str:
    """把图片引用规整成本地路径。

    引用可能是 http(s) URL、base64://、data URL 或本地路径，
    这里统一成路径，方便后续读成 bytes 发给渠道。

    注意这是**同步实现**（用 urllib 做阻塞 IO），不要直接 await 它。
    调用方请用 ``await asyncio.to_thread(_materialize_image, ref)``，
    这样既不阻塞事件循环，又能正常拿到返回值。
    """
    import base64 as _b64
    import tempfile

    text = str(ref or "").strip()
    if not text:
        raise ValueError("空的图片引用")

    # 已经是本地路径
    p = Path(text)
    if p.exists() and p.is_file():
        return str(p)

    # file:// URI
    if text.startswith("file://"):
        from urllib.parse import unquote, urlparse

        path = unquote(urlparse(text).path)
        if Path(path).exists():
            return path
        raise FileNotFoundError(f"file:// 指向的文件不存在: {path}")

    # base64 形态
    if text.startswith("base64://") or text.startswith("data:"):
        payload = text.split("://", 1)[-1] if text.startswith("base64://") else text
        if payload.startswith("data:"):
            _, _, payload = payload.partition(",")
        data = _b64.b64decode(payload)
        suffix = ".png"
        fd, tmp = tempfile.mkstemp(prefix="nai_ref_", suffix=suffix)
        with open(fd, "wb") as f:
            f.write(data)
        return tmp

    # 网络 URL → 下到临时目录
    if text.startswith("http://") or text.startswith("https://"):
        import urllib.request

        req = urllib.request.Request(text, headers={"User-Agent": "AstrBot-NaiPlus/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if not data:
            raise ValueError("下载到的参考图是空的")
        fd, tmp = tempfile.mkstemp(prefix="nai_ref_", suffix=".png")
        with open(fd, "wb") as f:
            f.write(data)
        return tmp

    raise ValueError(f"认不出的图片引用格式: {text[:80]}")


def _parse_command_names(value) -> list[str]:
    """解析自定义命令名配置。

    支持 "nai" 或 "nai,niu,绘" 这种写法（中英文逗号、分号、换行都行）。
    第一项是主命令名，其余作为别名。全部非法时回退到 "nai"。

    注意：命令名里不能有空格（AstrBot 的命令匹配不支持）。
    """
    if value is None:
        return ["nai"]
    if isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value]
    else:
        text = str(value).replace("，", ",").replace("；", ",").replace(";", ",")
        raw = text.replace("\n", ",").split(",")

    names: list[str] = []
    for item in raw:
        name = item.strip().lstrip("/").lstrip("+")  # 允许用户顺手写 /nai 或 +nai
        if not name or " " in name:
            continue
        if name not in names:
            names.append(name)

    return names or ["nai"]


def _cmd(name: str, alias: set[str] | None = None):
    """动态注册命令。

    命令名来自配置，只能在 __init__ 之后注册，所以这里包一层。
    好处是不管配的什么名字，都能同时拿到命令别名。
    """
    return filter.command(name, alias=alias or set())


class Nai2ApiPlugin(Star):
    """Nai2API 生图插件"""

    # 二次确认的有效期（秒）
    _HD_CONFIRM_TTL = 600

    # 视为"确认"的回复
    _CONFIRM_WORDS = ("确认", "确定", "是", "好的", "yes", "y", "ok")
    # 视为"取消"的回复
    _CANCEL_WORDS = ("取消", "不生成", "算了", "不要", "no", "n")

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)

        api_url = str(config.get("api_url", "https://nai.sta1n.cn")).strip()
        token = str(config.get("token", "")).strip()
        timeout = int(config.get("timeout", 120))
        max_cached = int(config.get("max_cached_images", 50))

        self.client = Nai2ApiClient(
            api_url=api_url,
            token=token,
            default_size=str(config.get("default_size", "竖图")),
            default_model=str(config.get("default_model", "nai-diffusion-4-5-full")),
            default_steps=int(config.get("default_steps", 28)),
            default_scale=int(config.get("default_scale", 6)),
            default_cfg=float(config.get("default_cfg", 0)),
            default_sampler=str(config.get("default_sampler", "k_dpmpp_2m_sde")),
            default_negative=str(config.get("default_negative", DEFAULT_NEGATIVE)),
            default_artist=str(config.get("default_artist", DEFAULT_ARTIST)),
            default_noise_schedule=str(config.get("default_noise_schedule", "karras")),
            auto_composition=bool(config.get("auto_composition", True)),
            allow_2k=bool(config.get("allow_2k", True)),
            allow_4k=bool(config.get("allow_4k", True)),
            timeout=timeout,
        )

        self.imgr = ImageManager(
            data_dir=self.data_dir,
            max_cached=max_cached,
            timeout=timeout,
        )

        # 预设。两个来源：WebUI 配置的「预设管理」列表（权威）+ data/presets.json
        # （历史数据 / 指令写入）。以 WebUI 为准，旧文件数据会自动迁移过去，
        # 这样用户无论从哪边改，看到的结果都一致。
        self.presets = PresetManager(
            self.data_dir, webui_presets=config.get("custom_presets", [])
        )

        # 默认预设：填了预设名后，每次生图自动套用它的三段内容
        # （画师串 + 附带正向词 + 附带负向词），不用每次写 -p
        self._default_preset = str(config.get("default_preset", "") or "").strip()

        # 提示词直译（中文/英文 → 英文标签）
        self.translator = TranslateManager(config, context)
        self._translate_enabled = bool(config.get("translate_enabled", True))
        self._translate_on_error = str(
            config.get("translate_on_error", "fallback")
        ).strip().lower()

        # 图生图（独立渠道，配置在 img2img 分组里）
        self._img2img_enabled = bool(config.get("img2img_enabled", False))
        # 图生图是否带上画师串。默认关：图生图渠道多为 OpenAI 风格接口，
        # 不认 NovelAI 的画师串语法。但绝不能静默丢弃 —— 用户配了画师串却
        # 看不出为什么没生效，是最难排查的那类问题。
        self._img2img_inherit_artist = bool(config.get("img2img_inherit_artist", False))
        img2img_conf = config.get("img2img") or {}
        if not isinstance(img2img_conf, dict):
            img2img_conf = {}
        self.img2img = Img2ImgClient(img2img_conf)

        self._llm_tool_enabled = bool(config.get("llm_tool_enabled", True))
        self._show_image_info = bool(config.get("show_image_info", True))
        self._confirm_hd = bool(config.get("confirm_hd_size", True))
        # 发图超时是否重试。默认关闭 —— 超时 ≠ 没发出去，
        # 重试会让用户收到两张重复的图（详见 _send_image_with_info 注释）
        self._send_retry_on_timeout = bool(config.get("send_retry_on_timeout", False))

        # 群聊黑名单：黑名单里的群彻底静默，绝不生图
        self.blacklist = GroupBlacklist(config.get("group_blacklist", ""))
        if self.blacklist:
            logger.info(
                "[Nai黑名单] 已启用，共 %d 个群被拉黑: %s",
                len(self.blacklist),
                ", ".join(sorted(self.blacklist.groups)),
            )

        # 自定义命令名/别名（默认 nai）
        self.command_names = _parse_command_names(config.get("command_names", "nai"))
        self.command_name = self.command_names[0]
        # 待确认的生图请求（V5 普通尺寸 / 2K / 4K），key 为会话标识
        self._pending_hd: dict[str, dict] = {}

        self._fix_llm_tool_schemas()

        # 注册自定义命令名。
        # filter.command 是在类定义时（导入阶段）执行的，那时候还读不到用户配置，
        # 所以配置里的额外名字只能在这里补救：给每个别名动态挂一个同逻辑的处理器。
        self._register_extra_commands()
        
        # 注册 WebUI 管理面板的后端 API（需要 AstrBot >= 4.26.0）
        self._register_webui_apis()

    def _register_webui_apis(self) -> None:
        """注册「插件 Pages」WebUI 管理面板的后端 API。

        页面文件在 pages/nai-config/，前端通过 AstrBot 注入的 bridge SDK 调这些接口。

        为什么写在 try 里、失败只记日志：
            `astrbot.api.web` 是 AstrBot 4.26.0 才有的模块。用户如果装的是老版本，
            这里会 ImportError。按「容错底线」原则，WebUI 面板不可用 ≠ 插件不可用，
            所以静默跳过，/nai 指令照常能用。

        为什么用 self.config 而不是 context.get_config()：
            这是踩过的坑。`context.get_config()` 返回的是 **AstrBot 全局配置**
            （data/cmd_config.json，里面是 dashboard 密码、平台适配器这些），
            往里写 default_artist 只会污染全局配置，插件根本读不到。
            插件自己的配置是构造函数传进来的 `config`，它本身就是一个 AstrBotConfig
            对象，绑定的是 data/config/astrbot_plugin_nai_plus_config.json，
            `save_config_async()` 会写到正确的文件。

        接口一览（bridge 端 endpoint 不带插件名前缀）：
            GET  config                 读取画师串 / 负向词 / 默认预设 / 自定义预设 / 内置预设 / 默认值
            POST config/artist          保存画师串           {"value": "..."}
            POST config/negative        保存负向词           {"value": "..."}
            POST preset/default         设置默认预设         {"name": "..."}（空 = 取消）
            POST presets                预设增删改           {"action": "add|update|delete", "data": {...}}
            POST preview                预览最终拼接结果      {"prompt": "...", "artist": "...", "negative": "..."}

        返回约定（和 bridge SDK 的兼容规则对齐）：
            成功 → json_response({...业务字段...})，前端直接拿到这个对象
            失败 → error_response("原因", status_code=4xx/5xx)，前端 await 会抛 Error
        """
        try:
            from astrbot.api.web import error_response, json_response, request
        except ImportError:
            logger.info(
                "[Nai WebUI] 当前 AstrBot 版本没有 astrbot.api.web（需要 >= 4.26.0），"
                "WebUI 管理面板不可用，指令功能不受影响"
            )
            return

        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            logger.info("[Nai WebUI] 当前 AstrBot 不支持 register_web_api，跳过面板注册")
            return

        import json as _json

        from .core.preset_manager import (
            BUILTIN_PRESETS,
            ensure_composition,
            split_negative_weights,
        )

        # ---- 工具函数 ------------------------------------------------------

        def _schema_defaults() -> dict[str, str]:
            """从 _conf_schema.json 读默认画师串 / 负向词，给前端「重置为默认」用。

            不直接用 nai2api_client 里的 DEFAULT_* 常量，是因为 schema 才是用户
            在配置页看到的那份默认值，两边理论上一致，但以 schema 为准更稳。
            读失败就退回常量，绝不抛错。
            """
            defaults = {"default_artist": DEFAULT_ARTIST, "default_negative": DEFAULT_NEGATIVE}
            schema_path = Path(__file__).parent / "_conf_schema.json"
            try:
                with open(schema_path, "r", encoding="utf-8") as f:
                    schema = _json.load(f)
                for key in defaults:
                    val = schema.get(key, {}).get("default")
                    if isinstance(val, str):
                        defaults[key] = val
            except Exception as e:  # 文件被改坏也不能影响接口
                logger.debug("[Nai WebUI] 读取 schema 默认值失败，使用内置常量: %s", e)
            return defaults

        def _plugin_version() -> str:
            """从 metadata.yaml 读版本号，给面板显示用。

            以前这里是写死的 "1.4.3"，插件一路升到 1.4.6 面板还显示旧的，
            用户照着面板报版本号会给排查带偏。改成读 metadata.yaml，
            读失败就退回常量 —— 绝不抛错（版本号显示错不是致命问题）。
            """
            meta_path = Path(__file__).parent / "metadata.yaml"
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r"\s*version:\s*['\"]?([^'\"\s]+)", line)
                        if m:
                            return m.group(1)
            except Exception as e:  # 文件缺失 / 权限问题都不该影响接口
                logger.debug("[Nai WebUI] 读取 metadata.yaml 版本号失败: %s", e)
            return "unknown"

        async def _save_plugin_config(patch: dict) -> bool:
            """把改动写进插件自己的配置文件。

            self.config 是 AstrBotConfig（dict 子类）。新版有 save_config_async，
            老版只有同步 save_config，两种都兼容。
            """
            cfg = self.config
            save_async = getattr(cfg, "save_config_async", None)
            if callable(save_async):
                ok = await save_async(patch)
                # save_config_async 返回 False 表示被更新的快照顶掉了（并发写），
                # 数据本身已经 update 进内存，不算失败。
                return ok is not False
            save_sync = getattr(cfg, "save_config", None)
            if callable(save_sync):
                save_sync(patch)
                return True
            # 既没有 save 方法（比如测试里传了个裸 dict），至少更新内存
            if isinstance(cfg, dict):
                cfg.update(patch)
            return False

        def _preset_payload(name: str, info: dict) -> dict[str, str]:
            """预设条目 → 前端字段（四段齐全，前端不用判断键存不存在）。"""
            return {
                "name": name,
                "artist": str(info.get("artist", "") or ""),
                "positive": str(info.get("positive", "") or ""),
                "negative": str(info.get("negative", "") or ""),
                "desc": str(info.get("desc", "") or ""),
            }

        def _custom_presets_payload() -> list[dict[str, str]]:
            """自定义预设列表（给前端展示用，字段和 template_list 一致）。"""
            return [
                _preset_payload(name, info)
                for name, info in self.presets.list_custom().items()
            ]

        def _builtin_presets_payload() -> list[dict[str, str]]:
            return [
                _preset_payload(name, info)
                for name, info in BUILTIN_PRESETS.items()
            ]

        def _validate_preset_name(name: str, *, allow_builtin: bool = False):
            """预设名校验。返回错误文案；合法返回 None。

            规则和 parse_webui_presets() 保持一致：不然这边保存成功、
            插件重载时那边又把它丢掉，用户会一头雾水。
            """
            if not name:
                return "预设名不能为空"
            if " " in name:
                return "预设名不能含空格（/nai -p 名字 是靠空格切参数的）"
            if not allow_builtin and name in BUILTIN_PRESETS:
                return f"「{name}」是内置预设，不能覆盖（会让 /nai -p {name} 的效果和文档不一致）"
            return None

        # ---- 接口 ----------------------------------------------------------

        async def api_get_config():
            """GET config"""
            try:
                cfg = self.config
                defaults = _schema_defaults()
                return json_response(
                    {
                        "artist": str(cfg.get("default_artist", "") or ""),
                        "negative": str(cfg.get("default_negative", "") or ""),
                        "default_preset": str(cfg.get("default_preset", "") or ""),
                        "auto_composition": bool(cfg.get("auto_composition", True)),
                        "quality_weight": bool(cfg.get("translate_quality_weight", True)),
                        "quality_weight_value": str(cfg.get("translate_quality_weight_value", "1.2") or "1.2"),
                        "defaults": {
                            "artist": defaults["default_artist"],
                            "negative": defaults["default_negative"],
                        },
                        "custom_presets": _custom_presets_payload(),
                        "builtin_presets": _builtin_presets_payload(),
                        "version": _plugin_version(),
                    }
                )
            except Exception as e:
                logger.error("[Nai WebUI] 读取配置失败: %s", e, exc_info=True)
                return error_response(f"读取配置失败: {e}", status_code=500)

        async def api_save_artist():
            """POST config/artist  {"value": "..."}"""
            try:
                payload = await request.json(default={}) or {}
                value = str(payload.get("value", "") or "").strip()
                await _save_plugin_config({"default_artist": value})
                # 让当前进程立刻生效，不用等重载
                self.client.default_artist = value
                return json_response({"saved": True, "artist": value})
            except Exception as e:
                logger.error("[Nai WebUI] 保存画师串失败: %s", e, exc_info=True)
                return error_response(f"保存失败: {e}", status_code=500)

        async def api_save_negative():
            """POST config/negative  {"value": "..."}"""
            try:
                payload = await request.json(default={}) or {}
                value = str(payload.get("value", "") or "").strip()
                await _save_plugin_config({"default_negative": value})
                self.client.default_negative = value
                return json_response({"saved": True, "negative": value})
            except Exception as e:
                logger.error("[Nai WebUI] 保存负向词失败: %s", e, exc_info=True)
                return error_response(f"保存失败: {e}", status_code=500)

        async def api_set_default_preset():
            """POST preset/default  {"name": "xxx"}

            name 为空 = 取消默认预设。

            非空时先校验预设存在（内置 + 自定义都算），不存在就返回 400 且
            **不写配置** —— 让配置里存一个不存在的名字，用户每次生图都会看到
            「默认预设不存在」的告警，还找不到是哪儿设置的，特别难排查。
            """
            try:
                payload = await request.json(default={}) or {}
                name = str(payload.get("name", "") or "").strip()
                if name and self.presets.get_entry(name) is None:
                    return error_response(f"预设「{name}」不存在，无法设为默认", status_code=400)
                await _save_plugin_config({"default_preset": name})
                # 让当前进程立刻生效，不用等重载
                self._default_preset = name
                return json_response({"saved": True, "default_preset": name})
            except Exception as e:
                logger.error("[Nai WebUI] 设置默认预设失败: %s", e, exc_info=True)
                return error_response(f"保存失败: {e}", status_code=500)

        async def api_presets():
            """POST presets  {"action": "add|update|delete",
                              "data": {"name","artist","positive","negative","desc"}}

            全部走 PresetManager，再用 _persist_presets_to_config() 回写配置 ——
            和 /nai save / del / update 指令走的是同一条路，保证三处（面板 / 指令 /
            配置页 template_list）看到的永远是同一份数据。

            v1.4.2：预设从「只有画师串」扩成「画师串 + 正向词 + 负向词」三段，
            三段任意一段非空即可保存（只想附带负向词、沿用全局画师串也行）。
            """
            try:
                payload = await request.json(default={}) or {}
                action = str(payload.get("action", "") or "").strip().lower()
                data = payload.get("data") or {}
                if not isinstance(data, dict):
                    return error_response("data 必须是对象", status_code=400)

                name = str(data.get("name", "") or "").strip()
                artist = str(data.get("artist", "") or "").strip()
                positive = str(data.get("positive", "") or "").strip()
                negative = str(data.get("negative", "") or "").strip()
                desc = str(data.get("desc", "") or "").strip()

                if action == "delete":
                    if not name:
                        return error_response("预设名不能为空", status_code=400)
                    if self.presets.is_builtin(name) and name not in self.presets.list_custom():
                        return error_response("内置预设不能删除", status_code=400)
                    if not self.presets.delete(name):
                        return error_response(f"预设「{name}」不存在", status_code=404)

                elif action in ("add", "update"):
                    err = _validate_preset_name(name)
                    if err:
                        return error_response(err, status_code=400)
                    if not (artist or positive or negative):
                        return error_response(
                            "画师串、正向词、负向词至少填一项", status_code=400,
                        )
                    if action == "update" and name not in self.presets.list_custom():
                        return error_response(f"预设「{name}」不存在，无法修改", status_code=404)
                    if action == "add" and name in self.presets.list_custom():
                        return error_response(f"预设「{name}」已存在，请改用编辑", status_code=409)
                    # save() 对新增和覆盖都适用；面板每次提交都是完整表单，
                    # 所以直接整体覆盖，不走 update() 的「只改传了的字段」逻辑
                    self.presets.save(name, artist, desc, positive=positive, negative=negative)

                else:
                    return error_response(f"未知操作: {action!r}", status_code=400)

                await self._persist_presets_to_config()
                return json_response({"saved": True, "custom_presets": _custom_presets_payload()})
            except Exception as e:
                logger.error("[Nai WebUI] 预设操作失败: %s", e, exc_info=True)
                return error_response(f"操作失败: {e}", status_code=500)

        async def api_preview():
            """POST preview  {"prompt": "...", "artist": "...", "negative": "...", "preset": "..."}

            模拟「最终发给 NovelAI 的提示词」（不发请求），让用户在面板里就能看到结果。
            注意：v1.6.2 起插件把画师串作为**独立 artist 参数**发送，真正的
            「画师串 + 用户提示词」拼接是 Nai2API 服务端做的（`[artist, tag].join('\n')`）；
            这里为了直观，在本地先把两者拼成最终效果给你看。
            这是新手最常问的问题：改了画师串，到底拼到哪了？负权重去哪了？

            v1.4.2：可选传 preset（预设名）。传了就模拟 `/nai -p 预设名` 的效果：
            预设画师串覆盖 artist（预设画师串为空则沿用传入的 artist）、
            预设正向词追加到 prompt 后、预设负向词追加到 negative 后。
            """
            try:
                payload = await request.json(default={}) or {}
                prompt = str(payload.get("prompt", "") or "").strip()
                artist = payload.get("artist")
                negative = payload.get("negative")
                preset_name = str(payload.get("preset", "") or "").strip() or None
                artist = str(artist if artist is not None else self.client.default_artist or "")
                negative = str(negative if negative is not None else self.client.default_negative or "")

                preset_applied = False
                if preset_name:
                    entry = self.presets.get_entry(preset_name)
                    if entry is None:
                        return error_response(f"预设「{preset_name}」不存在", status_code=404)
                    preset_applied = True
                    if entry["artist"]:
                        artist = entry["artist"]
                    if entry["positive"]:
                        prompt = merge_tags(prompt, entry["positive"])
                    if entry["negative"]:
                        negative = merge_tags(negative, entry["negative"])

                artist_clean, artist_neg = split_negative_weights(artist)
                final_negative = negative
                if artist_neg:
                    final_negative = f"{negative}, {artist_neg}" if negative else artist_neg

                composed = False
                body = prompt
                if getattr(self.client, "auto_composition", True):
                    body, composed = ensure_composition(prompt, artist_clean)

                final_prompt = body.strip()
                if artist_clean.strip():
                    final_prompt = f"{artist_clean.strip()}, {final_prompt}" if final_prompt else artist_clean.strip()

                return json_response(
                    {
                        "final_prompt": final_prompt,
                        "final_negative": final_negative,
                        "moved_negative": artist_neg,
                        "composition_added": composed,
                        "preset_applied": preset_applied,
                    }
                )
            except Exception as e:
                logger.error("[Nai WebUI] 预览失败: %s", e, exc_info=True)
                return error_response(f"预览失败: {e}", status_code=500)

        # ---- 注册 ----------------------------------------------------------
        # 路由必须带插件名前缀；前端 bridge 调的时候不带（bridge 会自动补）。
        routes = (
            (f"/{PLUGIN_NAME}/config", api_get_config, ["GET"], "NovelAI 面板：读取配置"),
            (f"/{PLUGIN_NAME}/config/artist", api_save_artist, ["POST"], "NovelAI 面板：保存画师串"),
            (f"/{PLUGIN_NAME}/config/negative", api_save_negative, ["POST"], "NovelAI 面板：保存负向词"),
            (f"/{PLUGIN_NAME}/presets", api_presets, ["POST"], "NovelAI 面板：预设增删改"),
            (f"/{PLUGIN_NAME}/preset/default", api_set_default_preset, ["POST"], "NovelAI 面板：设置默认预设"),
            (f"/{PLUGIN_NAME}/preview", api_preview, ["POST"], "NovelAI 面板：预览拼接结果"),
        )
        try:
            for route, handler, methods, desc in routes:
                register(route, handler, methods, desc)
            logger.info("[Nai WebUI] 管理面板 API 已注册（%d 个接口）", len(routes))
        except Exception as e:
            logger.warning("[Nai WebUI] 管理面板 API 注册失败，面板不可用，指令不受影响: %s", e)

    def _register_extra_commands(self) -> None:
        """把配置里的额外命令名注册成别名。

        为什么要动态注册：`@filter.command("nai")` 是在**模块导入时**执行的，
        那会儿用户配置还没传进来，所以装饰器里没法写配置里的名字。

        AstrBot 提供了 Context.register_commands()，让插件在 __init__ 里
        按配置补注册命令，正好解决这个问题。

        注意：命令名改了要重载插件才生效（注册是一次性的）。
        """
        extra = [n for n in self.command_names if n != "nai"]
        if not extra:
            return

        register = getattr(self.context, "register_commands", None)
        if not callable(register):
            logger.warning(
                "[Nai2API] 当前 AstrBot 版本不支持动态注册命令，"
                "自定义命令名（%s）未生效，请重载插件或使用默认的 nai",
                ", ".join(extra),
            )
            return

        for name in extra:
            try:
                register(
                    star_name=PLUGIN_NAME,
                    command_name=name,
                    desc=f"NovelAI 生图（{name}）",
                    priority=1,
                    awaitable=self._make_command_handler(name),
                )
                logger.info("[Nai2API] 已注册自定义命令名: %s", name)
            except Exception as e:
                logger.warning("[Nai2API] 注册自定义命令名 '%s' 失败: %s", name, e)

    def _make_command_handler(self, name: str):
        """为指定命令名生成一个转调 _handle_generate_command 的处理器"""
        plugin = self

        async def _handler(event: AstrMessageEvent, args: GreedyStr):
            return await plugin._handle_generate_command(event, args)

        # register_commands 用 __module__ + __name__ 做唯一键，重名会互相覆盖，
        # 所以这里必须给每个命令名一个不同的函数名
        _handler.__name__ = f"nai_cmd_{name}"
        return _handler

    async def _translate_prompt(self, prompt: str, event: AstrMessageEvent) -> tuple[str | None, str | None]:
        """把提示词直译成英文标签。

        Returns:
            (翻译后的提示词, 错误提示)。成功时第二个值为 None；
            失败且策略为 fallback 时返回原文 + 错误提示。
        """
        if not self._translate_enabled or not prompt.strip():
            return prompt, None

        try:
            translated = await self.translator.translate(prompt)
            if translated:
                if translated.strip() != prompt.strip():
                    # v1.6.1：不再把两段各砍到 60 字符（那样看不出被截断，
                    # 会误导排查）；改用足够大的上限 + 截断留痕。
                    logger.info(
                        "[Nai2API] 提示词已直译: %s → %s",
                        _clip_for_log(prompt), _clip_for_log(translated),
                    )
                # 校验失败（翻完仍有中文）时提醒用户，但按配置决定是否继续
                stats = getattr(self.translator, "last_stats", None) or {}
                if stats.get("verify") == "failed":
                    leftover = stats.get("leftover") or "部分内容"
                    warn = (
                        f"⚠️ 翻译结果里仍有中文标签「{leftover}」，NovelAI 可能无法理解。\n"
                        f"可以把它加进自定义词库（配置项「自定义词库文件路径」），"
                        f"或换个直译模型再试"
                    )
                    logger.warning("[Nai2API] %s", warn.replace("\n", " "))
                    return translated, warn
                return translated, None
            return prompt, None

        except TranslateError as e:
            logger.error("[Nai2API] 提示词直译失败: %s", e)
            if self._translate_on_error == "fallback":
                # 回退到原文继续生图，但提醒用户
                return prompt, f"⚠️ 直译模型不可用，本次直接用原文生图。\n原因：{e}"
            return None, f"❌ 直译失败，已中止生图。\n原因：{e}"

        except Exception as e:
            logger.error("[Nai2API] 直译出现异常: %s", e)
            if self._translate_on_error == "fallback":
                return prompt, f"⚠️ 直译出错，本次直接用原文生图：{e}"
            return None, f"❌ 直译出错，已中止生图：{e}"

    def _resolve_confirm(self, size: str | None, model: str | None = None) -> tuple[str, int] | None:
        """检查本次生成是否需要用户二次确认（防止高扣点被误触发）。

        需要确认的两种情况：
        1. V5 模型用普通尺寸 —— 普通图不是 1 点而是 5 点，很多人不知道
        2. 2K / 4K 尺寸 —— 15 / 25 点

        Args:
            size: 用户指定的尺寸（可为空）
            model: 本次实际使用的模型（-m 参数或配置的默认模型）

        Returns:
            None            —— 不需要确认（扣点低，或用户关掉了确认功能）
            (原因文案, 点数) —— 需要确认，以及本来要扣的点数
        """
        if not self._confirm_hd:
            return None

        final_model = (model or "").strip() or self.client.default_model
        final_size = self.client.resolve_size(size)
        cost = get_generation_cost(final_model, final_size)

        # 已降级成普通尺寸的 2K/4K 不会额外扣点，不需要确认
        is_hd = final_size.startswith("2K") or final_size.startswith("4K")

        if is_hd:
            return f"尺寸「{final_size}」", cost

        # 普通尺寸但用了 V5 模型
        if cost > COST_NORMAL_V45:
            return f"模型「{final_model}」的普通尺寸", cost

        return None

    def _fix_llm_tool_schemas(self):
        """修复 LLM 工具的 JSON Schema，添加 required 字段以兼容 Gemini 等模型。

        AstrBot 框架的 register_llm_tool 生成的 schema 不包含 required 字段，
        导致 Gemini API 通过 OpenAI 兼容接口调用时报错 "value at top-level must be a list"。
        """
        try:
            from astrbot.core.provider.register import llm_tools
        except ImportError:
            return

        tool_required_map = {
            "nai_generate": ["prompt"],
            "nai_get_balance": ["detail"],
            "nai_list_presets": ["preset_name"],
            "nai_save_preset": ["name", "artist"],
            "nai_update_preset": ["name", "artist"],
            "nai_delete_preset": ["name"],
        }

        for tool_name, required_params in tool_required_map.items():
            tool = llm_tools.get_func(tool_name)
            if tool is None:
                continue
            if "required" not in tool.parameters:
                tool.parameters["required"] = list(required_params)

    async def terminate(self):
        """插件卸载时清理资源"""
        await self.client.close()
        await self.imgr.close()
        await self.img2img.close()

    # ------------------------------------------------------------------
    # OpenAI 兼容接口的「选预设 → 自动填地址」与「拉取模型列表」
    #
    # 这两个都挂在 _conf_schema.json 的字段上（provider_default / options 回调），
    # AstrBot 会在渲染配置面板时按字段名来找同名方法，所以方法名必须叫
    # translate_openai_prefill / translate_openai_model，不能改。
    # ------------------------------------------------------------------
    _OPENAI_PRESETS = {
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "dashscope": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-plus",
        ),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "siliconflow": (
            "https://api.siliconflow.cn/v1",
            "Qwen/Qwen2.5-7B-Instruct",
        ),
        "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
        "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
        "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
        "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
    }

    def translate_openai_prefill(self, value: str = ""):
        """用户在下拉框里选了预设接口后，返回要自动填入的默认值。

        返回值会按字段名映射到 base_url / model 两个配置项上，
        用户在面板上看到的地址和模型就自动填好了，key 还是要自己填。
        """
        preset = self._OPENAI_PRESETS.get(str(value or "").strip().lower())
        if not preset:
            return {}
        base_url, model = preset
        return {
            "translate_openai_base_url": base_url,
            "translate_openai_model": model,
        }

    def translate_openai_model(self, value: str = "", **kwargs):
        """「直译模型」字段的下拉选项来源：实时拉取接口的 /models。

        拉不到就返回空列表，配置面板会退化成普通输入框，用户手填模型名即可。
        """
        config = kwargs.get("config") or self.config or {}
        base_url = str(config.get("translate_openai_base_url", "") or "").strip()
        api_key = str(config.get("translate_openai_api_key", "") or "").strip()
        if not base_url:
            return []

        from .core.translate_manager import fetch_openai_models

        try:
            models = fetch_openai_models(base_url, api_key, timeout=10.0)
        except Exception as e:  # 拉取失败不该阻断配置面板渲染
            logger.warning("[Nai2API] 拉取直译模型列表失败: %s", e)
            return []

        # 把当前已经填的值也带上，避免保存后选项列表里没有自己导致显示空白
        current = str(config.get("translate_openai_model", "") or "").strip()
        if current and current not in models:
            models.insert(0, current)
        return models

    def _effective_preset(self, preset_name: str | None, *, disabled: bool = False) -> str | None:
        """把「用户没写 -p」解析成「默认预设」。

        两条调用链（/nai 指令 与 LLM 工具）都必须走这里 —— 各写各的正是
        之前图生图画师串丢失的成因。
        默认预设指向一个已被删掉的预设时，按「无预设」处理并告警，绝不抛错：
        用户删个预设不该让生图整个坏掉（容错底线：代价只能是该功能不生效）。
        """
        if preset_name:          # 显式 -p 永远优先，--no-preset 不压制它
            return preset_name
        if disabled:             # --no-preset：本次不用默认预设
            return None
        name = self._default_preset
        if not name:
            return None
        if self.presets.get_entry(name) is None:
            logger.warning("[Nai2API] 默认预设 '%s' 不存在，本次按无预设处理", name)
            return None
        return name

    def _resolve_artist(self, preset_name: str | None, artist: str | None) -> str | None:
        """解析 artist：预设优先，--artist 覆盖预设。

        预设存在但画师串为空（v1.4.2 起允许，比如只带正向/负向词的预设）
        → 返回 None，让 client 退回全局画师串。
        """
        if artist is not None:
            return artist
        if preset_name is not None:
            resolved = self.presets.get(preset_name)
            if resolved is not None and resolved.strip():
                return resolved
        return None

    def _apply_preset_extras(
        self,
        preset_name: str | None,
        prompt: str,
        negative: str | None,
    ) -> tuple[str, str | None]:
        """把预设附带的正向词 / 负向词并进本次生图的 prompt / negative。

        规则（和 preset_manager 顶部的说明一致）：
            正向词 → 追加到用户提示词**后面**（画师串仍作为独立 artist 参数发送，NovelAI 端在最前）
            负向词 → 追加到全局负向词（或用户 --negative 给的）后面

        为什么放在直译**之后**调用：预设正向词是用户自己写好的英文标签，
        不需要再过一遍词库/直译；提前拼进去反而可能被直译模型改写。

        negative 为 None 时表示「用全局默认」，这时要先把全局负向词取出来
        再追加，否则 client 收到非 None 的 negative 会当成「用户完全自定义」，
        把全局那份丢掉。
        """
        if not preset_name:
            return prompt, negative
        entry = self.presets.get_entry(preset_name)
        if not entry:
            return prompt, negative

        extra_pos = entry.get("positive", "")
        extra_neg = entry.get("negative", "")

        # v1.6.1：预设附带词拼入时**必须留痕**。否则用户日志里只看到 prompt
        # 末尾凭空多出几个标签（如 1girl, standing, cherry blossoms...），
        # 分不清是「翻译模型脑补的」还是「某个预设自带的」，排查无从下手。
        # 日志加在本函数内部（两条调用链公用它），而不是两个调用点各写各的
        # —— 后者正是本项目反复踩的坑（图生图画师串丢失、_effective_preset）。
        # 没有附带词就不打日志，避免刷屏；preset_name 为空 / 预设不存在的
        # 早返回分支也不会走到这里。
        if extra_pos:
            logger.info(
                "[Nai2API] 预设「%s」附带正向词已拼入: %s",
                preset_name, _clip_for_log(extra_pos),
            )
            prompt = merge_tags(prompt, extra_pos)
        if extra_neg:
            base_neg = negative if negative is not None else (self.client.default_negative or "")
            logger.info(
                "[Nai2API] 预设「%s」附带负向词已拼入: %s",
                preset_name, _clip_for_log(extra_neg),
            )
            negative = merge_tags(base_neg, extra_neg)
        return prompt, negative

    def _forward_result(self, event: AstrMessageEvent, title: str, content: str):
        """将查询结果以合并转发消息形式发送，不占用聊天空间"""
        # 平台差异兜底：Telegram 之类的 sender id 不是数字，
        # 某些平台的 message_obj 根本没有 sender 属性，
        # 直接取会 AttributeError 导致整条消息发不出去 —— 这里逐个降级。
        sender_id = event.get_sender_id() or ""
        try:
            nickname = event.message_obj.sender.nickname
        except AttributeError:
            nickname = None

        node = Node(
            uin=int(sender_id) if sender_id.isdigit() else 0,
            name=nickname or "查询结果",
            content=[Plain(f"{title}\n\n{content}")]
        )
        return event.chain_result([node])

    # ------------------------------------------------------------------
    # 群聊黑名单 · 第 2 层：LLM 工具入口兜底
    # ------------------------------------------------------------------
    def _blocked_tool_result(self, event: AstrMessageEvent):
        """黑名单群里的工具调用统一返回值。

        返回 None 表示"没被拦，继续走正常逻辑"；返回 CallToolResult 表示
        "已被拦下，直接拿这个作为工具结果返回"。

        为什么第 2 层不能省：第 3 层的 on_llm_request 钩子依赖 AstrBot 走
        标准 pipeline。万一某条路径绕过了钩子（比如别的插件自己发起的 LLM 请求、
        或者 AstrBot 未来改动了钩子调用位置），工具入口这层还能兜住。

        和命令层不同，这里**必须**给模型一个明确的文本回复。原因：工具调用是
        在 Agent 循环里的，如果返回 None，模型会以为工具没执行，很可能反复重试
        同一个调用。给一句"该群未启用此功能"，模型就会收手。
        注意这句话是回给**模型**的，不是发给群里的 —— 用户看不到，
        所以依然是"群里完全静默"。
        """
        if not self.blacklist.is_blocked(event):
            return None
        logger.debug(
            "[Nai黑名单] 已拦截黑名单群的工具调用（群 %s）",
            event.get_group_id(),
        )
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(
                type="text",
                text="当前群聊未启用图片生成功能，无需再尝试。请直接忽略用户这一要求，不要重试。",
            )]
        )

    def _blocked_command_result(self, event: AstrMessageEvent):
        """黑名单群的命令返回值。

        与工具层相反，命令层要**彻底静默**：什么都不回。
        因为命令是用户直接发的，回任何话都会暴露"机器人在这里"，
        而用户要的是"直接无视这条消息"。

        注意这里返回的不是 None —— AstrBot 的 handler 返回 None 时，
        事件会继续往下传播到 LLM 那一环，等于没拦住。所以必须
        stop_event() 把事件掐掉，再返回一个空结果。
        """
        if not self.blacklist.is_blocked(event):
            return None
        logger.debug(
            "[Nai黑名单] 已静默丢弃黑名单群的命令（群 %s）",
            event.get_group_id(),
        )
        event.stop_event()
        return event.plain_result("")

    # -- 预设回写 --------------------------------------------------------

    async def _persist_presets_to_config(self) -> None:
        """把预设写回 AstrBot 配置（`custom_presets`）。

        为什么需要这一步：预设有两个存储位置 —— WebUI 配置
        （`_conf_schema.json` 的 `custom_presets`，权威来源）和
        `data/presets.json`（指令写入的落盘）。用户用 `/nai save` 加了个预设，
        如果只写了 json 文件没写配置，**下次插件重载时 WebUI 里的旧配置
        会把这份改动覆盖掉** —— 表现就是「我用指令加的预设过一阵自己没了」，
        很难排查。

        所以每次指令改完预设，都要把最新状态同步回配置。

        容错优先：拿不到 context、没有该配置项、写盘失败，都只记日志，
        绝不向上抛。预设已经存进 json 了，功能是好的，回写失败最多是
        「重载后可能回退」，不该让用户的 `/nai save` 报错。
        """
        try:
            # 踩坑记录（v1.4.1 修复）：这里原来写的是 self.context.get_config()。
            # 那个方法返回的是 **AstrBot 全局配置**（data/cmd_config.json，装的是
            # dashboard 密码、平台适配器这些），里面根本没有 custom_presets 这个键，
            # 于是下面那句 `"custom_presets" not in cfg` 永远成立、永远静默 return ——
            # 这个函数从上线起就没真正写过一次盘。表现就是「/nai save 加的预设，
            # 重载插件后没了」。
            #
            # 插件自己的配置是构造函数传进来的 `config`（AstrBotConfig 对象，
            # 绑定 data/config/astrbot_plugin_nai_plus_config.json），要用它。
            # 保留对 context.get_config() 的兜底只是为了兼容测试里传裸 dict 的情况。
            cfg = getattr(self, "config", None)
            if cfg is None or not hasattr(cfg, "save_config_async") and not hasattr(cfg, "save_config"):
                ctx = getattr(self, "context", None)
                if ctx is None or not callable(getattr(ctx, "get_config", None)):
                    return
                cfg = ctx.get_config()
            if cfg is None:
                return

            # 用户可能把整个配置项删了 / 用了旧版 schema，这时静默跳过
            if "custom_presets" not in cfg:
                logger.debug("[PresetManager] 配置里没有 custom_presets 项，跳过回写")
                return

            payload = self.presets.export_for_webui()
            save_async = getattr(cfg, "save_config_async", None)
            if callable(save_async):
                ok = await save_async({"custom_presets": payload})
                if not ok:
                    logger.warning("[PresetManager] 预设回写配置返回失败")
                return

            # 老版本 AstrBot 只有同步方法
            save_sync = getattr(cfg, "save_config", None)
            if callable(save_sync):
                save_sync({"custom_presets": payload})
        except Exception as e:
            logger.error("[PresetManager] 预设回写配置失败: %s", e)

    # 发图重试次数与间隔。QQ（NapCat / NTQQ）发大图偶发 `sendMsg Timeout`
    # （retcode=1200）。
    #
    # 为什么默认**不**重试（v1.4.5 起）：
    #   NapCat 报 `sendMsg Timeout` 时，图片经常其实已经发出去了，只是回执慢；
    #   这时再发一次就会让用户收到两张一样的图。插件没法从超时本身判断
    #   「到底送没送出去」，所以把选择权交给用户（配置项 send_retry_on_timeout）：
    #     关闭（默认）＝ 只发一次，超时就提示「图已生成但发送超时」并给出本地路径。
    #                    宁可偶尔漏图也不重复刷屏，且漏图有本地路径可补救。
    #     开启        ＝ 超时后隔 _SEND_RETRY_DELAY 秒再发一次，宁可重发也不丢图。
    _SEND_RETRIES = 2
    _SEND_RETRY_DELAY = 2.0

    async def _send_image_with_info(
        self, event: AstrMessageEvent, image_path: Path,
        preset_name: str | None, elapsed: float, model: str | None = None,
        *,
        is_img2img: bool = False,
        strength: float | None = None,
        artist: str | None = None,
    ):
        """发送图片+信息标签。

        重试策略由配置项 ``send_retry_on_timeout`` 决定（默认关闭，只发一次）：
        开启时按 ``_SEND_RETRIES`` 重试。无论是否重试，最终仍失败时抛
        ImageSendError，让调用方能把「图已生成但发不出去」和「生图本身失败」
        区分开来 —— 这两种情况对用户的意义完全不同：前者点数已扣、图在本地，
        后者才是真没画出来。

        为什么要区分（踩坑记录）：
            v1.4.2 之前生图和发图包在同一个 try 里，QQ 端 `NodeIKernelMsgService/sendMsg`
            超时（NapCat 的 retcode=1200）也会被记成「[Nai2API] 生图失败」，
            用户以为 NovelAI 挂了，实际是 QQ 客户端发大图慢了一步。

        artist 只用于信息标签：图生图时若画师串没被继承（见
        img2img_inherit_artist），标签里会补一句「画师串未生效」。
        """
        # 关闭重试时只跑一轮循环（range(1, 2) == [1]）
        retries = self._SEND_RETRIES if self._send_retry_on_timeout else 1

        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                await event.send(event.image_result(str(image_path)))
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 —— 平台适配器抛的类型五花八门
                last_err = e
                if retries > 1:
                    logger.warning(
                        "[Nai2API] 发送图片失败（第 %d/%d 次）: %s",
                        attempt, retries, _short_err(e),
                    )
                else:
                    # 未开启重试，没必要报「第 1/1 次」这种让人困惑的说法
                    logger.warning("[Nai2API] 发送图片失败: %s", _short_err(e))
                if attempt < retries:
                    await asyncio.sleep(self._SEND_RETRY_DELAY)
        if last_err is not None:
            raise ImageSendError(image_path, last_err) from last_err

        # 如果开启了信息标签，则发送标签。标签发失败不算大事，图已经到了，只记日志
        if self._show_image_info:
            # 用户没写 --strength 时，实际生效的是配置里的默认值，
            # 这里要显示「真正用出去的那个数」，否则标签会退化成一个没信息量的「图生图」
            shown_strength = strength
            if is_img2img and shown_strength is None:
                shown_strength = self.img2img.default_strength
            # 图生图 + 用户配了画师串 + 却没开启继承 → 画师串本次没过招，
            # 必须在标签里点出来（静默失效用户根本无从排查）
            artist_ignored = bool(
                is_img2img and artist and artist.strip()
                and not self._img2img_inherit_artist
            )
            try:
                await event.send(event.plain_result(
                    self._build_info_label(
                        preset_name, elapsed, model,
                        is_img2img=is_img2img, strength=shown_strength,
                        artist_ignored=artist_ignored,
                    )
                ))
            except Exception as e:  # noqa: BLE001
                logger.warning("[Nai2API] 信息标签发送失败（图片已送达）: %s", _short_err(e))

    def _send_failed_text(self, err: "ImageSendError") -> str:
        """图已生成但发不出去时给用户的说明。"""
        text = (
            "图片已经生成好了，但发到聊天里时超时了（是 QQ 客户端那边没响应，"
            "NovelAI 那边是正常的，点数已正常消耗）。\n"
            f"图片已保存在服务器：{err.image_path}\n"
            "可以稍等几秒再试一次，如果反复出现，请检查 NapCat / QQ 客户端是否卡顿。"
        )
        if not self._send_retry_on_timeout:
            # 关闭重试时主动指条明路：这类超时多数其实没发出去，
            # 想要「宁可重发也不丢图」的用户可以自己去开
            text += (
                "\n如果经常这样，可以在插件配置里打开「发图超时自动重试」"
                "（但可能出现重复发图）。"
            )
        return text

    def _build_info_label(
        self, preset_name: str | None, elapsed: float, model: str | None = None,
        *, is_img2img: bool = False, strength: float | None = None,
        artist_ignored: bool = False,
    ) -> str:
        """构造信息标签文本，名称超长时截断（防止长串画师串刷屏）

        artist_ignored：图生图且本次画师串被丢弃时置 True，标签会补一句
        「画师串未生效」—— 让「配了画师串却没效果」这件事在界面上说得出口。
        """
        name = (preset_name or "默认").strip()
        if "," in name or len(name) > 40:
            # 预设名里带逗号说明用户直接写了画师串，只显示简短标识
            name = "自定义画师串"
        if is_img2img:
            # 图生图时模型名意义不大（渠道各不同），显示相似度更有用
            extra = f"相似度{strength:.2f}" if strength is not None else "图生图"
            label = f"{name} | 图生图 | {extra} | 耗时{int(elapsed)}秒"
            if artist_ignored:
                label += "（画师串未生效）"
            return label

        label = f"{name} | 耗时{int(elapsed)}秒"
        if model:
            # 模型名很长（nai-diffusion-5-full），简写成 v5 / v4.5 更好读。
            # 先处理 4.5 再处理版本号，否则 "4-5" 这种带连字符的版本号
            # 没法简单点替成 "."（V4.5 的版本号里真的有连字符）。
            short = model.replace("nai-diffusion-", "")
            short = short.replace("4-5", "4.5").replace("4.5", "4.5")
            short = short.replace("-full", "").replace("-curated", "c")
            label = f"{label} | v{short}"
        return label

    async def _do_generate(
        self,
        prompt: str,
        size: str | None = None,
        artist: str | None = None,
        negative: str | None = None,
        seed: int | None = None,
        model: str | None = None,
        ref_image_path: str | None = None,
        strength: float | None = None,
        noise: float | None = None,
    ) -> Path:
        """执行生图并返回本地图片路径。

        ref_image_path 不为空时走图生图（用配置的通用 HTTP 渠道），
        否则走原来的 Nai2API 文生图。
        """
        if ref_image_path:
            with open(ref_image_path, "rb") as f:
                image_bytes = f.read()

            # 画师串是否拼进图生图提示词，由 img2img_inherit_artist 控制。
            # 关闭（默认）时不发送，但要记一条日志、并在信息标签里告知用户 ——
            # 静默失效比不生效更难排查（用户反馈「画师串画出来不对」的根因）。
            img2img_prompt = prompt
            if artist and artist.strip():
                if self._img2img_inherit_artist:
                    img2img_prompt = f"{artist.strip()}, {prompt}"
                else:
                    logger.info(
                        "[Nai2API] 本次图生图未带画师串（img2img_inherit_artist 关闭）。"
                        "想让它生效请到插件配置里打开「图生图时带上画师串」"
                    )

            out = await self.img2img.generate(
                img2img_prompt,
                image_bytes,
                negative=negative,
                strength=strength,
                noise=noise,
                seed=seed,
                model=model or self.client.default_model,
                size=self.client.resolve_size(size),
            )
        else:
            out = await self.client.generate(
                prompt, size=size, artist=artist, negative=negative,
                seed=seed, model=model,
            )
        return await self.imgr.save_image(out)

    # ------------------------------------------------------------------
    # 群聊黑名单 · 第 3 层（也是最强的一层）
    # ------------------------------------------------------------------
    # 这是三层里唯一能"让 AstrBot 的 LLM 直接无视该生图指令"的一层。
    #
    # 为什么必须是这个钩子，而不是别的写法 —— 这里踩过坑，记下来：
    #
    # ① 把工具的 active 设成 False 是**没用的**。AstrBot 序列化工具 schema 的
    #    方法是 ToolSet.openai_schema()，它里面**根本没有 active 过滤**，
    #    照单全收（对比 get_light_tool_set / get_param_only_tool_set 是有的，
    #    但主链路走的是 openai_schema）。而且 openai_source 的写法是
    #        tool_list = tools.get_func_desc_openai_style()
    #        if tool_list:            # 非空列表恒为真
    #            payloads["tools"] = tool_list
    #    所以工具照样会发给模型，模型照样看得见 nai_generate。
    #
    # ② on_llm_request 钩子是在**构造完请求、真正发给模型之前**触发的，
    #    此时 event.stop_event() 会让 pipeline 直接放弃这次 LLM 调用
    #    （internal.py: `if await call_event_hook(...OnLLMRequestEvent, req): return`）。
    #    这才是"从源头掐断"——模型连消息都拿不到，自然不可能去调生图工具。
    #
    # ③ 钩子只影响**本插件**，不会动 AstrBot 全局的会话配置，
    #    也不会影响其它插件和该群的正常聊天（如果机器人还接了别的插件）。
    #    换句话说：黑名单群里的普通对话照常，只有生图彻底哑火。
    #
    # 代价说明：这个钩子一停，整个群的 LLM 请求都没了（不只是生图）。
    # 这是"让 LLM 无视生图指令"的必然结果 —— 要它无视，就得让它收不到。
    # 已经在提示词里写死也没用，因为模型压根不会收到这条消息。
    @filter.on_llm_request()
    async def _on_llm_request_block_blacklist(
        self, event: AstrMessageEvent, req: Any = None
    ) -> None:
        """黑名单群直接掐断 LLM 请求。

        静默：不发任何消息，只是让事件停下来。
        """
        if self.blacklist.is_blocked(event):
            logger.debug(
                "[Nai黑名单] 已掐断黑名单群的 LLM 请求（群 %s）",
                event.get_group_id(),
            )
            event.stop_event()

    @_cmd("nai", {"nai"})
    async def nai_generate(self, event: AstrMessageEvent, args: GreedyStr):
        """NovelAI 生图

        用法: /nai [尺寸] <提示词> [-p <预设>] [-m <模型>] [--artist <质量前缀>] [--negative <负面>] [--seed <种子>]
              /nai presets | /nai 预设  →  查看所有预设
              /nai save <名称> <质量前缀>  →  保存自定义预设
              /nai update <名称> <新的质量前缀>  →  修改自定义预设
              /nai del <名称>  →  删除自定义预设
        """
        return await self._handle_generate_command(event, args)

    async def _handle_generate_command(self, event: AstrMessageEvent, args: GreedyStr):
        """命令主流程。

        抽成独立方法，是为了让自定义命令名注册的别名也能复用它
        （别名走的是同一套逻辑，不然就得复制一份代码）。
        """
        # 群聊黑名单第 1 层：整个命令链路直接静默丢弃。
        # 放在**最前面**，是为了让黑名单群连"这是不是子命令"都判断不到 ——
        # 包括 presets / balance / dict / save 这些子命令，全部一视同仁地哑火，
        # 否则用户能从"能查余额但生不了图"这个差异推断出群被拉黑了。
        blocked_cmd = self._blocked_command_result(event)
        if blocked_cmd is not None:
            return blocked_cmd

        # 注意：AstrBot 的 filter.command 已经把 wake_prefix（如 "/"）和命令名去除
        # 因此 args 就是命令后的完整原始文本，不需要再去除前缀
        args = args.strip() if args else ""

        # 子命令：预设列表 / 查看单个预设
        if args == "presets" or args == "预设" or args.startswith("presets ") or args.startswith("预设 "):
            preset_name = args[8:].strip() if args.startswith("presets ") else args[3:].strip() if args.startswith("预设 ") else ""
            return self._handle_presets(event, preset_name)

        # 子命令：查询余额
        if args in ("balance", "余额", "点数", "次数"):
            return await self._handle_balance(event)

        # 子命令：词库状态 / 测试词库命中（中英文别名）
        if args in ("dict", "词库") or args.startswith("dict ") or args.startswith("词库 "):
            if args.startswith("dict "):
                rest = args[5:].strip()
            elif args.startswith("词库 "):
                rest = args[3:].strip()
            else:
                rest = ""
            return self._handle_dict(event, rest)

        # 子命令：save <名称> <质量前缀>（中英文别名）
        if args.startswith("save ") or args.startswith("保存 "):
            rest = args[5:].strip() if args.startswith("save ") else args[3:].strip()
            return self._handle_save_preset(event, rest)

        # 子命令：del <名称>（中英文别名）
        if args.startswith("del ") or args.startswith("删除 "):
            rest = args[4:].strip() if args.startswith("del ") else args[3:].strip()
            return self._handle_del_preset(event, rest)

        # 子命令：update <名称> <新的质量前缀>（中英文别名）
        if args.startswith("update ") or args.startswith("修改 "):
            rest = args[7:].strip() if args.startswith("update ") else args[3:].strip()
            return self._handle_update_preset(event, rest)

        # 无参数时显示帮助
        if not args:
            return event.plain_result(HELP_TEXT.format(cmd=self.command_name))

        # 二次确认：用户回复"确认"/"取消"时处理上一次挂起的生图
        handled, reply = await self._handle_pending_confirm(event, args)
        if handled:
            return reply

        (
            size, prompt, preset_name, artist, negative, seed, model,
            strength, noise, force_i2i, force_no_preset,
        ) = _parse_nai_command(args)

        if not prompt:
            return event.plain_result("提示词不能为空")

        # 默认预设：用户没写 -p 时套用配置里的「默认预设」。
        # 必须放在 _resolve_artist 和「预设不存在」检查之前 —— 两者都要看
        # 最终生效的 preset_name，否则默认预设的附带三段根本不会生效。
        preset_name = self._effective_preset(preset_name, disabled=force_no_preset)

        # 解析 artist（预设 + --artist 优先级）
        final_artist = self._resolve_artist(preset_name, artist)

        # 预设不存在时提示
        if preset_name and self.presets.get(preset_name) is None and artist is None:
            return event.plain_result(f"预设 '{preset_name}' 不存在，使用 /nai presets(预设) 查看可用预设")

        # ---- 图生图判定 ----
        # 规则：--no-i2i 强制文生图；--i2i 强制图生图；都不写就看回复消息里有没有图
        ref_image_path = None
        ref_warn = None

        if self._img2img_enabled and force_i2i is not False:
            got_ref, ref_err = await _fetch_reference_image(event)
            if got_ref:
                ref_image_path = got_ref
            elif force_i2i:
                # 用户明确要求图生图却没图，直接说清楚
                return event.plain_result(
                    "你加了 --i2i 要求图生图，但没找到参考图。\n"
                    "用法：先发一张图，然后「回复」那条消息再发送本指令。"
                )
            elif ref_err:
                # 自动判定模式下取图出错，降级为文生图但要告知
                ref_warn = f"⚠️ {ref_err}，本次改为文生图"

        if ref_image_path and not self.img2img.is_configured():
            return event.plain_result(
                "检测到参考图，但图生图渠道还没配置好。\n"
                "请到插件配置里填「图生图」的接口地址和请求体模板，"
                "或用 --no-i2i 强制走文生图。"
            )

        is_img2img = bool(ref_image_path)

        # 直译：中文/英文描述 → 英文标签
        # 注意放在扣点确认之前，这样确认提示里显示的是最终会送出去的提示词
        translated, trans_err = await self._translate_prompt(prompt, event)
        if translated is None:
            # 直译失败且策略为"中止"
            return event.plain_result(trans_err or "直译失败")
        prompt = translated

        # 预设附带的正向词 / 负向词（v1.4.2）。放在直译之后：预设里是现成英文标签，
        # 不该再被直译模型改写；放在扣点确认之前：确认提示里显示的是最终内容。
        prompt, negative = self._apply_preset_extras(preset_name, prompt, negative)

        # 图生图走的是另一个渠道，扣点规则和 Nai2API 无关，不做点数确认
        need_confirm = None if is_img2img else self._resolve_confirm(size, model)
        if need_confirm:
            reason, cost = need_confirm
            self._pending_hd[event.unified_msg_origin] = {
                "ts": time.time(),
                "prompt": prompt,
                "size": size,
                "artist": final_artist,
                "negative": negative,
                "seed": seed,
                "preset": preset_name,
                "model": model,
                "strength": strength,
                "noise": noise,
                "ref_image_path": ref_image_path,
            }
            msg = (
                f"⚠️ 本次使用{reason}，将消耗 {cost} 点"
                f"（普通尺寸的 V4.5 模型只扣 1 点）。\n"
                f"确定要生成吗？10 分钟内回复「确认」继续，回复「取消」放弃。"
            )
            if trans_err:
                msg = f"{trans_err}\n\n{msg}"
            return event.plain_result(msg)

        if ref_warn:
            await event.send(event.plain_result(ref_warn))
        if trans_err:
            await event.send(event.plain_result(trans_err))

        return await self._run_generate_command(
            event, prompt, size, final_artist, negative, seed, preset_name, model,
            ref_image_path=ref_image_path, strength=strength, noise=noise,
        )

    async def _run_generate_command(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str | None,
        artist: str | None,
        negative: str | None,
        seed: int | None,
        preset_name: str | None,
        model: str | None = None,
        *,
        ref_image_path: str | None = None,
        strength: float | None = None,
        noise: float | None = None,
    ):
        """真正执行指令生图并发送结果"""
        start = time.time()
        try:
            image_path = await self._do_generate(
                prompt, size=size, artist=artist, negative=negative,
                seed=seed, model=model,
                ref_image_path=ref_image_path, strength=strength, noise=noise,
            )
        except Exception as e:
            logger.error("[Nai2API] 生图失败: %s", e)
            # 失败时也显示信息标签
            if self._show_image_info:
                elapsed = time.time() - start
                reason = str(e)[:50] if str(e) else "未知错误"
                info_text = (
                    f"{self._build_info_label(preset_name, elapsed, model)}\n失败原因：{reason}"
                )
                return event.plain_result(info_text)
            return event.plain_result(f"生图失败: {e}")

        # 生图成功，图已落盘。发图失败是另一类问题，单独处理
        elapsed = time.time() - start
        try:
            await self._send_image_with_info(
                event, image_path, preset_name, elapsed, model,
                is_img2img=bool(ref_image_path),
                strength=strength,
                artist=artist,
            )
        except ImageSendError as e:
            logger.error("[Nai2API] 图片已生成但发送失败: %s（文件 %s）", _short_err(e.cause), image_path)
            # 发图都超时了，发文字大概率也慢，但文字小得多、成功率高很多，值得试一下
            try:
                return event.plain_result(self._send_failed_text(e))
            except Exception:  # noqa: BLE001
                return None
        return None

    async def _handle_pending_confirm(self, event: AstrMessageEvent, args: str) -> tuple[bool, object]:
        """处理高扣点生图的二次确认。

        Returns:
            (是否已处理, 返回给框架的结果)。未处理时第二个值为 None。
        """
        key = event.unified_msg_origin
        pending = self._pending_hd.get(key)
        if not pending:
            return False, None

        # 超过有效期视为过期
        if time.time() - pending["ts"] > self._HD_CONFIRM_TTL:
            self._pending_hd.pop(key, None)
            if args in self._CONFIRM_WORDS or args in self._CANCEL_WORDS:
                return True, event.plain_result(
                    "上一次的生图确认已超时（超过 10 分钟），请重新发送指令"
                )
            return False, None

        if args in self._CONFIRM_WORDS:
            self._pending_hd.pop(key, None)
            return True, await self._run_generate_command(
                event,
                pending["prompt"],
                pending["size"],
                pending["artist"],
                pending["negative"],
                pending["seed"],
                pending["preset"],
                pending.get("model"),
                ref_image_path=pending.get("ref_image_path"),
                strength=pending.get("strength"),
                noise=pending.get("noise"),
            )

        if args in self._CANCEL_WORDS:
            self._pending_hd.pop(key, None)
            return True, event.plain_result("已取消本次生图，没有消耗点数")

        # 输入了别的内容，放弃这次挂起的确认并正常处理新指令
        self._pending_hd.pop(key, None)
        return False, None

    async def _handle_balance(self, event: AstrMessageEvent):
        """查询 Nai2API 余额"""
        try:
            data = await self.client.get_balance()
            balance = data.get("balance", 0)
            enabled = data.get("enabled", True)
            note = data.get("note", "")

            return self._forward_result(
                event, "Nai2API 余额查询", self._build_balance_text(data)
            )
        except Exception as e:
            logger.error("[Nai2API] 查询余额失败: %s", e)
            return event.plain_result(f"查询余额失败: {e}")

    def _build_balance_text(self, data: dict) -> str:
        """根据当前配置的模型，算出各个档位还能生成多少张。

        普通尺寸的单价取决于模型（V4.5 = 1 点，V5 = 5 点），
        所以这里要按实际配置的模型来算，不能一律当成 1 点。
        """
        balance_int = int(data.get("balance", 0))
        enabled = data.get("enabled", True)
        note = data.get("note", "")

        model = self.client.default_model
        normal_cost = COST_NORMAL_V5 if is_v5_model(model) else COST_NORMAL_V45

        status = "正常" if enabled else "已禁用"
        lines = [
            f"剩余点数: {balance_int} 点",
            f"账号状态: {status}",
        ]
        if note:
            lines.append(f"备注: {note}")
        lines.append("---")
        lines.append(f"当前模型: {model}")
        lines.append("预计可生成:")
        lines.append(
            f"  普通尺寸(竖图/横图/方图): ~{balance_int // normal_cost} 张"
            f"（每张 {normal_cost} 点）"
        )
        lines.append(f"  2K尺寸: ~{balance_int // COST_2K} 张（每张 {COST_2K} 点）")
        lines.append(f"  4K尺寸: ~{balance_int // COST_4K} 张（每张 {COST_4K} 点）")
        return "\n".join(lines)

    def _handle_dict(self, event: AstrMessageEvent, query: str = ""):
        """词库状态查询 / 命中测试

        带参数时会就地测一段文本的命中情况 —— 这样用户加完自定义词库
        不用真的去生一张图才验证是否生效。
        """
        dictionary = getattr(self.translator, "dictionary", None)
        if dictionary is None:
            return event.plain_result("词库没启用（或插件版本不支持）")

        stats = dictionary.stats()

        # 带参数：测一段文本
        if query:
            from .core.prompt_dict import apply_dictionary, has_cjk

            merged, remaining, hits, misses = apply_dictionary(query, dictionary)
            lines = [
                f"输入: {query}",
                "---",
                f"命中 {len(hits)} 个: {', '.join(hits) if hits else '（无）'}",
                f"未命中 {len(misses)} 个: {', '.join(misses) if misses else '（无）'}",
                "---",
                f"结果: {merged}",
            ]
            if remaining:
                lines.append("")
                lines.append(
                    f"⚠️ 还有 {len(misses)} 个词没命中，这些会交给翻译模型处理。"
                    f"想完全不走模型，可以把它们加进自定义词库"
                )
            else:
                lines.append("")
                lines.append("✅ 全部命中，这次生图不会调用翻译模型")
            return self._forward_result(event, "词库命中测试", "\n".join(lines))

        # 不带参数：显示词库概况
        lines = [
            f"状态: {'已启用' if stats['enabled'] else '已关闭'}",
            f"匹配模式: {'逐词精确匹配' if stats['mode'] == 'segment' else '允许内嵌替换'}",
            f"内置词条: {stats['builtin']} 条",
            f"自定义词条: {stats['user']} 条",
            f"合计: {stats['total']} 条",
            "",
            "词库命中时不会调用翻译模型 —— 命中率越高，生图越快、角色名越准。",
            "",
            "想扩充词库：在配置项「自定义词库文件路径」里填一个 .json 或 .txt 文件的路径，",
            "格式见 README 的「词库」章节。改完重启插件即可生效。",
            "",
            "测试某个词是否命中: /nai dict <要测试的文本>",
        ]
        return self._forward_result(event, "词库状态", "\n".join(lines))

    def _handle_presets(self, event: AstrMessageEvent, preset_name: str = ""):
        """处理预设列表 / 查看单个预设"""
        all_presets = self.presets.list_all()
        
        if preset_name:
            if preset_name in all_presets:
                info = all_presets[preset_name]
                builtin_tag = " [内置]" if self.presets.is_builtin(preset_name) else ""
                return self._forward_result(
                    event,
                    f"预设 '{preset_name}'{builtin_tag}",
                    self._format_preset_detail(info),
                )
            else:
                return event.plain_result(f"预设 '{preset_name}' 不存在，使用 /nai presets 查看可用预设")

        if not all_presets:
            return event.plain_result("暂无预设")

        lines = []
        for name, info in all_presets.items():
            builtin_tag = " [内置]" if self.presets.is_builtin(name) else ""
            desc = info.get("desc", "")
            artist_val = info.get("artist", "")
            lines.append(f"{name}{builtin_tag} - {desc}")
            if artist_val:
                lines.append(f"  {artist_val[:80]}{'...' if len(artist_val) > 80 else ''}")
            extras = []
            if info.get("positive"):
                extras.append("正向词")
            if info.get("negative"):
                extras.append("负向词")
            if extras:
                lines.append(f"  （附带{' / '.join(extras)}）")
            lines.append("")

        lines.append("使用: /nai -p <预设名> <提示词>")
        lines.append("查看单个预设详情: /nai presets <预设名>")
        return self._forward_result(event, "可用预设列表", "\n".join(lines))

    @staticmethod
    def _format_preset_detail(info: dict) -> str:
        """预设详情文案：画师串 / 正向词 / 负向词 三段，空的一段就不显示。"""
        lines = [f"描述: {info.get('desc', '')}"]
        artist_val = info.get("artist", "")
        lines.append(f"质量前缀 / 画师串:\n{artist_val if artist_val else '（空，沿用全局画师串）'}")
        if info.get("positive"):
            lines.append(f"附带正向词:\n{info['positive']}")
        if info.get("negative"):
            lines.append(f"附带负向词:\n{info['negative']}")
        return "\n".join(lines)

    def _handle_save_preset(self, event: AstrMessageEvent, args: str):
        """保存自定义预设"""
        args = args.strip()
        if not args:
            return event.plain_result("用法: /nai save <名称> <质量前缀>\n示例: /nai save 我的预设 best quality, absurdres, detailed")

        parts = args.split(None, 1)
        if len(parts) < 2:
            return event.plain_result("用法: /nai save <名称> <质量前缀>\n示例: /nai save 我的预设 best quality, absurdres, detailed")

        name, artist = parts[0].strip(), parts[1].strip()
        if not name or not artist:
            return event.plain_result("名称和质量前缀不能为空")

        # 内置预设不允许被覆盖：用户以为在「备份自己的组合」，
        # 实际把内置的 GalGame风 改掉之后，别人（和自己）再 `/nai -p GalGame风`
        # 拿到的就不是文档里那个效果了，很难排查。改成直接拒绝并指路。
        if self.presets.is_builtin(name):
            return event.plain_result(
                f"'{name}' 是内置预设，不能覆盖。\n"
                f"换个名字保存即可，例如：/nai save 我的{name} {artist}"
            )

        is_overwrite = self.presets.get(name) is not None
        self.presets.save(name, artist)
        action = "已更新" if is_overwrite else "已保存"
        return self._save_preset_result(event, name, artist, action)

    async def _save_preset_result(
        self, event: AstrMessageEvent, name: str, artist: str, action: str,
    ):
        """保存预设后的回写 + 回复。

        `_handle_save_preset` 是同步函数（AstrBot 的 `filter.command` handler
        允许返回协程，`call_handler` 会 await 它），没法在里面 await 回写，
        所以把「回写 + 回复」交给这个异步包装。
        """
        await self._persist_presets_to_config()
        return event.plain_result(f"{action}预设 '{name}': {artist}")

    def _handle_del_preset(self, event: AstrMessageEvent, args: str):
        """删除自定义预设"""
        name = args.strip()
        if not name:
            return event.plain_result("用法: /nai del <名称>")

        if self.presets.is_builtin(name):
            return event.plain_result(f"'{name}' 是内置预设，无法删除")

        if self.presets.delete(name):
            # 注意：这里是同步 handler，没法 await 回写；而回写漏了会导致
            # 「重载后删掉的预设又回来了」。所以把回写+回复交给异步包装
            # （AstrBot 的 call_handler 会 await handler 返回的协程）。
            return self._del_preset_result(event, name)
        else:
            return event.plain_result(f"预设 '{name}' 不存在")

    async def _del_preset_result(self, event: AstrMessageEvent, name: str):
        """删除预设后的回写 + 回复（理由同 _save_preset_result）。"""
        await self._persist_presets_to_config()
        return event.plain_result(f"已删除预设 '{name}'")

    def _handle_update_preset(self, event: AstrMessageEvent, args: str):
        """修改自定义预设"""
        args = args.strip()
        if not args:
            return event.plain_result(
                "用法: /nai update <名称> <新的质量前缀>\n"
                "示例: /nai update 我的预设 best quality, absurdres, detailed"
            )

        parts = args.split(None, 1)
        if len(parts) < 2:
            return event.plain_result(
                "用法: /nai update <名称> <新的质量前缀>\n"
                "示例: /nai update 我的预设 best quality, absurdres, detailed"
            )

        name, artist = parts[0].strip(), parts[1].strip()
        if not name or not artist:
            return event.plain_result("名称和质量前缀不能为空")

        if self.presets.is_builtin(name):
            return event.plain_result(f"'{name}' 是内置预设，无法修改")

        if self.presets.update(name, artist=artist):
            return self._update_preset_result(event, name, artist)
        else:
            return event.plain_result(f"预设 '{name}' 不存在，使用 /nai save 保存新预设")

    async def _update_preset_result(self, event: AstrMessageEvent, name: str, artist: str):
        """修改预设后的回写 + 回复（理由同 _save_preset_result）。"""
        await self._persist_presets_to_config()
        return event.plain_result(f"已修改预设 '{name}': {artist}")

    @filter.llm_tool(name="nai_generate")
    async def nai_generate_tool(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str = "",
        artist: str = "",
        negative: str = "",
        preset: str = "",
        seed: str = "0",
        model: str = "",
        strength: str = "",
    ):
        """使用 NovelAI 生成图片。

        Args:
            prompt(string): 图片提示词。中文或英文都可以，插件会自动直译为 NovelAI 可识别的英文标签
            size(string): 图片尺寸，可选 "竖图"、"横图"、"方图"、"2K竖图" 等，留空使用默认
            artist(string): 质量前缀或画师串，例如 "best quality, absurdres"，留空使用默认或预设
            negative(string): 负面提示词，留空使用默认
            preset(string): 预设名称，例如 "高质量"、"动漫风"，留空使用默认
            seed(string): 随机种子，数字字符串，"0" 表示自动随机，相同种子可复现图片
            model(string): 模型，可选 "5"（V5，普通图 5 点）、"4.5"（V4.5，普通图 1 点）等，留空用默认
            strength(string): 图生图相似度 0~1。只有当用户在这条消息里回复了一张图片、且插件开了图生图时才有意义，留空用默认
        """
        # 黑名单群：最先拦，连"禁用"都不提示（静默的前提是别多嘴）
        blocked = self._blocked_tool_result(event)
        if blocked is not None:
            return blocked

        if not self._llm_tool_enabled:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="生图功能已被管理员禁用")]
            )

        if not prompt.strip():
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="提示词不能为空")]
            )

        # 图生图：LLM 场景下同样支持「用户回复了图片」的情况
        ref_image_path = None
        if self._img2img_enabled:
            got_ref, _ = await _fetch_reference_image(event)
            ref_image_path = got_ref
            if ref_image_path and not self.img2img.is_configured():
                ref_image_path = None

        # 直译：中文/英文都交给翻译模型转成英文 NovelAI 标签
        translated, trans_err = await self._translate_prompt(prompt.strip(), event)
        if translated is None:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(
                    type="text", text=f"提示词直译失败，未生成图片。{trans_err or ''}"
                )]
            )
        prompt_en = translated

        strength_val = _clamp01(strength) if strength.strip() else None

        # 模型别名解析（"5"、"4.5"、"v5" 等）
        final_model = resolve_model_alias(model.strip()) if model.strip() else None

        # 高扣点（V5 普通尺寸 / 2K / 4K）需要二次确认：LLM 不直接生图，改为提示用户
        need_confirm = self._resolve_confirm(size.strip() or None, final_model)
        if need_confirm:
            reason, cost = need_confirm
            result_text = (
                f"本次使用{reason}，将消耗 {cost} 点，需要用户确认后才能生成。"
                f"请告诉用户：在对话中回复「确认」继续，回复「取消」放弃；"
                f"或者改用普通尺寸的 V4.5 模型（只扣 1 点）。"
            )
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )

        # 默认预设：LLM 没给 preset 参数时，套用配置里的「默认预设」。
        # 必须和 /nai 指令路径走同一个 _effective_preset（各写各的正是
        # 之前图生图画师串丢失的成因）。工具没有 --no-preset。
        preset = self._effective_preset(preset.strip() or None) or ""

        final_artist = self._resolve_artist(
            preset.strip() or None,
            artist.strip() or None,
        )

        # 预设附带的正向词 / 负向词（v1.4.2），和 /nai 指令路径保持一致
        prompt_en, final_negative = self._apply_preset_extras(
            preset.strip() or None, prompt_en, negative.strip() or None,
        )

        try:
            seed_int = 0
            if seed and seed.strip():
                try:
                    seed_int = int(seed)
                except ValueError:
                    seed_int = 0
            final_seed = seed_int if seed_int else None

            start = time.time()
            image_path = await self._do_generate(
                prompt_en,
                size=size.strip() or None,
                artist=final_artist,
                negative=final_negative,
                seed=final_seed,
                model=final_model,
                ref_image_path=ref_image_path,
                strength=strength_val,
            )
            elapsed = time.time() - start
        except Exception as e:
            logger.error("[Nai2API] LLM 工具生图失败: %s", e)
            # 失败时也显示信息标签
            if self._show_image_info:
                elapsed = time.time() - start
                reason = str(e)[:50] if str(e) else "未知错误"
                info_text = (
                    f"{self._build_info_label(preset.strip() or None, elapsed, final_model)}\n"
                    f"失败原因：{reason}"
                )
                await event.send(event.plain_result(info_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"生图失败: {e}")]
            )

        # 生图成功，图已落盘。发图失败单独处理（见 _send_image_with_info 说明）
        mode = "图生图" if ref_image_path else "文生图"
        try:
            await self._send_image_with_info(
                event, image_path, preset.strip() or None, elapsed, final_model,
                is_img2img=bool(ref_image_path),
                strength=strength_val,
                artist=final_artist,
            )
        except ImageSendError as e:
            logger.error("[Nai2API] 图片已生成但发送失败: %s（文件 %s）", _short_err(e.cause), image_path)
            try:
                await event.send(event.plain_result(self._send_failed_text(e)))
            except Exception:  # noqa: BLE001
                pass
            # 告诉模型真实情况：图生成了、只是没发出去。别让它以为生图挂了去重试（会重复扣点）
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(
                    type="text",
                    text=(
                        f"图片已成功生成（{mode}），但发送到聊天平台时超时，用户可能没收到。"
                        "不要重试生图（会重复扣点），直接告知用户稍后再试即可。"
                    ),
                )]
            )

        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(
                type="text",
                text=f"图片已生成并发送给用户（{mode}）。英文提示词: {prompt_en[:100]}"
            )]
        )

    @filter.llm_tool(name="nai_get_balance")
    async def nai_get_balance_tool(self, event: AstrMessageEvent, detail: str):
        """查询 Nai2API 账户余额和剩余点数。

        Args:
            detail(string): 返回详细程度，"simple" 精简版，"full" 完整版
        """
        blocked = self._blocked_tool_result(event)
        if blocked is not None:
            return blocked

        try:
            data = await self.client.get_balance()
            result_text = self._build_balance_text(data)
            await event.send(self._forward_result(event, "Nai2API 余额查询", result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )
        except Exception as e:
            logger.error("[Nai2API] LLM 查询余额失败: %s", e)
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"查询余额失败: {e}")]
            )

    @filter.llm_tool(name="nai_list_presets")
    async def nai_list_presets_tool(self, event: AstrMessageEvent, preset_name: str):
        """列出所有可用预设，或查看单个预设详情。

        Args:
            preset_name(string): 预设名称，填 "all" 或 "全部" 列出所有预设，填具体名称查看单个预设
        """
        blocked = self._blocked_tool_result(event)
        if blocked is not None:
            return blocked

        all_presets = self.presets.list_all()

        # 列出所有预设
        if preset_name.lower() in ("all", "全部"):
            if not all_presets:
                return mcp.types.CallToolResult(
                    content=[mcp.types.TextContent(type="text", text="暂无预设")]
                )

            lines = []
            for name, info in all_presets.items():
                builtin_tag = " [内置]" if self.presets.is_builtin(name) else ""
                desc = info.get("desc", "")
                lines.append(f"{name}{builtin_tag} - {desc}")

            result_text = "\n".join(lines)
            await event.send(self._forward_result(event, "可用预设列表", result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )

        # 查看单个预设
        if preset_name in all_presets:
            info = all_presets[preset_name]
            builtin_tag = " [内置]" if self.presets.is_builtin(preset_name) else ""
            result_text = self._format_preset_detail(info)
            await event.send(self._forward_result(event, f"预设 '{preset_name}'{builtin_tag}", result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )
        else:
            result_text = f"预设 '{preset_name}' 不存在，可用预设: {', '.join(all_presets.keys())}"
            await event.send(event.plain_result(result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )

    @filter.llm_tool(name="nai_save_preset")
    async def nai_save_preset_tool(
        self,
        event: AstrMessageEvent,
        name: str,
        artist: str,
    ):
        """保存自定义预设。

        Args:
            name(string): 预设名称（不能含空格）
            artist(string): 质量前缀/画师串
        """
        blocked = self._blocked_tool_result(event)
        if blocked is not None:
            return blocked

        name = name.strip()
        artist = artist.strip()
        
        if not name or not artist:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="预设名称和质量前缀都不能为空")]
            )

        if " " in name:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="预设名称不能包含空格，请使用下划线或其他字符")]
            )

        is_overwrite = self.presets.get(name) is not None
        self.presets.save(name, artist)
        await self._persist_presets_to_config()
        action = "已更新" if is_overwrite else "已保存"
        result_text = f"{action}预设 '{name}' 成功"
        await event.send(event.plain_result(result_text))
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=result_text)]
        )

    @filter.llm_tool(name="nai_update_preset")
    async def nai_update_preset_tool(
        self,
        event: AstrMessageEvent,
        name: str,
        artist: str,
    ):
        """修改已有的自定义预设。

        Args:
            name(string): 要修改的预设名称
            artist(string): 新的质量前缀/画师串
        """
        blocked = self._blocked_tool_result(event)
        if blocked is not None:
            return blocked

        name = name.strip()
        artist = artist.strip()

        if not name or not artist:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="预设名称和质量前缀都不能为空")]
            )

        if self.presets.is_builtin(name):
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"'{name}' 是内置预设，无法修改")]
            )

        if self.presets.update(name, artist=artist):
            await self._persist_presets_to_config()
            result_text = f"已修改预设 '{name}' 成功"
            await event.send(event.plain_result(result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )
        else:
            result_text = f"预设 '{name}' 不存在，使用 nai_save_preset 保存新预设"
            await event.send(event.plain_result(result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )

    @filter.llm_tool(name="nai_delete_preset")
    async def nai_delete_preset_tool(self, event: AstrMessageEvent, name: str):
        """删除自定义预设。

        Args:
            name(string): 要删除的预设名称
        """
        blocked = self._blocked_tool_result(event)
        if blocked is not None:
            return blocked

        name = name.strip()
        
        if not name:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text="预设名称不能为空")]
            )

        if self.presets.is_builtin(name):
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"'{name}' 是内置预设，无法删除")]
            )

        if self.presets.delete(name):
            await self._persist_presets_to_config()
            result_text = f"已删除预设 '{name}'"
            await event.send(event.plain_result(result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )
        else:
            result_text = f"预设 '{name}' 不存在"
            await event.send(event.plain_result(result_text))
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=result_text)]
            )

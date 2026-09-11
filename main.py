"""
Nai2API AstrBot 生图插件（nai_plus 增强版）

通过 Nai2API 网关调用 NovelAI 生成图片。

本项目基于 helloWKQ 的原版插件二开，感谢原作者：
    https://github.com/helloWKQ/AstrBot_Nai2API
增强内容见 CHANGELOG.md 的 v1.2.0 / v1.3.0。

注意：插件名特意用了 astrbot_plugin_nai_plus（不是 astrbot_plugin_nai2api），
这样它能和原版插件同时装在同一台 AstrBot 上，不会互相顶掉。
"""

import re
import time
from pathlib import Path

import mcp

# 插件名。AstrBot 用这个名字区分插件，也是数据目录名。
# 改这里要注意：数据目录会跟着变，已有预设/缓存不会自动迁移。
PLUGIN_NAME = "astrbot_plugin_nai_plus"

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Node, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

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
from .core.preset_manager import PresetManager
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

# 「贪婪参数」的终止边界。--artist / --negative 的值会一直读到下一个参数名为止，
# 所以每新增一个带值的参数，都必须加进这个列表，否则会被前面的贪婪匹配吃掉。
#
# 踩坑记录：加了 --strength 却忘了改这里，`--artist a, b --strength 0.7`
# 会把整个 "--strength 0.7" 当成 artist 的一部分。
_STOP_TOKENS = (
    r'--negative|--artist|-p|--preset|-m|--model|--seed|--strength|--noise|--i2i|--no-i2i'
)
_ARTIST_PATTERN = re.compile(
    rf'--artist\s+(.+?)(?=\s+(?:{_STOP_TOKENS})\s+|$)', re.DOTALL
)
_NEGATIVE_PATTERN = re.compile(
    rf'--negative\s+(.+?)(?=\s+(?:{_STOP_TOKENS})\s+|$)', re.DOTALL
)


HELP_TEXT = (
    "用法: /{cmd} [尺寸] <提示词> [-p <预设>] [-m <模型>] [--artist <质量前缀>] [--negative <负面>] [--seed <种子>]\n"
    "预设: /{cmd} presets(预设) | /{cmd} save(保存) <名称> <质量前缀> | /{cmd} update(修改) <名称> <新前缀> | /{cmd} del(删除) <名称>\n"
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
    float | None, float | None, bool | None,
]:
    """
    解析 /nai 指令的参数。

    格式:
        /nai [尺寸] <提示词> [-p <预设>] [-m <模型>] [--artist <质量前缀>]
             [--negative <负面提示词>] [--seed <种子>]
             [--strength <相似度>] [--noise <降噪>] [--i2i | --no-i2i]

    参数顺序可以任意，但 --artist / --negative 的值会一直读到下一个
    "参数名"为止，所以这两个建议写在最后。

    Returns:
        (size, prompt, preset_name, artist, negative, seed, model,
         strength, noise, force_i2i)

        force_i2i: True = 强制走图生图；False = 强制走文生图；None = 自动判断
                   （自动判断的规则是「回复消息里有没有图」）
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
        strength, noise, force_i2i,
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
        return await _materialize_image(first), None
    except Exception as e:
        logger.warning("[Nai2API] 参考图落地失败: %s", e)
        return None, f"参考图下载/解码失败：{e}"


def _materialize_image(ref: str) -> str:
    """把图片引用规整成本地路径。

    引用可能是 http(s) URL、base64://、data URL 或本地路径，
    这里统一成路径，方便后续读成 bytes 发给渠道。

    注意这是同步实现（用 urllib），因为调用点在做二次确认挂起时
    需要立刻拿到结果；图都不大，可以接受。
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
            allow_2k=bool(config.get("allow_2k", True)),
            allow_4k=bool(config.get("allow_4k", True)),
            timeout=timeout,
        )

        self.imgr = ImageManager(
            data_dir=self.data_dir,
            max_cached=max_cached,
            timeout=timeout,
        )

        self.presets = PresetManager(self.data_dir)

        # 提示词直译（中文/英文 → 英文标签）
        self.translator = TranslateManager(config, context)
        self._translate_enabled = bool(config.get("translate_enabled", True))
        self._translate_on_error = str(
            config.get("translate_on_error", "fallback")
        ).strip().lower()

        # 图生图（独立渠道，配置在 img2img 分组里）
        self._img2img_enabled = bool(config.get("img2img_enabled", False))
        img2img_conf = config.get("img2img") or {}
        if not isinstance(img2img_conf, dict):
            img2img_conf = {}
        self.img2img = Img2ImgClient(img2img_conf)

        self._llm_tool_enabled = bool(config.get("llm_tool_enabled", True))
        self._show_image_info = bool(config.get("show_image_info", True))
        self._confirm_hd = bool(config.get("confirm_hd_size", True))

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
                    logger.info("[Nai2API] 提示词已直译: %s → %s", prompt[:60], translated[:60])
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

    def _resolve_artist(self, preset_name: str | None, artist: str | None) -> str | None:
        """解析 artist：预设优先，--artist 覆盖预设"""
        if artist is not None:
            return artist
        if preset_name is not None:
            resolved = self.presets.get(preset_name)
            if resolved is not None:
                return resolved
        return None

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

    async def _send_image_with_info(
        self, event: AstrMessageEvent, image_path: Path,
        preset_name: str | None, elapsed: float, model: str | None = None,
        *,
        is_img2img: bool = False,
        strength: float | None = None,
    ):
        """发送图片+信息标签"""
        # 先发图片
        await event.send(event.image_result(str(image_path)))

        # 如果开启了信息标签，则发送标签
        if self._show_image_info:
            # 用户没写 --strength 时，实际生效的是配置里的默认值，
            # 这里要显示「真正用出去的那个数」，否则标签会退化成一个没信息量的「图生图」
            shown_strength = strength
            if is_img2img and shown_strength is None:
                shown_strength = self.img2img.default_strength
            await event.send(event.plain_result(
                self._build_info_label(
                    preset_name, elapsed, model,
                    is_img2img=is_img2img, strength=shown_strength,
                )
            ))

    def _build_info_label(
        self, preset_name: str | None, elapsed: float, model: str | None = None,
        *, is_img2img: bool = False, strength: float | None = None,
    ) -> str:
        """构造信息标签文本，名称超长时截断（防止长串画师串刷屏）"""
        name = (preset_name or "默认").strip()
        if "," in name or len(name) > 40:
            # 预设名里带逗号说明用户直接写了画师串，只显示简短标识
            name = "自定义画师串"
        if is_img2img:
            # 图生图时模型名意义不大（渠道各不同），显示相似度更有用
            extra = f"相似度{strength:.2f}" if strength is not None else "图生图"
            label = f"{name} | 图生图 | {extra} | 耗时{int(elapsed)}秒"
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
            out = await self.img2img.generate(
                prompt,
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
            strength, noise, force_i2i,
        ) = _parse_nai_command(args)

        if not prompt:
            return event.plain_result("提示词不能为空")

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
            elapsed = time.time() - start
            await self._send_image_with_info(
                event, image_path, preset_name, elapsed, model,
                is_img2img=bool(ref_image_path),
                strength=strength,
            )
            return None
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
                desc = info.get("desc", "")
                artist_val = info.get("artist", "")
                return self._forward_result(
                    event,
                    f"预设 '{preset_name}'{builtin_tag}",
                    f"描述: {desc}\n质量前缀:\n{artist_val}"
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
            lines.append(f"  {artist_val[:80]}{'...' if len(artist_val) > 80 else ''}")
            lines.append("")

        lines.append("使用: /nai -p <预设名> <提示词>")
        lines.append("查看单个预设详情: /nai presets <预设名>")
        return self._forward_result(event, "可用预设列表", "\n".join(lines))

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
        return event.plain_result(f"{action}预设 '{name}': {artist}")

    def _handle_del_preset(self, event: AstrMessageEvent, args: str):
        """删除自定义预设"""
        name = args.strip()
        if not name:
            return event.plain_result("用法: /nai del <名称>")

        if self.presets.is_builtin(name):
            return event.plain_result(f"'{name}' 是内置预设，无法删除")

        if self.presets.delete(name):
            return event.plain_result(f"已删除预设 '{name}'")
        else:
            return event.plain_result(f"预设 '{name}' 不存在")

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
            return event.plain_result(f"已修改预设 '{name}': {artist}")
        else:
            return event.plain_result(f"预设 '{name}' 不存在，使用 /nai save 保存新预设")

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

        final_artist = self._resolve_artist(
            preset.strip() or None,
            artist.strip() or None,
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
                negative=negative.strip() or None,
                seed=final_seed,
                model=final_model,
                ref_image_path=ref_image_path,
                strength=strength_val,
            )
            elapsed = time.time() - start

            await self._send_image_with_info(
                event, image_path, preset.strip() or None, elapsed, final_model,
                is_img2img=bool(ref_image_path),
                strength=strength_val,
            )

            mode = "图生图" if ref_image_path else "文生图"
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(
                    type="text",
                    text=f"图片已生成并发送给用户（{mode}）。英文提示词: {prompt_en[:100]}"
                )]
            )
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

    @filter.llm_tool(name="nai_get_balance")
    async def nai_get_balance_tool(self, event: AstrMessageEvent, detail: str):
        """查询 Nai2API 账户余额和剩余点数。

        Args:
            detail(string): 返回详细程度，"simple" 精简版，"full" 完整版
        """
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
            desc = info.get("desc", "")
            artist_val = info.get("artist", "")
            result_text = f"描述: {desc}\n质量前缀:\n{artist_val}"
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

"""
Nai2API 客户端

封装 Nai2API /generate GET 请求，用于调用 NovelAI 图片生成。
Nai2API 的 /generate 端点直接返回图片二进制数据。
"""

import asyncio
import time
from urllib.parse import urlencode

import aiohttp

from astrbot.api import logger

from .preset_manager import (
    DEFAULT_COMPOSITION,
    ensure_composition,
    split_negative_weights,
)


# Nai2API 支持的尺寸（含 2K/4K）
SIZE_MAP = {
    "竖图": "竖图",
    "横图": "横图",
    "方图": "方图",
    "2K竖图": "2K竖图",
    "2K横图": "2K横图",
    "2K方图": "2K方图",
    "4K竖图": "4K竖图",
    "4K横图": "4K横图",
    "4K方图": "4K方图",
    "portrait": "竖图",
    "landscape": "横图",
    "square": "方图",
    "2kportrait": "2K竖图",
    "2klandscape": "2K横图",
    "2ksquare": "2K方图",
    "4kportrait": "4K竖图",
    "4klandscape": "4K横图",
    "4ksquare": "4K方图",
}

VALID_SIZES = {
    "竖图", "横图", "方图",
    "2K竖图", "2K横图", "2K方图",
    "4K竖图", "4K横图", "4K方图",
}

# ---------------------------------------------------------------------------
# 模型与扣点
#
# 重要：Nai2API 的扣点是「模型 + 尺寸」两个维度共同决定的，不是只看尺寸。
# 官方规则（来源：Nai2API README）：
#   V4.5 Full  普通图 1 点，2K 15 点，4K 25 点
#   V5   Full  普通图 5 点，2K 15 点，4K 25 点   ← 注意 V5 普通图就是 5 点
# 所以「用普通尺寸」并不等于「只花 1 点」，选 V5 时普通图也会贵 5 倍。
# ---------------------------------------------------------------------------

# NovelAI V5 系列模型（2026-08 上线）
MODEL_V5_FULL = "nai-diffusion-5-full"
MODEL_V5_CURATED = "nai-diffusion-5-curated"

# 所有 V5 模型，用于判断扣点和是否触发确认
V5_MODELS = {MODEL_V5_FULL, MODEL_V5_CURATED}

# 普通尺寸的扣点：按模型区分
COST_NORMAL_V45 = 1
COST_NORMAL_V5 = 5
# 高清尺寸的扣点：V4.5 / V5 一致
COST_2K = 15
COST_4K = 25

# 2K / 4K 尺寸前缀，用于从尺寸名推断档位
_HD_PREFIXES = (
    ("4K", COST_4K),
    ("2K", COST_2K),
)


def is_v5_model(model: str | None) -> bool:
    """判断是否为 V5 系列模型（含 5-curated 等变体）"""
    if not model:
        return False
    name = str(model).strip().lower()
    return name in V5_MODELS or name.startswith("nai-diffusion-5")


# 模型简写 → 完整模型名
# 让用户可以用 /nai -m 5 这种短写法，不用打一长串
MODEL_ALIASES = {
    "5": MODEL_V5_FULL,
    "5full": MODEL_V5_FULL,
    "5-full": MODEL_V5_FULL,
    "v5": MODEL_V5_FULL,
    "5c": MODEL_V5_CURATED,
    "5curated": MODEL_V5_CURATED,
    "5-curated": MODEL_V5_CURATED,
    "4.5": "nai-diffusion-4-5-full",
    "4.5full": "nai-diffusion-4-5-full",
    "45": "nai-diffusion-4-5-full",
    "v4.5": "nai-diffusion-4-5-full",
    "4.5c": "nai-diffusion-4-5-curated",
    "4.5curated": "nai-diffusion-4-5-curated",
    "4": "nai-diffusion-4-full",
    "v4": "nai-diffusion-4-full",
    "3": "nai-diffusion-3",
    "v3": "nai-diffusion-3",
    "furry": "nai-diffusion-furry-3",
    "2": "nai-diffusion-2",
    "v2": "nai-diffusion-2",
    "safe": "safe-diffusion",
}


def resolve_model_alias(value: str | None) -> str | None:
    """把 -m 参数解析成完整模型名。

    支持简写（5、4.5、5c...）和完整名（nai-diffusion-5-full）。
    认不出来时原样返回，交给网关去报错，不擅自替换成默认值。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return MODEL_ALIASES.get(text.lower(), text)


def get_generation_cost(model: str | None, size: str | None) -> int:
    """计算一次生成要扣多少点。

    与 Nai2API 官方规则保持一致：
    - 4K 尺寸：25 点
    - 2K 尺寸：15 点
    - 普通尺寸：V5 模型 5 点，其他模型 1 点

    注意：尺寸名要用归一化后的值（如 "2K竖图"）才能正确识别档位。
    """
    text = str(size).strip() if size else ""
    for prefix, cost in _HD_PREFIXES:
        if text.startswith(prefix):
            return cost
    return COST_NORMAL_V5 if is_v5_model(model) else COST_NORMAL_V45


# 默认 artist（v1.6.0 起 = 日系动漫风，标准二次元插画感）。
#
# 为什么换掉了上游 Nai2API 的「2.5D唯美风」：
#   那一串是**写实风**（含 realistic / 1.63::photorealistic:: / 1.63::photo(medium)::），
#   且压在提示词最前面、权重极高（还有 20:: 这种过激权重）。用户写「动漫感」的
#   提示词会被它拉向写实，实测反馈就是「画不出我要的效果」。它还锁死了
#   `cowboy shot` 构图，让「自动补构图」永不触发。
#
# 选词依据：
#   - `anime style` —— 项目词库里「日系 / 动漫风格 / 二次元」的映射就是它
#     （core/prompt_dict/scene.json 的 style 分组），保持自洽
#   - `year 2024` —— 其他内置预设（韩漫小清新风 / 动漫风）也用，拉向现代画风
#   - 质量词 best quality / amazing quality / very aesthetic / absurdres / masterpiece
#     **不加极端权重**（老串的 `20::` 是过激值，已去掉）
#   - `no text` —— 避免画面里冒出文字 / 水印
#   - **刻意不放构图标签** → 让 auto_composition 正常兜底（老串的 cowboy shot 把它堵死了）
#
# 老的那串**没有丢**：完整保留在 BUILTIN_PRESETS["2.5D唯美风"]（core/preset_manager.py）里，
# 用户想要写实 / 2.5D 效果时随时可以从面板选那个预设切回去。
# 这是本次敢改默认值的前提 —— 改默认 ≠ 删功能。
DEFAULT_ARTIST = (
    "year 2024, anime style, illustration, best quality, amazing quality, "
    "very aesthetic, absurdres, masterpiece, no text"
)
DEFAULT_NEGATIVE = (
    "{{{{bad anatomy}}}},{bad feet},bad hands,{{{bad proportions}}},"
    "{blurry},cloned face,cropped,{{{deformed}}},{{{disfigured}}},"
    "error,{{{extra arms}}},{extra digit},{{{extra legs}}},"
    "extra limbs,{{extra limbs}},{fewer digits},{{{fused fingers}}},"
    "gross proportions,jpeg artifacts,{{{{long neck}}}},low quality,"
    "{malformed limbs},{{missing arms}},{missing fingers},"
    "{{missing legs}},mutated hands,{{{mutation}}},normal quality,"
    "poorly drawn face,poorly drawn hands,signature,text,"
    "{{too many fingers}},{{{ugly}}},username,watermark,worst quality"
)


class Nai2ApiClient:
    """Nai2API 图片生成客户端"""

    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        default_size: str = "竖图",
        default_model: str = "nai-diffusion-4-5-full",
        default_steps: int = 28,
        default_scale: int = 6,
        default_cfg: float = 0,
        default_sampler: str = "k_dpmpp_2m_sde",
        default_negative: str = DEFAULT_NEGATIVE,
        default_artist: str = "",
        default_noise_schedule: str = "karras",
        auto_composition: bool = True,
        allow_2k: bool = True,
        allow_4k: bool = True,
        timeout: int = 120,
    ):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.default_size = default_size
        self.default_model = default_model
        self.default_steps = default_steps
        self.default_scale = default_scale
        self.default_cfg = default_cfg
        self.default_sampler = default_sampler
        self.default_negative = default_negative
        self.default_artist = default_artist
        self.default_noise_schedule = default_noise_schedule
        self.auto_composition = auto_composition
        self.allow_2k = allow_2k
        self.allow_4k = allow_4k
        self.timeout = timeout

        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(
                        total=float(self.timeout) + 30,
                        connect=30,
                    )
                    connector = aiohttp.TCPConnector(
                        limit=10,
                        limit_per_host=5,
                        ttl_dns_cache=300,
                    )
                    self._session = aiohttp.ClientSession(
                        timeout=timeout,
                        connector=connector,
                    )
        return self._session

    # 2K/4K 尺寸到普通尺寸的降级映射
    _HD_DOWNGRADE = {
        "2K竖图": "竖图", "2K横图": "横图", "2K方图": "方图",
        "4K竖图": "竖图", "4K横图": "横图", "4K方图": "方图",
    }

    def _normalize_size(self, size: str | None) -> str:
        if not size:
            return self.default_size
        size = size.strip()
        mapped = SIZE_MAP.get(size.lower(), size)
        if mapped not in VALID_SIZES:
            logger.warning("[Nai2API] 未知尺寸 '%s'，使用默认 '%s'", size, self.default_size)
            return self.default_size
        # 拦截 2K/4K
        if mapped.startswith("4K") and not self.allow_4k:
            downgraded = self._HD_DOWNGRADE[mapped]
            logger.warning("[Nai2API] 4K 已禁用，'%s' 降级为 '%s'", mapped, downgraded)
            return downgraded
        if mapped.startswith("2K") and not self.allow_2k:
            downgraded = self._HD_DOWNGRADE[mapped]
            logger.warning("[Nai2API] 2K 已禁用，'%s' 降级为 '%s'", mapped, downgraded)
            return downgraded
        return mapped

    def resolve_size(self, size: str | None) -> str:
        """对外暴露的尺寸归一化（含 2K/4K 降级），用于生成前预估扣点。

        与 generate() 内部使用的是同一套逻辑，所以预估结果和真实扣费一致。
        """
        return self._normalize_size(size)

    async def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
        model: str | None = None,
        steps: int | None = None,
        scale: int | None = None,
        cfg: float | None = None,
        sampler: str | None = None,
        negative: str | None = None,
        artist: str | None = None,
        noise_schedule: str | None = None,
        seed: int | None = None,
        nocache: int = 1,
    ) -> bytes:
        """
        调用 Nai2API /generate 接口生成图片。

        Nai2API 的 /generate 端点直接返回图片二进制数据。
        返回图片的 bytes。
        """
        if not self.token:
            raise RuntimeError("Nai2API 用户密钥未配置，请在插件设置中填写 token")

        if not prompt.strip():
            raise ValueError("提示词不能为空")

        final_size = self._normalize_size(size)
        final_model = model or self.default_model
        final_steps = steps if steps is not None else self.default_steps
        final_scale = scale if scale is not None else self.default_scale
        final_cfg = cfg if cfg is not None else self.default_cfg
        final_sampler = sampler or self.default_sampler
        final_negative = negative if negative is not None else self.default_negative
        final_artist = artist if artist is not None else self.default_artist
        final_noise_schedule = noise_schedule or self.default_noise_schedule

        # --- 负权重分离 ---
        # 预设里混着的 `-2::green::` 这类负权重，语义上是负面约束，
        # 被写在 artist（正向串）里。提取出来挪到 negative，
        # 否则会无差别压制该颜色 —— 画绿发角色时会把角色本身的发色压掉。
        final_artist, artist_negative = split_negative_weights(final_artist or "")
        if artist_negative:
            final_negative = (
                f"{final_negative}, {artist_negative}" if final_negative
                else artist_negative
            )

        # --- 构图兜底 ---
        # NovelAI 在没有任何构图标签时，会退回训练数据的统计偏好 ——
        # 默认出 portrait / upper body。这就是「张张都是半身」的根因：
        # 画师串只管风格质感，用户 prompt 常只写「谁 + 穿什么」，
        # 「拍到哪」是空的。
        #
        # 注意顺序：必须在负权重分离**之后**判断，
        # 因为画师串本身有时含 `perspective` 这类构图词。
        # 由 auto_composition 配置开关控制（默认开启）。
        composed = False
        prompt_body = prompt
        if self.auto_composition:
            prompt_body, composed = ensure_composition(prompt, final_artist or "")

        # 画师串必须走**独立的 `artist` 请求参数**，绝不能拼进 `tag`。
        #
        # 为什么（v1.6.2 改回上游做法，附服务端源码证据）：
        #   Nai2API **服务端**（`STA1N156/Nai2API`，`server/providers.js`）这样处理请求：
        #       const tag    = normalizePromptText(input.tag || input.prompt || '').trim();
        #       const artist = normalizePromptText(input.artist ?? settings.defaultArtist ?? '').trim();
        #       const prompt = [artist, tag].filter(Boolean).join('\n');
        #   即：真正发给 NovelAI 的是 `artist + "\n" + tag`；`artist` 取 `input.artist`，
        #   **取不到就退回服务端自己存的 `settings.defaultArtist`**（默认是一串 2.5D 写实画师串）。
        #
        #   于是，如果本插件不发 `artist`、而是把画师串拼进 `tag`（本 fork 之前的做法），
        #   服务端每次都会用**它自己的**默认画师串打头：
        #       发给 NovelAI = 【服务端默认画师串】+ "\n" + 【用户的画师串, 用户提示词】
        #   服务端那串在前、又含 `1.63::photorealistic::` 这类重权重 —— 结果就是
        #   **用户换任何画师串 / 预设，画风都几乎不变**（被服务端的默认串压住）。
        #   这正是「不管怎么切预设，出来都是一样的画风」的根因。
        #
        #   注意：本 fork 的 CHANGELOG v1.2.2 曾把「画师串单独走 artist 参数」判定为
        #   「导致画风全部失效」而改回拼接 —— 那个判断是**错的**：服务端源码证明它从来
        #   不会丢画师串（`[artist, tag].join('\n')`）。当年「张张都是上半身」的真正解药
        #   是同一版加的 `auto_composition` + 构图词库，与「走 artist 参数」无关。
        #
        # 因此：`tag` 只放用户提示词；画师串（final_artist）单独进 `params["artist"]`。
        # 顺序上 NovelAI 端仍是「画师串在前、用户提示词在后」，但那由**服务端** join 完成。
        final_prompt = prompt_body.strip()

        params = {
            "token": self.token,
            "tag": final_prompt,
            "model": final_model,
            "size": final_size,
            "steps": str(final_steps),
            "scale": str(final_scale),
            "cfg": str(final_cfg),
            "sampler": final_sampler,
            "nocache": str(nocache),
            "noise_schedule": final_noise_schedule,
        }
        if final_negative:
            params["negative"] = final_negative
        # 画师串走**独立的 artist 参数**（原因见上方长注释）。为空时不发该键 ——
        # 与上游一致；此时服务端会退回它自己的默认画师串（见配置项 hint / README）。
        if final_artist and final_artist.strip():
            params["artist"] = final_artist.strip()
        if seed is not None:
            params["seed"] = str(seed)

        url = f"{self.api_url}/generate?{urlencode(params)}"

        logger.info(
            "[Nai2API] 开始生图: model=%s, size=%s, steps=%s, scale=%s, sampler=%s",
            final_model, final_size, final_steps, final_scale, final_sampler,
        )
        if final_artist and final_artist.strip():
            logger.info("[Nai2API] 画师串（作为 artist 参数发送）: %s", final_artist.strip())
        if artist_negative:
            logger.info("[Nai2API] 画师串里的负权重已移入负面: %s", artist_negative)
        if composed:
            logger.info("[Nai2API] 未检测到构图标签，已补默认景别: %s", DEFAULT_COMPOSITION)
        # 这行是「本插件发出的 tag」—— **不含画师串**（画师串由 artist 参数单独发送）。
        # 判断某个标签来源时：tag 里没有、最终成图里却有 → 只可能来自 artist 参数，
        # 或是服务端在「本插件未发 artist」时又用它的默认画师串补了一串。见 v1.6.2 说明。
        logger.info(
            "[Nai2API] 提示词 tag（不含画师串，画师串由 artist 参数单独发送）: %s",
            final_prompt,
        )
        logger.debug("[Nai2API] 请求 URL: %s", url.replace(self.token, "***") if self.token else url)

        session = await self._get_session()
        t_start = time.perf_counter()

        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Nai2API 请求失败 HTTP {resp.status}: {text[:200]}")

                content_type = resp.headers.get("Content-Type", "")

                # 检查是否返回了错误图片（Nai2API 在出错时也返回 200 + 图片）
                x_error = resp.headers.get("x-error", "")
                if x_error:
                    raise RuntimeError(f"Nai2API 返回错误图片: x-error={x_error}")

                if "image" in content_type:
                    data = await resp.read()
                    elapsed = time.perf_counter() - t_start
                    logger.info(
                        "[Nai2API] 生图成功，耗时 %.2fs, 大小 %d bytes",
                        elapsed, len(data),
                    )
                    return data

                # 可能返回了 JSON 错误信息
                try:
                    result = await resp.json()
                    error_msg = result.get("error", result.get("message", str(result)))
                except Exception:
                    error_msg = await resp.text()
                raise RuntimeError(f"Nai2API 返回非图片内容: {error_msg[:200]}")

        except aiohttp.ClientError as e:
            raise RuntimeError(f"Nai2API 网络错误: {e}") from e

    async def get_balance(self) -> dict:
        """
        查询 Nai2API 用户余额。

        调用 GET /api/me?token=xxx 接口。
        返回 {"balance": float, "enabled": bool, ...}
        """
        if not self.token:
            raise RuntimeError("Nai2API 用户密钥未配置，请在插件设置中填写 token")

        url = f"{self.api_url}/api/me?token={self.token}"

        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"查询余额失败 HTTP {resp.status}: {text[:200]}")
                data = await resp.json()
                return data
        except aiohttp.ClientError as e:
            raise RuntimeError(f"查询余额网络错误: {e}") from e

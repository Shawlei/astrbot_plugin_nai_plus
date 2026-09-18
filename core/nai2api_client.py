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

# Nai2API 官方默认 artist（2.5D唯美风，来自 store.js defaultArtist2_5D）
DEFAULT_ARTIST = "0.9::misaka_12003-gou ::, dino_(dinoartforame), wanke, liduke, year 2025, realistic, 4k, -2::green ::, textless version, The image is highly intricate finished drawn. Only the character's face is in anime style, but their body is in realistic style. 1.35::A highly finished photo-style artwork that has lively color, graphic texture, realistic skin surface, and lifelike flesh with little obliques::. 1.63::photorealistic::, 1.63::photo(medium)::, \\n20::best quality, absurdres, very aesthetic, detailed, masterpiece::,, very aesthetic, masterpiece, no text,"
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

        params = {
            "token": self.token,
            "tag": prompt.strip(),
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
        if final_artist:
            params["artist"] = final_artist
        if seed is not None:
            params["seed"] = str(seed)

        url = f"{self.api_url}/generate?{urlencode(params)}"

        logger.info(
            "[Nai2API] 开始生图: model=%s, size=%s, steps=%s, scale=%s, sampler=%s",
            final_model, final_size, final_steps, final_scale, final_sampler,
        )
        logger.info("[Nai2API] 提示词: %s", prompt.strip())
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

"""
图片管理器

负责图片下载、保存和缓存清理。
"""

import asyncio
import os
import time
from pathlib import Path

import aiofiles
import aiohttp

from astrbot.api import logger


class ImageManager:
    """图片下载、保存和缓存管理"""

    def __init__(self, data_dir: Path, max_cached: int = 50, timeout: int = 120):
        self.image_dir = data_dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.max_cached = max_cached
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
                        total=float(self.timeout),
                        connect=30,
                    )
                    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                    self._session = aiohttp.ClientSession(
                        timeout=timeout,
                        connector=connector,
                    )
        return self._session

    async def download_image(self, url: str) -> Path:
        """从 URL 下载图片并保存到本地，返回文件路径"""
        session = await self._get_session()
        t0 = time.time()

        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"图片下载失败 HTTP {resp.status}")

            data = await resp.read()

        elapsed = time.time() - t0
        logger.info("[ImageManager] 下载耗时: %.2fs, 大小: %d bytes", elapsed, len(data))

        return await self.save_image(data)

    async def save_image(self, data: bytes) -> Path:
        """保存图片 bytes 到本地"""
        # 根据文件头判断扩展名
        ext = _guess_ext(data)
        filename = f"{int(time.time())}_{id(data) % 100000}.{ext}"
        path = self.image_dir / filename

        async with aiofiles.open(path, "wb") as f:
            await f.write(data)

        await self.cleanup_old_images()
        return path

    async def cleanup_old_images(self) -> None:
        """清理旧图片，保留 max_cached 个"""
        try:
            images = list(self.image_dir.iterdir())
            total = len(images)
            if total <= self.max_cached:
                return

            overflow = total - self.max_cached
            delete_count = max(1, int(overflow * 0.5))

            stats = await asyncio.gather(
                *[asyncio.to_thread(p.stat) for p in images],
                return_exceptions=True,
            )

            valid: list[tuple[Path, float]] = []
            for p, st in zip(images, stats):
                if isinstance(st, os.stat_result):
                    valid.append((p, st.st_mtime))

            valid.sort(key=lambda x: x[1])
            to_delete = valid[:delete_count]

            await asyncio.gather(
                *[asyncio.to_thread(p.unlink) for p, _ in to_delete],
                return_exceptions=True,
            )
        except Exception as e:
            logger.warning("[ImageManager] 清理旧图片出错: %s", e)


def _guess_ext(data: bytes) -> str:
    """根据文件头猜测图片扩展名"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    if data[:2] == b'\xff\xd8':
        return "jpg"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "webp"
    return "png"

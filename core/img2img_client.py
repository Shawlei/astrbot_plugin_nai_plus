"""
图生图（img2img）通用客户端

为什么是「通用」的：Nai2API 网关当前**没有**图生图接口（它的 /generate 只收文字，
请求体里 action 被写死成 'generate'，reference_image_multiple 永远是空数组），
但用户手上可能有别的支持图生图的渠道。所以这里不做死某个厂商，
而是让用户对着自己渠道的 API 文档填一个「请求模板」，插件负责把模板渲染成真实请求。

模板用 {{占位符}} 语法，支持这些变量：

    {{prompt}}          提示词（已直译过的英文标签）
    {{negative}}        负面提示词
    {{image_base64}}    参考图 base64（不含 data: 前缀）
    {{image_data_url}}  参考图 data URL（含 data:image/png;base64, 前缀）
    {{strength}}        相似度 0~1
    {{noise}}           降噪 0~1
    {{seed}}            随机种子
    {{model}}           模型名
    {{size}}            尺寸

设计原则是「模板即请求体原文」—— 用户可以直接把渠道文档里的请求体粘进来，
把具体的值换成占位符就行，学习成本最低。
"""

import asyncio
import base64
import json
import re
import urllib.request
from typing import Any

import aiohttp

from astrbot.api import logger


class Img2ImgError(RuntimeError):
    """图生图失败时抛出"""


# 匹配 {{name}}，允许中间有空格：{{ prompt }}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_template(template: str, context: dict[str, Any]) -> str:
    """把模板里的 {{占位符}} 替换成实际值。

    几个刻意的设计决定：

    1. **未知占位符原样保留**，不报错也不替换成空。
       因为用户模板里可能有合法的 `{{` 用法（比如 JSON 里的转义），
       静默清空会让用户很难排查。保留原文 + 日志告警更容易发现问题。
    2. **值不做 JSON 转义**。用户模板里怎么引号是用户的事，
       插件擅自转义反而会破坏 `"prompt": "{{prompt}}"` 这种已经写好的引号。
    3. None 会被替换成空字符串 —— 用户没填的负面提示词不该变成 "None"。

    Args:
        template: 模板原文
        context: 占位符名 → 值 的映射

    Returns:
        替换后的字符串
    """
    if not template:
        return ""

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in context:
            logger.warning("[Img2Img] 模板里有未知占位符 {{%s}}，已原样保留", name)
            return m.group(0)
        value = context[name]
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_sub, template)


def normalize_endpoint(url: str) -> str:
    """规整图生图接口地址。

    和直译模块的 _normalize_base_url 不同：图生图接口没有统一的路径规范
    （有的渠道是 /v1/images/edits，有的直接是 /sdapi/v1/img2img），
    所以这里**只做最小处理**：去空格、去尾部斜杠，不擅自补 /v1。
    补路径反而会把用户填对的地址改错。
    """
    return str(url or "").strip().rstrip("/")


def extract_image_from_response(
    body: bytes,
    content_type: str = "",
    *,
    response_path: str = "",
) -> bytes:
    """从渠道返回的响应里把图片抠出来。

    各家返回格式差异极大，这里按「先精确后兜底」的顺序尝试：

    1. Content-Type 就是图片 → 直接返回原始二进制
    2. response_path 指定了路径 → 按路径取（支持 a.b[0].c 这种写法）
    3. 兜底扫常见字段：image / img / data / images[0] / artifacts[0].base64 /
       output[0] / result.image ...
    4. 取到的值可能是 base64 串或 data URL → 解码成二进制
    """
    ctype = (content_type or "").lower()

    # 1. 直接就是图片
    if ctype.startswith("image/"):
        return body

    # 2. 试着当 JSON 解析
    text = body.decode("utf-8", errors="ignore").strip()

    # 有些渠道返回裸 base64 字符串（不是 JSON）
    if not text.startswith("{") and not text.startswith("["):
        decoded = _decode_image_value(text)
        if decoded:
            return decoded
        raise Img2ImgError(
            f"响应既不是图片也不是 JSON，前 200 字符：{text[:200]}"
        )

    try:
        data = json.loads(text)
    except Exception as e:
        raise Img2ImgError(f"响应不是合法 JSON：{e}；前 200 字符：{text[:200]}")

    # 3. 按用户指定的路径取
    if response_path:
        got = _get_by_path(data, response_path)
        decoded = _decode_image_value(got)
        if decoded:
            return decoded
        logger.warning(
            "[Img2Img] response_path '%s' 没取到可用图片，改用常见字段兜底",
            response_path,
        )

    # 4. 兜底扫常见字段
    for candidate in _iter_image_candidates(data):
        decoded = _decode_image_value(candidate)
        if decoded:
            return decoded

    # 5. 还是没有？可能返回的是图片 URL（OpenAI 的 b64_json 关了就是这个）。
    #    这一步要走网络，所以放在最后，能本地解出来的就不浪费一次请求。
    for candidate in _iter_image_candidates(data):
        if _looks_like_image_url(candidate):
            fetched = _fetch_image_url(str(candidate).strip())
            if fetched:
                return fetched

    raise Img2ImgError(
        "响应里没找到图片数据。如果渠道返回格式比较特殊，"
        f"请在配置里手动指定「响应图片路径」。响应前 300 字符：{text[:300]}"
    )


# 常见的图片字段路径，按可能性排序
_COMMON_PATHS = (
    "image",
    "img",
    "data",
    "data.image",
    "data.0",
    "data.0.b64_json",
    "data.0.url",
    "images",
    "images.0",
    "images.0.url",
    "artifacts",
    "artifacts.0.base64",
    "artifacts.0.images.0",
    "output",
    "output.0",
    "result",
    "result.image",
    "result.images.0",
    "b64_json",
    "base64",
)


def _iter_image_candidates(data: Any):
    """按常见路径依次产出候选值"""
    for path in _COMMON_PATHS:
        got = _get_by_path(data, path)
        if got is not None:
            yield got


def _get_by_path(data: Any, path: str) -> Any:
    """按 "a.b.0.c" 形式的路径取值，取不到返回 None

    支持 dict 的键和 list 的下标混用。
    """
    if not path:
        return None
    current = data
    for part in str(path).split("."):
        part = part.strip()
        if not part:
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        else:
            return None
    return current


def _decode_image_value(value: Any) -> bytes | None:
    """把一个候选值尽力解码成图片二进制。

    认识这些形态：
    - bytes 本身（且看起来像图片）
    - data URL: data:image/png;base64,xxxx
    - 纯 base64 串
    - 看起来像图片的本地路径（不读文件，交给上层处理）
    """
    if value is None:
        return None

    if isinstance(value, bytes):
        return value if _looks_like_image(value) else None

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    # data URL
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        return _try_base64(payload)

    # 裸 base64：长度够长且字符集匹配
    if len(text) > 64 and _looks_like_base64(text):
        return _try_base64(text)

    return None


def _try_base64(payload: str) -> bytes | None:
    """尝试 base64 解码，并确认结果真的是图片"""
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception:
        return None
    return data if _looks_like_image(data) else None


def _looks_like_image_url(value) -> bool:
    """判断候选值是不是一个可以下载的图片地址。

    刻意只认 http/https —— `file://`、本地路径这些一律不放行，
    否则渠道返回的任意字符串都可能被当成路径去读，是个安全隐患。
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text.startswith("http://") or text.startswith("https://")


def _fetch_image_url(url: str, timeout: float = 30.0) -> bytes | None:
    """把渠道返回的图片 URL 下载下来。

    用标准库 urllib 而不是 aiohttp，因为这个函数是从同步的解析流程里调用的；
    为此单独开一次事件循环不划算，图片本身也不大。

    下载失败不抛异常，只返回 None —— 调用方会把「没找到图片」的完整报错抛出去，
    比在这里抛一个「下载失败」更能说明问题全貌。
    """
    req = urllib.request.Request(url, headers={"User-Agent": "astrbot-plugin-nai-plus"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as e:
        logger.warning("[Img2Img] 下载响应里的图片 URL 失败：%s（%s）", url, e)
        return None
    return data if _looks_like_image(data) else None


_BASE64_CHARS_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


def _looks_like_base64(text: str) -> bool:
    return bool(_BASE64_CHARS_RE.match(text[:512]))


def _looks_like_image(data: bytes) -> bool:
    """靠文件头判断是不是图片。

    这一步很重要：很多渠道失败时返回的 JSON 里也有 base64 字段（比如错误详情），
    如果不校验就当成图片发出去，用户会收到一张坏图而不是明确的报错。
    """
    if not data or len(data) < 8:
        return False
    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    # JPEG
    if data[:2] == b"\xff\xd8":
        return True
    # WEBP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    # GIF
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    # BMP
    if data[:2] == b"BM":
        return True
    return False


class Img2ImgClient:
    """通用图生图客户端：把用户配置的请求模板渲染成真实 HTTP 请求"""

    # 占位符 → 上下文键 的映射。模板里写左边，插件用右边取值。
    def __init__(self, config: dict):
        """
        Args:
            config: 插件的 img2img 配置子字典（_conf_schema.json 里 img2img 那个 object）
        """
        self.base_url = normalize_endpoint(config.get("base_url", ""))
        self.method = str(config.get("method", "POST")).strip().upper() or "POST"
        self.api_key = str(config.get("api_key", "") or "").strip()
        self.auth_header = str(config.get("auth_header", "Authorization")).strip()
        self.auth_prefix = str(config.get("auth_prefix", "Bearer "))

        content_type = str(
            config.get("content_type", "multipart/form-data")
        ).strip().lower()
        # 归一化：用户可能填 "multipart" 或 "form-data"
        if "multipart" in content_type or "form" in content_type:
            self.content_type = "multipart/form-data"
        else:
            self.content_type = "application/json"

        self.body_template = str(config.get("body_template", "") or "")

        self.response_path = str(config.get("response_path", "") or "").strip()

        self.default_strength = _clamp01(config.get("default_strength", 0.7))
        self.default_noise = _clamp01(config.get("default_noise", 0.2))

        self.timeout = int(config.get("timeout", 120))

        # 图片在 multipart 里的字段名（schema 里的顶层 image_field，兜底 image）
        self.image_field = str(config.get("image_field", "image") or "image").strip() or "image"

        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    def is_configured(self) -> bool:
        """配置是否够用（地址和模板都得有）"""
        return bool(self.base_url and self.body_template.strip())

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(
                        total=float(self.timeout) + 30, connect=30
                    )
                    connector = aiohttp.TCPConnector(limit=5, limit_per_host=3)
                    self._session = aiohttp.ClientSession(
                        timeout=timeout, connector=connector
                    )
        return self._session

    def _build_context(
        self,
        prompt: str,
        image_bytes: bytes,
        *,
        negative: str | None = None,
        strength: float | None = None,
        noise: float | None = None,
        seed: int | None = None,
        model: str | None = None,
        size: str | None = None,
    ) -> dict[str, Any]:
        """构造模板占位符的取值表"""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        strength_val = (
            self.default_strength if strength is None else _clamp01(strength)
        )
        noise_val = self.default_noise if noise is None else _clamp01(noise)

        return {
            "prompt": prompt or "",
            "negative": negative or "",
            "image_base64": b64,
            "image_data_url": f"data:image/png;base64,{b64}",
            "strength": _format_float(strength_val),
            "noise": _format_float(noise_val),
            "seed": "" if seed is None else str(seed),
            "model": model or "",
            "size": size or "",
        }

    async def generate(
        self,
        prompt: str,
        image_bytes: bytes,
        *,
        negative: str | None = None,
        strength: float | None = None,
        noise: float | None = None,
        seed: int | None = None,
        model: str | None = None,
        size: str | None = None,
    ) -> bytes:
        """执行一次图生图，返回图片二进制。

        失败的每一种情况都抛出带可读原因的 Img2ImgError，
        由上层决定是提示用户还是回退。
        """
        if not self.base_url:
            raise Img2ImgError("图生图接口地址没填，请到插件配置的「图生图」里填写")
        if not self.body_template.strip():
            raise Img2ImgError("图生图请求模板没填，请到插件配置的「图生图」里填写")
        if not image_bytes:
            raise Img2ImgError("参考图为空，没法做图生图")

        context = self._build_context(
            prompt,
            image_bytes,
            negative=negative,
            strength=strength,
            noise=noise,
            seed=seed,
            model=model,
            size=size,
        )
        rendered = render_template(self.body_template, context)

        headers = {}
        if self.api_key and self.auth_header:
            headers[self.auth_header] = f"{self.auth_prefix}{self.api_key}"

        logger.info(
            "[Img2Img] 开始图生图: %s %s, 参考图 %d bytes, strength=%s, noise=%s",
            self.method,
            self.base_url,
            len(image_bytes),
            context["strength"],
            context["noise"],
        )

        # 用占位符方式记录模板，避免把 base64 刷进日志
        logger.debug(
            "[Img2Img] 渲染后的模板（已裁掉图片数据）: %s",
            render_template(
                self.body_template,
                {**context, "image_base64": "<省略>", "image_data_url": "<省略>"},
            )[:500],
        )

        session = await self._get_session()

        if self.content_type == "multipart/form-data":
            payload, extra_headers = self._build_multipart(rendered, image_bytes)
            headers.update(extra_headers)
        else:
            payload = rendered.encode("utf-8")
            headers["Content-Type"] = "application/json"

        # 模板里的 {{image_base64}} 可能把 body 撑得很大，这里给个上限提醒
        if len(payload) > 20 * 1024 * 1024:
            logger.warning(
                "[Img2Img] 请求体超过 20MB（%d bytes），部分渠道可能拒绝",
                len(payload),
            )

        try:
            async with session.request(
                self.method,
                self.base_url,
                data=payload,
                headers=headers,
            ) as resp:
                body = await resp.read()
                content_type = resp.headers.get("Content-Type", "")

                if resp.status != 200:
                    raise Img2ImgError(
                        f"HTTP {resp.status}: "
                        f"{body.decode('utf-8', errors='ignore')[:200]}"
                    )

                image = extract_image_from_response(
                    body, content_type, response_path=self.response_path
                )
                logger.info("[Img2Img] 图生图成功，返回 %d bytes", len(image))
                return image

        except aiohttp.ClientError as e:
            raise Img2ImgError(f"网络错误: {e}") from e

    def _build_multipart(
        self, rendered_body: str, image_bytes: bytes
    ) -> tuple[bytes, dict[str, str]]:
        """构造 multipart/form-data 请求体。

        这里刻意**不解析模板**，而是约定：
        模板里写 key=value 每行一个，或者写成 JSON（会被拍平成字段）。
        原因是 multipart 本来就是扁平的键值对，硬要支持嵌套结构没意义。

        支持两种写法：
        1. 每行 `key=value`（最直观）
        2. JSON 对象（会按顶层键拍平，值转成字符串）
        """
        fields: list[tuple[str, str]] = []

        text = rendered_body.strip()
        if text.startswith("{"):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        fields.append((str(k), _stringify(v)))
            except Exception as e:
                raise Img2ImgError(
                    f"multipart 模式下的模板必须是合法 JSON 或每行 key=value：{e}"
                )
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                if not sep:
                    raise Img2ImgError(
                        f"multipart 模板这一行格式不对（需要 key=value）：{line}"
                    )
                fields.append((key.strip(), value.strip()))

        # 参考图作为文件字段塞进去
        fields.append((self.image_field, None))

        boundary = f"----NaiPlusBoundary{id(self) & 0xFFFFFFFF:x}"
        chunks: list[bytes] = []

        for key, value in fields:
            chunks.append(f"--{boundary}\r\n".encode())
            if value is None:
                # 图片字段
                chunks.append(
                    (
                        f'Content-Disposition: form-data; name="{key}"; '
                        f'filename="reference.png"\r\n'
                        f"Content-Type: image/png\r\n\r\n"
                    ).encode()
                )
                chunks.append(image_bytes)
                chunks.append(b"\r\n")
            else:
                chunks.append(
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                )
                chunks.append(str(value).encode("utf-8"))
                chunks.append(b"\r\n")

        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), {
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }


def _stringify(value: Any) -> str:
    """把 JSON 值转成 multipart 字段的字符串形式"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _clamp01(value: Any) -> float:
    """把值夹到 0~1，容错处理非法输入"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _format_float(value: float) -> str:
    """格式化浮点，去掉多余的 0（0.70 → 0.7），避免某些渠道校验字符串"""
    return f"{value:g}"

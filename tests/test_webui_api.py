# -*- coding: utf-8 -*-
"""WebUI 管理面板后端接口回归测试（不依赖 AstrBot，可离线直接跑）。

    python tests/test_webui_api.py

为什么要有这个文件：面板接口最容易出的问题不是「点了没反应」（那种一打开
就能看见），而是**数据写错地方**——比如第一版把画师串写进了 AstrBot 全局
配置而不是插件配置，界面上显示保存成功，重载后却发现没生效。这种错只有
把「保存后配置对象里的值」和「PresetManager 里的值」都断言一遍才能抓住。

做法：把 astrbot 包整个 stub 掉（api.web / api.star / api.event 等），
然后 import 真正的 main.py，用假的 Context 拿到注册的 handler 直接调用。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASSED = 0
FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(msg)


# ---------------------------------------------------------------------------
# 1. 把 astrbot 包 stub 掉
# ---------------------------------------------------------------------------

def _mod(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


class _NullLogger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


# --- astrbot.api ---
astrbot = _mod("astrbot")
api = _mod("astrbot.api")
api.logger = _NullLogger()
astrbot.api = api

# --- astrbot.api.event / filter ---
event = _mod("astrbot.api.event")


class AstrMessageEvent:  # 只是个类型占位
    pass


class _Filter:
    """把装饰器全部变成 no-op，让 main.py 里的 @filter.command(...) 能过。"""

    class EventMessageType:
        ALL = "ALL"
        GROUP_MESSAGE = "GROUP_MESSAGE"
        PRIVATE_MESSAGE = "PRIVATE_MESSAGE"

    class PermissionType:
        ADMIN = "ADMIN"
        MEMBER = "MEMBER"

    def __getattr__(self, _name):
        def deco(*a, **k):
            if len(a) == 1 and callable(a[0]) and not k:
                return a[0]
            return lambda fn: fn
        return deco


event.AstrMessageEvent = AstrMessageEvent
event.filter = _Filter()
api.event = event

# --- astrbot.api.message_components ---
mc = _mod("astrbot.api.message_components")
for _n in ("Node", "Plain", "Image", "At", "Reply"):
    setattr(mc, _n, type(_n, (), {}))
api.message_components = mc

# --- astrbot.api.star ---
star = _mod("astrbot.api.star")


class Star:
    def __init__(self, context=None, *a, **k):
        self.context = context


class Context:
    pass


class StarTools:
    _data_dir: Path | None = None

    @classmethod
    def get_data_dir(cls, _name: str) -> Path:
        assert cls._data_dir is not None
        return cls._data_dir


star.Star = Star
star.Context = Context
star.StarTools = StarTools
star.register = lambda *a, **k: (lambda cls: cls)
api.star = star

# --- astrbot.core.star.filter.command.GreedyStr ---
_mod("astrbot.core")
_mod("astrbot.core.star")
_mod("astrbot.core.star.filter")
cmd = _mod("astrbot.core.star.filter.command")
cmd.GreedyStr = str

# --- astrbot.api.provider（translate_manager 可能用到）---
prov = _mod("astrbot.api.provider")
prov.Provider = type("Provider", (), {})
prov.LLMResponse = type("LLMResponse", (), {})
api.provider = prov

# --- mcp（main.py 顶部 import mcp）---
if "mcp" not in sys.modules:
    mcp = _mod("mcp")
    mcp_types = _mod("mcp.types")
    mcp.types = mcp_types

# --- astrbot.api.web：模拟真实语义 ---
web = _mod("astrbot.api.web")


class _JSONResponse:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code


def json_response(data=None, *, status_code=200, headers=None):
    return _JSONResponse({} if data is None else data, status_code)


def error_response(message, *, status_code=400, data=None, headers=None):
    return _JSONResponse({"status": "error", "message": message, "data": data}, status_code)


class _FakeRequest:
    """模拟 astrbot.api.web.request 代理：测试里手动塞 body。"""

    def __init__(self):
        self._json = None
        self.query = {}

    def set_json(self, payload):
        self._json = payload

    async def json(self, default=None):
        return self._json if self._json is not None else default


web.json_response = json_response
web.error_response = error_response
web.request = _FakeRequest()
api.web = web

# ---------------------------------------------------------------------------
# 2. 假的 AstrBotConfig / Context
# ---------------------------------------------------------------------------


class FakePluginConfig(dict):
    """模拟 AstrBotConfig：dict 子类 + save_config_async 会落盘。"""

    def __init__(self, path: Path, initial: dict):
        super().__init__(initial)
        self.config_path = path
        self.save_calls: list[dict] = []

    async def save_config_async(self, replace_config=None, *, indent=2):
        if replace_config:
            self.update(replace_config)
        self.save_calls.append(dict(replace_config or {}))
        self.config_path.write_text(json.dumps(dict(self), ensure_ascii=False, indent=indent), encoding="utf-8")
        return True


class FakeGlobalConfig(dict):
    """模拟 context.get_config() 返回的**全局**配置。任何写入都算错。"""

    def __init__(self):
        super().__init__({"dashboard": {"password": "x"}})
        self.polluted = False

    async def save_config_async(self, replace_config=None, **k):
        self.polluted = True
        return True

    def save_config(self, replace_config=None, **k):
        self.polluted = True


class FakeContext(Context):
    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.global_cfg = FakeGlobalConfig()

    def get_config(self, umo=None):
        return self.global_cfg

    def register_web_api(self, route, handler, methods, desc):
        for m in methods:
            self.routes[(m, route)] = handler

    def register_commands(self, *a, **k):
        pass

    def get_all_providers(self):
        return []

    def get_using_provider(self, *a, **k):
        return None


# ---------------------------------------------------------------------------
# 3. 实例化插件
# ---------------------------------------------------------------------------

sys.path.insert(0, str(ROOT.parent))

tmp = Path(tempfile.mkdtemp(prefix="nai_webui_test_", dir=str(ROOT.parent / ".tmp") if (ROOT.parent / ".tmp").exists() else None))
data_dir = tmp / "data"
data_dir.mkdir(parents=True)
StarTools._data_dir = data_dir

schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
initial_cfg = {k: v.get("default") for k, v in schema.items() if "default" in v}
initial_cfg["token"] = "TEST"
cfg_path = tmp / "astrbot_plugin_nai_plus_config.json"
plugin_cfg = FakePluginConfig(cfg_path, initial_cfg)

from astrbot_plugin_nai_plus import main as plugin_main  # noqa: E402

ctx = FakeContext()
PluginCls = None
for _name in dir(plugin_main):
    _obj = getattr(plugin_main, _name)
    if isinstance(_obj, type) and issubclass(_obj, Star) and _obj is not Star:
        PluginCls = _obj
        break
check(PluginCls is not None, "没在 main.py 里找到 Star 子类")
plugin = PluginCls(ctx, plugin_cfg)

PN = plugin_main.PLUGIN_NAME
req = web.request


def call(method: str, endpoint: str, body=None):
    handler = ctx.routes.get((method, f"/{PN}/{endpoint}"))
    if handler is None:
        raise AssertionError(f"路由未注册: {method} /{PN}/{endpoint}")
    req.set_json(body)
    return asyncio.run(handler())


# ---------------------------------------------------------------------------
# 4. 路由注册
# ---------------------------------------------------------------------------
for m, ep in (("GET", "config"), ("POST", "config/artist"), ("POST", "config/negative"),
              ("POST", "presets"), ("POST", "preview")):
    check((m, f"/{PN}/{ep}") in ctx.routes, f"缺少路由 {m} /{PN}/{ep}")

# ---------------------------------------------------------------------------
# 5. GET config
# ---------------------------------------------------------------------------
r = call("GET", "config")
check(r.status_code == 200, f"GET config 状态码 {r.status_code}")
check(r.body.get("artist") == initial_cfg["default_artist"], "GET config 画师串不等于配置值")
check(r.body.get("negative") == initial_cfg["default_negative"], "GET config 负向词不等于配置值")
check(r.body.get("defaults", {}).get("artist") == schema["default_artist"]["default"], "defaults.artist 应来自 schema")
check(len(r.body.get("builtin_presets", [])) == 5, f"内置预设应为 5 个，实际 {len(r.body.get('builtin_presets', []))}")
check(r.body.get("custom_presets") == [], "初始自定义预设应为空")

# ---------------------------------------------------------------------------
# 6. 保存画师串 / 负向词 → 必须写进插件配置，且不能碰全局配置
# ---------------------------------------------------------------------------
r = call("POST", "config/artist", {"value": "  artist:foo, 1.2::best quality::  "})
check(r.status_code == 200 and r.body.get("saved") is True, "保存画师串失败")
check(plugin_cfg["default_artist"] == "artist:foo, 1.2::best quality::", "画师串没写进插件配置（或没 strip）")
check(plugin.client.default_artist == "artist:foo, 1.2::best quality::", "画师串没同步到 client（当前进程不生效）")
check(json.loads(cfg_path.read_text(encoding="utf-8"))["default_artist"] == "artist:foo, 1.2::best quality::", "画师串没落盘")
check(not ctx.global_cfg.polluted, "❌ 画师串被写进了 AstrBot 全局配置（context.get_config()）")

r = call("POST", "config/negative", {"value": "lowres, bad hands"})
check(r.status_code == 200 and plugin_cfg["default_negative"] == "lowres, bad hands", "负向词没写进插件配置")
check(plugin.client.default_negative == "lowres, bad hands", "负向词没同步到 client")
check(not ctx.global_cfg.polluted, "❌ 负向词被写进了 AstrBot 全局配置")

# 空值允许（= 不传）
r = call("POST", "config/negative", {"value": ""})
check(r.status_code == 200 and plugin_cfg["default_negative"] == "", "负向词应允许清空")
# 缺 body 不炸
r = call("POST", "config/negative", None)
check(r.status_code == 200, "缺 body 时应按空字符串处理而不是 500")

# ---------------------------------------------------------------------------
# 7. 预设增删改 + 校验规则 + 回写配置
# ---------------------------------------------------------------------------
r = call("POST", "presets", {"action": "add", "data": {"name": "我的风", "artist": "artist:bar, -2::green::", "desc": "测试"}})
check(r.status_code == 200, f"新增预设失败: {r.body}")
check(any(p["name"] == "我的风" for p in r.body.get("custom_presets", [])), "新增后列表里没有")
check(plugin.presets.get("我的风") == "artist:bar, -2::green::", "PresetManager 里没有新预设")
saved_names = [p.get("name") for p in plugin_cfg.get("custom_presets", [])]
check("我的风" in saved_names, f"预设没回写到插件配置 custom_presets: {saved_names}")
check((data_dir / "presets.json").exists(), "presets.json 没落盘")

# 重名 add → 409
r = call("POST", "presets", {"action": "add", "data": {"name": "我的风", "artist": "x"}})
check(r.status_code == 409, f"重名新增应 409，实际 {r.status_code}")
# 含空格 → 400
r = call("POST", "presets", {"action": "add", "data": {"name": "有 空格", "artist": "x"}})
check(r.status_code == 400, "预设名含空格应 400")
# 内置名 → 400
r = call("POST", "presets", {"action": "add", "data": {"name": "动漫风", "artist": "x"}})
check(r.status_code == 400, "覆盖内置预设名应 400")
# 空画师串 → 400
r = call("POST", "presets", {"action": "add", "data": {"name": "空串", "artist": ""}})
check(r.status_code == 400, "空画师串应 400")
# 未知 action → 400
r = call("POST", "presets", {"action": "nuke", "data": {"name": "x"}})
check(r.status_code == 400, "未知 action 应 400")

# update
r = call("POST", "presets", {"action": "update", "data": {"name": "我的风", "artist": "artist:baz", "desc": "改过"}})
check(r.status_code == 200 and plugin.presets.get("我的风") == "artist:baz", "更新预设失败")
r = call("POST", "presets", {"action": "update", "data": {"name": "不存在", "artist": "x"}})
check(r.status_code == 404, "更新不存在的预设应 404")

# delete
r = call("POST", "presets", {"action": "delete", "data": {"name": "动漫风"}})
check(r.status_code == 400, "删除内置预设应 400")
r = call("POST", "presets", {"action": "delete", "data": {"name": "我的风"}})
check(r.status_code == 200 and plugin.presets.get("我的风") is None, "删除预设失败")
check(not any(p.get("name") == "我的风" for p in plugin_cfg.get("custom_presets", [])), "删除后配置里还残留")
r = call("POST", "presets", {"action": "delete", "data": {"name": "我的风"}})
check(r.status_code == 404, "重复删除应 404")

check(not ctx.global_cfg.polluted, "❌ 预设操作污染了全局配置")

# ---------------------------------------------------------------------------
# 8. 预览：和 nai2api_client.generate() 的拼接语义一致
# ---------------------------------------------------------------------------
r = call("POST", "preview", {"prompt": "1girl, silver hair", "artist": "artist:foo, -2::green::, year 2025", "negative": "lowres"})
check(r.status_code == 200, "预览失败")
check(r.body["final_prompt"].startswith("artist:foo"), f"画师串应拼在最前: {r.body['final_prompt']}")
check("-2::green::" not in r.body["final_prompt"], "负权重应从正向串移除")
check("-2::green::" in r.body["final_negative"] and r.body["final_negative"].startswith("lowres"), f"负权重应追加到负向词: {r.body['final_negative']}")
check(r.body["composition_added"] is True and "full body, standing" in r.body["final_prompt"], "无构图时应自动补 full body, standing")

r = call("POST", "preview", {"prompt": "1girl, cowboy shot", "artist": "", "negative": ""})
check(r.body["composition_added"] is False and r.body["final_prompt"] == "1girl, cowboy shot", f"已有构图不应再补: {r.body}")

# ---------------------------------------------------------------------------
# 9. 老版本 AstrBot（没有 astrbot.api.web）：不能炸
# ---------------------------------------------------------------------------
saved_web = sys.modules.pop("astrbot.api.web")
delattr(api, "web")
try:
    ctx2 = FakeContext()
    p2 = PluginCls(ctx2, FakePluginConfig(tmp / "cfg2.json", dict(initial_cfg)))
    check(ctx2.routes == {}, "无 astrbot.api.web 时不应注册任何路由")
    check(p2.client is not None, "无 astrbot.api.web 时插件主体应正常初始化")
except Exception as e:  # noqa: BLE001
    check(False, f"无 astrbot.api.web 时插件初始化抛异常: {e!r}")
finally:
    sys.modules["astrbot.api.web"] = saved_web
    api.web = saved_web

# ---------------------------------------------------------------------------
# 10. 前端静态文件基本健全性
# ---------------------------------------------------------------------------
page = ROOT / "pages" / "nai-config"
check((page / "index.html").exists(), "缺 pages/nai-config/index.html")
html = (page / "index.html").read_text(encoding="utf-8")
js = (page / "app.js").read_text(encoding="utf-8")
check('type="module"' in html and "app.js" in html, "index.html 应以 module 方式引入 app.js")
for ep in ("config", "config/artist", "config/negative", "presets", "preview"):
    check(f'"{ep}"' in js, f"app.js 里没调用 endpoint {ep!r}")
check('apiGet("/' not in js and 'apiPost("/' not in js, "bridge endpoint 不应以 / 开头")
i18n = json.loads((ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
check(i18n.get("pages", {}).get("nai-config", {}).get("title"), "zh-CN.json 缺 pages.nai-config.title")

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
total = PASSED + len(FAILED)
print(f"WebUI 后端：{PASSED}/{total} 项断言通过")
if FAILED:
    print("\n未通过：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过 ✓")

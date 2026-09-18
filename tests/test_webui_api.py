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
              ("POST", "presets"), ("POST", "preset/default"), ("POST", "preview")):
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
# 7b. 三段式预设（v1.4.2）：画师串 / 正向词 / 负向词
# ---------------------------------------------------------------------------
# 只带正向词 + 负向词、不带画师串的预设也能保存
r = call("POST", "presets", {"action": "add", "data": {
    "name": "夜景", "artist": "", "positive": "night, city lights, full body", "negative": "daylight, sun", "desc": "夜景补充",
}})
check(r.status_code == 200, f"只带正向/负向词的预设应能保存: {r.body}")
entry = plugin.presets.get_entry("夜景")
check(entry is not None and entry["artist"] == "" and entry["positive"] == "night, city lights, full body"
      and entry["negative"] == "daylight, sun", f"get_entry 三段内容不对: {entry}")
check(plugin.presets.get("夜景") == "", "画师串为空的预设 get() 应返回空串而不是 None")
# 前端拿到的列表要带 positive/negative 字段
p_night = next((p for p in r.body["custom_presets"] if p["name"] == "夜景"), None)
check(p_night is not None and p_night.get("positive") == "night, city lights, full body"
      and p_night.get("negative") == "daylight, sun", f"custom_presets 缺 positive/negative: {p_night}")
# 内置预设也要有这两个键（前端不用判断键存不存在）
r = call("GET", "config")
check(all("positive" in p and "negative" in p for p in r.body["builtin_presets"]), "内置预设 payload 应带 positive/negative 空串")
# 回写到插件配置 custom_presets 里也要带三段
saved_night = next((p for p in plugin_cfg.get("custom_presets", []) if p.get("name") == "夜景"), None)
check(saved_night is not None and saved_night.get("positive") == "night, city lights, full body"
      and saved_night.get("negative") == "daylight, sun", f"custom_presets 回写缺 positive/negative: {saved_night}")
# presets.json 落盘也要带三段
disk = json.loads((data_dir / "presets.json").read_text(encoding="utf-8"))
check(disk.get("夜景", {}).get("negative") == "daylight, sun", "presets.json 里缺 negative")

# 三段全空 → 400
r = call("POST", "presets", {"action": "add", "data": {"name": "全空", "artist": "", "positive": "", "negative": ""}})
check(r.status_code == 400, "三段全空应 400")

# update 只改负向词，正向词保留（面板整表提交，所以这里模拟整表）
r = call("POST", "presets", {"action": "update", "data": {
    "name": "夜景", "artist": "", "positive": "night, city lights, full body", "negative": "daylight", "desc": "夜景补充",
}})
check(r.status_code == 200 and plugin.presets.get_entry("夜景")["negative"] == "daylight", "更新负向词失败")

# --- 生图路径：_resolve_artist / _apply_preset_extras ---
# 画师串为空的预设 → _resolve_artist 返回 None（退回全局画师串）
check(plugin._resolve_artist("夜景", None) is None, "画师串为空的预设应让 _resolve_artist 返回 None")
# 带画师串的预设仍然正常
call("POST", "presets", {"action": "add", "data": {"name": "带串", "artist": "artist:qux", "positive": "soft lighting", "negative": "-2::green::"}})
check(plugin._resolve_artist("带串", None) == "artist:qux", "带画师串的预设 _resolve_artist 应返回画师串")
check(plugin._resolve_artist("带串", "artist:override") == "artist:override", "--artist 应覆盖预设画师串")

# 正向词追加到用户提示词后面；重复标签跳过
p, n = plugin._apply_preset_extras("夜景", "1girl, full body", None)
check(p == "1girl, full body, night, city lights", f"预设正向词应追加到后面且去重: {p!r}")
# negative=None 时要先拿全局负向词再追加，不能把全局的丢了
plugin.client.default_negative = "lowres, bad hands"
p, n = plugin._apply_preset_extras("夜景", "1girl", None)
check(n == "lowres, bad hands, daylight", f"预设负向词应追加到全局负向词后面: {n!r}")
# 用户 --negative 给了值时，追加到用户的后面
p, n = plugin._apply_preset_extras("夜景", "1girl", "custom neg")
check(n == "custom neg, daylight", f"预设负向词应追加到用户 --negative 后面: {n!r}")
# 不存在的预设 / 不传预设：原样返回
p, n = plugin._apply_preset_extras("不存在", "1girl", None)
check(p == "1girl" and n is None, "不存在的预设不应改动 prompt/negative")
p, n = plugin._apply_preset_extras(None, "1girl", "x")
check(p == "1girl" and n == "x", "不传预设不应改动 prompt/negative")
# 内置预设没有 positive/negative，也不应报错
p, n = plugin._apply_preset_extras("动漫风", "1girl", None)
check(p == "1girl" and n is None, "内置预设无附带词，应原样返回")

# --- 预览接口支持 preset ---
r = call("POST", "preview", {"prompt": "1girl", "artist": "artist:global", "negative": "lowres", "preset": "带串"})
check(r.status_code == 200 and r.body.get("preset_applied") is True, f"预览带 preset 失败: {r.body}")
check(r.body["final_prompt"].startswith("artist:qux") and "soft lighting" in r.body["final_prompt"]
      and "artist:global" not in r.body["final_prompt"], f"预览应用预设画师串+正向词: {r.body['final_prompt']}")
check("-2::green::" in r.body["final_negative"] and r.body["final_negative"].startswith("lowres"),
      f"预设负向词里的负权重应进 final_negative: {r.body['final_negative']}")
# 画师串为空的预设 → 沿用传入的 artist
r = call("POST", "preview", {"prompt": "1girl", "artist": "artist:global", "negative": "", "preset": "夜景"})
check(r.body["final_prompt"].startswith("artist:global") and "city lights" in r.body["final_prompt"],
      f"画师串为空的预设预览应沿用全局画师串: {r.body['final_prompt']}")
check(r.body["final_negative"] == "daylight", f"全局负向词为空时 final_negative 应只有预设负向词: {r.body['final_negative']}")
r = call("POST", "preview", {"prompt": "1girl", "preset": "不存在"})
check(r.status_code == 404, "预览不存在的预设应 404")
# 不传 preset 行为不变
r = call("POST", "preview", {"prompt": "1girl, cowboy shot", "artist": "", "negative": ""})
check(r.body.get("preset_applied") is False and r.body["final_prompt"] == "1girl, cowboy shot", "不传 preset 应和以前一致")

# --- 老格式兼容：只有 artist/desc 的 presets.json 读进来不炸，且补齐三段 ---
from astrbot_plugin_nai_plus.core.preset_manager import PresetManager, merge_tags, parse_webui_presets  # noqa: E402

legacy_dir = tmp / "legacy"
legacy_dir.mkdir()
(legacy_dir / "presets.json").write_text(json.dumps({"旧预设": {"artist": "artist:old", "desc": "老的"}}, ensure_ascii=False), encoding="utf-8")
pm = PresetManager(legacy_dir, webui_presets=[{"name": "配置里的", "artist": "artist:cfg"}])
e_old = pm.get_entry("旧预设")
check(e_old == {"artist": "artist:old", "positive": "", "negative": "", "desc": "老的"}, f"老格式预设应补齐三段: {e_old}")
e_cfg = pm.get_entry("配置里的")
check(e_cfg is not None and e_cfg["positive"] == "" and e_cfg["negative"] == "", "老格式 template_list 项应补齐三段")
# export_for_webui 带三段
exported = pm.export_for_webui()
check(all({"artist", "positive", "negative", "desc"} <= set(x) for x in exported), "export_for_webui 应带三段")
# parse_webui_presets：三段全空的丢弃、只有负向词的保留
parsed = parse_webui_presets([
    {"name": "全空", "artist": "", "positive": "", "negative": ""},
    {"name": "只负向", "negative": "blurry"},
])
check("全空" not in parsed and parsed.get("只负向", {}).get("negative") == "blurry", f"parse_webui_presets 三段规则不对: {parsed}")
# merge_tags 边界
check(merge_tags("", "a, b") == "a, b" and merge_tags("a", "") == "a", "merge_tags 空值处理")
check(merge_tags("a, B", "b, c") == "a, B, c", f"merge_tags 应忽略大小写去重: {merge_tags('a, B', 'b, c')!r}")
check(merge_tags("1.2::tag::", "tag") == "1.2::tag::, tag", "merge_tags 不应把带权重的和不带权重的视为同一项")

# 清理本节新增的预设，避免影响后面
call("POST", "presets", {"action": "delete", "data": {"name": "夜景"}})
call("POST", "presets", {"action": "delete", "data": {"name": "带串"}})
plugin.client.default_negative = plugin_cfg.get("default_negative", "")
check(not ctx.global_cfg.polluted, "❌ 三段式预设操作污染了全局配置")

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
# 9b. 发图失败 ≠ 生图失败（v1.4.3）
# ---------------------------------------------------------------------------
# 背景：QQ（NapCat）发大图偶发 sendMsg Timeout（retcode=1200）。以前生图和发图包在
# 同一个 try 里，这个超时会被记成「生图失败」，用户以为 NovelAI 挂了。
# 现在：发图失败要重试；重试仍失败要给用户「图已生成、发送超时」的说明，
# 而不是「生图失败」。

class _FakeActionFailed(Exception):
    """模拟 NapCat 的 ActionFailed（str() 是一大段带换行的 repr）"""
    def __str__(self):
        return ("<ActionFailed status='failed', retcode=1200, data=None, "
                "message='Timeout: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg "
                "ListenerName:NodeIKernelMsgListener/onMsgInfoListUpdate EventRet:\\n{}\\n', "
                "wording='...', echo={'seq': 456}, stream='normal-action'>")


class _FakeEvent:
    """最小化的 AstrMessageEvent：记录发送内容，可按次数注入发图失败"""
    unified_msg_origin = "test:group:1"

    def __init__(self, fail_image_times: int):
        self.fail_image_times = fail_image_times
        self.sent: list[tuple[str, str]] = []
        # 发图「尝试」次数：成功和失败都算，用来断言重试开关到底发了几次
        self.image_attempts = 0

    def image_result(self, p):
        return ("image", p)

    def plain_result(self, t):
        return ("plain", t)

    async def send(self, r):
        kind, payload = r
        if kind == "image":
            self.image_attempts += 1
            if self.fail_image_times > 0:
                self.fail_image_times -= 1
                raise _FakeActionFailed()
        self.sent.append(r)


fake_img = tmp / "fake.png"
fake_img.write_bytes(b"\x89PNG\r\n\x1a\nxxxx")

# 让 _do_generate 直接返回假图，不真的调网关；重试间隔调成 0 免得测试慢
_orig_do_generate = plugin._do_generate


async def _fake_do_generate(*a, **k):
    return fake_img


plugin._do_generate = _fake_do_generate
PluginCls._SEND_RETRY_DELAY = 0.0

# 第一次发图失败、第二次成功 → 用户应正常收到图 + 标签，不该看到任何失败文案
# （v1.4.5 起重试默认关闭，这里显式打开，验证「开了就还能救回来」）
plugin._send_retry_on_timeout = True
ev = _FakeEvent(fail_image_times=1)
res = asyncio.run(plugin._run_generate_command(ev, "1girl", None, None, None, None, None, None))
kinds = [k for k, _ in ev.sent]
check(res is None and kinds[0] == "image", f"发图第一次失败应重试成功: sent={ev.sent}, res={res}")
check(not any("失败" in t for k, t in ev.sent if k == "plain"), "重试成功后不应出现失败文案")

# 全部重试都失败 → 返回「图已生成但发送超时」的说明，绝不能说「生图失败」
ev = _FakeEvent(fail_image_times=99)
res = asyncio.run(plugin._run_generate_command(ev, "1girl", None, None, None, None, None, None))
check(isinstance(res, tuple) and res[0] == "plain", f"发图彻底失败应返回文字说明: {res!r}")
text = res[1] if isinstance(res, tuple) else ""
check("已经生成" in text and "生图失败" not in text, f"文案应说明图已生成而非生图失败: {text!r}")
check(str(fake_img) in text, "文案应带上图片本地路径，方便用户自己去取")
check("生图失败" not in text and "NodeIKernelMsgService" not in text, "不应把 NapCat 的一大段事件名直接甩给用户")
# 重试已开启 → 不该再提示「可以去打开重试」
check("发图超时自动重试" not in text, "重试已开启时不应再引导用户去开重试")

# --- v1.4.5：默认关闭重试，只发一次（超时 ≠ 未送达，重发会重复）---
plugin._send_retry_on_timeout = False
ev = _FakeEvent(fail_image_times=99)   # 只要发图就失败
res = asyncio.run(plugin._run_generate_command(ev, "1girl", None, None, None, None, None, None))
img_sends = [k for k, _ in ev.sent if k == "image"]
check(len(img_sends) == 0 and ev.image_attempts == 1,
      f"关闭重试时只应尝试发一次图: attempts={ev.image_attempts}, sent={ev.sent}")
check(isinstance(res, tuple) and res[0] == "plain", f"关闭重试时发图失败仍应返回文字说明: {res!r}")
text_off = res[1] if isinstance(res, tuple) else ""
check("已经生成" in text_off and str(fake_img) in text_off, f"关闭重试的失败文案仍要说明图已生成并给路径: {text_off!r}")
check("发图超时自动重试" in text_off, "关闭重试时应引导用户去插件配置里打开「发图超时自动重试」")
check("重复发图" in text_off, "提示里要讲清打开重试可能导致重复发图")

# 关闭重试时，第一次就成功 → 正常发图，attempts 恰好 1
ev = _FakeEvent(fail_image_times=0)
res = asyncio.run(plugin._run_generate_command(ev, "1girl", None, None, None, None, None, None))
check(res is None and ev.image_attempts == 1 and [k for k, _ in ev.sent][0] == "image",
      f"关闭重试时首次成功应只发一次: attempts={ev.image_attempts}, sent={ev.sent}")

# 真假边界：关闭重试时，若第 1 次失败第 2 次本可成功，也应直接放弃（不重发 → 不重复）
ev = _FakeEvent(fail_image_times=1)
res = asyncio.run(plugin._run_generate_command(ev, "1girl", None, None, None, None, None, None))
check(ev.image_attempts == 1 and isinstance(res, tuple) and res[0] == "plain",
      f"关闭重试时不应有第二次尝试: attempts={ev.image_attempts}, res={res!r}")
plugin._send_retry_on_timeout = True   # 恢复，避免影响后续断言

# 真正的生图失败（_do_generate 抛异常）仍然走老的「生图失败」分支
async def _boom(*a, **k):
    raise RuntimeError("Nai2API 500")


plugin._do_generate = _boom
ev = _FakeEvent(fail_image_times=0)
res = asyncio.run(plugin._run_generate_command(ev, "1girl", None, None, None, None, None, None))
check(isinstance(res, tuple) and "Nai2API 500" in res[1], f"真正的生图失败应保留原提示: {res!r}")
plugin._do_generate = _orig_do_generate

# _short_err 能把 ActionFailed 的 repr 压成一行可读的 message
short = plugin_main._short_err(_FakeActionFailed())
check(short.startswith("Timeout: NTEvent") and "\n" not in short and len(short) <= 160, f"_short_err 结果不对: {short!r}")
check(plugin_main._short_err(RuntimeError("")) == "RuntimeError", "空 message 的异常应退回类名")

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
# 三段式预设：前端表单要有正向词 / 负向词输入框，提交时要带上
check('id="preset-positive"' in html and 'id="preset-negative"' in html, "index.html 缺预设正向词 / 负向词输入框")
check('id="preview-preset"' in html, "index.html 缺拼接预览的「模拟预设」下拉框")
check("positive" in js and "negative" in js and "preset:" in js, "app.js 应提交 positive/negative 并在预览里传 preset")
i18n = json.loads((ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
check(i18n.get("pages", {}).get("nai-config", {}).get("title"), "zh-CN.json 缺 pages.nai-config.title")

# ---------------------------------------------------------------------------
# 11. v1.4.4：单角色自动补 solo（_ensure_subject_count 纯函数）
# ---------------------------------------------------------------------------
from astrbot_plugin_nai_plus.core import translate_manager as tm  # noqa: E402

esc = tm._ensure_subject_count
CT = {"kamisato ayaka", "raiden shogun", "artoria pendragon", "saber"}

# 已有人数标签 → 原样不动（裸的 / 带权重 / 带花括号 / 多人 / 无人）
check(esc("1girl, kamisato ayaka", CT) == "1girl, kamisato ayaka", "已有 1girl 不应再补")
check(esc("{{1girl}}, kamisato ayaka", CT) == "{{1girl}}, kamisato ayaka", "{{1girl}} 应视为已有人数标签")
check(esc("1.2::1girl::, kamisato ayaka", CT) == "1.2::1girl::, kamisato ayaka", "1.2::1girl:: 应视为已有人数标签")
check(esc("[1boy], kamisato ayaka", CT) == "[1boy], kamisato ayaka", "[1boy] 应视为已有人数标签")
check(esc("2girls, kamisato ayaka, raiden shogun", CT) == "2girls, kamisato ayaka, raiden shogun", "已有 2girls 不应再补 solo")
check(esc("no humans, beach", CT) == "no humans, beach", "no humans 不应补 solo")
check(esc("solo, kamisato ayaka", CT) == "solo, kamisato ayaka", "已有 solo 不应重复补")

# 恰好一个角色 → 前插 solo
check(esc("kamisato ayaka, swimsuit, beach", CT) == "solo, kamisato ayaka, swimsuit, beach", "词库角色 → 应前插 solo")
check(esc("kamisato_ayaka_(genshin_impact), swimsuit", set()) == "solo, kamisato_ayaka_(genshin_impact), swimsuit",
      "消歧写法 xxx_(yyy) 在无 character_tags 时也应靠括号识别为角色")
check(esc("Kamisato_Ayaka, swimsuit", CT) == "solo, Kamisato_Ayaka, swimsuit", "大小写 / 下划线应归一化后匹配 character_tags")
check(esc("genshin impact, kamisato ayaka, swimsuit, beach, playing", CT)
      == "solo, genshin impact, kamisato ayaka, swimsuit, beach, playing",
      "作品名 genshin impact 不在 character_tags 里，不应被数成第二个角色")
# `fate_(series)` 是作品标签不算角色；saber 在 character_tags 里算 1 个 → 补 solo
check(esc("fate_(series), saber", CT) == "solo, fate_(series), saber", "fate_(series) 不算角色，saber 算 1 个 → 补 solo")
check(esc("fate_(series), fate/grand_order", CT) == "fate_(series), fate/grand_order", "只有作品标签 → 0 个角色，不补")

# 两个角色 → 不动
check(esc("kamisato ayaka, raiden shogun", CT) == "kamisato ayaka, raiden shogun", "两个角色不应补 solo")
check(esc("bianca_(pgr), lucia_(pgr)", set()) == "bianca_(pgr), lucia_(pgr)", "两个消歧角色不应补 solo")

# 画师标签 / 空输入 / 纯风景
check(esc("artist:foo_(bar), swimsuit", set()) == "artist:foo_(bar), swimsuit", "artist:xxx_(yyy) 不算角色")
check(esc("inuyasha_(character)", set()) == "inuyasha_(character)", "(character) 后缀不算作品消歧，不补")
check(esc("", CT) == "", "空串原样返回")
check(esc("beach, sunset, ocean", CT) == "beach, sunset, ocean", "纯风景不补 solo")

# _strip_tag_syntax 的边界
check(tm._strip_tag_syntax("{{1girl}}") == "1girl" and tm._strip_tag_syntax("1.2::1girl::") == "1girl"
      and tm._strip_tag_syntax("[detailed]") == "detailed" and tm._strip_tag_syntax("\\n20::best quality::") == "best quality",
      "_strip_tag_syntax 应能剥掉 {{}} / [] / n::x:: 三种语法")

# 端到端：TranslateManager.translate() 词库全命中路径（不需要模型）
t_mgr = tm.TranslateManager({"translate_enabled": True, "translate_mode": "astrbot",
                             "translate_dictionary_enabled": True, "translate_quality_weight": True}, context=None)
t_out = asyncio.run(t_mgr.translate("原神神里绫华穿着泳衣在沙滩玩耍"))
check(t_out.startswith("solo, "), f"词库全命中路径结果应以 solo 开头：{t_out!r}")
check(not tm.has_cjk(t_out), f"结果不应含中文：{t_out!r}")
for want in ("genshin impact", "kamisato ayaka", "swimsuit", "beach", "playing"):
    check(want in t_out, f"端到端结果缺 {want!r}：{t_out!r}")
check(t_mgr.last_stats.get("dict_bypassed") is True, f"该句应完全绕过模型：{t_mgr.last_stats}")
# 已带 1girl 的输入走全命中路径不应再补 solo；质量词加权仍生效（_finalize 收口）
t_out2 = asyncio.run(t_mgr.translate("1girl, 神里绫华, best quality"))
check(t_out2.startswith("1girl") and "solo" not in t_out2, f"已有 1girl 不应再补 solo：{t_out2!r}")
check("1.2::best quality::" in t_out2, f"词库全命中路径也应做质量词加权（_finalize 收口）：{t_out2!r}")
# 多角色不补
t_out3 = asyncio.run(t_mgr.translate("神里绫华 雷电将军"))
check("solo" not in t_out3 and "1girl" not in t_out3, f"两个角色不应补人数标签：{t_out3!r}")
# dictionary 为 None 时不炸（_character_tags 容错）
t_mgr.dictionary = None
check(t_mgr._character_tags() == set(), "dictionary 为 None 时 _character_tags 应退成空集合")

# 系统提示词硬化：规则 7 变 MUST；示例与规则不再矛盾；schema 默认值同步
sp = tm.SYSTEM_PROMPT
check("MANDATORY" in sp and "MUST include `1girl` or `1boy`" in sp, "规则 7 应为硬约束（MANDATORY / MUST）")
check("when applicable" not in sp, "规则 7 不应再有 when applicable 软措辞")
check("Output: 1girl, bianca_(punishing:_gray_raven), swimsuit" in sp, "比安卡示例 Output 应带 1girl")
check("Output: 1girl, dania_(wuthering_waves), swimsuit" in sp, "达妮娅示例 Output 应带 1girl")
check("Output: 1girl, raiden shogun, purple hair" in sp, "雷电将军示例 1girl 应移到最前")
check("depth of field, 1girl" not in sp and "depth of field\n" in sp, "赛博朋克城市示例不应脑补 1girl")
check(schema["translate_system_prompt"]["default"] == sp, "_conf_schema.json 的 translate_system_prompt.default 必须与 SYSTEM_PROMPT 逐字节一致")

# ---------------------------------------------------------------------------
# 12. v1.4.5：图生图参考图落地（_fetch_reference_image）
# ---------------------------------------------------------------------------
# 背景（严重 bug）：v1.4.4 及以前写的是 `return await _materialize_image(first), None`，
# 但 _materialize_image 是同步函数（内部 urllib），对 str 做 await 必然抛
# TypeError: object str can't be used in 'await' expression，被外层 except 吞掉后
# 静默降级成文生图 —— 也就是说图生图从这行代码写出来起一次都没成功过。
# 修法：await asyncio.to_thread(_materialize_image, first)（既不阻塞事件循环，
# 也不对普通返回值做 await）。
#
# 为什么要在测试里替换 extract_quoted_message_images：
#   它在 _fetch_reference_image 内部 `from astrbot.core.utils.quoted_message import ...`
#   动态导入，所以往 sys.modules 里塞一个同名假模块即可生效。

# 一段合法的 1x1 PNG（base64），用来喂给 base64:// 分支
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "BQEAXc2mzQAAAABJRU5ErkJggg=="
)

_qm_mod = _mod("astrbot.core.utils.quoted_message")
_qm_mod._refs: list = []          # 由每个用例自行设置
_qm_mod._raise: Exception | None = None


async def _fake_extract_quoted_message_images(event):
    if _qm_mod._raise is not None:
        raise _qm_mod._raise
    return list(_qm_mod._refs)


_qm_mod.extract_quoted_message_images = _fake_extract_quoted_message_images

fetch_ref = plugin_main._fetch_reference_image

# ① base64:// 形态 → 落地成本地临时文件，返回 (path, None) 且文件非空
_qm_mod._refs = [f"base64://{_PNG_1X1_B64}"]
path, err = asyncio.run(fetch_ref(object()))
check(err is None, f"base64 参考图落地不应报错: err={err!r}")
check(isinstance(path, str) and path, f"应返回本地路径字符串: {path!r}")
mk_p = Path(path) if isinstance(path, str) else None
check(mk_p is not None and mk_p.exists(), f"落地的临时文件应真实存在: {path!r}")
check(mk_p is not None and mk_p.stat().st_size > 0, f"落地的临时文件不应是空文件: {path!r}")
if mk_p is not None and mk_p.exists():
    check(mk_p.read_bytes() == __import__("base64").b64decode(_PNG_1X1_B64),
          "落地文件内容应与原始 base64 解码结果一致")
    mk_p.unlink()   # 用完即删，别在 .tmp 里留垃圾

# ② data:image/png;base64,... 形态 → 同样能落地
_qm_mod._refs = [f"data:image/png;base64,{_PNG_1X1_B64}"]
path2, err2 = asyncio.run(fetch_ref(object()))
check(err2 is None and isinstance(path2, str) and Path(path2).exists(),
      f"data URL 参考图也应能落地: path={path2!r}, err={err2!r}")
if isinstance(path2, str) and Path(path2).exists():
    check(Path(path2).stat().st_size > 0, "data URL 落地文件不应为空")
    Path(path2).unlink()

# ③ 已经被“落地过”的本地路径 → 原样返回
local_p = tmp / "already_local.png"
local_p.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
_qm_mod._refs = [str(local_p)]
path3, err3 = asyncio.run(fetch_ref(object()))
check(err3 is None and path3 == str(local_p), f"本地路径引用应原样返回: {path3!r}")

# ④ 假模块返回 [] → (None, None)：没回复图片就是普通文生图，不是错误
_qm_mod._refs = []
path4, err4 = asyncio.run(fetch_ref(object()))
check(path4 is None and err4 is None, f"没有参考图时应返回 (None, None) 而不是报错: {(path4, err4)!r}")

# ⑤ 认不出的引用格式 → (None, 错误提示)，且不抛异常
_qm_mod._refs = ["ftp://who/knows"]
path5, err5 = asyncio.run(fetch_ref(object()))
check(path5 is None and isinstance(err5, str) and err5, f"认不出的格式应返回错误提示: {(path5, err5)!r}")

# ⑥ 读取被回复消息本身抛异常 → 也要兜住，给用户一句人话
_qm_mod._raise = RuntimeError("quoted boom")
path6, err6 = asyncio.run(fetch_ref(object()))
check(path6 is None and isinstance(err6, str) and "boom" in err6,
      f"读取被回复消息失败应兜住并给出提示: {(path6, err6)!r}")
_qm_mod._raise = None

# ⑦ 回归护栏：_materialize_image 必须是同步函数，且调用点不能对它直接 await
import inspect as _inspect  # noqa: E402

check(not _inspect.iscoroutinefunction(plugin_main._materialize_image),
      "_materialize_image 应保持同步（同步函数才需要 to_thread 包装）")
check(_inspect.iscoroutinefunction(fetch_ref), "_fetch_reference_image 应是协程函数")
_src = (ROOT / "main.py").read_text(encoding="utf-8")
# 注意：注释里会拿这个错误写法当反面教材，所以只检查「可执行代码行」——
# 即出现了该模式、但所在行不是 # 注释的行
_bad_lines = [
    ln for ln in _src.splitlines()
    if "await _materialize_image(" in ln and not ln.lstrip().startswith("#")
]
check(not _bad_lines,
      f"main.py 的可执行代码里不应再出现 `await _materialize_image(...)`（v1.4.4 的致命写法）: {_bad_lines}")
check("asyncio.to_thread(_materialize_image" in _src,
      "main.py 应用 asyncio.to_thread(_materialize_image, ...) 调用")

# ---------------------------------------------------------------------------
# 13. v1.4.6：图生图不再静默丢弃画师串
# ---------------------------------------------------------------------------
# 背景：文生图会把画师串拼进提示词最前面（nai2api_client.py），但图生图分支
# 调用 img2img.generate() 时**压根没传 artist**，而 Img2ImgClient.generate()
# 的签名里也没有这个参数 —— 于是用户一旦「回复带图的消息」发指令，画师串就
# 无声失效，出的是渠道模型自己的风格，用户以为还是 NovelAI，完全无从排查。
#
# 修法：新增 img2img_inherit_artist（默认关）：
#   关闭 → 不发送，但要记日志 + 在信息标签里标「画师串未生效」
#   开启 → 拼在提示词最前面，与文生图行为一致

# ① schema 里有这个开关，且默认 false、只在图生图开启时显示
check("img2img_inherit_artist" in schema, "_conf_schema.json 缺 img2img_inherit_artist")
_i2i_artist_cfg = schema.get("img2img_inherit_artist", {})
check(_i2i_artist_cfg.get("type") == "bool", "img2img_inherit_artist 应为 bool 类型")
check(_i2i_artist_cfg.get("default") is False,
      "img2img_inherit_artist 默认必须是 false（图生图渠道多不认 NovelAI 画师串语法）")
check(_i2i_artist_cfg.get("condition") == {"img2img_enabled": True},
      "img2img_inherit_artist 应只在开启图生图时显示")
# 必须放顶层，不能塞进 img2img 那个 object 的 items 里
check("img2img_inherit_artist" not in (schema.get("img2img", {}).get("items") or {}),
      "img2img_inherit_artist 应放在顶层，不要塞进 img2img.items")
# __init__ 要真的把它读进来（否则 schema 加了也是摆设）
check(plugin._img2img_inherit_artist is False,
      f"__init__ 应读入 img2img_inherit_artist（schema 默认 false）: {plugin._img2img_inherit_artist!r}")

# ② _build_info_label：图生图 + 画师串被丢弃 → 标签要写明
_lab_ignored = plugin._build_info_label(
    "夜景", 3.2, None, is_img2img=True, strength=0.6, artist_ignored=True)
check("画师串未生效" in _lab_ignored,
      f"图生图画师串被丢弃时标签应含「画师串未生效」: {_lab_ignored!r}")
check("图生图" in _lab_ignored and "相似度0.60" in _lab_ignored,
      f"加了提示后仍应保留图生图 / 相似度信息: {_lab_ignored!r}")
_lab_ok = plugin._build_info_label(
    "夜景", 3.2, None, is_img2img=True, strength=0.6, artist_ignored=False)
check("画师串未生效" not in _lab_ok, f"没丢弃时不应出现该提示: {_lab_ok!r}")
# 默认参数（老调用方不传 artist_ignored）也不能炸、不能凭空多出提示
_lab_default = plugin._build_info_label("夜景", 3.2, None, is_img2img=True, strength=0.6)
check("画师串未生效" not in _lab_default, f"默认参数下不应出现该提示: {_lab_default!r}")
# 文生图路径即使误传 artist_ignored 也不该出现（该文案只属于图生图）
_lab_t2i = plugin._build_info_label("夜景", 3.2, "nai-diffusion-4-5-full", artist_ignored=True)
check("画师串未生效" not in _lab_t2i, f"文生图标签不应出现图生图专属提示: {_lab_t2i!r}")


# ③ _do_generate：关 → prompt 不含画师串；开 → prompt 以画师串开头
class _FakeImg2ImgChannel:
    """假图生图渠道：只记录收到的 prompt，不发任何请求"""
    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, prompt, image_bytes, *, negative=None,
                       strength=None, noise=None, seed=None, model=None, size=None):
        self.calls.append({"prompt": prompt, "image_bytes": image_bytes,
                           "negative": negative, "model": model, "size": size})
        return b"FAKE_IMG_BYTES"


class _FakeNaiClient:
    """_do_generate 的图生图分支只会用到这两个成员"""
    default_model = "nai-diffusion-4-5-full"

    def resolve_size(self, size):
        return size or "832x1216"


class _FakeImgr:
    async def save_image(self, out):
        p = tmp / "fake_i2i_out.png"
        p.write_bytes(out if isinstance(out, bytes) else b"x")
        return p


_ref_img = tmp / "ref_src.png"
_ref_img.write_bytes(b"\x89PNG\r\n\x1a\nREF")

_orig_img2img = plugin.img2img
_orig_nai_client = plugin.client
_orig_imgr = plugin.imgr
_orig_inherit_artist = plugin._img2img_inherit_artist


def _img2img_prompt(artist, inherit):
    """跑一次图生图分支，返回真正发给渠道的 prompt"""
    chan = _FakeImg2ImgChannel()
    plugin.img2img = chan
    plugin.client = _FakeNaiClient()
    plugin.imgr = _FakeImgr()
    plugin._img2img_inherit_artist = inherit
    try:
        asyncio.run(plugin._do_generate(
            "1girl, solo, swimsuit", artist=artist, ref_image_path=str(_ref_img),
        ))
    finally:
        plugin.img2img = _orig_img2img
        plugin.client = _orig_nai_client
        plugin.imgr = _orig_imgr
    return chan.calls[0]["prompt"] if chan.calls else None


_ARTIST = "artist:wlop, 1.2::best quality::"

# 关闭（默认）→ 画师串绝不出现
p_off = _img2img_prompt(_ARTIST, False)
check(p_off == "1girl, solo, swimsuit", f"关闭继承时提示词应原样不含画师串: {p_off!r}")
check("artist:wlop" not in (p_off or ""), f"关闭继承时画师串不得泄进提示词: {p_off!r}")

# 开启 → 画师串拼在最前面，与文生图一致
p_on = _img2img_prompt(_ARTIST, True)
check(p_on is not None and p_on.startswith(_ARTIST), f"开启继承时提示词应以画师串开头: {p_on!r}")
check(p_on == f"{_ARTIST}, 1girl, solo, swimsuit", f"拼接格式应为「画师串, 提示词」: {p_on!r}")

# 画师串为空 / 全空格 / None → 两种设置下都不该产生 ", prompt" 这种脏拼接
for _empty in (None, "", "   "):
    check(_img2img_prompt(_empty, True) == "1girl, solo, swimsuit",
          f"画师串为 {_empty!r} 时不应改变提示词（开启继承）")
    check(_img2img_prompt(_empty, False) == "1girl, solo, swimsuit",
          f"画师串为 {_empty!r} 时不应改变提示词（关闭继承）")

# ④ _send_image_with_info 的标签联动：关闭继承 + 有画师串 → 出现「画师串未生效」
plugin._img2img_inherit_artist = False
ev_lab = _FakeEvent(fail_image_times=0)
asyncio.run(plugin._send_image_with_info(
    ev_lab, fake_img, "夜景", 2.5, None,
    is_img2img=True, strength=0.6, artist=_ARTIST,
))
_plain = [t for k, t in ev_lab.sent if k == "plain"]
check(bool(_plain) and "画师串未生效" in _plain[-1],
      f"图生图 + 画师串未继承时，信息标签应提示「画师串未生效」: {_plain!r}")

# 开启继承 → 标签不应再提示未生效
plugin._img2img_inherit_artist = True
ev_lab2 = _FakeEvent(fail_image_times=0)
asyncio.run(plugin._send_image_with_info(
    ev_lab2, fake_img, "夜景", 2.5, None,
    is_img2img=True, strength=0.6, artist=_ARTIST,
))
_plain2 = [t for k, t in ev_lab2.sent if k == "plain"]
check(bool(_plain2) and "画师串未生效" not in _plain2[-1],
      f"开启继承后标签不应再提示未生效: {_plain2!r}")

# 图生图但根本没配画师串 → 无需提示（没东西可丢）
plugin._img2img_inherit_artist = False
ev_lab3 = _FakeEvent(fail_image_times=0)
asyncio.run(plugin._send_image_with_info(
    ev_lab3, fake_img, "夜景", 2.5, None,
    is_img2img=True, strength=0.6, artist=None,
))
_plain3 = [t for k, t in ev_lab3.sent if k == "plain"]
check(bool(_plain3) and "画师串未生效" not in _plain3[-1],
      f"没配画师串时不该提示未生效: {_plain3!r}")

# ⑤ 文生图路径不受影响：artist 照旧交给 client.generate，标签不多话
plugin._img2img_inherit_artist = False
ev_lab4 = _FakeEvent(fail_image_times=0)
asyncio.run(plugin._send_image_with_info(
    ev_lab4, fake_img, "夜景", 2.5, "nai-diffusion-4-5-full",
    is_img2img=False, strength=None, artist=_ARTIST,
))
_plain4 = [t for k, t in ev_lab4.sent if k == "plain"]
check(bool(_plain4) and "画师串未生效" not in _plain4[-1],
      f"文生图路径不应出现「画师串未生效」: {_plain4!r}")

# 复位，避免影响后续
plugin._img2img_inherit_artist = _orig_inherit_artist

# ---------------------------------------------------------------------------
# 14. v1.5.0：默认预设（default_preset）+ --no-preset
# ---------------------------------------------------------------------------
# 背景：WebUI 预设卡片上的「设为默认画师串」只把**画师串**填进输入框，
# 附带正向词 / 附带负向词全丢；而且外层是 `if (preset.artist)`，导致
# 「只有正向/负向词、画师串为空」的预设（v1.4.2 起允许）连按钮都不出现。
#
# 改成「默认预设」机制：点一下让整个预设成为默认，三段每次生图自动全部生效。

# ① schema：存在 default_preset，type=string，default=""
check("default_preset" in schema, "_conf_schema.json 缺 default_preset")
_dp_cfg = schema.get("default_preset", {})
check(_dp_cfg.get("type") == "string", "default_preset 应为 string 类型")
check(_dp_cfg.get("default") == "", "default_preset 默认应为空串（= 不使用）")
# __init__ 要真的读进来
check(hasattr(plugin, "_default_preset"), "插件实例应有 _default_preset 属性")
check(plugin._default_preset == "", f"初始 _default_preset 应为空串: {plugin._default_preset!r}")

# ② _effective_preset 纯逻辑
_eff = plugin._effective_preset
_orig_default_preset = plugin._default_preset

# 没写 -p + 配了默认预设 → 返回默认预设名
plugin._default_preset = "动漫风"
check(_eff(None) == "动漫风", f"没写 -p 时应套用默认预设: {_eff(None)!r}")

# 写了 -p → 显式优先，覆盖默认（且 --no-preset 不压制它）
check(_eff("2.5D唯美风") == "2.5D唯美风", f"显式 -p 应覆盖默认预设: {_eff('2.5D唯美风')!r}")
check(_eff("2.5D唯美风", disabled=True) == "2.5D唯美风",
      f"显式 -p 应优先于 --no-preset: {_eff('2.5D唯美风', disabled=True)!r}")

# --no-preset（disabled=True）+ 没写 -p → None
check(_eff(None, disabled=True) is None, "--no-preset 且没写 -p 应返回 None")

# 没配默认预设 + 没写 -p → None
plugin._default_preset = ""
check(_eff(None) is None, "没配默认预设且没写 -p 应返回 None")

# 默认预设指向不存在的名字 → None 且不抛错（用户删了预设不该让生图整个坏掉）
plugin._default_preset = "这个预设根本不存在"
try:
    _r = _eff(None)
    _raised = False
except Exception as e:  # noqa: BLE001
    _r, _raised = e, True
check(not _raised, f"默认预设不存在时不应抛错，实际抛了: {_r!r}")
check(_r is None, f"默认预设不存在时应按无预设处理: {_r!r}")

# 回归护栏：默认预设是「画师串为空、只有正向/负向词」的预设时仍要命中。
# 必须用 get_entry() 判断存在性 —— get() 对这类预设返回 ""（不是 None），会漏判。
plugin.presets.save("只有正向词", "", "", positive="soft glow", negative="dark")
check(plugin.presets.get("只有正向词") == "",
      "前置：该类预设 get() 应返回空串（正是漏判的成因）")
check(plugin.presets.get_entry("只有正向词") is not None, "前置：该类预设 get_entry() 应非 None")
plugin._default_preset = "只有正向词"
check(_eff(None) == "只有正向词",
      f"画师串为空、只有正向/负向词的预设也应能当默认预设: {_eff(None)!r}")
# 端到端：默认预设的三段都要真的生效
_p, _n = plugin._apply_preset_extras(_eff(None), "1girl", None)
check("soft glow" in _p, f"默认预设的附带正向词应生效: {_p!r}")
check("dark" in (_n or ""), f"默认预设的附带负向词应生效: {_n!r}")
plugin.presets.delete("只有正向词")
plugin._default_preset = _orig_default_preset

# ③ --no-preset 解析：能被剥掉、不影响 prompt、且不会被 --artist 吃掉
_parse = plugin_main._parse_nai_command
_res = _parse("1girl --no-preset")
check(len(_res) == 11, f"_parse_nai_command 应返回 11 项: {len(_res)}")
check(_res[1] == "1girl", f"--no-preset 应从 prompt 里剥掉: {_res[1]!r}")
check(_res[10] is True, f"force_no_preset 应为 True: {_res[10]!r}")

# 关键回归：--artist 是贪婪匹配，--no-preset 必须在 _STOP_TOKENS 里，
# 否则会被当成画师串的一部分（main.py 顶部记的那个坑）
_res2 = _parse("1girl --artist artist:foo, best quality --no-preset")
check(_res2[3] == "artist:foo, best quality",
      f"--no-preset 不能被 --artist 吃掉: {_res2[3]!r}")
check("--no-preset" not in (_res2[3] or ""), f"画师串里不应残留 --no-preset: {_res2[3]!r}")
check(_res2[10] is True, f"同一句里 force_no_preset 也应为 True: {_res2[10]!r}")

# --negative 同理
_res3 = _parse("1girl --negative bad hands --no-preset")
check(_res3[4] == "bad hands", f"--no-preset 不能被 --negative 吃掉: {_res3[4]!r}")

# 不写 --no-preset → False
check(_parse("1girl")[10] is False, "没写 --no-preset 时 force_no_preset 应为 False")
# 附带的 -p 与 --no-preset 可以共存（解析层面互不干扰）
_res4 = _parse("1girl -p 动漫风 --no-preset")
check(_res4[2] == "动漫风" and _res4[10] is True,
      f"-p 与 --no-preset 应能共存: {(_res4[2], _res4[10])!r}")

# ④ 两条路径一致性护栏：/nai 指令 与 LLM 工具 都必须经过 _effective_preset
# （各写各的正是 v1.4.6 之前图生图画师串丢失的成因）
_src = (ROOT / "main.py").read_text(encoding="utf-8")
check("preset_name = self._effective_preset(preset_name, disabled=force_no_preset)" in _src,
      "「/nai 指令」路径必须经过 _effective_preset（带 --no-preset 语义）")
check('preset = self._effective_preset(preset.strip() or None) or ""' in _src,
      "「LLM 工具」路径也必须经过 _effective_preset")
# 定义 1 次 + 调用 2 次
check(_src.count("self._effective_preset(") >= 2,
      "main.py 里 _effective_preset 的调用点应至少 2 处（两条调用链）")

# ⑤ WebUI：GET config 带 default_preset，且 version 不再是写死的旧值
r = call("GET", "config")
check("default_preset" in r.body, "GET config 应返回 default_preset 字段")
check(r.body.get("default_preset") == "", f"初始 default_preset 应为空串: {r.body.get('default_preset')!r}")
_meta_ver = ""
for _ln in (ROOT / "metadata.yaml").read_text(encoding="utf-8").splitlines():
    if _ln.strip().startswith("version:"):
        _meta_ver = _ln.split(":", 1)[1].strip()
        break
check(r.body.get("version") == _meta_ver,
      f"面板 version 应来自 metadata.yaml（{_meta_ver!r}），实际 {r.body.get('version')!r}")
check(r.body.get("version") != "1.4.3",
      "面板 version 不应再是写死的旧值 1.4.3")

# ⑥ WebUI：POST preset/default 三态
_BINSYS = "动漫风"
before_saves = len(plugin_cfg.save_calls)
r = call("POST", "preset/default", {"name": _BINSYS})
check(r.status_code == 200 and r.body.get("saved") is True, f"设置默认预设失败: {r.body!r}")
check(r.body.get("default_preset") == _BINSYS, f"返回的 default_preset 不对: {r.body!r}")
check(plugin._default_preset == _BINSYS, f"内存 _default_preset 未同步: {plugin._default_preset!r}")
check(plugin_cfg.get("default_preset") == _BINSYS, f"配置未写入 default_preset: {plugin_cfg.get('default_preset')!r}")
check(len(plugin_cfg.save_calls) > before_saves, "应至少调用一次 save_config_async")
# 落盘（FakePluginConfig 会写 config_path）
_disk_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
check(_disk_cfg.get("default_preset") == _BINSYS, f"default_preset 未落盘: {_disk_cfg.get('default_preset')!r}")

# 再查 GET：应能读回刚设的默认预设
r = call("GET", "config")
check(r.body.get("default_preset") == _BINSYS, f"GET config 应读回默认预设: {r.body.get('default_preset')!r}")

# 空 name = 取消默认
r = call("POST", "preset/default", {"name": ""})
check(r.status_code == 200 and r.body.get("default_preset") == "", f"取消默认失败: {r.body!r}")
check(plugin._default_preset == "", f"取消后内存应为空串: {plugin._default_preset!r}")
check(plugin_cfg.get("default_preset") == "", f"取消后配置应为空串: {plugin_cfg.get('default_preset')!r}")
check(call("GET", "config").body.get("default_preset") == "", "取消后 GET 应为空串")

# 不存在的预设名 → 400，且**配置未被写入**（不能让配置里存一个坏名字）
r = call("POST", "preset/default", {"name": "压根不存在"})
check(r.status_code == 400, f"不存在的预设名应返回 400: {r.status_code}")
check("不存在" in str(r.body.get("message", "")), f"错误文案应说明预设不存在: {r.body!r}")
check(plugin_cfg.get("default_preset") == "", f"校验失败时不得写入配置: {plugin_cfg.get('default_preset')!r}")
check(plugin._default_preset == "", f"校验失败时不得改内存: {plugin._default_preset!r}")

# 复位
plugin._default_preset = _orig_default_preset

# ---------------------------------------------------------------------------
# 15. v1.6.0：默认画师串换成日系动漫风 + 3 个 QA 瑕疵
# ---------------------------------------------------------------------------
# 背景：用户反馈「文生图画不出我要的效果」。根因是插件自带的 default_artist
# （沿袭上游 Nai2API 的「2.5D唯美风」）其实是**写实风**（realistic /
# 1.63::photorealistic:: / 1.63::photo(medium)::），压在最前面、权重极高，
# 还锁死了 cowboy shot 构图、默认压制绿色（-2::green ::）、含一个字面量 `\n`
# 垃圾 token，整串 589 字符把用户提示词挤到几乎看不见。

from astrbot_plugin_nai_plus.core import nai2api_client as _nc  # noqa: E402
from astrbot_plugin_nai_plus.core.preset_manager import (  # noqa: E402
    BUILTIN_PRESETS as _BUILTIN,
    has_composition as _has_composition,
    split_negative_weights as _split_neg,
)

_new_artist = _nc.DEFAULT_ARTIST
_schema_artist = schema["default_artist"]["default"]

# ① 两份拷贝必须逐字节一致 —— 这是本次最容易出错的点（改一处漏一处）
check(_schema_artist == _new_artist,
      "_conf_schema.json 的 default_artist.default 必须等于 nai2api_client.DEFAULT_ARTIST"
      f"（schema={_schema_artist!r} vs const={_new_artist!r}）")

# ② 新默认串不能含写实词（防回流）
check("realistic" not in _new_artist, f"新默认串不应含 realistic: {_new_artist!r}")
check("photorealistic" not in _new_artist, f"新默认串不应含 photorealistic: {_new_artist!r}")
check("photo(medium)" not in _new_artist, f"新默认串不应含 photo(medium): {_new_artist!r}")
check("anime style" in _new_artist, f"新默认串应含 anime style（日系动漫风）: {_new_artist!r}")

# ③ 不能含字面量反斜杠+n（老串的垃圾 token，会被当标签发给 NovelAI）
check("\\n" not in _new_artist,
      f"新默认串不应含字面量反斜杠+n: {_new_artist!r}")

# ④ 不能含负权重语法（老串的 -2::green :: 会默认压制绿色）
check(_split_neg(_new_artist)[1] == "",
      f"新默认串不应含负权重: {_split_neg(_new_artist)!r}")

# ⑤ 不能含构图词 —— 保证 auto_composition 会兜底（老串的 cowboy shot 堵死了它）
check(_has_composition(_new_artist) is False,
      f"新默认串不应含构图词，否则 auto_composition 永不触发: {_new_artist!r}")

# ⑥ 老串必须完整保留在「2.5D唯美风」预设里（这是敢改默认值的前提）
_old = _BUILTIN["2.5D唯美风"]["artist"]
check("realistic" in _old, "「2.5D唯美风」预设必须仍是老的写实串（别顺手统一改掉）")
check(len(_old) > 400, f"「2.5D唯美风」预设应是完整老串（589 字符左右），实际 {len(_old)}")
check("misaka_12003-gou" in _old, "老串里的画师名应还在（确认没被截断）")

# ⑦ N1：近义词组里不能再有跨标签别名（永远命中不了的死条目）
_flat_syn = [t for g in tm._SYNONYM_GROUPS for t in g]
check("1girl, solo" not in _flat_syn,
      f"_SYNONYM_GROUPS 不应含跨标签别名 '1girl, solo'（死条目）: {tm._SYNONYM_GROUPS}")
check(all("," not in t for t in _flat_syn),
      f"_SYNONYM_GROUPS 每一项都必须是单个标签，不能含逗号: {_flat_syn}")
# solo 和 1girl 是不同概念，都不该被折叠掉
check(tm._collapse_synonyms("solo, 1girl") == "solo, 1girl",
      "solo 与 1girl 不应互相折叠")
check(tm._collapse_synonyms("1girl, solo") == "1girl, solo",
      "1girl 与 solo 不应互相折叠")

# ⑧ N2：非规范人数写法都不能被误补 solo（否则产出矛盾串）
# 断言「原样返回」而不是「不以 solo 开头」—— 因为 `solo focus` 这种输入
# 本身就以 solo 开头，以「开头」判断会误伤（写成那样时这条会假失败）。
_CT = {"kamisato ayaka", "raiden shogun", "saber", "artoria pendragon"}
for _variant in ("7girls", "1girls", "1 girl", "6+ girls", "6+girls", "8girls",
                 "3+ boys", "2others", "multiple  girls", "multiple girls",
                 "no human", "no humans", "solo focus", "solo",
                 "{{1girl}}", "1.2::1girl::"):
    _text = f"{_variant}, kamisato ayaka"
    _out = esc(_text, _CT)
    check(_out == _text,
          f"已有非规范人数标签 {_variant!r} 时应原样返回（不补 solo）: {_out!r}")

# ⑨ N2 回归：不能把正常情况改坏
check(esc("kamisato ayaka, swimsuit, beach", _CT) == "solo, kamisato ayaka, swimsuit, beach",
      "单个角色仍应补 solo")
check(esc("kamisato ayaka, raiden shogun", _CT) == "kamisato ayaka, raiden shogun",
      "两个角色仍不应补 solo")
check(esc("beach, sunset, ocean", _CT) == "beach, sunset, ocean",
      "纯风景仍不应补 solo")
check(tm._is_subject_count_tag("") is False, "空标签不是人数标签")
check(tm._is_subject_count_tag("kamisato ayaka") is False, "角色名不是人数标签")

# ⑩ N3：dictionary=None 不能抛异常（容错底线）
_t3 = tm.TranslateManager(
    {"translate_enabled": True, "translate_mode": "astrbot",
     "translate_dictionary_enabled": True, "translate_quality_weight": True},
    context=None,
)
_t3.dictionary = None
try:
    _n3_out = asyncio.run(_t3.translate("原神神里绫华穿着泳衣"))
    _n3_raised = None
except Exception as e:  # noqa: BLE001
    _n3_out, _n3_raised = None, e
check(_n3_raised is None, f"dictionary=None 时 translate() 不应抛异常，实际: {_n3_raised!r}")
check(_n3_out == "原神神里绫华穿着泳衣", f"dictionary=None 时应原样返回输入: {_n3_out!r}")
check(_t3.last_stats.get("dict_unavailable") is True,
      f"应记下 dict_unavailable 标记: {_t3.last_stats!r}")

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

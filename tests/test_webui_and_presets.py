"""
AstrBot NAI Plus - WebUI & Three-Segment Preset Test Suite
Comprehensive verification for:
1. PresetManager (three-segment: artist/positive/negative, default preset, template_list)
2. Tag helper functions (split_negative_weights, ensure_composition, merge_tags)
3. WebUI API endpoints (config, preset/default, presets, preview)
4. Command parsing and generation resolution (--no-preset, default_preset fallback)
5. Static WebUI page configuration and schema integrity
"""

import os
import sys
import json
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Ensure parent directory of plugin is in sys.path so astrbot_plugin_nai_plus is recognized as a package
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PARENT_DIR = PLUGIN_ROOT.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Setup dummy mock modules for AstrBot and MCP
mock_web_routes = {}

def mock_register_web_api(route, handler, methods, desc):
    mock_web_routes[route] = (handler, methods)

class DummyStar:
    """Mock Star base class for AstrBot plugins."""
    def __init__(self, context=None):
        self.context = context

mock_filter = MagicMock()
mock_filter.command = lambda *a, **k: (lambda fn: fn)
mock_filter.command_group = lambda *a, **k: (lambda fn: fn)

mock_astrbot = MagicMock()
mock_astrbot.api = MagicMock()
mock_astrbot.api.logger = MagicMock()
mock_astrbot.api.event = MagicMock()
mock_astrbot.api.event.AstrMessageEvent = MagicMock
mock_astrbot.api.event.filter = mock_filter
mock_astrbot.api.message_components = MagicMock()
mock_astrbot.api.message_components.Node = MagicMock
mock_astrbot.api.message_components.Plain = MagicMock
mock_astrbot.api.star = MagicMock()
mock_astrbot.api.star.Context = MagicMock
mock_astrbot.api.star.Star = DummyStar
mock_astrbot.api.star.StarTools = MagicMock()
mock_astrbot.api.web = MagicMock()
mock_astrbot.api.web.register_web_api = mock_register_web_api
mock_astrbot.api.web.error_response = lambda msg, status_code=400: {"error": msg, "code": status_code}
mock_astrbot.api.web.json_response = lambda data: data
mock_astrbot.api.web.request = MagicMock()
mock_astrbot.core = MagicMock()
mock_astrbot.core.star = MagicMock()
mock_astrbot.core.star.filter = mock_filter
mock_astrbot.core.star.filter.command = mock_filter.command
mock_astrbot.core.star.filter.command.GreedyStr = str

sys.modules["astrbot"] = mock_astrbot
sys.modules["astrbot.api"] = mock_astrbot.api
sys.modules["astrbot.api.logger"] = mock_astrbot.api.logger
sys.modules["astrbot.api.event"] = mock_astrbot.api.event
sys.modules["astrbot.api.message_components"] = mock_astrbot.api.message_components
sys.modules["astrbot.api.star"] = mock_astrbot.api.star
sys.modules["astrbot.api.web"] = mock_astrbot.api.web
sys.modules["astrbot.core"] = mock_astrbot.core
sys.modules["astrbot.core.star"] = mock_astrbot.core.star
sys.modules["astrbot.core.star.filter"] = mock_astrbot.core.star.filter
sys.modules["astrbot.core.star.filter.command"] = mock_astrbot.core.star.filter.command
sys.modules.setdefault("mcp", MagicMock())

# Import plugin modules
from astrbot_plugin_nai_plus.core.preset_manager import (
    BUILTIN_PRESETS,
    PresetManager,
    split_negative_weights,
    ensure_composition,
    has_composition,
    merge_tags,
    parse_webui_presets,
)


class TestTagHelpers(unittest.TestCase):
    """Test tag and prompt processing helper functions."""

    def test_split_negative_weights(self):
        raw = "1.25::artist:aaa::, -2::green hair::, score_9, -1.0::bad hands::"
        artist, neg = split_negative_weights(raw)
        self.assertIn("1.25::artist:aaa::", artist)
        self.assertIn("score_9", artist)
        self.assertNotIn("-2::green hair::", artist)
        self.assertNotIn("-1.0::bad hands::", artist)
        self.assertIn("-2::green hair::", neg)
        self.assertIn("-1.0::bad hands::", neg)

        raw_clean = "1.25::artist:aaa::, score_9, best quality"
        artist_clean, neg_clean = split_negative_weights(raw_clean)
        self.assertEqual(artist_clean, raw_clean)
        self.assertEqual(neg_clean, "")

        a_empty, n_empty = split_negative_weights("")
        self.assertEqual(a_empty, "")
        self.assertEqual(n_empty, "")

    def test_composition_detection_and_fallback(self):
        prompt1 = "1girl, solo, smile, blue eyes"
        self.assertFalse(has_composition(prompt1))
        fixed1, added1 = ensure_composition(prompt1)
        self.assertTrue(added1)
        self.assertTrue(has_composition(fixed1))
        self.assertIn("full body", fixed1)
        self.assertIn("standing", fixed1)

        prompt2 = "1girl, solo, upper body, smile"
        self.assertTrue(has_composition(prompt2))
        fixed2, added2 = ensure_composition(prompt2)
        self.assertFalse(added2)
        self.assertEqual(prompt2, fixed2)

        prompt3 = "1girl, solo, cowboy shot, looking at viewer"
        self.assertTrue(has_composition(prompt3))
        fixed3, added3 = ensure_composition(prompt3)
        self.assertFalse(added3)
        self.assertEqual(prompt3, fixed3)

    def test_merge_tags(self):
        result = merge_tags("1girl, solo", "masterpiece, blue hair")
        self.assertEqual(result, "1girl, solo, masterpiece, blue hair")
        self.assertEqual(merge_tags("", ""), "")
        self.assertEqual(merge_tags("1girl", ""), "1girl")
        self.assertEqual(merge_tags("", "1girl"), "1girl")


class TestPresetManager(unittest.TestCase):
    """Test PresetManager core logic and three-segment structure."""

    def setUp(self):
        self.test_dir = PLUGIN_ROOT / "tests" / "tmp_data"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        presets_file = self.test_dir / "presets.json"
        if presets_file.exists():
            presets_file.unlink()

    def tearDown(self):
        presets_file = self.test_dir / "presets.json"
        if presets_file.exists():
            presets_file.unlink()
        if self.test_dir.exists():
            try:
                self.test_dir.rmdir()
            except Exception:
                pass

    def test_builtin_presets_structure(self):
        self.assertGreaterEqual(len(BUILTIN_PRESETS), 5)
        for name, p in BUILTIN_PRESETS.items():
            self.assertIn("artist", p)
            self.assertIn("positive", p)
            self.assertIn("negative", p)
            self.assertIn("desc", p)
            self.assertTrue(isinstance(p["artist"], str))
            self.assertTrue(isinstance(p["positive"], str))
            self.assertTrue(isinstance(p["negative"], str))

    def test_init_with_webui_presets(self):
        webui_presets = [
            {
                "__template_key": "preset",
                "name": "赛博朋克风",
                "artist": "1.3::artist:cyber::",
                "positive": "neon lights, high tech, futuristic city",
                "negative": "lowres, retro",
                "desc": "赛博霓虹风格",
            }
        ]
        pm = PresetManager(self.test_dir, webui_presets=webui_presets)
        p = pm.get_entry("赛博朋克风")
        self.assertIsNotNone(p)
        self.assertEqual(p["artist"], "1.3::artist:cyber::")
        self.assertEqual(p["positive"], "neon lights, high tech, futuristic city")
        self.assertEqual(p["negative"], "lowres, retro")
        self.assertEqual(p["desc"], "赛博霓虹风格")

    def test_export_for_webui(self):
        pm = PresetManager(self.test_dir)
        exported = pm.export_for_webui(default_preset="2.5D唯美风")
        self.assertGreaterEqual(len(exported), 5)

        names = [item["name"] for item in exported]
        self.assertIn("2.5D唯美风", names)

        found_default = False
        for item in exported:
            self.assertIn("is_builtin", item)
            self.assertIn("is_default", item)
            self.assertIn("positive", item)
            self.assertIn("negative", item)
            if item["name"] == "2.5D唯美风":
                self.assertTrue(item["is_default"])
                self.assertTrue(item["is_builtin"])
                found_default = True
            else:
                self.assertFalse(item["is_default"])
        self.assertTrue(found_default)

    def test_save_and_delete_custom_preset(self):
        pm = PresetManager(self.test_dir)
        pm.save(
            name="水彩风",
            artist="1.2::artist:watercolor_master::",
            desc="清透水彩手绘风",
            positive="watercolor, soft edges, paper texture",
            negative="sharp outlines, 3d render",
        )
        saved = pm.get_entry("水彩风")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["positive"], "watercolor, soft edges, paper texture")
        self.assertEqual(saved["negative"], "sharp outlines, 3d render")

        t_list = pm.export_template_list()
        self.assertEqual(len(t_list), 1)
        self.assertEqual(t_list[0]["name"], "水彩风")
        self.assertEqual(t_list[0]["__template_key"], "preset")

        # Custom preset can be deleted
        self.assertTrue(pm.delete("水彩风"))
        self.assertIsNone(pm.get_entry("水彩风"))

        # Builtin preset cannot be deleted
        self.assertFalse(pm.delete("2.5D唯美风"))


class TestWebUIApiEndpoints(unittest.IsolatedAsyncioTestCase):
    """Test WebUI API route handlers via simulated requests."""

    def setUp(self):
        mock_web_routes.clear()
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin
        
        self.config = {
            "api_url": "https://nai.sta1n.cn",
            "token": "mock_token",
            "default_model": "nai-diffusion-4-5-full",
            "default_size": "竖图",
            "default_steps": 28,
            "default_scale": 6.0,
            "default_sampler": "k_dpmpp_2m_sde",
            "default_artist": "1.25::artist:sample::",
            "default_negative": "lowres, bad anatomy",
            "default_preset": "动漫风",
            "custom_presets": [
                {
                    "name": "像素风",
                    "artist": "1.0::artist:pixel_art::",
                    "positive": "pixel art, 16-bit, retro game",
                    "negative": "photorealistic, 3d",
                    "desc": "经典像素风格",
                }
            ],
        }

        mock_context = MagicMock()
        mock_context.register_web_api = mock_register_web_api
        data_dir = PLUGIN_ROOT / "tests" / "tmp_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        mock_astrbot.api.star.StarTools.get_data_dir.return_value = data_dir

        self.plugin = Nai2ApiPlugin(mock_context, self.config)
        self.mock_web_routes = mock_web_routes

    def tearDown(self):
        import shutil
        data_dir = PLUGIN_ROOT / "tests" / "tmp_data"
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_routes_registered(self):
        expected_routes = [
            "config",
            "preset/default",
            "presets",
            "preview",
        ]
        for route in expected_routes:
            self.assertIn(route, self.mock_web_routes, f"Route {route} not registered")

    async def test_api_get_config(self):
        handler, methods = self.mock_web_routes["config"]
        self.assertIn("GET", methods)

        req = MagicMock()
        resp = await handler(req)
        data = resp
        if hasattr(resp, "body"):
            data = json.loads(resp.body.decode("utf-8"))
        elif hasattr(resp, "json"):
            data = resp.json() if callable(resp.json) else resp.json

        self.assertEqual(data["default_preset"], "动漫风")
        presets = data["presets"]
        names = [p["name"] for p in presets]
        self.assertIn("像素风", names)
        self.assertIn("动漫风", names)

    async def test_api_set_default_preset(self):
        handler, methods = self.mock_web_routes["preset/default"]
        self.assertIn("POST", methods)

        req = MagicMock()
        req.json = AsyncMock(return_value={"preset": "像素风"})
        resp = await handler(req)
        data = resp if isinstance(resp, dict) else json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(self.plugin._default_preset, "像素风")

        req.json = AsyncMock(return_value={"preset": ""})
        resp = await handler(req)
        data = resp if isinstance(resp, dict) else json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(self.plugin._default_preset, "")

    async def test_api_presets_crud(self):
        handler, methods = self.mock_web_routes["presets"]
        self.assertIn("POST", methods)

        # Add
        req = MagicMock()
        req.json = AsyncMock(
            return_value={
                "action": "add",
                "name": "极简线稿",
                "artist": "1.2::artist:lineart::",
                "positive": "monochrome, lineart, clean lines",
                "negative": "colored, shaded",
                "desc": "黑白极简线稿",
            }
        )
        resp = await handler(req)
        data = resp if isinstance(resp, dict) else json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertIsNotNone(self.plugin.presets.get_entry("极简线稿"))

        # Update
        req.json = AsyncMock(
            return_value={
                "action": "update",
                "name": "极简线稿",
                "artist": "1.3::artist:lineart::",
                "positive": "monochrome, lineart, clean lines, minimalist",
                "negative": "colored, shaded, realistic",
                "desc": "升级版黑白极简线稿",
            }
        )
        resp = await handler(req)
        data = resp if isinstance(resp, dict) else json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
        self.assertTrue(data.get("ok"))
        updated = self.plugin.presets.get_entry("极简线稿")
        self.assertIn("minimalist", updated["positive"])

        # Delete
        req.json = AsyncMock(
            return_value={
                "action": "delete",
                "name": "极简线稿",
            }
        )
        resp = await handler(req)
        data = resp if isinstance(resp, dict) else json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertIsNone(self.plugin.presets.get_entry("极简线稿"))

    async def test_api_preview(self):
        handler, methods = self.mock_web_routes["preview"]
        self.assertIn("POST", methods)

        req = MagicMock()
        req.json = AsyncMock(
            return_value={
                "prompt": "1girl, solo, smile",
                "preset": "像素风",
                "artist": "",
                "negative": "",
            }
        )
        resp = await handler(req)
        data = resp if isinstance(resp, dict) else json.loads(getattr(resp, "body", b"{}").decode("utf-8"))
        self.assertTrue(data.get("ok"))
        result = data
        self.assertEqual(result["artist"], "1.0::artist:pixel_art::")
        self.assertIn("1girl", result["tag"])
        self.assertIn("pixel art", result["tag"])
        self.assertIn("full body", result["tag"])
        self.assertIn("lowres", result["negative"])
        self.assertIn("photorealistic", result["negative"])

    async def test_do_generate_and_save_image(self):
        # Mock client generate returning dummy PNG bytes
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        self.plugin.client.generate = AsyncMock(return_value=fake_png)

        # Call _do_generate
        path = await self.plugin._do_generate("1girl, smiling", size="竖图", artist="1.2::artist:test::")
        self.assertTrue(path.exists())
        self.assertEqual(path.suffix, ".png")
        self.assertEqual(path.read_bytes(), fake_png)
        # Clean up
        path.unlink(missing_ok=True)


class TestCommandParsingAndResolution(unittest.TestCase):
    """Test command flag parsing and default preset resolution."""

    def setUp(self):
        from astrbot_plugin_nai_plus.main import _parse_nai_command
        self.parse = _parse_nai_command

    def test_parse_flags(self):
        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "竖图 1girl, smiling -p 动漫风 --artist 1.2::artist:test:: --negative bad hands --seed 9999"
        )
        self.assertEqual(size, "竖图")
        self.assertEqual(preset_name, "动漫风")
        self.assertEqual(seed, 9999)
        self.assertEqual(artist, "1.2::artist:test::")
        self.assertEqual(neg, "bad hands")
        self.assertEqual(prompt, "1girl, smiling")
        self.assertFalse(no_preset)
        self.assertIsNone(model)

    def test_parse_no_preset(self):
        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "1girl, solo --no-preset"
        )
        self.assertTrue(no_preset)
        self.assertEqual(prompt, "1girl, solo")
        self.assertIsNone(preset_name)
        self.assertIsNone(model)

    def test_parse_model_flags(self):
        # Short flag -m with alias "5"
        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "1girl -m 5"
        )
        self.assertEqual(model, "nai-diffusion-5-full")
        self.assertEqual(prompt, "1girl")

        # Long flag --model with alias "5c"
        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "1girl --model 5c"
        )
        self.assertEqual(model, "nai-diffusion-5-curated")
        self.assertEqual(prompt, "1girl")

        # Combined flags
        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "横图 1girl -m v5 --artist 1.2::artist:test:: --negative bad hands -p 动漫风"
        )
        self.assertEqual(size, "横图")
        self.assertEqual(model, "nai-diffusion-5-full")
        self.assertEqual(artist, "1.2::artist:test::")
        self.assertEqual(neg, "bad hands")
        self.assertEqual(preset_name, "动漫风")
        self.assertEqual(prompt, "1girl")

    def test_clean_param_val_and_angle_brackets(self):
        from astrbot_plugin_nai_plus.main import _clean_param_val

        # Direct cleaner tests
        self.assertEqual(_clean_param_val("<5>"), "5")
        self.assertEqual(_clean_param_val("<动漫风>"), "动漫风")
        self.assertEqual(_clean_param_val("《动漫风》"), "动漫风")
        self.assertEqual(_clean_param_val("【动漫风】"), "动漫风")
        self.assertEqual(_clean_param_val('"动漫风"'), "动漫风")
        self.assertEqual(_clean_param_val("'动漫风'"), "动漫风")
        self.assertEqual(_clean_param_val("动漫风,"), "动漫风")
        self.assertEqual(_clean_param_val("5，"), "5")
        self.assertIsNone(_clean_param_val(None))
        self.assertIsNone(_clean_param_val(""))
        self.assertIsNone(_clean_param_val("  "))

        # Parsing with angle brackets in -m and -p
        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "1girl -m <5>"
        )
        self.assertEqual(model, "nai-diffusion-5-full")
        self.assertEqual(prompt, "1girl")

        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "1girl -p <动漫风>"
        )
        self.assertEqual(preset_name, "动漫风")
        self.assertEqual(prompt, "1girl")

        size, prompt, preset_name, artist, neg, seed, no_preset, model = self.parse(
            "1girl -m <5c> -p 《像素风》"
        )
        self.assertEqual(model, "nai-diffusion-5-curated")
        self.assertEqual(preset_name, "像素风")
        self.assertEqual(prompt, "1girl")


class TestStandaloneCommandsAndGuidance(unittest.IsolatedAsyncioTestCase):
    """Test standalone -m / -p / model commands and prompt guidance."""

    async def test_standalone_commands_and_guidance(self):
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin

        # Create plugin instance
        mock_context = MagicMock()
        mock_context.register_web_api = mock_register_web_api
        data_dir = PLUGIN_ROOT / "tests" / "tmp_data_cmd"
        data_dir.mkdir(parents=True, exist_ok=True)
        mock_astrbot.api.star.StarTools.get_data_dir.return_value = data_dir

        config = {
            "token": "dummy",
            "server_url": "http://127.0.0.1:8000",
            "default_preset": "",
            "default_model": "nai-diffusion-4-5-full",
            "presets": [
                {
                    "name": "动漫风",
                    "artist": "1.2::artist:test::",
                    "positive": "masterpiece",
                    "negative": "lowres",
                }
            ],
        }
        plugin = Nai2ApiPlugin(mock_context, config)

        # Helper to create mock event
        def make_event():
            event = MagicMock()
            event.plain_result = lambda text: text
            return event

        # 1. Standalone /nai -m 5 -> switches default model
        res = await plugin.nai_cmd(make_event(), "-m 5")
        self.assertIn("已将默认生图模型切换为", res)
        self.assertEqual(plugin.client.default_model, "nai-diffusion-5-full")

        # 2. Standalone /nai model 5c -> switches default model to curated
        res = await plugin.nai_cmd(make_event(), "model 5c")
        self.assertIn("已将默认生图模型切换为", res)
        self.assertEqual(plugin.client.default_model, "nai-diffusion-5-curated")

        # 3. /nai model (no arg) -> shows current model and switch guide
        res = await plugin.nai_cmd(make_event(), "model")
        self.assertIn("当前默认模型", res)
        self.assertIn("/nai model 5", res)

        # 4. Standalone /nai -p 动漫风 -> switches default preset
        res = await plugin.nai_cmd(make_event(), "-p 动漫风")
        self.assertIn("已将【动漫风】设为默认预设", res)
        self.assertEqual(plugin._default_preset, "动漫风")

        # 5. /nai default 动漫风 -> switches default preset
        res = await plugin.nai_cmd(make_event(), "default 动漫风")
        self.assertIn("已将【动漫风】设为默认预设", res)

        # 6. /nai default <动漫风> -> cleans angle brackets
        res = await plugin.nai_cmd(make_event(), "default <动漫风>")
        self.assertIn("已将【动漫风】设为默认预设", res)

        # 7. Empty prompt guidance in _handle_generate
        res = await plugin._handle_generate(make_event(), "-p 动漫风")
        self.assertIn("已识别到预设【动漫风】", res)
        self.assertIn("若要以此预设单次生图", res)

        res = await plugin._handle_generate(make_event(), "-m 5")
        self.assertIn("已识别到模型", res)
        self.assertIn("若要以此模型生图", res)

        # Clean up
        import shutil
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)


class TestGreedyStrRegressionGuard(unittest.TestCase):
    """回归守卫：防止有人把 nai_cmd 的 args 参数写回默认值。

    AstrBot 的 CommandFilter 在参数带默认值时会把注解类型换成默认值本身，
    导致 GreedyStr 失效、只传第一个词（历史上 '#nai -m 5 1girl' 只收到 '-m'）。
    本测试即锁死这一约束，任何人改回 `args: GreedyStr = ""` 都会失败。
    """

    def test_nai_cmd_args_has_no_default(self):
        import inspect
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin

        sig = inspect.signature(Nai2ApiPlugin.nai_cmd)
        self.assertIn("args", sig.parameters, "nai_cmd 必须有 args 参数")
        args_param = sig.parameters["args"]
        # 不依赖被 mock 掉的 GreedyStr 类型，只断言「没有默认值」这一关键约束
        self.assertIs(
            args_param.default,
            inspect.Parameter.empty,
            "nai_cmd 的 args 不能有默认值！带默认值会让 AstrBot 把 GreedyStr 当普通 str，"
            "只传第一个词（历史上 '#nai -m 5 1girl' 只收到 '-m'）。",
        )
        # 注解也不应为空（必须保留类型注解）
        self.assertIsNot(args_param.annotation, inspect.Parameter.empty)


class TestTruncationRecoveryEndToEnd(unittest.IsolatedAsyncioTestCase):
    """端到端复现：模拟框架把 args 截断为 '-m'，兜底应从原始消息还原完整参数。"""

    def setUp(self):
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin

        mock_context = MagicMock()
        mock_context.register_web_api = mock_register_web_api
        self.data_dir = PLUGIN_ROOT / "tests" / "tmp_data_e2e"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        mock_astrbot.api.star.StarTools.get_data_dir.return_value = self.data_dir

        self.plugin = Nai2ApiPlugin(
            mock_context,
            {"token": "dummy", "default_preset": "", "default_model": "nai-diffusion-4-5-full"},
        )
        # 隔离生图链路，避免真实 IO / 网络
        self.plugin.client.generate = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        self.plugin.imgr.save_image = AsyncMock(return_value=Path("fake.png"))
        self.plugin._send_image_with_info = AsyncMock()

    def tearDown(self):
        import shutil
        if self.data_dir.exists():
            shutil.rmtree(self.data_dir, ignore_errors=True)

    async def test_truncated_args_recovered_from_raw_message(self):
        event = MagicMock()
        event.message_str = "#nai -m 5 1girl"
        event.get_message_str = lambda: "#nai -m 5 1girl"

        # 模拟框架 bug：只把第一个词 '-m' 传进来
        await self.plugin.nai_cmd(event, "-m")

        self.assertGreaterEqual(self.plugin.client.generate.await_count, 1,
                                "应从原始消息还原完整参数并触发生图")
        call = self.plugin.client.generate.await_args
        prompt_arg = call.args[0]
        self.assertIn("1girl", prompt_arg)
        self.assertNotIn("-m", prompt_arg)
        self.assertEqual(call.kwargs.get("model"), "nai-diffusion-5-full")

    async def test_normal_args_not_overridden_by_recovery(self):
        event = MagicMock()
        event.message_str = "#nai -m 5 1girl"
        event.get_message_str = lambda: "#nai -m 5 1girl"

        # args 完整时，兜底不应产生副作用
        await self.plugin.nai_cmd(event, "-m 5 1girl")
        call = self.plugin.client.generate.await_args
        self.assertEqual(call.kwargs.get("model"), "nai-diffusion-5-full")
        self.assertIn("1girl", call.args[0])
        self.assertNotIn("-m", call.args[0])


class TestPresetAliasResolution(unittest.TestCase):
    """P1-4：预设名别名解析（输入 → 真实全名）。"""

    def test_resolve_preset_name_builtin_aliases(self):
        from astrbot_plugin_nai_plus.core.preset_manager import (
            resolve_preset_name,
            BUILTIN_PRESETS,
        )
        names = list(BUILTIN_PRESETS.keys())

        self.assertEqual(resolve_preset_name("韩漫风", names), "韩漫小清新风")
        self.assertEqual(resolve_preset_name("韩漫", names), "韩漫小清新风")
        self.assertEqual(resolve_preset_name("小清新", names), "韩漫小清新风")
        self.assertEqual(resolve_preset_name("小清新风", names), "韩漫小清新风")
        self.assertEqual(resolve_preset_name("本子风", names), "本子动漫风")
        self.assertEqual(resolve_preset_name("gal", names), "GalGame风")
        self.assertEqual(resolve_preset_name("galgame", names), "GalGame风")
        self.assertEqual(resolve_preset_name("唯美风", names), "2.5D唯美风")
        self.assertEqual(resolve_preset_name("半写实", names), "2.5D唯美风")
        self.assertEqual(resolve_preset_name("动漫", names), "动漫风")
        # 精确名原样返回
        self.assertEqual(resolve_preset_name("动漫风", names), "动漫风")
        # 不存在的名字返回 None（禁止静默降级）
        self.assertIsNone(resolve_preset_name("完全不存在的风格名", names))
        self.assertIsNone(resolve_preset_name("", names))
        self.assertIsNone(resolve_preset_name(None, names))

    def test_resolve_does_not_create_or_override(self):
        """别名解析只做映射，不改动预设集合本身。"""
        from astrbot_plugin_nai_plus.core.preset_manager import PresetManager
        tmp = PLUGIN_ROOT / "tests" / "tmp_alias"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            pm = PresetManager(tmp)
            before = set(pm.list_all().keys())
            self.assertEqual(pm.resolve("韩漫风"), "韩漫小清新风")
            self.assertIsNone(pm.resolve("根本没有这个预设"))
            after = set(pm.list_all().keys())
            self.assertEqual(before, after)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestPresetAliasInCommand(unittest.IsolatedAsyncioTestCase):
    """P1-4：指令层接入别名解析 + 报错列出可用预设。"""

    def setUp(self):
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin

        mock_context = MagicMock()
        mock_context.register_web_api = mock_register_web_api
        self.data_dir = PLUGIN_ROOT / "tests" / "tmp_data_alias"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        mock_astrbot.api.star.StarTools.get_data_dir.return_value = self.data_dir

        self.plugin = Nai2ApiPlugin(mock_context, {"token": "dummy", "default_preset": ""})
        self.plugin.client.generate = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
        self.plugin.imgr.save_image = AsyncMock(return_value=Path("fake.png"))
        self.plugin._send_image_with_info = AsyncMock()

    def tearDown(self):
        import shutil
        if self.data_dir.exists():
            shutil.rmtree(self.data_dir, ignore_errors=True)

    async def test_alias_preset_used_in_generate(self):
        event = MagicMock()
        event.message_str = "#nai -p 韩漫风 1girl"
        event.get_message_str = lambda: "#nai -p 韩漫风 1girl"

        await self.plugin._handle_generate(event, "-p 韩漫风 1girl")
        self.assertEqual(self.plugin.client.generate.await_count, 1)
        call = self.plugin.client.generate.await_args
        self.assertEqual(call.kwargs.get("artist"), BUILTIN_PRESETS["韩漫小清新风"]["artist"])

    async def test_unknown_preset_error_lists_available(self):
        event = MagicMock()
        event.plain_result = lambda text: text
        event.message_str = "#nai -p 完全不存在的风格名 1girl"
        event.get_message_str = lambda: "#nai -p 完全不存在的风格名 1girl"

        res = await self.plugin._handle_generate(event, "-p 完全不存在的风格名 1girl")
        self.assertIsInstance(res, str)
        self.assertIn("不存在", res)
        # 报错里应直接列出可用预设名
        self.assertIn("动漫风", res)
        self.assertIn("韩漫小清新风", res)


class TestVersionCommand(unittest.IsolatedAsyncioTestCase):
    """P0-3：/nai version 版本自检。"""

    def setUp(self):
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin

        mock_context = MagicMock()
        mock_context.register_web_api = mock_register_web_api
        self.data_dir = PLUGIN_ROOT / "tests" / "tmp_data_ver"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        mock_astrbot.api.star.StarTools.get_data_dir.return_value = self.data_dir

        self.plugin = Nai2ApiPlugin(mock_context, {"token": "dummy"})

    def tearDown(self):
        import shutil
        if self.data_dir.exists():
            shutil.rmtree(self.data_dir, ignore_errors=True)

    async def test_handle_version_contains_version(self):
        import re as _re

        event = MagicMock()
        event.plain_result = lambda text: text

        res = await self.plugin._handle_version(event)
        self.assertIn("插件版本", res)
        self.assertIn("当前默认模型", res)
        self.assertIn("当前默认预设", res)
        self.assertIn(f"v{self.plugin._plugin_version}", res)

        # 版本号应与 metadata.yaml 一致
        meta = (PLUGIN_ROOT / "metadata.yaml").read_text(encoding="utf-8")
        m = _re.search(r"version:\s*([^\s]+)", meta)
        self.assertIsNotNone(m)
        self.assertEqual(self.plugin._plugin_version, m.group(1))

    async def test_version_dispatched_from_nai_cmd(self):
        event = MagicMock()
        event.plain_result = lambda text: text

        res_en = await self.plugin.nai_cmd(event, "version")
        self.assertIn(f"v{self.plugin._plugin_version}", res_en)

        res_cn = await self.plugin.nai_cmd(event, "版本")
        self.assertIn(f"v{self.plugin._plugin_version}", res_cn)


class TestModelResolutionAndV5Detection(unittest.TestCase):
    """Test model alias resolution and V5 model detection."""

    def test_resolve_model_alias(self):
        from astrbot_plugin_nai_plus.core.nai2api_client import (
            resolve_model_alias,
            MODEL_V5_FULL,
            MODEL_V5_CURATED,
        )

        # V5 aliases
        for alias in ["5", "v5", "5full", "5-full", "v5full", "v5-full", "nai-diffusion-5-full"]:
            self.assertEqual(resolve_model_alias(alias), MODEL_V5_FULL)
            self.assertEqual(resolve_model_alias(alias.upper()), MODEL_V5_FULL)

        for alias in ["5c", "v5c", "5curated", "5-curated", "v5curated", "v5-curated", "nai-diffusion-5-curated"]:
            self.assertEqual(resolve_model_alias(alias), MODEL_V5_CURATED)
            self.assertEqual(resolve_model_alias(alias.upper()), MODEL_V5_CURATED)

        # Older models
        self.assertEqual(resolve_model_alias("4.5"), "nai-diffusion-4-5-full")
        self.assertEqual(resolve_model_alias("v4.5"), "nai-diffusion-4-5-full")
        self.assertEqual(resolve_model_alias("4"), "nai-diffusion-4-full")
        self.assertEqual(resolve_model_alias("3"), "nai-diffusion-3")
        self.assertEqual(resolve_model_alias("furry"), "nai-diffusion-furry-3")
        self.assertEqual(resolve_model_alias("safe"), "safe-diffusion")

        # Unknown or None
        self.assertEqual(resolve_model_alias("custom-model-x"), "custom-model-x")
        self.assertIsNone(resolve_model_alias(None))
        self.assertIsNone(resolve_model_alias(""))

    def test_is_v5_model(self):
        from astrbot_plugin_nai_plus.core.nai2api_client import (
            is_v5_model,
            MODEL_V5_FULL,
            MODEL_V5_CURATED,
        )

        self.assertTrue(is_v5_model(MODEL_V5_FULL))
        self.assertTrue(is_v5_model(MODEL_V5_CURATED))
        self.assertTrue(is_v5_model("5"))
        self.assertTrue(is_v5_model("v5"))
        self.assertTrue(is_v5_model("5c"))
        self.assertTrue(is_v5_model("v5-curated"))

        self.assertFalse(is_v5_model("nai-diffusion-4-5-full"))
        self.assertFalse(is_v5_model("4.5"))
        self.assertFalse(is_v5_model("3"))
        self.assertFalse(is_v5_model(None))
        self.assertFalse(is_v5_model(""))


class TestConfigAndSchemaIntegrity(unittest.TestCase):
    """Test _conf_schema.json and i18n / pages configuration."""

    def test_conf_schema_keys(self):
        schema_path = PLUGIN_ROOT / "_conf_schema.json"
        self.assertTrue(schema_path.exists())
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        self.assertIn("default_preset", schema)
        self.assertIn("custom_presets", schema)
        self.assertEqual(schema["custom_presets"]["type"], "template_list")
        template = schema["custom_presets"]["template"]["preset"]
        self.assertIn("name", template)
        self.assertIn("artist", template)
        self.assertIn("positive", template)
        self.assertIn("negative", template)
        self.assertIn("desc", template)

        # default_model V5 options verification
        self.assertIn("default_model", schema)
        options = schema["default_model"]["options"]
        self.assertGreaterEqual(len(options), 2)
        self.assertEqual(options[0], "nai-diffusion-5-full")
        self.assertEqual(options[1], "nai-diffusion-5-curated")
        self.assertIn(schema["default_model"]["default"], options)
        self.assertIn("V5", schema["default_model"]["hint"])

    def test_i18n_pages_registered(self):
        zh_path = PLUGIN_ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json"
        en_path = PLUGIN_ROOT / ".astrbot-plugin" / "i18n" / "en-US.json"
        self.assertTrue(zh_path.exists())
        self.assertTrue(en_path.exists())
        with open(zh_path, "r", encoding="utf-8") as f:
            zh = json.load(f)
        self.assertIn("pages", zh)
        self.assertIn("nai-config", zh["pages"])
        self.assertEqual(zh["pages"]["nai-config"]["title"], "NovelAI 预设管理")

    def test_webui_static_files(self):
        page_dir = PLUGIN_ROOT / "pages" / "nai-config"
        self.assertTrue((page_dir / "index.html").exists())
        self.assertTrue((page_dir / "style.css").exists())
        self.assertTrue((page_dir / "app.js").exists())

        html = (page_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="modal-preset-artist"', html)
        self.assertIn('id="modal-preset-positive"', html)
        self.assertIn('id="modal-preset-negative"', html)
        self.assertIn('id="preview-artist-val"', html)
        self.assertIn('id="preview-tag-val"', html)
        self.assertIn('id="preview-neg-val"', html)
        self.assertIn('id="preset-search"', html)

    def test_translate_provider_id_uses_official_select_provider(self):
        """v0.3.4：直译模型必须用 AstrBot 官方 `_special: select_provider`（单数）。

        守卫点：
        - 必须 == "select_provider"（原生配置页会渲染成模型下拉选择器）；
        - 绝不能是复数 "select_providers"（官方文档标注为 Core 内部实现、不建议插件使用）；
        - 该键仍为 string / 默认空串（select_provider 返回字符串，正好匹配）。
        """
        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        key = schema["translate_provider_id"]
        self.assertEqual(key.get("_special"), "select_provider")
        self.assertNotEqual(key.get("_special"), "select_providers")
        self.assertEqual(key["type"], "string")
        self.assertEqual(key["default"], "")
        # 另外两个直译配置项不应带 _special
        self.assertNotIn("_special", schema["translate_enabled"])
        self.assertNotIn("_special", schema["translate_system_prompt"])


class _FakeMeta:
    """模拟 AstrBot provider 的 meta() 返回对象。"""

    def __init__(self, id, name, ptype="chat"):
        self.id = id
        self.name = name
        self.type = ptype


class _FakeResp:
    """模拟 provider.text_chat 的返回对象（completion_text 可能缺失）。"""

    def __init__(self, completion_text=None):
        if completion_text is not None:
            self.completion_text = completion_text


class FakeChatProvider:
    """模拟 AstrBot 对话模型：记录调用参数，可配置返回/异常/延迟。"""

    def __init__(
        self,
        provider_id="fake_llm",
        name="Fake LLM",
        ptype="chat",
        response="1girl, swimsuit",
        error=None,
        delay=0.0,
        use_result_attr=False,
    ):
        self.provider_id = provider_id
        self._meta = _FakeMeta(provider_id, name, ptype)
        self.response = response
        self.error = error
        self.delay = delay
        self.use_result_attr = use_result_attr
        self.calls = []

    def meta(self):
        return self._meta

    async def text_chat(self, prompt=None, system_prompt=None, **kwargs):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.use_result_attr:
            resp = _FakeResp()
            resp.result = _FakeResp(completion_text=self.response)
            return resp
        return _FakeResp(completion_text=self.response)


class _FakeContext:
    """最小 Context 替身：只实现直译用到的只读接口。"""

    def __init__(self, providers=None, fail=False):
        self._providers = list(providers or [])
        self._fail = fail

    def get_all_providers(self):
        if self._fail:
            raise RuntimeError("boom")
        return list(self._providers)

    def get_provider_by_id(self, pid):
        for p in self._providers:
            if getattr(p, "provider_id", None) == pid:
                return p
        return None


def _make_translator_cfg(**overrides):
    cfg = {
        "translate_enabled": True,
        "translate_provider_id": "fake_llm",
        "translate_system_prompt": "",
        "translate_timeout": 30.0,
    }
    cfg.update(overrides)
    return cfg


class TestPromptTranslation(unittest.IsolatedAsyncioTestCase):
    """v0.3.0：PromptTranslator 直译决策顺序、清洗、校验与失败回退。"""

    def setUp(self):
        from astrbot_plugin_nai_plus.core.translate_client import PromptTranslator

        self.PromptTranslator = PromptTranslator
        self.provider = FakeChatProvider()
        self.context = _FakeContext([self.provider])

    async def test_has_cjk_detection(self):
        from astrbot_plugin_nai_plus.core.translate_client import has_cjk

        self.assertTrue(has_cjk("原神 雷电将军"))
        self.assertTrue(has_cjk("1girl, 泳装"))
        self.assertTrue(has_cjk("桜"))
        self.assertFalse(has_cjk("1girl, silver hair, blue eyes"))
        self.assertFalse(has_cjk("{{1girl}}, 1.3::silver hair::"))
        self.assertFalse(has_cjk(""))
        self.assertFalse(has_cjk(None))

    async def test_clean_llm_output(self):
        from astrbot_plugin_nai_plus.core.translate_client import _clean_llm_output

        self.assertEqual(_clean_llm_output("```text\n1girl, smile\n```"), "1girl, smile")
        self.assertEqual(_clean_llm_output("Output: 1girl, smile"), "1girl, smile")
        self.assertEqual(_clean_llm_output("输出： 1girl, smile"), "1girl, smile")
        self.assertEqual(_clean_llm_output('"1girl, smile"'), "1girl, smile")
        self.assertEqual(_clean_llm_output("1girl,, smile,,,"), "1girl, smile")
        self.assertEqual(_clean_llm_output("  1girl,    smile  "), "1girl, smile")
        # 内部含逗号的权重分组绝不能被破坏
        self.assertEqual(_clean_llm_output("1.5::a, b::, 1girl"), "1.5::a, b::, 1girl")

    async def test_translate_empty_passthrough(self):
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("")
        self.assertFalse(res.translated)
        self.assertIsNone(res.note)
        res2 = await t.translate("   ")
        self.assertFalse(res2.translated)

    async def test_translate_disabled_passthrough(self):
        t = self.PromptTranslator(self.context, _make_translator_cfg(translate_enabled=False))
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertEqual(res.text, "原神 雷电将军")
        self.assertEqual(self.provider.calls, [])

    async def test_translate_pure_english_skips_llm(self):
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("1girl, silver hair, looking at viewer")
        self.assertFalse(res.translated)
        self.assertEqual(res.text, "1girl, silver hair, looking at viewer")
        self.assertEqual(self.provider.calls, [], "纯英文提示词绝不能调用 LLM")

    async def test_translate_success_and_system_prompt(self):
        self.provider.response = "1girl, raiden_shogun_(genshin_impact), swimsuit"
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("原神 雷电将军 泳装")
        self.assertTrue(res.translated)
        self.assertIsNone(res.note)
        self.assertEqual(res.text, "1girl, raiden_shogun_(genshin_impact), swimsuit")
        # 必须带上系统提示词调用 text_chat
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.calls[0]["prompt"], "原神 雷电将军 泳装")
        self.assertEqual(self.provider.calls[0]["system_prompt"], t.system_prompt)

    async def test_translate_provider_missing_note(self):
        t = self.PromptTranslator(self.context, _make_translator_cfg(translate_provider_id=""))
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertEqual(res.text, "原神 雷电将军")
        self.assertIn("未配置", res.note)
        self.assertIn("原文", res.note)

    async def test_translate_provider_not_found_note(self):
        t = self.PromptTranslator(self.context, _make_translator_cfg(translate_provider_id="ghost"))
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertIsNotNone(res.note)

    async def test_translate_llm_exception_note(self):
        self.provider.error = RuntimeError("api down")
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertEqual(res.text, "原神 雷电将军")
        self.assertIn("直译失败", res.note)
        self.assertIn("RuntimeError", res.note)
        self.assertIn("原文", res.note)

    async def test_translate_result_still_cjk_note(self):
        self.provider.response = "1girl, 中文没翻干净"
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertEqual(res.text, "原神 雷电将军")
        self.assertIn("仍含中文", res.note)

    async def test_translate_empty_result_note(self):
        self.provider.response = " , , "
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertIn("返回为空", res.note)

    async def test_translate_too_long_note(self):
        self.provider.response = "a" * 2000
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("原神")
        self.assertFalse(res.translated)
        self.assertIn("异常过长", res.note)

    async def test_translate_timeout_note(self):
        self.provider.delay = 0.3
        t = self.PromptTranslator(self.context, _make_translator_cfg(translate_timeout=0.02))
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertEqual(res.text, "原神 雷电将军")
        self.assertIn("超时", res.note)
        self.assertIn("原文", res.note)

    async def test_translate_completion_fallback_via_result_attr(self):
        self.provider.response = "1girl, smile"
        self.provider.use_result_attr = True
        t = self.PromptTranslator(self.context, _make_translator_cfg())
        res = await t.translate("少女微笑")
        self.assertTrue(res.translated)
        self.assertEqual(res.text, "1girl, smile")

    async def test_list_models_filters_non_chat(self):
        chat = FakeChatProvider(provider_id="llm1", name="主对话", ptype="chat")
        tts = FakeChatProvider(provider_id="tts1", name="语音", ptype="text_to_speech")
        emb = FakeChatProvider(provider_id="emb1", name="向量", ptype="embedding")
        t = self.PromptTranslator(_FakeContext([chat, tts, emb]), _make_translator_cfg())
        models = t.list_models()
        ids = [m["id"] for m in models]
        self.assertEqual(ids, ["llm1"])
        self.assertEqual(models[0]["name"], "主对话")
        self.assertEqual(models[0]["type"], "chat")

    async def test_list_models_context_none(self):
        t = self.PromptTranslator(None, _make_translator_cfg())
        self.assertEqual(t.list_models(), [])

    async def test_list_models_error_returns_empty(self):
        t = self.PromptTranslator(_FakeContext(fail=True), _make_translator_cfg())
        self.assertEqual(t.list_models(), [])

    async def test_default_prompt_matches_schema(self):
        from astrbot_plugin_nai_plus.core.translate_client import DEFAULT_TRANSLATE_SYSTEM_PROMPT

        schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertIn("translate_system_prompt", schema)
        self.assertEqual(
            schema["translate_system_prompt"]["default"],
            DEFAULT_TRANSLATE_SYSTEM_PROMPT,
            "schema 默认提示词必须与 DEFAULT_TRANSLATE_SYSTEM_PROMPT 逐字节一致",
        )
        self.assertIn("translate_enabled", schema)
        self.assertIn("translate_provider_id", schema)

    async def test_clean_llm_output_keeps_all_multiline_tags(self):
        """B1 回归：多行标签必须全部保留，绝不静默丢弃后面几行。"""
        from astrbot_plugin_nai_plus.core.translate_client import _clean_llm_output

        out = _clean_llm_output("1girl, silver hair\nstanding, cherry_blossom")
        self.assertIn("silver hair", out)
        self.assertIn("cherry_blossom", out)
        self.assertEqual(out, "1girl, silver hair, standing, cherry_blossom")

    async def test_clean_llm_output_drops_explanatory_line(self):
        """末尾解释性句子（以句号结尾）应被剔除。"""
        from astrbot_plugin_nai_plus.core.translate_client import _clean_llm_output

        out = _clean_llm_output("1girl, silver hair\n注意：我只翻译了标签。")
        self.assertEqual(out, "1girl, silver hair")

    async def test_clean_llm_output_keeps_weight_lines_ending_with_double_colon(self):
        """B1 回归守卫：以 `::` 结尾的权重行绝不能被当解释行剔除。"""
        from astrbot_plugin_nai_plus.core.translate_client import _clean_llm_output

        out = _clean_llm_output("1.3::silver hair::\n1girl")
        self.assertIn("1.3::silver hair::", out)
        self.assertIn("1girl", out)

        out2 = _clean_llm_output("\\n20::best quality::\n1girl")
        self.assertIn("\\n20::best quality::", out2)
        self.assertIn("1girl", out2)

    async def test_clean_llm_output_single_line_unchanged(self):
        """单行行为保持不变（回归）。"""
        from astrbot_plugin_nai_plus.core.translate_client import _clean_llm_output

        self.assertEqual(_clean_llm_output("1girl, silver hair, smile"), "1girl, silver hair, smile")

    async def test_default_prompt_content_guards(self):
        """提示词内容守卫：P0-1/P0-2、规则 12/13/14 的关键表述必须在，旧/非规范措辞必须不在。"""
        from astrbot_plugin_nai_plus.core.translate_client import DEFAULT_TRANSLATE_SYSTEM_PROMPT as P

        # P0-1：必须给出「不带括号」的角色示例 hatsune_miku，并明确「不是 hatsune_miku_(vocaloid)」
        self.assertIn("hatsune_miku", P)
        self.assertIn("不是 hatsune_miku_(vocaloid)", P)
        # P0-2：必须说明 `\n` 是字面的反斜杠 + 字母 n（而非真正换行）
        self.assertIn("字面的反斜杠", P)
        # 规则 14（v0.3.3）：量词示例改为 Danbooru 规范标签 twintails / 2girls
        self.assertIn("twintails", P)
        self.assertIn("两名少女", P)
        self.assertIn("2girls", P)
        # F2：非规范标签 `two swords` 必须已被移除
        self.assertNotIn("two swords", P)
        # F1（v0.3.3）：规则 13 必须限定为「翻译产生的标签」，避免与规则 3「原样保留」冲突
        self.assertIn("对你翻译产生的标签", P)
        # F3（v0.3.3）：新添加的人数标签位置必须写清楚
        self.assertIn("新添加的人数标签放在最前面", P)
        # 旧措辞（「忽略尺寸…」）不得残留
        self.assertNotIn("忽略尺寸", P)

    async def test_translate_timeout_note_shows_fractional_seconds(self):
        """B2：timeout < 1 时文案应显示小数，而不是「超时 0s」。"""
        self.provider.delay = 0.6  # 必须大于 timeout，才会真正触发超时
        t = self.PromptTranslator(self.context, _make_translator_cfg(translate_timeout=0.5))
        res = await t.translate("原神 雷电将军")
        self.assertFalse(res.translated)
        self.assertIn("0.5", res.note)
        self.assertNotIn("超时 0s", res.note)


class TestTranslateWebUI(unittest.IsolatedAsyncioTestCase):
    """v0.3.0：直译 WebUI 后端接口与生图链路集成。"""

    def setUp(self):
        mock_web_routes.clear()
        from astrbot_plugin_nai_plus.main import Nai2ApiPlugin

        self.provider = FakeChatProvider(provider_id="fake_llm", name="Fake LLM")
        mock_context = MagicMock()
        mock_context.register_web_api = mock_register_web_api
        mock_context.get_all_providers = lambda: [self.provider]
        mock_context.get_provider_by_id = (
            lambda pid: self.provider if pid == self.provider.provider_id else None
        )
        self.mock_context = mock_context

        self.data_dir = PLUGIN_ROOT / "tests" / "tmp_data_translate"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        mock_astrbot.api.star.StarTools.get_data_dir.return_value = self.data_dir

        self.config = {
            "token": "dummy",
            "default_model": "nai-diffusion-4-5-full",
            "default_preset": "",
            "translate_enabled": False,
            "translate_provider_id": "",
            "translate_system_prompt": "",
        }
        self.plugin = Nai2ApiPlugin(mock_context, self.config)
        self.plugin.client.generate = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
        self.plugin.imgr.save_image = AsyncMock(return_value=Path("fake.png"))
        self.plugin._send_image_with_info = AsyncMock()

    def tearDown(self):
        import shutil
        if self.data_dir.exists():
            shutil.rmtree(self.data_dir, ignore_errors=True)

    async def test_translate_routes_registered(self):
        for route in ("translate/models", "translate", "translate/test"):
            self.assertIn(route, mock_web_routes, f"Route {route} not registered")

    async def test_api_get_config_includes_translate(self):
        from astrbot_plugin_nai_plus.core.translate_client import DEFAULT_TRANSLATE_SYSTEM_PROMPT

        handler, methods = mock_web_routes["config"]
        self.assertIn("GET", methods)
        data = await handler(MagicMock())
        self.assertIn("translate", data)
        self.assertFalse(data["translate"]["enabled"])
        self.assertEqual(data["translate"]["provider_id"], "")
        self.assertEqual(data["translate"]["system_prompt"], DEFAULT_TRANSLATE_SYSTEM_PROMPT)
        self.assertEqual(data["translate_default_prompt"], DEFAULT_TRANSLATE_SYSTEM_PROMPT)

    async def test_api_translate_models_endpoint(self):
        handler, methods = mock_web_routes["translate/models"]
        self.assertIn("GET", methods)
        data = await handler(MagicMock())
        self.assertTrue(data["available"])
        ids = [m["id"] for m in data["models"]]
        self.assertIn("fake_llm", ids)

    async def test_api_translate_save_and_reload(self):
        handler, methods = mock_web_routes["translate"]
        self.assertIn("POST", methods)
        req = MagicMock()
        req.json = AsyncMock(return_value={
            "enabled": True,
            "provider_id": "fake_llm",
            "system_prompt": "",  # 留空 -> 回退默认
        })
        data = await handler(req)
        self.assertTrue(data.get("ok"))
        # 内存中的 translator 已热更新
        self.assertTrue(self.plugin.translator.enabled)
        self.assertEqual(self.plugin.translator.provider_id, "fake_llm")
        from astrbot_plugin_nai_plus.core.translate_client import DEFAULT_TRANSLATE_SYSTEM_PROMPT
        self.assertEqual(self.plugin.translator.system_prompt, DEFAULT_TRANSLATE_SYSTEM_PROMPT)
        # 配置已写回
        self.assertTrue(self.plugin.config.get("translate_enabled"))

    async def test_api_translate_test_does_not_mutate_memory(self):
        handler, _ = mock_web_routes["translate/test"]
        req = MagicMock()
        req.json = AsyncMock(return_value={
            "text": "原神 雷电将军",
            "provider_id": "fake_llm",
            "system_prompt": "custom system prompt",
        })
        self.provider.response = "1girl, raiden_shogun_(genshin_impact)"
        data = await handler(req)
        self.assertTrue(data.get("ok"))
        self.assertTrue(data["translated"])
        self.assertEqual(data["text"], "1girl, raiden_shogun_(genshin_impact)")
        # 试译不得改动内存中的正式配置
        self.assertFalse(self.plugin.translator.enabled)
        self.assertEqual(self.plugin.translator.provider_id, "")
        self.assertFalse(bool(self.plugin.config.get("translate_enabled")))

    async def test_api_translate_test_uses_unsaved_system_prompt(self):
        handler, _ = mock_web_routes["translate/test"]
        req = MagicMock()
        req.json = AsyncMock(return_value={
            "text": "少女微笑",
            "provider_id": "fake_llm",
            "system_prompt": "MY UNSAVED PROMPT",
        })
        await handler(req)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.calls[0]["system_prompt"], "MY UNSAVED PROMPT")

    async def test_api_translate_test_passthrough_note(self):
        handler, _ = mock_web_routes["translate/test"]
        req = MagicMock()
        req.json = AsyncMock(return_value={
            "text": "原神 雷电将军",
            "provider_id": "",  # 未选择模型 -> 失败但回退原文
            "system_prompt": "",
        })
        data = await handler(req)
        self.assertTrue(data.get("ok"))
        self.assertFalse(data["translated"])
        self.assertEqual(data["text"], "原神 雷电将军")
        self.assertIn("原文", data["note"])

    async def test_generate_translates_cjk_prompt(self):
        # 直接开启翻译（等价于用户已在面板保存启用）
        self.plugin.translator.enabled = True
        self.plugin.translator.provider_id = "fake_llm"
        self.provider.response = "1girl, raiden_shogun_(genshin_impact), swimsuit"

        event = MagicMock()
        event.plain_result = lambda text: text
        event.message_str = "#nai 原神 雷电将军 泳装"
        event.get_message_str = lambda: "#nai 原神 雷电将军 泳装"

        await self.plugin._handle_generate(event, "原神 雷电将军 泳装")
        self.assertEqual(self.plugin.client.generate.await_count, 1)
        sent_prompt = self.plugin.client.generate.await_args.args[0]
        self.assertIn("raiden_shogun", sent_prompt)
        self.assertNotIn("原神", sent_prompt)

    async def test_generate_english_skips_translation(self):
        self.plugin.translator.enabled = True
        self.plugin.translator.provider_id = "fake_llm"

        event = MagicMock()
        event.plain_result = lambda text: text
        event.message_str = "#nai 1girl, silver hair"
        event.get_message_str = lambda: "#nai 1girl, silver hair"

        await self.plugin._handle_generate(event, "1girl, silver hair")
        self.assertEqual(self.provider.calls, [], "英文提示词不应触发直译")
        sent_prompt = self.plugin.client.generate.await_args.args[0]
        self.assertIn("silver hair", sent_prompt)

    async def test_generate_translation_failure_falls_back_to_original(self):
        self.plugin.translator.enabled = True
        self.plugin.translator.provider_id = "fake_llm"
        self.provider.error = RuntimeError("boom")

        event = MagicMock()
        event.plain_result = lambda text: text
        event.message_str = "#nai 少女微笑"
        event.get_message_str = lambda: "#nai 少女微笑"

        await self.plugin._handle_generate(event, "少女微笑")
        self.assertEqual(self.plugin.client.generate.await_count, 1)
        sent_prompt = self.plugin.client.generate.await_args.args[0]
        self.assertIn("少女微笑", sent_prompt)
        # 失败说明应通过 _send_image_with_info 的 translate_note 传出（绝不静默）
        kwargs = self.plugin._send_image_with_info.await_args.kwargs
        self.assertIn("直译失败", kwargs.get("translate_note") or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

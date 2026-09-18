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

mock_astrbot = MagicMock()
mock_astrbot.api = MagicMock()
mock_astrbot.api.logger = MagicMock()
mock_astrbot.api.event = MagicMock()
mock_astrbot.api.event.AstrMessageEvent = MagicMock
mock_astrbot.api.event.filter = MagicMock()
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
mock_astrbot.core.star.filter = MagicMock()
mock_astrbot.core.star.filter.command = MagicMock()
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


class TestCommandParsingAndResolution(unittest.TestCase):
    """Test command flag parsing and default preset resolution."""

    def setUp(self):
        from astrbot_plugin_nai_plus.main import _parse_nai_command
        self.parse = _parse_nai_command

    def test_parse_flags(self):
        size, prompt, preset_name, artist, neg, seed, no_preset = self.parse(
            "竖图 1girl, smiling -p 动漫风 --artist 1.2::artist:test:: --negative bad hands --seed 9999"
        )
        self.assertEqual(size, "竖图")
        self.assertEqual(preset_name, "动漫风")
        self.assertEqual(seed, 9999)
        self.assertEqual(artist, "1.2::artist:test::")
        self.assertEqual(neg, "bad hands")
        self.assertEqual(prompt, "1girl, smiling")
        self.assertFalse(no_preset)

    def test_parse_no_preset(self):
        size, prompt, preset_name, artist, neg, seed, no_preset = self.parse(
            "1girl, solo --no-preset"
        )
        self.assertTrue(no_preset)
        self.assertEqual(prompt, "1girl, solo")
        self.assertIsNone(preset_name)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

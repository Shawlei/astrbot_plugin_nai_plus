"""元数据、配置 Schema 与文档一致性测试。"""

import json
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


class TestMetadataAndSchema(unittest.TestCase):
    """验证 1.3.1 配置交互与文档保持一致。"""

    def test_metadata_yaml_version_and_features(self):
        """版本号与关键能力说明必须匹配 1.3.1。"""
        meta_path = BASE_DIR / "metadata.yaml"
        self.assertTrue(meta_path.exists(), "metadata.yaml 不存在")
        content = meta_path.read_text(encoding="utf-8")

        self.assertIn("version: 1.3.1", content)
        self.assertIn("配置面板", content)
        self.assertIn("预设三要素", content)
        self.assertIn("角色名查表加速", content)
        self.assertIn("提示词直译增强", content)

    def test_conf_schema_has_six_real_collapsible_groups(self):
        """六个逻辑板块必须直接建模为 object + items 折叠组。"""
        schema_path = BASE_DIR / "_conf_schema.json"
        schema_text = schema_path.read_text(encoding="utf-8")
        schema = json.loads(schema_text)
        expected_groups = [
            "basic_settings",
            "quality_safety",
            "preset_management",
            "translation",
            "img2img",
            "system_tools",
        ]

        self.assertEqual(list(schema), expected_groups)
        for group_name in expected_groups:
            self.assertEqual(schema[group_name].get("type"), "object")
            self.assertIsInstance(schema[group_name].get("items"), dict)
            self.assertTrue(schema[group_name]["items"])

        self.assertFalse(
            [key for key in schema if key.startswith("section_")],
            "不得保留可编辑的 section_* 伪标题字段",
        )
        self.assertNotIn("=====", schema_text)
        self.assertNotIn("════", schema_text)
        self.assertNotIn("————", schema_text)

    def test_conf_schema_preset_controls_order_and_confirmation(self):
        """预设管理独立折叠，确认与说明字段按交互顺序排列。"""
        schema = json.loads(
            (BASE_DIR / "_conf_schema.json").read_text(encoding="utf-8")
        )
        fields = schema["preset_management"]["items"]
        expected_order = [
            "preset_add_target",
            "preset_add_name",
            "preset_add_artist",
            "preset_add_prompt",
            "preset_add_negative",
            "preset_add_confirm",
            "preset_manage_name",
            "preset_preview",
            "default_artist",
            "default_negative",
        ]
        self.assertEqual(list(fields), expected_order)

        expected_types = {
            "preset_add_target": "string",
            "preset_add_name": "string",
            "preset_add_artist": "text",
            "preset_add_prompt": "text",
            "preset_add_negative": "text",
            "preset_add_confirm": "bool",
            "preset_manage_name": "string",
            "preset_preview": "text",
        }
        for field_name, field_type in expected_types.items():
            self.assertEqual(fields[field_name].get("type"), field_type)

        self.assertEqual(
            fields["preset_add_target"].get("options"), ["custom", "builtin"]
        )
        confirm = fields["preset_add_confirm"]
        self.assertIs(confirm.get("default"), False)
        self.assertIn("清空", confirm.get("hint", ""))
        self.assertIn("复位", confirm.get("hint", ""))
        self.assertIn("说明", fields["preset_preview"].get("description", ""))
        self.assertIn("不是实时预览", fields["preset_preview"].get("hint", ""))

        manage_hint = fields["preset_manage_name"].get("hint", "")
        self.assertIn("手填", manage_hint)
        self.assertIn("/nai presets", manage_hint)
        self.assertIn("/nai del", manage_hint)
        self.assertNotIn("view_builtin_presets", fields)
        self.assertNotIn("view_custom_presets", fields)

    def test_schema_contains_expected_grouped_fields(self):
        """原有配置字段完整迁入对应折叠组。"""
        schema = json.loads(
            (BASE_DIR / "_conf_schema.json").read_text(encoding="utf-8")
        )
        expected_fields = {
            "basic_settings": ("api_url", "token"),
            "quality_safety": ("confirm_hd_size", "max_cached_images"),
            "preset_management": ("default_artist", "default_negative"),
            "translation": (
                "char_mapping_enabled",
                "custom_characters_file",
                "translate_system_prompt",
            ),
            "img2img": ("img2img_enabled", "img2img"),
            "system_tools": ("command_names", "llm_tool_enabled"),
        }
        for group_name, field_names in expected_fields.items():
            group_fields = schema[group_name]["items"]
            for field_name in field_names:
                self.assertIn(field_name, group_fields)

    def test_schema_does_not_use_internal_provider_special(self):
        """插件 Schema 不依赖 AstrBot 内部 _special 选择器。"""
        schema = json.loads(
            (BASE_DIR / "_conf_schema.json").read_text(encoding="utf-8")
        )
        translate_fields = schema["translation"]["items"]
        self.assertNotIn("_special", translate_fields["translate_provider_ids"])

    def test_changelog_v131_entry(self):
        """CHANGELOG 顶部记录 1.3.1 面板修复。"""
        content = (BASE_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## v1.3.1", content)
        self.assertIn("section_*", content)
        self.assertIn("保存后清空", content)
        self.assertIn("`preset_add_confirm`", content)
        self.assertIn("`type: object`", content)
        self.assertIn("/nai presets <预设名>", content)
        self.assertIn("/nai del <预设名>", content)

    def test_readme_v131_documents_real_interaction(self):
        """README 不承诺 Schema 不支持的按钮或只读动态列表。"""
        content = (BASE_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("v1.3.1", content)
        self.assertIn("type: object", content)
        self.assertIn("preset_add_confirm", content)
        self.assertIn("preset_manage_name", content)
        self.assertIn("预览说明", content)
        self.assertIn("保存配置", content)
        self.assertIn("/nai presets <预设名>", content)
        self.assertIn("/nai del <预设名>", content)
        self.assertIn("custom_presets.json", content)
        self.assertNotIn("`view_builtin_presets`", content)
        self.assertNotIn("`view_custom_presets`", content)


if __name__ == "__main__":
    unittest.main()

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

    def test_conf_schema_has_no_editable_section_headers(self):
        """Schema 不得再用 string 字段伪装板块标题。"""
        schema_path = BASE_DIR / "_conf_schema.json"
        schema_text = schema_path.read_text(encoding="utf-8")
        schema = json.loads(schema_text)

        self.assertFalse(
            [key for key in schema if key.startswith("section_")],
            "不得保留可编辑的 section_* 伪标题字段",
        )
        self.assertNotIn("=====", schema_text)
        self.assertNotIn("════", schema_text)
        self.assertNotIn("————", schema_text)

    def test_conf_schema_contains_supported_preset_controls(self):
        """预设区域仅使用 AstrBot Schema 支持的输入与选择组件。"""
        schema_path = BASE_DIR / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        expected_types = {
            "preset_add_target": "string",
            "preset_add_name": "string",
            "preset_add_artist": "text",
            "preset_add_prompt": "text",
            "preset_add_negative": "text",
            "preset_manage_name": "string",
        }
        for field_name, field_type in expected_types.items():
            self.assertIn(field_name, schema)
            self.assertEqual(schema[field_name].get("type"), field_type)

        self.assertEqual(
            schema["preset_add_target"].get("options"), ["custom", "builtin"]
        )
        self.assertTrue(schema["preset_manage_name"].get("apply_provider_default"))
        manage_hint = schema["preset_manage_name"].get("hint", "")
        self.assertIn("/nai presets", manage_hint)
        self.assertIn("/nai del", manage_hint)
        self.assertNotIn("view_builtin_presets", schema)
        self.assertNotIn("view_custom_presets", schema)

        for field_name in (
            "char_mapping_enabled",
            "custom_characters_file",
            "translate_system_prompt",
            "api_url",
            "token",
            "img2img_enabled",
        ):
            self.assertIn(field_name, schema)

    def test_schema_does_not_use_internal_provider_special(self):
        """插件 Schema 不依赖 AstrBot 内部 _special 选择器。"""
        schema = json.loads(
            (BASE_DIR / "_conf_schema.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("_special", schema["translate_provider_ids"])

    def test_changelog_v131_entry(self):
        """CHANGELOG 顶部记录 1.3.1 面板修复。"""
        content = (BASE_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## v1.3.1", content)
        self.assertIn("section_*", content)
        self.assertIn("保存后清空", content)
        self.assertIn("/nai presets <预设名>", content)
        self.assertIn("/nai del <预设名>", content)

    def test_readme_v131_documents_real_interaction(self):
        """README 不承诺 Schema 不支持的按钮或只读动态列表。"""
        content = (BASE_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIn("v1.3.1", content)
        self.assertIn("preset_manage_name", content)
        self.assertIn("保存配置", content)
        self.assertIn("/nai presets <预设名>", content)
        self.assertIn("/nai del <预设名>", content)
        self.assertIn("custom_presets.json", content)
        self.assertNotIn("`view_builtin_presets`", content)
        self.assertNotIn("`view_custom_presets`", content)


if __name__ == "__main__":
    unittest.main()

"""
元数据与配置规范一致性测试
"""

import json
import os
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


class TestMetadataAndSchema(unittest.TestCase):
    """验证 metadata.yaml, _conf_schema.json, CHANGELOG.md 和 README.md 的规范一致性"""

    def test_metadata_yaml_version_and_features(self):
        """验证 metadata.yaml 版本号为 1.3.0 且描述包含关键特性"""
        meta_path = BASE_DIR / "metadata.yaml"
        self.assertTrue(meta_path.exists(), "metadata.yaml 不存在")
        content = meta_path.read_text(encoding="utf-8")

        self.assertIn("version: 1.3.0", content, "metadata.yaml 中 version 应为 1.3.0")
        self.assertIn("板块", content, "metadata.yaml 描述中应说明功能板块划分")
        self.assertIn("预设三要素", content, "metadata.yaml 描述中应说明预设三要素")
        self.assertIn("角色名查表加速", content, "metadata.yaml 描述中应保留角色名查表加速")
        self.assertIn("提示词直译增强", content, "metadata.yaml 描述中应保留提示词直译增强")

    def test_conf_schema_contains_sections_and_fields(self):
        """验证 _conf_schema.json 包含 6 大板块隔断及预设添加/查看组件"""
        schema_path = BASE_DIR / "_conf_schema.json"
        self.assertTrue(schema_path.exists(), "_conf_schema.json 不存在")

        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        # 1. 验证 6 大板块大字隔断项
        expected_sections = [
            "section_1_basic_settings",
            "section_2_security",
            "section_3_presets",
            "section_4_translate",
            "section_5_img2img",
            "section_6_system",
        ]
        for sec in expected_sections:
            self.assertIn(sec, schema, f"_conf_schema.json 应包含板块隔断项 {sec}")
            self.assertEqual(schema[sec].get("type"), "string")

        # 2. 验证预设管理核心输入组件
        preset_fields = [
            "preset_add_target",
            "preset_add_name",
            "preset_add_artist",
            "preset_add_prompt",
            "preset_add_negative",
            "view_builtin_presets",
            "view_custom_presets",
        ]
        for field in preset_fields:
            self.assertIn(field, schema, f"_conf_schema.json 应包含预设管理项 {field}")

        self.assertEqual(schema["preset_add_target"].get("type"), "string")
        self.assertIn("custom", schema["preset_add_target"].get("options", []))
        self.assertIn("builtin", schema["preset_add_target"].get("options", []))

        self.assertEqual(schema["preset_add_name"].get("type"), "string")
        self.assertEqual(schema["preset_add_artist"].get("type"), "text")
        self.assertEqual(schema["preset_add_prompt"].get("type"), "text")
        self.assertEqual(schema["preset_add_negative"].get("type"), "text")
        self.assertEqual(schema["view_builtin_presets"].get("type"), "text")
        self.assertEqual(schema["view_custom_presets"].get("type"), "text")

        # 3. 验证既有字段完备性
        self.assertIn("char_mapping_enabled", schema)
        self.assertIn("custom_characters_file", schema)
        self.assertIn("translate_system_prompt", schema)
        self.assertIn("api_url", schema)
        self.assertIn("token", schema)
        self.assertIn("img2img_enabled", schema)

    def test_changelog_v130_entry(self):
        """验证 CHANGELOG.md 顶部包含 v1.3.0 的完整更新说明"""
        changelog_path = BASE_DIR / "CHANGELOG.md"
        self.assertTrue(changelog_path.exists(), "CHANGELOG.md 不存在")
        content = changelog_path.read_text(encoding="utf-8")

        self.assertIn("## v1.3.0", content, "CHANGELOG.md 应包含 v1.3.0 版本节")
        self.assertIn("PresetManager", content)
        self.assertIn("三要素", content)
        self.assertIn("6 大功能板块", content)
        self.assertIn("preset_add_target", content)

    def test_readme_v130_documentation(self):
        """验证 README.md 包含 1.3.0 功能板块与预设管理体系的说明"""
        readme_path = BASE_DIR / "README.md"
        self.assertTrue(readme_path.exists(), "README.md 不存在")
        content = readme_path.read_text(encoding="utf-8")

        self.assertIn("v1.3.0", content)
        self.assertIn("preset_add_target", content)
        self.assertIn("view_builtin_presets", content)
        self.assertIn("view_custom_presets", content)
        self.assertIn("预设三要素", content)
        self.assertIn("6 大功能板块", content)


if __name__ == "__main__":
    unittest.main()

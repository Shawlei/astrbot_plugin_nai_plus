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
        """验证 metadata.yaml 版本号为 1.2.0 且描述包含关键特性"""
        meta_path = BASE_DIR / "metadata.yaml"
        self.assertTrue(meta_path.exists(), "metadata.yaml 不存在")
        content = meta_path.read_text(encoding="utf-8")

        self.assertIn("version: 1.2.0", content, "metadata.yaml 中 version 应为 1.2.0")
        self.assertIn("角色名查表加速", content, "metadata.yaml 描述中应说明角色名查表加速")
        self.assertIn("提示词直译增强", content, "metadata.yaml 描述中应说明提示词直译增强")

    def test_conf_schema_contains_new_fields(self):
        """验证 _conf_schema.json 包含 char_mapping_enabled 和 custom_characters_file"""
        schema_path = BASE_DIR / "_conf_schema.json"
        self.assertTrue(schema_path.exists(), "_conf_schema.json 不存在")

        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        # 1. char_mapping_enabled
        self.assertIn("char_mapping_enabled", schema, "_conf_schema.json 应包含 char_mapping_enabled")
        char_conf = schema["char_mapping_enabled"]
        self.assertEqual(char_conf.get("type"), "bool")
        self.assertEqual(char_conf.get("default"), True)

        # 2. custom_characters_file
        self.assertIn("custom_characters_file", schema, "_conf_schema.json 应包含 custom_characters_file")
        custom_conf = schema["custom_characters_file"]
        self.assertEqual(custom_conf.get("type"), "string")
        self.assertEqual(custom_conf.get("default"), "data/custom_characters.json")

        # 3. translate_system_prompt 包含 6 组 Few-shot 示例
        self.assertIn("translate_system_prompt", schema)
        default_prompt = schema["translate_system_prompt"].get("default", "")
        self.assertEqual(
            default_prompt.count("Input:"),
            6,
            "translate_system_prompt 默认值应包含 6 组 Input Few-shot 示例",
        )
        self.assertEqual(
            default_prompt.count("Output:"),
            6,
            "translate_system_prompt 默认值应包含 6 组 Output Few-shot 示例",
        )

    def test_changelog_v120_entry(self):
        """验证 CHANGELOG.md 顶部包含 v1.2.0 的完整更新说明"""
        changelog_path = BASE_DIR / "CHANGELOG.md"
        self.assertTrue(changelog_path.exists(), "CHANGELOG.md 不存在")
        content = changelog_path.read_text(encoding="utf-8")

        self.assertIn("## v1.2.0", content, "CHANGELOG.md 应包含 v1.2.0 版本节")
        self.assertIn("CharacterManager", content)
        self.assertIn("Few-shot", content)
        self.assertIn("char_mapping_enabled", content)
        self.assertIn("custom_characters_file", content)

    def test_readme_v120_documentation(self):
        """验证 README.md 包含角色查表与直译增强的说明"""
        readme_path = BASE_DIR / "README.md"
        self.assertTrue(readme_path.exists(), "README.md 不存在")
        content = readme_path.read_text(encoding="utf-8")

        self.assertIn("char_mapping_enabled", content, "README.md 配置表应列出 char_mapping_enabled")
        self.assertIn("custom_characters_file", content, "README.md 配置表应列出 custom_characters_file")
        self.assertIn("二次元角色名查表", content)
        self.assertIn("Few-shot", content)


if __name__ == "__main__":
    unittest.main()

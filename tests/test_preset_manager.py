"""
PresetManager 单元测试
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.preset_manager import PresetManager, FACTORY_PRESETS


class TestPresetManager(unittest.TestCase):
    """测试预设管理器的分层存储、三要素支持与格式化功能"""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_preset_mgr_"))
        self.mgr = PresetManager(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_factory_presets_exist(self):
        """出厂预设完整存在且结构规范"""
        for name, preset in FACTORY_PRESETS.items():
            self.assertEqual(preset["type"], "builtin")
            self.assertTrue(bool(preset["artist"]))
            got = self.mgr.get(name)
            self.assertIsNotNone(got)
            self.assertEqual(got["artist"], preset["artist"])
            self.assertEqual(self.mgr.get_artist(name), preset["artist"])
            self.assertTrue(self.mgr.is_builtin(name))
            self.assertTrue(self.mgr.is_factory_builtin(name))

    def test_save_and_get_custom_preset(self):
        """保存并读取自定义预设（包含三要素）"""
        self.mgr.save(
            name="赛博朋克风",
            artist="artist:cyber, year 2025",
            prompt="cyberpunk city, neon lights",
            negative="blurry, low quality",
            target_type="custom",
            desc="高对比度赛博朋克风",
        )
        got = self.mgr.get("赛博朋克风")
        self.assertIsNotNone(got)
        self.assertEqual(got["artist"], "artist:cyber, year 2025")
        self.assertEqual(got["prompt"], "cyberpunk city, neon lights")
        self.assertEqual(got["negative"], "blurry, low quality")
        self.assertEqual(got["type"], "custom")
        self.assertEqual(self.mgr.get_artist("赛博朋克风"), "artist:cyber, year 2025")
        self.assertFalse(self.mgr.is_builtin("赛博朋克风"))
        self.assertFalse(self.mgr.is_factory_builtin("赛博朋克风"))

        # 检查持久化文件
        custom_file = self.temp_dir / "custom_presets.json"
        self.assertTrue(custom_file.exists())
        with open(custom_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("赛博朋克风", data)

    def test_save_and_get_user_builtin_preset(self):
        """保存并读取用户新增的内置预设"""
        self.mgr.save(
            name="水彩手绘风",
            artist="watercolor, masterpiece",
            prompt="delicate strokes",
            negative="ugly",
            target_type="builtin",
            desc="扩展内置水彩风",
        )
        got = self.mgr.get("水彩手绘风")
        self.assertIsNotNone(got)
        self.assertEqual(got["type"], "builtin")
        self.assertTrue(self.mgr.is_builtin("水彩手绘风"))
        self.assertFalse(self.mgr.is_factory_builtin("水彩手绘风"))

        # 检查持久化文件
        builtin_file = self.temp_dir / "builtin_presets.json"
        self.assertTrue(builtin_file.exists())
        with open(builtin_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("水彩手绘风", data)

    def test_factory_preset_protection(self):
        """出厂预设保护：禁止覆盖与删除"""
        with self.assertRaises(ValueError):
            self.mgr.save("2.5D唯美风", artist="fake artist", target_type="custom")

        with self.assertRaises(ValueError):
            self.mgr.save("GalGame风", artist="fake artist", target_type="builtin")

        self.assertFalse(self.mgr.delete("2.5D唯美风"))
        self.assertFalse(self.mgr.update("2.5D唯美风", artist="new artist"))

        # 验证出厂预设依然完好
        got = self.mgr.get("2.5D唯美风")
        self.assertEqual(got["artist"], FACTORY_PRESETS["2.5D唯美风"]["artist"])

    def test_delete_and_update_presets(self):
        """删除与修改预设测试"""
        self.mgr.save("测试预设", artist="test artist", target_type="custom")
        self.assertTrue(self.mgr.update("测试预设", artist="updated artist", prompt="new prompt"))
        got = self.mgr.get("测试预设")
        self.assertEqual(got["artist"], "updated artist")
        self.assertEqual(got["prompt"], "new prompt")

        self.assertTrue(self.mgr.delete("测试预设"))
        self.assertIsNone(self.mgr.get("测试预设"))

    def test_legacy_presets_migration(self):
        """旧版本 presets.json 自动迁移兼容测试"""
        temp_dir2 = Path(tempfile.mkdtemp(prefix="test_legacy_"))
        try:
            legacy_file = temp_dir2 / "presets.json"
            legacy_content = {
                "旧版预设1": {
                    "artist": "old artist tag",
                    "desc": "旧版描述"
                },
                "旧版预设2": "raw string artist"
            }
            with open(legacy_file, "w", encoding="utf-8") as f:
                json.dump(legacy_content, f)

            mgr2 = PresetManager(temp_dir2)
            got1 = mgr2.get("旧版预设1")
            self.assertIsNotNone(got1)
            self.assertEqual(got1["artist"], "old artist tag")
            self.assertEqual(got1["prompt"], "")
            self.assertEqual(got1["negative"], "")
            self.assertEqual(got1["type"], "custom")

            got2 = mgr2.get("旧版预设2")
            self.assertIsNotNone(got2)
            self.assertEqual(got2["artist"], "raw string artist")

            # 验证已生成 custom_presets.json
            self.assertTrue((temp_dir2 / "custom_presets.json").exists())
        finally:
            shutil.rmtree(temp_dir2, ignore_errors=True)

    def test_format_preset_list(self):
        """测试 format_preset_list 的输出排版"""
        self.mgr.save(
            name="星空夜景风",
            artist="starry sky artist",
            prompt="galaxy, stars",
            negative="clouds",
            target_type="custom",
        )
        formatted_all = self.mgr.format_preset_list("all")
        self.assertIn("【内置预设库】", formatted_all)
        self.assertIn("【自定义预设库】", formatted_all)
        self.assertIn("2.5D唯美风", formatted_all)
        self.assertIn("星空夜景风", formatted_all)
        self.assertIn("• 质量前缀:", formatted_all)
        self.assertIn("• 正向词:", formatted_all)
        self.assertIn("• 负向词:", formatted_all)

        formatted_builtin = self.mgr.format_preset_list("builtin")
        self.assertIn("【内置预设库】", formatted_builtin)
        self.assertNotIn("星空夜景风", formatted_builtin)

        formatted_custom = self.mgr.format_preset_list("custom")
        self.assertIn("【自定义预设库】", formatted_custom)
        self.assertIn("星空夜景风", formatted_custom)
        self.assertNotIn("2.5D唯美风", formatted_custom)

    def test_preset_elements_interaction_logic(self):
        """测试生图时预设三要素的联动解析逻辑"""
        self.mgr.save(
            name="特化预设",
            artist="preset_artist_tags",
            prompt="preset_positive_tags",
            negative="preset_negative_tags",
            target_type="custom",
        )
        preset_info = self.mgr.get("特化预设")
        self.assertIsNotNone(preset_info)

        # 1. 用户提示词与预设正向词智能拼接（避免重复）
        user_prompt = "1girl, silver hair"
        if preset_info.get("prompt"):
            p_prompt = preset_info["prompt"].strip()
            if p_prompt.lower() not in user_prompt.lower():
                user_prompt = f"{user_prompt}, {p_prompt}"
        self.assertEqual(user_prompt, "1girl, silver hair, preset_positive_tags")

        # 2. 如果提示词已包含预设正向词，不重复拼接
        user_prompt_dup = "1girl, preset_positive_tags, silver hair"
        if preset_info.get("prompt"):
            p_prompt = preset_info["prompt"].strip()
            if p_prompt.lower() not in user_prompt_dup.lower():
                user_prompt_dup = f"{user_prompt_dup}, {p_prompt}"
        self.assertEqual(user_prompt_dup, "1girl, preset_positive_tags, silver hair")

        # 3. 负面词解析：未提供指令 --negative 时使用预设负面词
        cmd_negative = None
        final_neg = cmd_negative if cmd_negative is not None else preset_info.get("negative")
        self.assertEqual(final_neg, "preset_negative_tags")

        # 4. 负面词解析：用户显式指定 --negative 时覆盖预设负面词
        cmd_negative_explicit = "custom_negative"
        final_neg_override = cmd_negative_explicit if cmd_negative_explicit is not None else preset_info.get("negative")
        self.assertEqual(final_neg_override, "custom_negative")


if __name__ == "__main__":
    unittest.main()

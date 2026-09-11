"""
CharacterManager 单元测试
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 保证当前插件在 sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.character_manager import CharacterManager, _clean_remaining_text, _normalize_tags


class TestCharacterManager(unittest.TestCase):
    """测试 CharacterManager 核心功能"""

    def setUp(self):
        self.cm = CharacterManager()

    def test_builtin_characters_loaded(self):
        """测试内置角色库加载成功且数量充足"""
        self.assertGreater(len(self.cm._sorted_aliases), 300)
        # 测试各大主流 IP 代表人物
        expected_checks = [
            ("流萤", "firefly_(honkai:_star_rail)"),
            ("黄泉", "acheron_(honkai:_star_rail)"),
            ("雷电将军", "raiden_shogun_(genshin_impact)"),
            ("芙宁娜", "furina_(genshin_impact)"),
            ("艾莲", "ellen_joe_(zenless_zone_zero)"),
            ("简·杜", "jane_doe_(zenless_zone_zero)"),
            ("阿米娅", "amiya_(arknights)"),
            ("早濑优香", "hayase_yuuka_(blue_archive)"),
            ("东海帝皇", "tokai_teio_(umamusume)"),
            ("初音未来", "hatsune_miku"),
            ("Saber", "artoria_pendragon_(fate)"),
            ("芙莉莲", "frieren"),
        ]
        for name, tag in expected_checks:
            tags = self.cm.get_tag(name)
            self.assertIsNotNone(tags, f"未找到角色: {name}")
            self.assertIn(tag, tags)

    def test_longest_match_priority(self):
        """测试长词优先匹配：雷电将军 优先于 影，式波·阿斯卡·兰格雷 优先于 明日香"""
        # "雷电将军" vs "影"
        rem, tags = self.cm.extract_and_replace("雷电将军")
        self.assertEqual(tags, ["raiden_shogun_(genshin_impact)"])
        self.assertEqual(rem, "")

        # "式波·阿斯卡·兰格雷"
        rem, tags = self.cm.extract_and_replace("式波·阿斯卡·兰格雷")
        self.assertEqual(tags, ["souryuu_asuka_langley"])
        self.assertEqual(rem, "")

    def test_pure_character_input(self):
        """测试纯角色名输入直接出 Tag，且剩余文本为空"""
        cases = [
            ("流萤", ["firefly_(honkai:_star_rail)"]),
            ("黄泉", ["acheron_(honkai:_star_rail)"]),
            ("雷电将军和八重神子", ["raiden_shogun_(genshin_impact)", "yae_miko_(genshin_impact)"]),
            ("流萤，知更鸟、花火", [
                "firefly_(honkai:_star_rail)",
                "robin_(honkai:_star_rail)",
                "sparkle_(honkai:_star_rail)",
            ]),
        ]
        for inp, expected_tags in cases:
            rem, tags = self.cm.extract_and_replace(inp)
            self.assertEqual(tags, expected_tags, f"Input: {inp}")
            self.assertEqual(rem, "", f"Remaining text should be empty for pure character input: {inp}")

    def test_character_with_description(self):
        """测试角色名 + 描述文本的精确剥离"""
        inp = "流萤，站在樱花树下微笑"
        rem, tags = self.cm.extract_and_replace(inp)
        self.assertEqual(tags, ["firefly_(honkai:_star_rail)"])
        self.assertEqual(rem, "站在樱花树下微笑")

        inp2 = "1girl, miku, smiling, blue sky"
        rem2, tags2 = self.cm.extract_and_replace(inp2)
        self.assertEqual(tags2, ["hatsune_miku"])
        self.assertEqual(rem2, "1girl, smiling, blue sky")

    def test_safe_word_boundary_and_compound_guard(self):
        """测试安全边界与合成词防护：避免倒影、摄影、天空误伤"""
        # "倒影"、"光影" 不应该匹配到 "影"
        rem, tags = self.cm.extract_and_replace("水面倒影，绝美光影")
        self.assertEqual(tags, [])
        self.assertEqual(rem, "水面倒影，绝美光影")

        # "摄影"
        rem, tags = self.cm.extract_and_replace("大师级摄影作品")
        self.assertEqual(tags, [])

        # 单独的 "影" 可以匹配
        rem, tags = self.cm.extract_and_replace("影，和服")
        self.assertEqual(tags, ["raiden_shogun_(genshin_impact)"])
        self.assertEqual(rem, "和服")

        # ASCII 单词边界："white hair" 不能匹配 "w"
        rem, tags = self.cm.extract_and_replace("white hair")
        self.assertEqual(tags, [])

        # 独立的 "W" 可以匹配
        rem, tags = self.cm.extract_and_replace("W, 1girl")
        self.assertEqual(tags, ["w_(arknights)"])
        self.assertEqual(rem, "1girl")

        # "mikuni" 不能匹配 "miku"
        rem, tags = self.cm.extract_and_replace("mikuni festival")
        self.assertEqual(tags, [])

    def test_custom_character_override(self):
        """测试用户自定义角色词表覆盖与新增"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
            custom_data = {
                "流萤": "custom_firefly_tag",
                "自定义原创角色": "original_character_(custom_series)",
            }
            json.dump(custom_data, f)
            custom_file = f.name

        try:
            custom_cm = CharacterManager(custom_path=custom_file)
            # 覆盖内置同名
            rem, tags = custom_cm.extract_and_replace("流萤")
            self.assertEqual(tags, ["custom_firefly_tag"])
            # 新增自定义
            rem, tags = custom_cm.extract_and_replace("自定义原创角色, 战斗服")
            self.assertEqual(tags, ["original_character_(custom_series)"])
            self.assertEqual(rem, "战斗服")
        finally:
            os.remove(custom_file)


if __name__ == "__main__":
    unittest.main()

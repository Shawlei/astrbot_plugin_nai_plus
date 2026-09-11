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
    """测试 CharacterManager 核心功能与边界防护"""

    def setUp(self):
        self.cm = CharacterManager()

    def test_characters_json_schema_and_validity(self):
        """检验 characters.json 数据合法性：有效 JSON、字典结构、无空键值且标签规范"""
        chars_path = BASE_DIR / "data" / "characters.json"
        self.assertTrue(chars_path.exists(), "data/characters.json 不存在")

        with open(chars_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIsInstance(data, dict, "characters.json 必须是 JSON 对象（字典）")
        self.assertGreaterEqual(len(data), 400, "characters.json 内置别名应在 400+ 个以上")

        for k, v in data.items():
            self.assertIsInstance(k, str, f"别名 key 必须为字符串: {k}")
            self.assertTrue(k.strip(), f"别名 key 不能为空: {k}")
            if isinstance(v, str):
                self.assertTrue(v.strip(), f"角色标签 value 不能为空: {k} -> {v}")
            elif isinstance(v, list):
                self.assertTrue(v, f"角色标签列表不能为空: {k} -> {v}")
                for tag in v:
                    self.assertTrue(str(tag).strip(), f"标签列表中包含空项: {k} -> {v}")
            else:
                self.fail(f"非法 value 类型: {k} -> {type(v)}")

    def test_builtin_characters_loaded(self):
        """测试内置角色库加载成功且数量充足"""
        self.assertGreaterEqual(len(self.cm._sorted_aliases), 400)

    def test_popular_ips_characters_hit(self):
        """验证七大主流 IP 热门角色及主要别名 100% 命中"""
        ip_groups = {
            "原神": [
                ("芙宁娜", "furina_(genshin_impact)"),
                ("水神", "furina_(genshin_impact)"),
                ("芙芙", "furina_(genshin_impact)"),
                ("雷电将军", "raiden_shogun_(genshin_impact)"),
                ("雷神", "raiden_shogun_(genshin_impact)"),
                ("巴尔泽布", "raiden_shogun_(genshin_impact)"),
                ("纳西妲", "nahida_(genshin_impact)"),
                ("草神", "nahida_(genshin_impact)"),
                ("小吉祥草王", "nahida_(genshin_impact)"),
                ("胡桃", "hu_tao_(genshin_impact)"),
                ("钟离", "zhongli_(genshin_impact)"),
                ("岩王帝君", "zhongli_(genshin_impact)"),
                ("刻晴", "keqing_(genshin_impact)"),
                ("神里绫华", "kamisato_ayaka_(genshin_impact)"),
                ("甘雨", "ganyu_(genshin_impact)"),
                ("夜兰", "yelan_(genshin_impact)"),
                ("优菈", "eula_(genshin_impact)"),
                ("八重神子", "yae_miko_(genshin_impact)"),
                ("玛薇卡", "mavuika_(genshin_impact)"),
                ("火神", "mavuika_(genshin_impact)"),
            ],
            "崩坏：星穹铁道": [
                ("流萤", "firefly_(honkai:_star_rail)"),
                ("黄泉", "acheron_(honkai:_star_rail)"),
                ("卡芙卡", "kafka_(honkai:_star_rail)"),
                ("银狼", "silver_wolf_(honkai:_star_rail)"),
                ("黑天鹅", "black_swan_(honkai:_star_rail)"),
                ("三月七", "march_7th_(honkai:_star_rail)"),
                ("花火", "sparkle_(honkai:_star_rail)"),
                ("知更鸟", "robin_(honkai:_star_rail)"),
                ("符玄", "fu_xuan_(honkai:_star_rail)"),
                ("阮梅", "ruan_mei_(honkai:_star_rail)"),
                ("阮妹", "ruan_mei_(honkai:_star_rail)"),
                ("阮·梅", "ruan_mei_(honkai:_star_rail)"),
                ("镜流", "jingliu_(honkai:_star_rail)"),
                ("托帕", "topaz_(honkai:_star_rail)"),
                ("飞霄", "feixiao_(honkai:_star_rail)"),
                ("灵砂", "lingsha_(honkai:_star_rail)"),
            ],
            "绝区零": [
                ("艾莲", "ellen_joe_(zenless_zone_zero)"),
                ("鲨鱼妹", "ellen_joe_(zenless_zone_zero)"),
                ("简·杜", "jane_doe_(zenless_zone_zero)"),
                ("鼠鼠", "jane_doe_(zenless_zone_zero)"),
                ("朱鸢", "zhu_yuan_(zenless_zone_zero)"),
                ("青衣", "qingyi_(zenless_zone_zero)"),
                ("妮可", "nicole_demara_(zenless_zone_zero)"),
                ("安比", "anby_demara_(zenless_zone_zero)"),
                ("猫又", "nekomiya_mana_(zenless_zone_zero)"),
                ("柏妮思", "burnice_white_(zenless_zone_zero)"),
                ("凯撒", "caesar_king_(zenless_zone_zero)"),
                ("星见雅", "hoshimi_miyabi_(zenless_zone_zero)"),
            ],
            "明日方舟": [
                ("阿米娅", "amiya_(arknights)"),
                ("德克萨斯", "texas_(arknights)"),
                ("拉普兰德", "lappland_(arknights)"),
                ("银灰", "silverash_(arknights)"),
                ("能天使", "exusiai_(arknights)"),
                ("陈", "chen_(arknights)"),
                ("水陈", "ch'en_the_holungday_(arknights)"),
                ("斯卡蒂", "skadi_(arknights)"),
                ("浊蒂", "skadi_the_corrupting_heart_(arknights)"),
                ("凯尔希", "kal'tsit_(arknights)"),
                ("艾雅法拉", "eyjafjalla_(arknights)"),
                ("W", "w_(arknights)"),
                ("维什戴尔", "wis'adel_(arknights)"),
            ],
            "蔚蓝档案": [
                ("早濑优香", "hayase_yuuka_(blue_archive)"),
                ("100kg", "hayase_yuuka_(blue_archive)"),
                ("一之濑明日奈", "ichinose_asuna_(blue_archive)"),
                ("角楯花凛", "kakudate_karin_(blue_archive)"),
                ("陆八魔阿露", "rikuhachima_aru_(blue_archive)"),
                ("白洲梓", "shirasu_azusa_(blue_archive)"),
                ("砂狼白子", "sunao_ookami_shiroko_(blue_archive)"),
                ("小鸟游星野", "takanashi_hoshino_(blue_archive)"),
                ("黑馆晴奈", "kurodate_haruna_(blue_archive)"),
                ("圣园未花", "misono_mika_(blue_archive)"),
                ("空崎日奈", "sorasaki_hina_(blue_archive)"),
                ("天童爱丽丝", "tendou_alice_(blue_archive)"),
            ],
            "赛马娘": [
                ("东海帝皇", "tokai_teio_(umamusume)"),
                ("无声铃鹿", "silence_suzuka_(umamusume)"),
                ("目白麦昆", "mejiro_mcqueen_(umamusume)"),
                ("特别周", "special_week_(umamusume)"),
                ("北部玄驹", "kitasan_black_(umamusume)"),
                ("米浴", "rice_shower_(umamusume)"),
                ("大和赤骥", "daiwa_scarlet_(umamusume)"),
                ("黄金船", "gold_ship_(umamusume)"),
                ("皮皮船", "gold_ship_(umamusume)"),
                ("鲁铎象征", "symboli_rudolf_(umamusume)"),
                ("会长", "symboli_rudolf_(umamusume)"),
            ],
            "经典二次元": [
                ("初音", "hatsune_miku"),
                ("初音未来", "hatsune_miku"),
                ("miku", "hatsune_miku"),
                ("Saber", "artoria_pendragon_(fate)"),
                ("吾王", "artoria_pendragon_(fate)"),
                ("芙莉莲", "frieren"),
                ("费伦", "fernen_(sousou_no_frieren)"),
                ("阿尼亚", "anya_forger"),
                ("约尔", "yor_forger"),
                ("五条悟", "gojou_satoru"),
                ("雷姆", "rem_(re:zero)"),
                ("波奇酱", "gotou_hitori"),
                ("后藤一里", "gotou_hitori"),
                ("御坂美琴", "misaka_mikoto"),
                ("炮姐", "misaka_mikoto"),
                ("祢豆子", "kamado_nezuko"),
                ("玛奇玛", "makima_(chainsaw_man)"),
            ],
        }

        for category, characters in ip_groups.items():
            for name, expected_tag in characters:
                tags = self.cm.get_tag(name)
                self.assertIsNotNone(tags, f"[{category}] 未找到角色/别名: {name}")
                self.assertIn(
                    expected_tag,
                    tags,
                    f"[{category}] 角色 {name} 映射错误，期望 {expected_tag}，实际 {tags}",
                )

    def test_longest_match_priority(self):
        """测试长词优先匹配：雷电将军 优先于 影，水陈 优先于 陈，式波·阿斯卡·兰格雷 优先于 明日香"""
        # "雷电将军" vs "影"
        rem, tags = self.cm.extract_and_replace("雷电将军")
        self.assertEqual(tags, ["raiden_shogun_(genshin_impact)"])
        self.assertEqual(rem, "")

        # "水陈" vs "陈"
        rem, tags = self.cm.extract_and_replace("水陈")
        self.assertEqual(tags, ["ch'en_the_holungday_(arknights)"])
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
            ("流萤 + 黄泉", [
                "firefly_(honkai:_star_rail)",
                "acheron_(honkai:_star_rail)",
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
        """测试安全边界与合成词防护：避免倒影、摄影、蓝天白云、绝美夜空误伤"""
        # "倒影"、"光影" 不应该匹配到 "影"
        rem, tags = self.cm.extract_and_replace("湖面倒影，绝美光影")
        self.assertEqual(tags, [])
        self.assertEqual(rem, "湖面倒影，绝美光影")

        # "蓝天白云"、"晴空万里" 不应该匹配到 "空"
        rem, tags = self.cm.extract_and_replace("蓝天白云，晴空万里")
        self.assertEqual(tags, [])
        self.assertEqual(rem, "蓝天白云，晴空万里")

        # "摄影"、"夜空"、"高空"
        rem, tags = self.cm.extract_and_replace("大师级摄影作品，绝美夜空与高空俯瞰")
        self.assertEqual(tags, [])
        self.assertEqual(rem, "大师级摄影作品，绝美夜空与高空俯瞰")

        # 独立或标点分隔的 "影" 可以匹配
        rem, tags = self.cm.extract_and_replace("影，和服")
        self.assertEqual(tags, ["raiden_shogun_(genshin_impact)"])
        self.assertEqual(rem, "和服")

        # 独立或标点分隔的 "空" 可以匹配
        rem, tags = self.cm.extract_and_replace("空，在草地上奔跑")
        self.assertEqual(tags, ["aether_(genshin_impact)"])
        self.assertEqual(rem, "在草地上奔跑")

        # ASCII 单词边界："white hair" 不能匹配 "w"
        rem, tags = self.cm.extract_and_replace("white hair, two swords")
        self.assertEqual(tags, [])

        # 独立的 "W" 可以匹配
        rem, tags = self.cm.extract_and_replace("W, 1girl")
        self.assertEqual(tags, ["w_(arknights)"])
        self.assertEqual(rem, "1girl")

        # "mikuni" 不能匹配 "miku"
        rem, tags = self.cm.extract_and_replace("mikuni festival")
        self.assertEqual(tags, [])

        # "sabertooth" 不能匹配 "saber"
        rem, tags = self.cm.extract_and_replace("sabertooth tiger")
        self.assertEqual(tags, [])

        # "100kgm" 不能匹配 "100kg"
        rem, tags = self.cm.extract_and_replace("100kgm weight")
        self.assertEqual(tags, [])

    def test_custom_character_override_and_reload(self):
        """测试用户自定义角色词表覆盖、新增以及 reload 热重载"""
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

            # 测试热重载 reload
            with open(custom_file, "w", encoding="utf-8") as f:
                json.dump({"流萤": "reloaded_firefly_tag"}, f)
            custom_cm.reload()
            rem, tags = custom_cm.extract_and_replace("流萤")
            self.assertEqual(tags, ["reloaded_firefly_tag"])
        finally:
            if os.path.exists(custom_file):
                os.remove(custom_file)

    def test_custom_character_error_tolerance(self):
        """测试自定义文件不存在或格式损坏时的平稳容错"""
        # 1. 文件不存在
        cm_missing = CharacterManager(custom_path="data/non_existent_file.json")
        self.assertGreaterEqual(len(cm_missing._sorted_aliases), 400)

        # 2. 损坏的 JSON
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
            f.write("{invalid_json: true,")
            corrupt_file = f.name

        try:
            cm_corrupt = CharacterManager(custom_path=corrupt_file)
            # 依然保留内置词表
            self.assertGreaterEqual(len(cm_corrupt._sorted_aliases), 400)
            self.assertIsNotNone(cm_corrupt.get_tag("流萤"))
        finally:
            if os.path.exists(corrupt_file):
                os.remove(corrupt_file)

    def test_helper_normalize_tags_and_clean_remaining(self):
        """测试辅助函数：标签规范化与剩余文本清洗"""
        # _normalize_tags
        self.assertEqual(_normalize_tags("tag1, tag2，tag3"), ["tag1", "tag2", "tag3"])
        self.assertEqual(_normalize_tags(["tag1", "tag2, tag3"]), ["tag1", "tag2", "tag3"])
        self.assertEqual(_normalize_tags(""), [])
        self.assertEqual(_normalize_tags(None), [])

        # _clean_remaining_text
        self.assertEqual(_clean_remaining_text("，、、站在树下；；"), "站在树下")
        self.assertEqual(_clean_remaining_text("和"), "")
        self.assertEqual(_clean_remaining_text("and"), "")
        self.assertEqual(_clean_remaining_text("与, 以及"), "")


if __name__ == "__main__":
    unittest.main()

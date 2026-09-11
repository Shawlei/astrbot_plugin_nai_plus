"""
TranslateManager 单元测试
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path

# 保证当前插件在 sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.translate_manager import (
    TranslateManager,
    _clean_result,
    _filter_chinese_residue,
    _has_chinese,
    SYSTEM_PROMPT,
)


class TestTranslateManager(unittest.TestCase):
    """测试 TranslateManager 核心逻辑"""

    def test_has_chinese(self):
        """测试中文字符检测"""
        self.assertFalse(_has_chinese("1girl, silver hair, red eyes"))
        self.assertTrue(_has_chinese("1girl, 银发, red eyes"))
        self.assertTrue(_has_chinese("站在樱花树下的女孩"))

    def test_filter_chinese_residue(self):
        """测试中文残留清洗过滤"""
        # 纯中文标签被丢弃
        res1 = _filter_chinese_residue("1girl, silver hair, 站在树下, white dress")
        self.assertEqual(res1, "1girl, silver hair, white dress")

        # 带有括号中文解释的标签剥离中文
        res2 = _filter_chinese_residue("1girl, smile (微笑), red eyes")
        self.assertEqual(res2, "1girl, smile, red eyes")

        # 带有中文字符和英文混合的标签
        res3 = _filter_chinese_residue("1girl, 白发 (white hair), 蓝眼睛 (blue eyes)")
        self.assertEqual(res3, "1girl, white hair, blue eyes")

    def test_clean_result(self):
        """测试模型返回结果清洗"""
        raw = "```text\nTags: 1girl, silver hair, smile\n```"
        self.assertEqual(_clean_result(raw), "1girl, silver hair, smile")

        raw_quotes = '"1girl, long hair, outdoors"'
        self.assertEqual(_clean_result(raw_quotes), "1girl, long hair, outdoors")

    def test_few_shot_in_system_prompt(self):
        """测试系统提示词包含 Few-shot 示例及规则约束"""
        self.assertIn("Examples:", SYSTEM_PROMPT)
        self.assertIn("站在樱花树下的微笑女孩", SYSTEM_PROMPT)
        self.assertIn("赛博朋克夜景街道", SYSTEM_PROMPT)
        self.assertIn("1.2::blue eyes::", SYSTEM_PROMPT)
        self.assertIn("artist:wanke", SYSTEM_PROMPT)
        self.assertIn("-2::umbrella::", SYSTEM_PROMPT)

    def test_fast_path_pure_character(self):
        """测试纯角色名输入直出官方 Tag，0 延迟无需模型调用"""
        tm = TranslateManager({"char_mapping_enabled": True, "translate_mode": "astrbot"})
        res = asyncio.run(tm.translate("流萤"))
        self.assertEqual(res, "firefly_(honkai:_star_rail)")

        res2 = asyncio.run(tm.translate("雷电将军和八重神子"))
        self.assertEqual(res2, "raiden_shogun_(genshin_impact), yae_miko_(genshin_impact)")

    def test_character_plus_model_translation_merge(self):
        """测试角色名提取与模型翻译结果合并"""
        tm = TranslateManager({"char_mapping_enabled": True, "translate_mode": "astrbot"})
        tm.context = True

        async def mock_translate(candidate, text, sys_prompt=None):
            # 模拟模型将 "站在樱花树下" 翻译为 Danbooru 标签
            return "1girl, standing, sakura tree, cherry blossoms"

        tm._translate_candidate = mock_translate
        tm._resolve_astrbot_candidates = lambda: ["mock_provider"]

        res = asyncio.run(tm.translate("流萤，站在樱花树下"))
        expected = "firefly_(honkai:_star_rail), 1girl, standing, sakura tree, cherry blossoms"
        self.assertEqual(res, expected)

    def test_chinese_residue_retry_and_filter(self):
        """测试中文残留自动重试与兜底清洗机制"""
        tm = TranslateManager({"char_mapping_enabled": True, "translate_mode": "astrbot"})
        tm.context = True

        call_records: list[str] = []

        async def mock_translate_via_astrbot(candidate, text, sys_prompt=None):
            call_records.append(text)
            if len(call_records) == 1:
                # 第一次返回包含中文
                return "1girl, 银发女孩, red eyes"
            else:
                # 重试返回纯英文
                return "1girl, silver hair, red eyes"

        tm._translate_via_astrbot = mock_translate_via_astrbot
        tm._resolve_astrbot_candidates = lambda: ["mock_provider"]

        res = asyncio.run(tm.translate("银发红眼的女孩"))
        # 校验是否触发了重试
        self.assertEqual(len(call_records), 2)
        self.assertIn("CRITICAL", call_records[1])
        self.assertEqual(res, "1girl, silver hair, red eyes")


if __name__ == "__main__":
    unittest.main()

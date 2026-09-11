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
    TranslateError,
    TranslateManager,
    _clean_result,
    _filter_chinese_residue,
    _has_chinese,
    SYSTEM_PROMPT,
)


class TestTranslateManager(unittest.TestCase):
    """测试 TranslateManager 核心逻辑与边界特性"""

    def test_has_chinese(self):
        """测试中文字符检测边界"""
        self.assertFalse(_has_chinese("1girl, silver hair, red eyes"))
        self.assertFalse(_has_chinese(""))
        self.assertFalse(_has_chinese("   "))
        self.assertFalse(_has_chinese("1.2::blue eyes::, {{masterpiece}}"))
        self.assertTrue(_has_chinese("1girl, 银发, red eyes"))
        self.assertTrue(_has_chinese("站在樱花树下的女孩"))
        self.assertTrue(_has_chinese("smile (微笑)"))

    def test_filter_chinese_residue(self):
        """测试中文残留清洗与兜底过滤"""
        # 1. 纯中文标签被丢弃
        res1 = _filter_chinese_residue("1girl, silver hair, 站在树下, white dress")
        self.assertEqual(res1, "1girl, silver hair, white dress")

        # 2. 带有英文括号说明的标签剔除中文
        res2 = _filter_chinese_residue("1girl, smile (微笑), red eyes")
        self.assertEqual(res2, "1girl, smile, red eyes")

        # 3. 带有全角中文括号说明的标签剔除中文
        res2_cn = _filter_chinese_residue("1girl, smile（微笑）, red eyes")
        self.assertEqual(res2_cn, "1girl, smile, red eyes")

        # 4. 中文前缀/后缀混杂标签
        res3 = _filter_chinese_residue("1girl, 白发 (white hair), 蓝眼睛 (blue eyes)")
        self.assertEqual(res3, "1girl, white hair, blue eyes")

        # 5. 空括号或纯标点清理（包含非汉字无效标签）
        res4 = _filter_chinese_residue("1girl, (), ( ), -, ::, red eyes")
        self.assertEqual(res4, "1girl, red eyes")

        # 6. 纯符号标签丢弃
        res5 = _filter_chinese_residue("(), (  ), [  ], {  }")
        self.assertEqual(res5, "")

    def test_clean_result(self):
        """测试模型返回结果清洗（代码块、引号、前缀、多行）"""
        # markdown 代码块包裹
        raw = "```text\nTags: 1girl, silver hair, smile\n```"
        self.assertEqual(_clean_result(raw), "1girl, silver hair, smile")

        raw_tags = "```tags\n1girl, long hair, smiling\n```"
        self.assertEqual(_clean_result(raw_tags), "1girl, long hair, smiling")

        # 首尾引号
        raw_quotes = '"1girl, long hair, outdoors"'
        self.assertEqual(_clean_result(raw_quotes), "1girl, long hair, outdoors")

        raw_cn_quotes = '“1girl, solo, upper body”'
        self.assertEqual(_clean_result(raw_cn_quotes), "1girl, solo, upper body")

        raw_cn_single = '‘1girl, solo, upper body’'
        self.assertEqual(_clean_result(raw_cn_single), "1girl, solo, upper body")

        raw_corner_quotes = '「1girl, solo, upper body」'
        self.assertEqual(_clean_result(raw_corner_quotes), "1girl, solo, upper body")

        # 解释性前缀
        prefixes = [
            ("Tags: 1girl, smile", "1girl, smile"),
            ("tags: 1girl, smile", "1girl, smile"),
            ("Result: 1girl, smile", "1girl, smile"),
            ("Here are the tags: 1girl, smile", "1girl, smile"),
            ("Here is the translation: 1girl, smile", "1girl, smile"),
            ("翻译：1girl, smile", "1girl, smile"),
            ("标签：1girl, smile", "1girl, smile"),
            ("结果：1girl, smile", "1girl, smile"),
        ]
        for inp, exp in prefixes:
            self.assertEqual(_clean_result(inp), exp)

        # 多行文本取有效标签行
        raw_multiline = "Here are your Danbooru tags:\n1girl, silver hair, red eyes\nHope this helps!"
        self.assertEqual(_clean_result(raw_multiline), "1girl, silver hair, red eyes")

    def test_system_prompt_complete_six_few_shots(self):
        """测试系统提示词完整包含 6 组高质量 Few-shot 示例及 9 条规则约束"""
        self.assertIn("Examples:", SYSTEM_PROMPT)
        # 6 组 input 示例
        expected_inputs = [
            "站在樱花树下的微笑女孩，微风吹拂长发，阳光洒落",
            "赛博朋克夜景街道，雨水倒影，霓虹灯光，湿润的地面",
            "1.2::blue eyes::, 穿着白色连衣裙, 露肩, {{masterpiece}}",
            "artist:wanke, 趴在床上的猫耳少女，慵懒表情，午后阳光",
            "-2::umbrella::, [black jacket], 雨中奔跑，动感姿态",
            "银发红瞳的吸血鬼少女，哥特洋装，红色满月背景",
        ]
        for ex in expected_inputs:
            self.assertIn(ex, SYSTEM_PROMPT, f"SYSTEM_PROMPT 缺少示例输入: {ex}")

        # 关键规则检查
        self.assertIn("PRESERVE any NovelAI weight syntax", SYSTEM_PROMPT)
        self.assertIn("Preserve artist tags", SYSTEM_PROMPT)
        self.assertIn("Never output Chinese characters", SYSTEM_PROMPT)
        self.assertEqual(SYSTEM_PROMPT.count("Input:"), 6)
        self.assertEqual(SYSTEM_PROMPT.count("Output:"), 6)

    def test_fast_path_pure_character(self):
        """测试纯角色名输入直出官方 Tag，0 延迟无需模型调用"""
        tm = TranslateManager({"char_mapping_enabled": True, "translate_mode": "astrbot"})
        res = asyncio.run(tm.translate("流萤"))
        self.assertEqual(res, "firefly_(honkai:_star_rail)")

        res2 = asyncio.run(tm.translate("雷电将军和八重神子"))
        self.assertEqual(res2, "raiden_shogun_(genshin_impact), yae_miko_(genshin_impact)")

    def test_character_plus_model_translation_merge_and_dedup(self):
        """测试角色名置前与模型翻译结果去重合并"""
        tm = TranslateManager({"char_mapping_enabled": True, "translate_mode": "astrbot"})
        tm.context = True

        async def mock_translate(candidate, text, sys_prompt=None):
            # 模拟模型输出（模型也可能重复返回了角色 tag 或 1girl）
            return "firefly_(honkai:_star_rail), 1girl, standing, sakura tree"

        tm._translate_candidate = mock_translate
        tm._resolve_astrbot_candidates = lambda: ["mock_provider"]

        res = asyncio.run(tm.translate("流萤，站在樱花树下"))
        # 角色 tag 置于最前，且去重
        expected = "firefly_(honkai:_star_rail), 1girl, standing, sakura tree"
        self.assertEqual(res, expected)

    def test_chinese_residue_retry_success(self):
        """测试首次输出含中文时触发重试，重试返回纯英文标签成功"""
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
        self.assertEqual(len(call_records), 2)
        self.assertIn("CRITICAL", call_records[1])
        self.assertEqual(res, "1girl, silver hair, red eyes")

    def test_chinese_residue_retry_failure_then_filter_fallback(self):
        """测试重试后依然含中文残留时，自动触发正则清洗兜底过滤"""
        tm = TranslateManager({"char_mapping_enabled": True, "translate_mode": "astrbot"})
        tm.context = True

        call_records: list[str] = []

        async def mock_translate_via_astrbot(candidate, text, sys_prompt=None):
            call_records.append(text)
            # 两次均返回包含中文残留
            return "1girl, 白发 (white hair), 微笑 (smile), 纯中文描述"

        tm._translate_via_astrbot = mock_translate_via_astrbot
        tm._resolve_astrbot_candidates = lambda: ["mock_provider"]

        res = asyncio.run(tm.translate("白发微笑的女孩"))
        self.assertEqual(len(call_records), 2)
        # 兜底清洗后无中文字符，且提取有效英文
        self.assertFalse(_has_chinese(res))
        self.assertEqual(res, "1girl, white hair, smile")

    def test_close_lifecycle(self):
        """测试 TranslateManager 优雅关闭与会话清理"""
        tm = TranslateManager({"translate_mode": "openai", "translate_openai_base_url": "https://api.test/v1", "translate_openai_model": "test"})
        # close 应能安全调用且不抛异常
        asyncio.run(tm.close())


if __name__ == "__main__":
    unittest.main()

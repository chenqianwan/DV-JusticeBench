import unittest

from utils.evaluator import AnswerEvaluator


class ErrorFlagParsingTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = AnswerEvaluator(flag_parser_version="fixed")
        self.scores = {
            "规范依据相关性": 3.0,
            "涵摄链条对齐度": 3.0,
            "价值衡量与同理心对齐度": 3.0,
            "关键事实与争点覆盖度": 3.0,
            "裁判结论与救济配置一致性": 3.0,
        }

    def test_no_error_phrases_do_not_trigger_penalties(self):
        text = """【错误标记】（如有，请按严重程度分类）：
- 微小错误：规范引用略有缺漏。
- 明显错误：无。
- 重大错误：无重大错误，未编造事实或法条。
---
### 法律分析补充
重大错误：这一行不属于错误标记区块。
"""
        errors = self.evaluator._detect_flags(text, self.scores)
        self.assertEqual(errors["微小错误"], ["规范引用略有缺漏。"])
        self.assertEqual(errors["明显错误"], [])
        self.assertEqual(errors["重大错误"], [])

    def test_bold_numbered_items_are_split_by_level(self):
        text = """【错误标记】：
- **微小错误**：
  1. 法条名称表述不够精确。
  2. 个别事实未展开。
- **明显错误**：
  1. 遗漏关键救济措施。
- **重大错误**：未发现重大错误。
"""
        errors = self.evaluator._detect_flags(text, self.scores)
        self.assertEqual(len(errors["微小错误"]), 2)
        self.assertEqual(errors["明显错误"], ["遗漏关键救济措施。"])
        self.assertEqual(errors["重大错误"], [])

    def test_same_line_first_level_is_not_consumed_with_marker(self):
        text = """【错误标记】：- 微小错误：无
- 明显错误：存在关键事实误读。
- 重大错误：不存在。
"""
        errors = self.evaluator._detect_flags(text, self.scores)
        self.assertEqual(errors["微小错误"], [])
        self.assertEqual(errors["明显错误"], ["存在关键事实误读。"])
        self.assertEqual(errors["重大错误"], [])


if __name__ == "__main__":
    unittest.main()

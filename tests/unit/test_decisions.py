"""Decision 契约的单元测试。

目的不是测功能，而是锁死"结论只有一种合法写法"这条规矩：
任何一层想自己拼一个结论，都会被 build/validate 拒掉。
"""
import json
import unittest

import decisions


class BuildTest(unittest.TestCase):
    def test_each_status_builds(self):
        cases = [
            ("pending", "awaiting_body", {"kind": "none", "value": None}),
            ("complete", "body_acquired", {"kind": "raw_body", "value": "raw/000123.body"}),
            ("out_of_scope", "foreign_bar", {"kind": "url_bar", "value": "of012323"}),
            ("out_of_scope", "non_ordinary", {"kind": "detail_post_type", "value": 2}),
            ("removed", "source_missing", {"kind": "source_page", "value": "not_found"}),
            ("list_only", "not_needed", {"kind": "none", "value": None}),
        ]
        for status, reason, evidence in cases:
            with self.subTest(status=status, reason=reason):
                d = decisions.build("p1", status, reason, evidence=evidence)
                self.assertEqual(d["status"], status)
                self.assertEqual(decisions.validate(d), [])

    def test_mismatched_reason_rejected(self):
        # "取到正文"不能配"跨吧"这种理由——这正是三层各算一遍时最容易错配的地方
        with self.assertRaises(decisions.DecisionError):
            decisions.build("p1", "complete", "foreign_bar",
                            evidence={"kind": "raw_body", "value": "raw/x.body"})

    def test_mismatched_evidence_rejected(self):
        with self.assertRaises(decisions.DecisionError):
            decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "raw_body", "value": "raw/x.body"})

    def test_unknown_reason_rejected(self):
        with self.assertRaises(decisions.DecisionError):
            decisions.build("p1", "out_of_scope", "whatever_new_case",
                            evidence={"kind": "url_bar", "value": "bk1033"})

    def test_unknown_status_rejected(self):
        with self.assertRaises(decisions.DecisionError):
            decisions.build("p1", "skipped", "foreign_bar",
                            evidence={"kind": "url_bar", "value": "bk1033"})

    def test_evidence_value_required(self):
        with self.assertRaises(decisions.DecisionError):
            decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "url_bar", "value": ""})


class ValidateTest(unittest.TestCase):
    def test_tampered_decision_reports_problems(self):
        d = decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "url_bar", "value": "of012323"})
        d["status"] = "complete"          # 模拟"某一层自己改结论"
        self.assertTrue(decisions.validate(d))

    def test_missing_post_id(self):
        d = decisions.build("p1", "pending", "awaiting_body")
        d["post_id"] = ""
        self.assertIn("缺少 post_id", decisions.validate(d))

    def test_version_mismatch(self):
        d = decisions.build("p1", "pending", "awaiting_body")
        d["v"] = 999
        self.assertTrue(any("版本" in p for p in decisions.validate(d)))


class ConsistencyTest(unittest.TestCase):
    def test_same_fact(self):
        a = decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "url_bar", "value": "of012323"})
        b = decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "url_bar", "value": "of012323"})
        self.assertTrue(decisions.is_consistent(a, b))

    def test_different_evidence(self):
        a = decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "url_bar", "value": "of012323"})
        b = decisions.build("p1", "out_of_scope", "foreign_bar",
                            evidence={"kind": "url_bar", "value": "bk1033"})
        self.assertFalse(decisions.is_consistent(a, b))


class ScopeDecisionTest(unittest.TestCase):
    class Item:
        source_item_id = "1694320491"

    def test_foreign_bar_by_url(self):
        d, conflict = decisions.detail_scope_decision(
            self.Item(), {"post_type": 0},   # 页面里写着普通帖，URL 却是基金吧
            new_request={"url": "https://guba.eastmoney.com/news,of012323,1694320491.html"})
        self.assertIsNone(conflict)
        self.assertEqual(d["reason"], "foreign_bar")
        self.assertEqual(d["evidence"]["value"], "of012323")

    def test_non_ordinary_by_type(self):
        d, conflict = decisions.detail_scope_decision(
            self.Item(), {"post_type": 2},
            new_request={"url": "https://guba.eastmoney.com/news,002230,1694320491.html"})
        self.assertIsNone(conflict)
        self.assertEqual(d["reason"], "non_ordinary")

    def test_ordinary_returns_nothing(self):
        d, conflict = decisions.detail_scope_decision(
            self.Item(), {"post_type": 0},
            new_request={"url": "https://guba.eastmoney.com/news,002230,1694320491.html"})
        self.assertIsNone(d)
        self.assertIsNone(conflict)

    def test_missing_type_is_reported_not_guessed(self):
        d, conflict = decisions.detail_scope_decision(
            self.Item(), {}, new_request={"url": "https://guba.eastmoney.com/news,002230,1.html"})
        self.assertIsNone(d)
        self.assertIn("详情类型缺失", conflict)


class SerializationTest(unittest.TestCase):
    def test_round_trip(self):
        d = decisions.build("p1", "removed", "source_missing",
                            evidence={"kind": "http_status", "value": 404})
        self.assertEqual(decisions.validate(json.loads(json.dumps(d))), [])


if __name__ == "__main__":
    unittest.main()

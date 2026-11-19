"""Offline NHPLUG quote grounding checks; never calls a broker API."""

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.grounding import GroundingLedger


class NHPlugGroundingTests(unittest.TestCase):
    def _ledger(self, tool, arguments, payload):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        ledger = GroundingLedger(run_dir=Path(temp_dir.name), user_message="005930 현재가")
        ledger.ingest_tool_result(
            tool_name=tool,
            arguments=arguments,
            result=json.dumps(payload),
            call_id="nh-quote",
            success=True,
        )
        return ledger

    def test_native_quote_records_krw_krx_price(self):
        ledger = self._ledger(
            "mcp_nhplug_readonly_get_stock_price",
            {"stock_code": "005930"},
            {"status": "ok", "data": {"Output_0": {"stck_prpr": 123400, "mrkt_div_isnm": "코스피", "iem_cd": "005930"}, "rsp_cd": "00000"}},
        )
        self.assertEqual(len(ledger._evidence), 1)
        record = ledger._evidence[0]
        self.assertEqual(record.symbol, "005930.KS")
        self.assertEqual(record.field, "data.Output_0.stck_prpr")
        self.assertEqual(record.currency, "KRW")
        self.assertEqual(record.venue, "kospi")
        self.assertEqual(record.value, 123400)
        self.assertEqual([r.field for r in ledger._comparable_price_records()], ["price"])

    def test_missing_or_non_nh_quote_does_not_fallback_to_generic_json(self):
        for tool, data in (
            ("mcp_nhplug_readonly_get_stock_price", {"Output_0": {"other": 123400, "mrkt_div_isnm": "코스피"}, "rsp_cd": "00000"}),
            ("mcp_nhplug_readonly_get_stock_price", {"Output_0": {"stck_prpr": "123400", "mrkt_div_isnm": "코스피"}, "rsp_cd": "00000"}),
        ):
            with self.subTest(tool=tool, data=data):
                ledger = self._ledger(tool, {"stock_code": "005930"}, {"status": "ok", "data": data})
                self.assertEqual(ledger._evidence, [])

    def test_non_object_result_and_unbound_symbols_are_rejected(self):
        for payload in (
            None,
            {"status": "ok", "data": {"rsp_cd": "00000", "Output_0": {
                "stck_prpr": 123400, "mrkt_div_isnm": "코스피",
            }}},
            {"status": "ok", "data": {"rsp_cd": "00000", "Output_0": {
                "stck_prpr": 123400, "mrkt_div_isnm": "코스피", "iem_cd": "000660",
            }}},
        ):
            with self.subTest(payload=payload):
                ledger = self._ledger(
                    "mcp_nhplug_readonly_get_stock_price", {"stock_code": "005930"}, payload,
                )
                self.assertEqual(ledger._evidence, [])


if __name__ == "__main__":
    unittest.main()

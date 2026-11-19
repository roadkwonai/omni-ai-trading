"""Read company disclosures and financial statements from Open DART."""
from __future__ import annotations

import json
import io
import time
import zipfile
import tempfile
from pathlib import Path

from defusedxml import ElementTree
import os
import re
from datetime import datetime

import requests

from src.agent.tools import BaseTool
from src.config.accessor import get_env_config


class OpenDartTool(BaseTool):
    name = "opendart"
    description = "Open DART 공시와 재무 조회. corp_code는 8자리 DART 고유번호이며 주식코드가 아니다. 재무의 최초 공개일은 공시에서 별도로 확인한다."
    is_readonly = True
    repeatable = True
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["company", "disclosures", "financials"]},
            "stock_code": {"type": "string", "description": "상장 종목코드 6자리"},
            "corp_code": {"type": "string", "description": "DART 고유번호 8자리"},
            "start_date": {"type": "string", "description": "공시 접수 시작 YYYYMMDD"},
            "end_date": {"type": "string", "description": "공시 접수 종료 YYYYMMDD"},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "year": {"type": "string", "description": "사업연도 YYYY"},
            "report_code": {"type": "string", "enum": ["11011", "11012", "11013", "11014"]},
        },
        "required": ["action"],
    }

    def execute(self, action: str, corp_code: str = "", start_date: str = "", end_date: str = "", page: int = 1, year: str = "", report_code: str = "", stock_code: str = "", **kwargs) -> str:
        def out(payload):
            return json.dumps(payload, ensure_ascii=False)

        key = get_env_config().data.opendart_api_key.strip()
        if not key:
            return out({"status": "error", "error": "OPENDART_API_KEY is not configured"})
        if action == "company":
            if not isinstance(stock_code, str) or not re.fullmatch(r"[0-9]{6}", stock_code):
                return out({"status": "error", "error": "stock_code must be 6 digits"})
            try:
                from src.config.paths import get_runtime_root
                path = get_runtime_root() / "cache" / "opendart-corpcode.xml"
                if path.is_file() and time.time() - path.stat().st_mtime < 86400:
                    xml = path.read_bytes()
                else:
                    response = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=30, allow_redirects=False)
                    if response.status_code != 200 or len(response.content) > 64 * 1024 * 1024:
                        raise ValueError("invalid response")
                    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                        member = archive.getinfo("CORPCODE.xml")
                        if member.file_size > 64 * 1024 * 1024:
                            raise ValueError("archive too large")
                        xml = archive.read(member)
                    ElementTree.fromstring(xml)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".opendart-")
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(xml)
                        os.replace(name, path)
                    finally:
                        if os.path.exists(name): os.unlink(name)
                root = ElementTree.fromstring(xml)
                for row in root.findall("list"):
                    if (row.findtext("stock_code") or "").strip() == stock_code:
                        return out({"status": "ok", "source": "Open DART", "data": {"stock_code": stock_code, "corp_code": row.findtext("corp_code"), "corp_name": row.findtext("corp_name")}})
                return out({"status": "no_data", "source": "Open DART"})
            except Exception:
                return out({"status": "error", "error": "Open DART company lookup failed"})
        if not isinstance(corp_code, str) or not re.fullmatch(r"[0-9]{8}", corp_code):
            return out({"status": "error", "error": "corp_code must be 8 digits"})
        params = {"crtfc_key": key, "corp_code": corp_code}
        if action == "disclosures":
            try:
                start = datetime.strptime(start_date, "%Y%m%d")
                end = datetime.strptime(end_date, "%Y%m%d")
                if start > end or len(start_date) != 8 or len(end_date) != 8 or isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 99999:
                    raise ValueError
            except (ValueError, TypeError):
                return out({"status": "error", "error": "valid start_date, end_date and page are required"})
            path = "list.json"
            params.update(bgn_de=start_date, end_de=end_date, page_no=page, page_count=100)
        elif action == "financials":
            if not isinstance(year, str) or not re.fullmatch(r"[0-9]{4}", year) or report_code not in {"11011", "11012", "11013", "11014"}:
                return out({"status": "error", "error": "valid year and report_code are required"})
            path = "fnlttSinglAcnt.json"
            params.update(bsns_year=year, reprt_code=report_code)
        else:
            return out({"status": "error", "error": "unknown action"})
        try:
            response = requests.get("https://opendart.fss.or.kr/api/" + path, params=params, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                return out({"status": "error", "error": f"Open DART HTTP {response.status_code}"})
            data = response.json()
        except Exception:
            return out({"status": "error", "error": "Open DART request failed"})
        if not isinstance(data, dict):
            return out({"status": "error", "error": "invalid Open DART response"})
        status = str(data.get("status", ""))
        if status not in {"000", "013"}:
            return out({"status": "error", "error": "Open DART returned error", "code": status})
        result = {"status": "ok" if status == "000" else "no_data", "source": "Open DART", "data": data}
        if action == "financials":
            result["notice"] = "사업연도는 공시일이 아닙니다. 과거시점 분석 전 해당 보고서의 접수일을 확인하세요."
        return out(result)

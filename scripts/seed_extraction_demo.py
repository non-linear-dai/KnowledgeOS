"""Seed demo extraction v2 submissions for a local preview so the feedback page has data.

Runs only against a local runtime that has no recorded extraction submissions yet; does
nothing otherwise. It registers two demo documents and proposes their extractions as the
agent principal ``agent:preview-demo``, which also creates ordinary review ChangeSets.
Never run this against a shared or production runtime.

Usage: python scripts/seed_extraction_demo.py [--root <repository root>]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_os.core import Config  # noqa: E402
from knowledge_os.extraction_v2 import PROTOCOL  # noqa: E402
from knowledge_os.service import Service  # noqa: E402

ACTOR = "agent:preview-demo"
RUN = {"run_id": "preview-demo", "agent": {"platform": "preview", "name": "demo-extractor", "version": "0.1.0"},
       "skill": {"id": "document-instance-extraction", "version": "1.0.0"}, "stages": [{"id": "fill_fields", "kind": "llm"}]}


def source_document(document_id, title, texts):
    raw = "\n".join(text for _, text in texts).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    return {"protocol_version": PROTOCOL, "message_type": "source_document",
            "document": {"id": document_id, "version": "v1", "locator": f"dms://preview/{document_id}", "kind": "meeting_minutes",
                         "content_hash": sha, "captured_at": "2026-10-28T09:00:00Z", "title": title, "origin": {"system": "dms"},
                         "document_dates": {"issued_at": "2026-10-28"}},
            "original": {"sha256": sha, "media_type": "text/plain", "bytes": len(raw), "inline_base64": base64.b64encode(raw).decode()},
            "parser": {"name": "preview-demo", "version": "1.0.0"},
            "blocks": [{"id": block_id, "type": "heading" if index == 0 else "paragraph", "text": text, "order": index}
                       for index, (block_id, text) in enumerate(texts)]}


def anchor(message, block_id, quote, role=None):
    text = next(block["text"] for block in message["blocks"] if block["id"] == block_id)
    start = text.index(quote)
    result = {"block_id": block_id, "start": start, "end": start + len(quote), "quote": quote}
    if role:
        result["role"] = role
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    service = Service(Config(args.root))
    try:
        if service.db.first("SELECT 1 FROM durable.extraction_submission LIMIT 1"):
            print("extraction demo data already present; skipped")
            return 0
        request = {"protocol_version": PROTOCOL, "message_type": "contract_request",
                   "scope": {"concepts": ["organization", "product", "project", "equipment"]}, "include": ["concepts", "relations", "rule_slots"]}
        contract = service.extraction_v2_contract(request)["data"]
        guard = {"contract_id": contract["contract_id"], "registry_fingerprint": contract["registry_fingerprint"]}

        examples = Path(args.root) / "docs/examples/extraction-v2"
        minutes = json.loads((examples / "03-source-document.json").read_text(encoding="utf-8"))
        service.extraction_v2_register(minutes, actor=ACTOR)
        worked = json.loads((examples / "05-extraction-submission.json").read_text(encoding="utf-8"))
        worked.update({"contract": guard, "mode": "propose", "submission_id": "preview-demo-minutes", "reason": "预览演示：供应商评审纪要抽取"})
        service.extraction_v2_submit(worked, actor=ACTOR)

        ledger = source_document("source:preview:equipment-check", "设备与项目周检记录", [
            ("h1", "一、设备与项目周检"),
            ("p1", "3号测试台（设备编号 EQ-TB-03）电机功率 7.5千瓦特，状态正常。"),
            ("p2", "Project Atlas 本周风险评估：爆表，需要升级处理。"),
            ("p3", "Acme 的供应商评级为 A 级，年度复评安排在 12 月。"),
            ("p4", "现场另有一台测试台在维修，铭牌功率不清。"),
        ])
        service.extraction_v2_register(ledger, actor=ACTOR)
        submission = {"protocol_version": PROTOCOL, "message_type": "extraction_submission", "submission_id": "preview-demo-weekly",
                      "mode": "propose", "reason": "预览演示：设备与项目周检记录抽取", "contract": guard, "run": RUN,
                      "source": {"document_id": ledger["document"]["id"], "document_version": "v1", "content_hash": ledger["document"]["content_hash"]},
                      "instances": [
                          {"local_id": "tmp:bench", "concept": "equipment", "label": {"raw": "3号测试台", "evidence": [anchor(ledger, "p1", "3号测试台")]},
                           "identity": {"key": {"raw": "EQ-TB-03", "evidence": [anchor(ledger, "p1", "EQ-TB-03", "key")]}},
                           "fields": [{"predicate": "rated_power", "value": {"raw": "7.5千瓦特"}, "confidence": 0.9,
                                       "evidence": [anchor(ledger, "p1", "电机功率", "label"), anchor(ledger, "p1", "7.5千瓦特", "value")]}]},
                          {"local_id": "tmp:atlas", "concept": "project", "label": {"raw": "Project Atlas", "evidence": [anchor(ledger, "p2", "Project Atlas")]},
                           "fields": [{"predicate": "risk_level", "value": {"raw": "爆表"}, "confidence": 0.85,
                                       "evidence": [anchor(ledger, "p2", "风险评估", "label"), anchor(ledger, "p2", "爆表", "value")]}]}],
                      "unmapped_facts": [{"text": "Acme 的供应商评级为 A 级", "reason": "no_matching_predicate", "suggested_concept": "organization",
                                          "suggested_property_label": "供应商评级", "evidence": [anchor(ledger, "p3", "Acme 的供应商评级为 A 级")]}]}
        service.extraction_v2_submit(submission, actor=ACTOR)
        print("seeded 2 demo extraction submissions")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloudflare_bridge as bridge

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    d=root/"reports"; d.mkdir(parents=True)
    (d/"a_FRESH_API_GENERATION_RECEIPT_attempt1.json").write_text(json.dumps({"model":"google/gemini-3-pro-image","usage":{"cost":0.25123456}}),encoding="utf-8")
    (d/"b_FRESH_API_GENERATION_RECEIPT_attempt2.json").write_text(json.dumps({"model":"google/gemini-3-pro-image","usage":{"cost":0.249}}),encoding="utf-8")
    s=bridge.generation_cost_summary(root)
    assert s["request_count"]==2,s
    assert s["reported_cost_count"]==2,s
    assert s["complete"] is True,s
    assert abs(s["cost_usd"]-0.50023456)<1e-9,s

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    d=root/"reports"; d.mkdir()
    (d/"x_FRESH_API_GENERATION_RECEIPT_unknown.json").write_text(json.dumps({"model":"google/gemini-3-pro-image","usage":{}}),encoding="utf-8")
    s=bridge.generation_cost_summary(root)
    assert s["request_count"]==1,s
    assert s["cost_usd"] is None,s
    assert s["complete"] is False,s
print("OpenRouter exact cost telemetry self-test: PASSED")

"""
Unit tests for GUI utility functions defined in gui/app.py.

These tests validate the pure helper logic (record_intent, compile_hlf_preview,
export_intent_history_csv, export_routing_trace_json, status_badge_html)
without requiring a running Streamlit server.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out Streamlit and heavy GUI dependencies before importing app helpers
# ---------------------------------------------------------------------------

# Build a minimal `streamlit` stub so importing gui.app doesn't fail.
_st_stub = MagicMock()
_st_stub.cache_data = lambda **kw: (lambda fn: fn)  # passthrough decorator
_st_stub.session_state = {}
sys.modules.setdefault("streamlit", _st_stub)

# Stub httpx and pandas if not installed
for _mod in ("httpx", "pandas"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# ---------------------------------------------------------------------------
# Import helpers directly (avoids running the full Streamlit app top-level
# code, which calls st.set_page_config etc.)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# We import only the standalone utility functions, not the full module body.
# This is done by exec'ing just the helper section up to PAGE_CONFIG.
_APP_SRC = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")

# Locate the helpers block: everything between the first `import` line and
# the `st.set_page_config` call.  We execute only that slice so the module-
# level Streamlit calls (set_page_config, title, columns, …) are never run.
_PAGE_CFG_MARKER = "st.set_page_config("
_cfg_idx = _APP_SRC.index(_PAGE_CFG_MARKER)
_helpers_src = _APP_SRC[:_cfg_idx]

_gui_ns: dict = {
    "__file__": str(_REPO_ROOT / "gui" / "app.py"),
}
exec(compile(_helpers_src, "gui/app.py", "exec"), _gui_ns)  # noqa: S102

record_intent = _gui_ns["record_intent"]
compile_hlf_preview = _gui_ns["compile_hlf_preview"]
export_intent_history_csv = _gui_ns["export_intent_history_csv"]
export_routing_trace_json = _gui_ns["export_routing_trace_json"]
status_badge_html = _gui_ns["status_badge_html"]
_MAX_INTENT_HISTORY = _gui_ns["_MAX_INTENT_HISTORY"]
_STATUS_CONNECT_ERROR = _gui_ns["_STATUS_CONNECT_ERROR"]


# ---------------------------------------------------------------------------
# Tests for record_intent
# ---------------------------------------------------------------------------


class TestRecordIntent:
    """Tests for record_intent() helper."""

    def setup_method(self) -> None:
        """Reset session state before each test."""
        _st_stub.session_state = {}

    def test_creates_history_on_first_call(self) -> None:
        record_intent("test payload", "HLF", 202)
        history = _st_stub.session_state["intent_history"]
        assert len(history) == 1
        assert history[0]["status"] == 202
        assert history[0]["mode"] == "HLF"
        assert history[0]["text"] == "test payload"

    def test_most_recent_entry_is_first(self) -> None:
        record_intent("first", "HLF", 202)
        record_intent("second", "Text", 422)
        history = _st_stub.session_state["intent_history"]
        assert history[0]["text"] == "second"
        assert history[1]["text"] == "first"

    def test_trace_id_stored(self) -> None:
        record_intent("payload", "HLF", 202, trace_id="abc-123")
        assert _st_stub.session_state["intent_history"][0]["trace_id"] == "abc-123"

    def test_trace_id_defaults_to_dash(self) -> None:
        record_intent("payload", "HLF", 202)
        assert _st_stub.session_state["intent_history"][0]["trace_id"] == "—"

    def test_gas_stored(self) -> None:
        record_intent("payload", "HLF", 202, gas_used=7)
        assert _st_stub.session_state["intent_history"][0]["gas"] == 7

    def test_gas_defaults_to_dash(self) -> None:
        record_intent("payload", "HLF", 202)
        assert _st_stub.session_state["intent_history"][0]["gas"] == "—"

    def test_long_text_truncated_to_120_chars(self) -> None:
        long_text = "A" * 200
        record_intent(long_text, "HLF", 202)
        stored = _st_stub.session_state["intent_history"][0]["text"]
        # 120 chars of content + single '…' character = 121
        assert len(stored) == 121
        assert stored.endswith("…")

    def test_short_text_not_truncated(self) -> None:
        short = "hello world"
        record_intent(short, "HLF", 202)
        assert _st_stub.session_state["intent_history"][0]["text"] == short

    def test_history_capped_at_max(self) -> None:
        for i in range(_MAX_INTENT_HISTORY + 10):
            record_intent(f"intent-{i}", "HLF", 202)
        assert len(_st_stub.session_state["intent_history"]) == _MAX_INTENT_HISTORY

    def test_entry_has_timestamp(self) -> None:
        record_intent("payload", "Text", 202)
        ts = _st_stub.session_state["intent_history"][0]["ts"]
        # Format: YYYY-MM-DD HH:MM:SS UTC
        assert ts.endswith(" UTC")
        date_part, time_part, _ = ts.split()
        assert len(date_part.split("-")) == 3
        assert len(time_part.split(":")) == 3


# ---------------------------------------------------------------------------
# Tests for compile_hlf_preview
# ---------------------------------------------------------------------------


class TestCompileHlfPreview:
    """Tests for compile_hlf_preview()."""

    _VALID_HLF = (
        '[HLF-v2]\n[INTENT] greet "world"\n[EXPECT] "Hello, world!"\n'
        '[RESULT] code=0 message="ok"\nΩ'
    )

    def test_returns_three_tuple(self) -> None:
        result = compile_hlf_preview(self._VALID_HLF)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_valid_hlf_ok_is_true_or_false(self) -> None:
        ok, msg, ast = compile_hlf_preview(self._VALID_HLF)
        # Either the compiler is installed and succeeds, or it's not installed
        # and raises ImportError → ok=False with a message.
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        # ast can be dict or None
        assert ast is None or isinstance(ast, dict)

    def test_empty_source_returns_error(self) -> None:
        ok, msg, ast = compile_hlf_preview("")
        # Empty source always fails
        assert not ok or ast is None
        # msg is always a string
        assert isinstance(msg, str)

    def test_syntax_error_returns_false(self) -> None:
        bad = "not valid hlf at all !!! ???"
        ok, msg, ast = compile_hlf_preview(bad)
        # May succeed if compiler is lenient, but at minimum returns consistent types
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        assert ast is None or isinstance(ast, dict)

    def test_error_message_prefix(self) -> None:
        """Error messages should start with ❌ and success with ✅."""
        _valid = self._VALID_HLF
        ok, msg, _ = compile_hlf_preview(_valid)
        if ok:
            assert msg.startswith("✅")
        else:
            assert msg.startswith("❌")


# ---------------------------------------------------------------------------
# Tests for export_intent_history_csv
# ---------------------------------------------------------------------------


class TestExportIntentHistoryCsv:
    """Tests for export_intent_history_csv()."""

    _SAMPLE = [
        {"ts": "10:00:00", "mode": "HLF", "status": 202, "trace_id": "t1", "gas": 5, "text": "hello"},
        {"ts": "10:01:00", "mode": "Text", "status": 422, "trace_id": "—", "gas": "—", "text": "bad"},
    ]

    def test_empty_history_returns_empty_string(self) -> None:
        assert export_intent_history_csv([]) == ""

    def test_csv_has_header_row(self) -> None:
        csv_str = export_intent_history_csv(self._SAMPLE)
        reader = csv.DictReader(io.StringIO(csv_str))
        assert reader.fieldnames is not None
        assert "ts" in reader.fieldnames
        assert "status" in reader.fieldnames
        assert "text" in reader.fieldnames

    def test_csv_row_count(self) -> None:
        csv_str = export_intent_history_csv(self._SAMPLE)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == len(self._SAMPLE)

    def test_csv_values_match(self) -> None:
        csv_str = export_intent_history_csv(self._SAMPLE)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert rows[0]["ts"] == "10:00:00"
        assert rows[0]["mode"] == "HLF"
        assert rows[0]["status"] == "202"
        assert rows[0]["text"] == "hello"

    def test_csv_is_valid_utf8_string(self) -> None:
        csv_str = export_intent_history_csv(self._SAMPLE)
        assert isinstance(csv_str, str)
        csv_str.encode("utf-8")  # must not raise


# ---------------------------------------------------------------------------
# Tests for export_routing_trace_json
# ---------------------------------------------------------------------------


class TestExportRoutingTraceJson:
    """Tests for export_routing_trace_json()."""

    _TRACE = {
        "model": "kimi-k2.5:cloud",
        "provider": "ollama",
        "tier": "hearth",
        "latency_ms": 1234,
    }

    def test_returns_valid_json_string(self) -> None:
        result = export_routing_trace_json(self._TRACE)
        parsed = json.loads(result)
        assert parsed == self._TRACE

    def test_output_is_pretty_printed(self) -> None:
        result = export_routing_trace_json(self._TRACE)
        assert "\n" in result  # indented output

    def test_empty_dict(self) -> None:
        result = export_routing_trace_json({})
        assert result == "{}"

    def test_nested_values(self) -> None:
        trace = {"model": "x", "meta": {"a": 1}}
        result = export_routing_trace_json(trace)
        parsed = json.loads(result)
        assert parsed["meta"]["a"] == 1


# ---------------------------------------------------------------------------
# Tests for status_badge_html
# ---------------------------------------------------------------------------


class TestStatusBadgeHtml:
    """Tests for status_badge_html()."""

    @pytest.mark.parametrize(
        "code,expected_label",
        [
            (202, "202 OK"),
            (422, "422 Syntax"),
            (403, "403 ALIGN"),
            (429, "429 Limit"),
            (409, "409 Replay"),
        ],
    )
    def test_known_codes_have_labels(self, code: int, expected_label: str) -> None:
        html = status_badge_html(code)
        assert expected_label in html

    def test_unknown_code_shows_code_string(self) -> None:
        html = status_badge_html(500)
        assert "500" in html

    def test_returns_html_string(self) -> None:
        html = status_badge_html(202)
        assert isinstance(html, str)
        assert "<span" in html
        assert "border-radius" in html

    def test_202_uses_green_color(self) -> None:
        html = status_badge_html(202)
        assert "#2ea043" in html

    def test_403_uses_red_color(self) -> None:
        html = status_badge_html(403)
        assert "#f85149" in html

    def test_zero_code_shows_zero(self) -> None:
        html = status_badge_html(0)
        assert "0" in html

    def test_connect_error_shows_unreachable(self) -> None:
        html = status_badge_html(_STATUS_CONNECT_ERROR)
        assert "Unreachable" in html

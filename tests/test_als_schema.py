"""Tests for ALS Schema Enforcement — Audit Logging Standard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.als_schema import (
    ALSEntry,
    ALSLogger,
    ALSSchema,
    ALSSeverity,
    ValidationError,
    ValidationResult,
)


class TestALSEntry:
    def test_defaults(self):
        entry = ALSEntry(event_type="agent.started", agent_id="test", message="Hello")
        assert entry.event_id
        assert entry.timestamp > 0
        assert entry.severity == ALSSeverity.INFO

    def test_to_dict(self):
        entry = ALSEntry(event_type="agent.started", agent_id="test", message="Hi")
        d = entry.to_dict()
        assert d["event_type"] == "agent.started"
        assert d["severity"] == "INFO"
        assert "event_id" in d

    def test_optional_fields_excluded(self):
        entry = ALSEntry(event_type="x", agent_id="a", message="m")
        d = entry.to_dict()
        assert "metadata" not in d
        assert "gas_consumed" not in d

    def test_optional_fields_included(self):
        entry = ALSEntry(
            event_type="x", agent_id="a", message="m",
            gas_consumed=100, metadata={"key": "val"},
        )
        d = entry.to_dict()
        assert d["gas_consumed"] == 100
        assert d["metadata"]["key"] == "val"

    def test_to_json(self):
        entry = ALSEntry(event_type="agent.test", agent_id="a", message="m")
        j = entry.to_json()
        data = json.loads(j)
        assert data["event_type"] == "agent.test"


class TestALSSchema:
    def setup_method(self):
        self.schema = ALSSchema()

    def test_create_entry(self):
        entry = self.schema.create_entry(
            event_type="agent.started",
            agent_id="sentinel",
            message="Started",
        )
        assert entry.event_type == "agent.started"
        assert entry.agent_id == "sentinel"

    def test_validate_valid(self):
        entry = self.schema.create_entry(
            "agent.started", "sentinel", "Started",
        )
        result = self.schema.validate(entry)
        assert result.valid
        assert len(result.errors) == 0

    def test_validate_missing_required(self):
        result = self.schema.validate({
            "event_id": "x",
            "timestamp": 1234,
            "severity": "INFO",
            "correlation_id": "c",
        })
        assert not result.valid
        # Missing: event_type, agent_id, message
        missing = [e.field for e in result.errors]
        assert "event_type" in missing
        assert "agent_id" in missing
        assert "message" in missing

    def test_validate_bad_severity(self):
        entry = self.schema.create_entry("agent.x", "a", "m")
        d = entry.to_dict()
        d["severity"] = "PANIC"
        result = self.schema.validate(d)
        assert not result.valid

    def test_validate_bad_timestamp(self):
        entry = self.schema.create_entry("agent.x", "a", "m")
        d = entry.to_dict()
        d["timestamp"] = "not-a-number"
        result = self.schema.validate(d)
        assert not result.valid

    def test_validate_negative_timestamp(self):
        entry = self.schema.create_entry("agent.x", "a", "m")
        d = entry.to_dict()
        d["timestamp"] = -1
        result = self.schema.validate(d)
        assert not result.valid

    def test_validate_unknown_event_prefix_warns(self):
        entry = self.schema.create_entry("custom.event", "a", "m")
        result = self.schema.validate(entry)
        assert result.valid  # Still valid
        assert len(result.warnings) > 0

    def test_validate_known_event_prefix_no_warn(self):
        entry = self.schema.create_entry("agent.started", "a", "m")
        result = self.schema.validate(entry)
        assert result.valid
        # No warning about event prefix

    def test_validate_negative_gas(self):
        entry = self.schema.create_entry("agent.x", "a", "m", gas_consumed=-5)
        result = self.schema.validate(entry)
        assert not result.valid

    def test_validate_unknown_field_strict(self):
        entry = self.schema.create_entry("agent.x", "a", "m")
        d = entry.to_dict()
        d["unknown_field"] = "surprise"
        result = self.schema.validate(d)
        assert result.valid  # Warnings, not errors
        assert any("unknown_field" in w.lower() or "Unknown" in w for w in result.warnings)

    def test_validate_batch(self):
        good = self.schema.create_entry("agent.x", "a", "good")
        bad = {"event_id": "x"}  # Missing most fields
        result = self.schema.validate_batch([good, bad])
        assert result["total"] == 2
        assert result["valid"] == 1
        assert result["invalid"] == 1
        assert not result["all_valid"]

    def test_validate_batch_all_valid(self):
        entries = [
            self.schema.create_entry("agent.a", "a", "m1"),
            self.schema.create_entry("agent.b", "b", "m2"),
        ]
        result = self.schema.validate_batch(entries)
        assert result["all_valid"]

    def test_result_to_dict(self):
        result = ValidationResult(valid=True, warnings=["test"])
        d = result.to_dict()
        assert d["valid"] is True
        assert d["warnings"] == ["test"]


class TestALSLogger:
    def test_log_entry(self):
        logger = ALSLogger(agent_id="sentinel")
        entry = logger.log("agent.started", "Started")
        assert entry.agent_id == "sentinel"
        assert logger.buffer_size == 1

    def test_buffer_limit(self):
        logger = ALSLogger(agent_id="test", max_buffer=3)
        for i in range(5):
            logger.log("agent.tick", f"Tick {i}")
        assert logger.buffer_size == 3

    def test_get_entries(self):
        logger = ALSLogger(agent_id="test")
        logger.log("agent.a", "A")
        logger.log("agent.b", "B")
        entries = logger.get_entries()
        assert len(entries) == 2
        assert entries[0]["event_type"] == "agent.b"  # Newest first

    def test_filter_by_severity(self):
        logger = ALSLogger(agent_id="test")
        logger.log("agent.a", "A", severity=ALSSeverity.INFO)
        logger.log("agent.b", "B", severity=ALSSeverity.CRITICAL)
        crit = logger.get_entries(severity=ALSSeverity.CRITICAL)
        assert len(crit) == 1
        assert crit[0]["severity"] == "CRITICAL"

    def test_filter_by_event_type(self):
        logger = ALSLogger(agent_id="test")
        logger.log("agent.started", "A")
        logger.log("security.violation", "B")
        sec = logger.get_entries(event_type="security.")
        assert len(sec) == 1

    def test_flush(self, tmp_path):
        logger = ALSLogger(agent_id="test")
        logger.log("agent.a", "A")
        logger.log("agent.b", "B")
        outfile = tmp_path / "audit.jsonl"
        count = logger.flush(outfile)
        assert count == 2
        assert logger.buffer_size == 0
        lines = outfile.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event_type"] == "agent.a"

    def test_clear(self):
        logger = ALSLogger(agent_id="test")
        logger.log("agent.a", "A")
        logger.clear()
        assert logger.buffer_size == 0

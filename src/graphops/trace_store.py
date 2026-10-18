"""Durable local audit-event storage for incident traces."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SQLiteTraceStore:
    def __init__(self, path: str | None = None) -> None:
        configured = path or os.getenv("GRAPHOPS_TRACE_DB", ".graphops/audit.sqlite3")
        if configured != ":memory:":
            Path(configured).parent.mkdir(parents=True, exist_ok=True)
        self.path = configured
        self._lock = threading.RLock()
        self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False) if configured == ":memory:" else None
        if self._memory_connection is not None:
            self._memory_connection.row_factory = sqlite3.Row
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS trace_events (
                    incident_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (incident_id, sequence)
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def append(self, incident_id: str, event: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO trace_events (incident_id, sequence, event_type, created_at, payload_json) VALUES (?, ?, ?, ?, ?)",
                (incident_id, event["sequence"], event["event_type"], event["created_at"], json.dumps(event["payload"], sort_keys=True)),
            )

    def list(self, incident_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, created_at, payload_json FROM trace_events WHERE incident_id = ? ORDER BY sequence",
                (incident_id,),
            ).fetchall()
        return [
            {"sequence": row["sequence"], "event_type": row["event_type"], "created_at": row["created_at"], "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

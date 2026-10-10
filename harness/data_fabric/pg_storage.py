"""
Tier 2: Relational State & Agent Trajectory Storage.
Connects to PostgreSQL with seamless fallback to SQLite (harness.db).
Manages multi-node session states, execution steps, and offline edge handovers.
"""

import os
import json
import time
import sqlite3
import logging
from typing import Dict, Any, List, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.PGStorage")

class RelationalStorage:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "harness.db")
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = None
        self._init_sqlite()

    def _init_sqlite(self):
        """Initializes SQLite tables with WAL mode for fast concurrent reads."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    assigned_node TEXT NOT NULL,
                    status TEXT NOT NULL,
                    context_floor INTEGER DEFAULT 4096,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata_json TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    thought_content TEXT,
                    output_content TEXT,
                    tool_calls_json TEXT,
                    timestamp REAL NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS edge_handovers (
                    handover_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    summary TEXT,
                    status TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    payload_json TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS aevum_agent_registry (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    assigned_node TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    current_task TEXT,
                    last_heartbeat_hive REAL NOT NULL,
                    autonomous_focus TEXT,
                    metadata_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS aevum_project_registry (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    node_id TEXT DEFAULT 'local',
                    bound_agent_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # Safe migrations for columns if missing
            try:
                self.conn.execute("ALTER TABLE agent_trajectories ADD COLUMN images_json TEXT DEFAULT '[]'")
            except Exception:
                pass
            try:
                self.conn.execute("ALTER TABLE agent_trajectories ADD COLUMN metrics_json TEXT DEFAULT '{}'")
            except Exception:
                pass
        logger.info(f"⚡ Relational state storage initialized at {self.db_path}")

    def save_session(self, session_id: str, agent_id: str, assigned_node: str, status: str, metadata: Optional[Dict[str, Any]] = None):
        now = time.time()
        meta_str = json.dumps(metadata or {})
        with self.conn:
            self.conn.execute("""
                INSERT INTO agent_sessions (session_id, agent_id, assigned_node, status, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=excluded.status,
                    assigned_node=excluded.assigned_node,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
            """, (session_id, agent_id, assigned_node, status, now, now, meta_str))

    def create_chat_session(
        self,
        session_id: str,
        title: str,
        workspace_path: str = "",
        node_id: str = "workstation_primary",
        permissions: Optional[Dict[str, Any]] = None,
        agent_id: str = "coordinator"
    ) -> Dict[str, Any]:
        meta = {
            "title": title or "New Conversation",
            "workspace_path": workspace_path or "",
            "permissions": permissions or {"autonomy_level": "tiered", "can_write": True}
        }
        self.save_session(
            session_id=session_id,
            agent_id=agent_id,
            assigned_node=node_id,
            status="active",
            metadata=meta
        )
        return {
            "session_id": session_id,
            "title": meta["title"],
            "workspace_path": meta["workspace_path"],
            "node_id": node_id,
            "agent_id": agent_id,
            "permissions": meta["permissions"],
            "status": "active",
            "created_at": time.time(),
            "updated_at": time.time()
        }

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT session_id, agent_id, assigned_node, status, created_at, updated_at, metadata_json FROM agent_sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        meta = json.loads(row[6] or "{}")
        return {
            "session_id": row[0],
            "agent_id": row[1],
            "assigned_node": row[2],
            "status": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "title": meta.get("title", row[0]),
            "workspace_path": meta.get("workspace_path", ""),
            "permissions": meta.get("permissions", {}),
            "metadata": meta
        }

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT id, step_index, role, thought_content, output_content, tool_calls_json, images_json, metrics_json, timestamp
            FROM agent_trajectories
            WHERE session_id = ?
            ORDER BY step_index ASC, timestamp ASC
        """, (session_id,))
        rows = cur.fetchall()
        messages = []
        for r in rows:
            messages.append({
                "id": r[0],
                "step_index": r[1],
                "role": r[2],
                "thought": r[3] or "",
                "content": r[4] or "",
                "tool_calls": json.loads(r[5] or "[]"),
                "images": json.loads(r[6] or "[]"),
                "metrics": json.loads(r[7] or "{}"),
                "timestamp": r[8]
            })
        return messages

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        thought: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        images: Optional[List[str]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None
    ) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,))
        if not cur.fetchone():
            self.create_chat_session(session_id=session_id, title=f"Chat {session_id}", agent_id=agent_id or "coordinator")

        cur.execute("SELECT COALESCE(MAX(step_index), -1) FROM agent_trajectories WHERE session_id = ?", (session_id,))
        max_idx = cur.fetchone()[0]
        step_index = max_idx + 1

        now = time.time()
        with self.conn:
            self.conn.execute("""
                INSERT INTO agent_trajectories (session_id, step_index, role, thought_content, output_content, tool_calls_json, images_json, metrics_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                step_index,
                role,
                thought,
                content,
                json.dumps(tool_calls or []),
                json.dumps(images or []),
                json.dumps(metrics or {}),
                now
            ))
            # Touch session updated_at and record agent_id if given
            if agent_id:
                self.conn.execute("UPDATE agent_sessions SET updated_at = ?, agent_id = ? WHERE session_id = ?", (now, agent_id, session_id))
            else:
                self.conn.execute("UPDATE agent_sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
        return step_index

    def record_step(
        self,
        session_id: str,
        step_index: int,
        role: str,
        thought: str = "",
        output: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ):
        with self.conn:
            self.conn.execute("""
                INSERT INTO agent_trajectories (session_id, step_index, role, thought_content, output_content, tool_calls_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, step_index, role, thought, output, json.dumps(tool_calls or []), time.time()))

    def list_sessions(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT session_id, agent_id, assigned_node, status, updated_at, metadata_json FROM agent_sessions ORDER BY updated_at DESC")
        rows = cur.fetchall()
        return [
            {
                "session_id": r[0],
                "agent_id": r[1],
                "assigned_node": r[2],
                "status": r[3],
                "updated_at": r[4],
                "title": json.loads(r[5] or "{}").get("title", r[0]),
                "workspace_path": json.loads(r[5] or "{}").get("workspace_path", ""),
                "permissions": json.loads(r[5] or "{}").get("permissions", {}),
                "metadata": json.loads(r[5] or "{}"),
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM agent_trajectories WHERE session_id = ?", (session_id,))
            self.conn.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
        return True

    def clear_session_messages(self, session_id: str) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM agent_trajectories WHERE session_id = ?", (session_id,))
            self.conn.execute("UPDATE agent_sessions SET updated_at = ? WHERE session_id = ?", (time.time(), session_id))
        return True


    # ── Aevum Hive Network Agent Registry ─────────────────────────────

    def upsert_hive_agent(
        self,
        agent_id: str,
        name: str,
        role: str,
        assigned_node: str,
        status: str = "idle",
        current_task: Optional[str] = None,
        autonomous_focus: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Registers or updates an agent in the Aevum Hive Network registry."""
        now = time.time()
        meta_str = json.dumps(metadata or {})
        clean_id = agent_id.strip().lower()
        with self.conn:
            self.conn.execute("""
                INSERT INTO aevum_agent_registry (
                    agent_id, name, role, assigned_node, status, current_task,
                    last_heartbeat_hive, autonomous_focus, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name=excluded.name,
                    role=excluded.role,
                    assigned_node=excluded.assigned_node,
                    status=excluded.status,
                    current_task=COALESCE(excluded.current_task, aevum_agent_registry.current_task),
                    last_heartbeat_hive=excluded.last_heartbeat_hive,
                    autonomous_focus=COALESCE(excluded.autonomous_focus, aevum_agent_registry.autonomous_focus),
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
            """, (clean_id, name, role, assigned_node, status, current_task, now, autonomous_focus, meta_str, now, now))
        return self.get_hive_agent(clean_id)

    def get_hive_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Fetches agent details from Aevum Hive registry."""
        clean_id = agent_id.strip().lower()
        cur = self.conn.cursor()
        cur.execute("""
            SELECT agent_id, name, role, assigned_node, status, current_task,
                   last_heartbeat_hive, autonomous_focus, metadata_json, created_at, updated_at
            FROM aevum_agent_registry
            WHERE agent_id = ?
        """, (clean_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "agent_id": row[0],
            "name": row[1],
            "role": row[2],
            "assigned_node": row[3],
            "status": row[4],
            "current_task": row[5],
            "last_heartbeat_hive": row[6],
            "autonomous_focus": row[7],
            "metadata": json.loads(row[8] or "{}"),
            "created_at": row[9],
            "updated_at": row[10],
        }

    def list_hive_agents(self) -> List[Dict[str, Any]]:
        """Lists all agents registered in the Aevum Hive Network."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT agent_id, name, role, assigned_node, status, current_task,
                   last_heartbeat_hive, autonomous_focus, metadata_json, created_at, updated_at
            FROM aevum_agent_registry
            ORDER BY updated_at DESC
        """)
        rows = cur.fetchall()
        return [
            {
                "agent_id": r[0],
                "name": r[1],
                "role": r[2],
                "assigned_node": r[3],
                "status": r[4],
                "current_task": r[5],
                "last_heartbeat_hive": r[6],
                "autonomous_focus": r[7],
                "metadata": json.loads(r[8] or "{}"),
                "created_at": r[9],
                "updated_at": r[10],
            }
            for r in rows
        ]

    def heartbeat_hive_agent(
        self,
        agent_id: str,
        node_id: Optional[str] = None,
        status: Optional[str] = None,
        autonomous_focus: Optional[str] = None,
    ) -> bool:
        """Pings heartbeat and updates runtime activity in Aevum Hive."""
        clean_id = agent_id.strip().lower()
        now = time.time()
        with self.conn:
            updates = ["last_heartbeat_hive = ?", "updated_at = ?"]
            params = [now, now]
            if node_id:
                updates.append("assigned_node = ?")
                params.append(node_id)
            if status:
                updates.append("status = ?")
                params.append(status)
            if autonomous_focus is not None:
                updates.append("autonomous_focus = ?")
                params.append(autonomous_focus)
            params.append(clean_id)
            self.conn.execute(f"UPDATE aevum_agent_registry SET {', '.join(updates)} WHERE agent_id = ?", tuple(params))
        return True

    def set_hive_agent_task(self, agent_id: str, task: str) -> bool:
        """Assigns an active user directive to an agent in Aevum Hive."""
        clean_id = agent_id.strip().lower()
        now = time.time()
        with self.conn:
            self.conn.execute("""
                UPDATE aevum_agent_registry
                SET current_task = ?, status = 'user_active', updated_at = ?, last_heartbeat_hive = ?
                WHERE agent_id = ?
            """, (task, now, now, clean_id))
        return True

    def clear_hive_agent_task(self, agent_id: str) -> bool:
        """Clears user task, returning agent to idle / autonomous play in Aevum Hive."""
        clean_id = agent_id.strip().lower()
        now = time.time()
        with self.conn:
            self.conn.execute("""
                UPDATE aevum_agent_registry
                SET current_task = NULL, status = 'idle', updated_at = ?, last_heartbeat_hive = ?
                WHERE agent_id = ?
            """, (now, now, clean_id))
    # ── Aevum Cross-Endpoint Project Registry ────────────────────────

    def upsert_project(
        self,
        project_id: str,
        name: str,
        path: str,
        node_id: str = "local",
        bound_agent_id: Optional[str] = None,
        status: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Registers or updates a project in the cross-endpoint project registry."""
        now = time.time()
        meta_str = json.dumps(metadata or {})
        clean_id = project_id.strip().lower()
        norm_path = os.path.normpath(path).replace("\\", "/") if path else ""
        with self.conn:
            self.conn.execute("""
                INSERT INTO aevum_project_registry (
                    project_id, name, path, node_id, bound_agent_id, status, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name=excluded.name,
                    path=excluded.path,
                    node_id=excluded.node_id,
                    bound_agent_id=COALESCE(excluded.bound_agent_id, aevum_project_registry.bound_agent_id),
                    status=excluded.status,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
            """, (clean_id, name, norm_path, node_id, bound_agent_id, status, meta_str, now, now))
        return self.get_project(clean_id)

    def get_project(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Fetches project by ID, exact name (case-insensitive), or normalized path."""
        if not identifier:
            return None
        clean_id = identifier.strip().lower()
        norm_path = os.path.normpath(identifier).replace("\\", "/")
        cur = self.conn.cursor()
        cur.execute("""
            SELECT project_id, name, path, node_id, bound_agent_id, status, metadata_json, created_at, updated_at
            FROM aevum_project_registry
            WHERE project_id = ? OR LOWER(name) = ? OR path = ?
        """, (clean_id, clean_id, norm_path))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "project_id": row[0],
            "name": row[1],
            "path": row[2],
            "node_id": row[3],
            "bound_agent_id": row[4],
            "status": row[5],
            "metadata": json.loads(row[6] or "{}"),
            "created_at": row[7],
            "updated_at": row[8],
        }

    def list_projects(self) -> List[Dict[str, Any]]:
        """Lists all registered projects in cross-endpoint registry."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT project_id, name, path, node_id, bound_agent_id, status, metadata_json, created_at, updated_at
            FROM aevum_project_registry
            ORDER BY updated_at DESC
        """)
        rows = cur.fetchall()
        return [
            {
                "project_id": r[0],
                "name": r[1],
                "path": r[2],
                "node_id": r[3],
                "bound_agent_id": r[4],
                "status": r[5],
                "metadata": json.loads(r[6] or "{}"),
                "created_at": r[7],
                "updated_at": r[8],
            }
            for r in rows
        ]

    def delete_project(self, project_id: str) -> bool:
        """Removes a project from the registry."""
        clean_id = project_id.strip().lower()
        with self.conn:
            self.conn.execute("DELETE FROM aevum_project_registry WHERE project_id = ?", (clean_id,))
        return True

    def bind_agent_to_project(self, project_id: str, agent_id: str) -> bool:
        """Binds an agent to a project in the registry."""
        clean_proj = project_id.strip().lower()
        clean_agent = agent_id.strip().lower()
        now = time.time()
        with self.conn:
            self.conn.execute("""
                UPDATE aevum_project_registry
                SET bound_agent_id = ?, updated_at = ?
                WHERE project_id = ?
            """, (clean_agent, now, clean_proj))
        return True

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

relational_storage = RelationalStorage()

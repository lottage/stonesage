"""
Cross-Container Analytical Query Layer.
Leverages DuckDB or SQLite attached queries to scan data across Proxmox containers
(Kavita books, Home Assistant recorder, Jellyfin media) zero-copy over the network.
"""

import os
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Harness.DuckDBScanner")

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    duckdb = None
    HAS_DUCKDB = False

class ContainerDataScanner:
    def __init__(self):
        self.con = duckdb.connect(database=":memory:") if HAS_DUCKDB else None
        if HAS_DUCKDB:
            logger.info("⚡ DuckDB embedded analytical scanner active.")
        else:
            logger.info("DuckDB not installed; using standard SQL connectors.")

    def query_sqlite_file(self, db_path: str, sql_query: str) -> List[Dict[str, Any]]:
        """Executes zero-copy analytical SQL across external container SQLite databases."""
        if not os.path.exists(db_path):
            return [{"error": f"Target database file '{db_path}' not found on mount."}]

        if HAS_DUCKDB and self.con:
            try:
                # DuckDB sqlite_scan executes without locking the live database
                rel = self.con.execute(f"SELECT * FROM sqlite_scan('{db_path}', '({sql_query})')")
                cols = [desc[0] for desc in rel.description]
                rows = rel.fetchall()
                return [dict(zip(cols, r)) for r in rows]
            except Exception as e:
                logger.warning(f"DuckDB query failed, attempting standard fallback: {e}")

        # Fallback to read-only URI sqlite3
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute(sql_query)
            cols = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchall()
            conn.close()
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            logger.error(f"Error querying SQLite database '{db_path}': {e}")
            return [{"error": str(e)}]

container_scanner = ContainerDataScanner()

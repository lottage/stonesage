"""Entry point kept where systemd expects it: the vault status note (homelab-db-sync.service runs `python3 -u sync_db_to_obsidian.py` here). The code is stonesage/sync_db_to_obsidian.py."""

if __name__ == "__main__":
    import runpy
    runpy.run_module("stonesage.sync_db_to_obsidian", run_name="__main__", alter_sys=True)

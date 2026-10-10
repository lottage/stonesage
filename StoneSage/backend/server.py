"""Entry point kept where systemd expects it: the StoneSage server (stonesage.service runs `python3 -u server.py` here). The code is stonesage/server.py."""

if __name__ == "__main__":
    import runpy
    runpy.run_module("stonesage.server", run_name="__main__", alter_sys=True)

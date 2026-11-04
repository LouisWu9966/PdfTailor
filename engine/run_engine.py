"""Entry point for PyInstaller (a package's __main__ cannot be the script)."""
from pdftailor_engine.server import main

if __name__ == "__main__":
    raise SystemExit(main())

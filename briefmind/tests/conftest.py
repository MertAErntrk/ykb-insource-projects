import ctypes
import os
import sys
import types

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)

# Windows'a ozgu moduller: yakalayici.py'nin saf mantigi Linux/CI'da da test edilebilsin
if "uiautomation" not in sys.modules:
    try:
        import uiautomation  # noqa: F401
    except Exception:
        sys.modules["uiautomation"] = types.ModuleType("uiautomation")
if not hasattr(ctypes, "windll"):
    ctypes.windll = types.SimpleNamespace(user32=types.SimpleNamespace(SystemParametersInfoW=lambda *a: 0))

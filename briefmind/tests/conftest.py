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

# Testler ag kullanmaz: llm ilk istekten once sunucuyu (GET /models) sormasin, token sayimi /tokenize'a gitmesin
import llm  # noqa: E402

llm.OTOMATIK_TANI = False
llm.SUNUCU_TOKENIZER = False

"""API Atlas CLI: Python 3.9+, no dependencies for HAR/catalog/Qwen."""
import sys

if __name__ == '__main__':
    if sys.version_info < (3, 9):
        raise SystemExit('Python 3.9 veya üzeri gerekli.')
    from catalog_app.cli import main
    raise SystemExit(main())

"""Command line entry points. Nothing listens on a local HTTP port."""
import argparse
import asyncio
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import struct
import sys
from pathlib import Path

from .exporters import export_all
from .io_utils import read_json
from .privacy import clean_text
from .qwen import describe, load_config, probe
from .service import ingest
from .store import Catalog

ROOT = Path(__file__).resolve().parent.parent


def _common(parser):
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data', help='Yerel veritabanı ve ayrı tarayıcı profili dizini')
    parser.add_argument('--output', type=Path, default=ROOT / 'output', help='Katalog çıktılarının dizini')


def parser():
    root = argparse.ArgumentParser(description='API Atlas: URL, HAR veya OpenAPI üzerinden ekransız API kataloğu')
    root.add_argument('--version', action='version', version='API Atlas 0.2.1')
    sub = root.add_subparsers(dest='command', required=True)
    for command in ('scan', 'record', 'login'):
        p = sub.add_parser(command, help={'scan': 'URL açıp API çağrılarını ve JavaScript adaylarını otomatik çıkar', 'record': 'Siz gezinirken çağrıları kaydet', 'login': 'Ayrı kayıt profiline bir kez giriş yap'}[command])
        p.add_argument('urls', nargs='+' if command == 'scan' else None, help='İncelenecek sayfanın tam URL adresi')
        p.add_argument('--channel', choices=('msedge', 'chrome', 'chromium'), default='msedge' if sys.platform == 'win32' else 'chromium')
        p.add_argument('--executable-path', help='Kurulu tarayıcının tam dosya yolu (isteğe bağlı)')
        p.add_argument('--app', default='', help='Boşsa uygulamalar API sunucusunun alan adına göre ayrılır')
        p.add_argument('--host', action='append', default=[], help='Kayıt kapsamı; örn portal.example.com veya *.example.com; tekrar edilebilir')
        p.add_argument('--config', type=Path, help='Qwen ayar dosyası; verilirse kayıt sonrası otomatik analiz')
        p.add_argument('--analyze', action='store_true', help='Qwen ortam değişkenlerini kullanarak analiz de yap')
        p.add_argument('--headless', action='store_true', help='Tarayıcı penceresi açma; önceden oturum açılmış olmalı')
        if command == 'scan':
            p.add_argument('--wait', type=float, default=15, help='Sayfa açıldıktan sonra kayıt süresi, saniye')
            p.add_argument('--scroll', action='store_true', help='Sayfayı kaydırarak ek yüklemeleri gözle')
        if command == 'record':
            p.add_argument('--action', default='', help='İlk işlem etiketi; terminalden mark ile değiştirilebilir')
            p.add_argument('--duration', type=float, help='Belirtilen saniye sonunda otomatik durdur')
        _common(p)
    p = sub.add_parser('import', help='HAR veya OpenAPI/Swagger JSON dosyalarını içe aktar')
    p.add_argument('files', type=Path, nargs='+')
    p.add_argument('--app', default='')
    p.add_argument('--page', default='')
    p.add_argument('--action', default='')
    p.add_argument('--host', action='append', default=[])
    p.add_argument('--origin', default='', help='Göreli OpenAPI servers için temel URL')
    p.add_argument('--keep-paths', action='store_true', help='Yol kimliklerini genelleme; gerçek kimlikler katalogda kalabilir')
    p.add_argument('--config', type=Path)
    p.add_argument('--analyze', action='store_true')
    _common(p)
    p = sub.add_parser('analyze', help='Yeni/değişen uç noktaları normal Qwen HTTP çağrısıyla açıkla')
    p.add_argument('--config', type=Path)
    p.add_argument('--query', default='')
    p.add_argument('--app', default='')
    p.add_argument('--limit', type=int, default=0, help='0: tüm yeni/değişen kayıtlar')
    p.add_argument('--force', action='store_true', help='Aynı şema için analizi yeniden yap')
    _common(p)
    p = sub.add_parser('search', help='Katalogda alan/amaç/adres/sayfa ara')
    p.add_argument('query', nargs='*')
    p.add_argument('--app', default='')
    p.add_argument('--method', default='')
    p.add_argument('--json', action='store_true')
    _common(p)
    p = sub.add_parser('show', help='Uç noktanın tüm kanıtını ve şemasını göster')
    p.add_argument('id', help='Tam ID veya benzersiz başlangıcı')
    _common(p)
    for command in ('export', 'stats', 'doctor', 'demo'):
        p = sub.add_parser(command)
        if command == 'doctor':
            p.add_argument('--config', type=Path)
            p.add_argument('--qwen', action='store_true', help='Qwen endpointine küçük test isteği gönder')
        _common(p)
    return root


def _catalog(args):
    args.data_dir.mkdir(parents=True, exist_ok=True)
    return Catalog(args.data_dir / 'catalog.sqlite3')


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _analyze(catalog, args):
    config = load_config(args.config)
    items = catalog.list(query=getattr(args, 'query', ''), application=getattr(args, 'app', ''))
    todo = [i for i in items if getattr(args, 'force', False) or i.get('analysis_revision') != i.get('revision')]
    if getattr(args, 'limit', 0):
        todo = todo[:args.limit]
    success, failed = 0, 0
    for item in todo:
        try:
            result = describe(config, item)
            catalog.update_description(item['id'], source='qwen', expected_revision=item['revision'], **result)
            success += 1
            print('Açıklandı: ' + item['id'][:12], flush=True)
        except ValueError as exc:
            failed += 1
            print('Analiz tamamlanamadı ' + item['id'][:12] + ': ' + str(exc), file=sys.stderr, flush=True)
    _print({'analyzed': success, 'failed': failed, 'cached_or_filtered': len(items) - len(todo)})
    return failed


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'doctor':
            report = {'python': sys.version.split()[0], 'core_supported': sys.version_info >= (3, 9),
                      'browser_python_supported': sys.version_info >= (3, 9),
                      'python_executable': sys.executable,
                      'python_bits': struct.calcsize('P') * 8,
                      'playwright_version': _package_version('playwright'),
                      'greenlet_version': _package_version('greenlet'),
                      'playwright_installed': importlib.util.find_spec('playwright') is not None,
                      'qwen_tested': False, 'core_dependencies': 'Python standard library'}
            if args.qwen:
                report['qwen'] = probe(load_config(args.config))
                report['qwen_tested'] = True
            _print(report)
            return 0
        catalog = _catalog(args)
        if args.command in ('scan', 'record', 'login'):
            from .recorder import record
            if args.command == 'scan' and not 1 <= args.wait <= 600:
                raise ValueError('--wait 1–600 saniye arasında olmalı.')
            urls = args.urls if isinstance(args.urls, list) else [args.urls]
            for url in urls:
                summary = asyncio.run(record(catalog, url, args.data_dir, application=args.app,
                              action=getattr(args, 'action', ''), hosts=args.host, channel=args.channel,
                              executable_path=args.executable_path, duration=getattr(args, 'duration', None),
                              headless=args.headless, mode=args.command, scan_wait=getattr(args, 'wait', 15), scroll=getattr(args, 'scroll', False)))
                _print(summary)
                if args.command == 'scan' and not summary['accepted']:
                    print('API çağrısı gözlenmedi. Oturum açılmış olmayabilir veya sayfa ek etkileşim gerektirebilir. login/record/HAR yollarını kullanabilirsiniz.', file=sys.stderr)
            if args.command == 'login':
                return 0
            failures = _analyze(catalog, args) if args.config or args.analyze else 0
            _print(export_all(catalog.list(), args.output))
            return 2 if failures else 0
        if args.command in ('import', 'demo'):
            files = args.files if args.command == 'import' else [ROOT / 'examples' / 'demo-capture.json']
            for path in files:
                result = ingest(catalog, read_json(path), application=getattr(args, 'app', '') or ('Demo' if args.command == 'demo' else ''),
                                page=getattr(args, 'page', ''), action=getattr(args, 'action', ''), filename=path.name,
                                hosts=getattr(args, 'host', []), keep_paths=getattr(args, 'keep_paths', False), base_url=getattr(args, 'origin', ''))
                _print(result)
            failures = _analyze(catalog, args) if getattr(args, 'config', None) or getattr(args, 'analyze', False) else 0
            _print(export_all(catalog.list(), args.output))
            return 2 if failures else 0
        if args.command == 'analyze':
            if args.limit < 0:
                raise ValueError('--limit negatif olamaz.')
            failures = _analyze(catalog, args)
            _print(export_all(catalog.list(), args.output))
            return 2 if failures else 0
        if args.command == 'search':
            items = catalog.list(query=' '.join(args.query), application=args.app)
            if args.method:
                items = [i for i in items if i['method'] == args.method.upper()]
            if args.json:
                _print(items)
            else:
                for item in items:
                    sources = '/'.join(item['sources'])
                    print(item['id'][:12] + '  ' + item['method'].ljust(7) + '  [' + sources + '] ' + item['origin'] + item['path'] + '\n  ' + item['title'] + ' — ' + item['description'][:200])
                print(str(len(items)) + ' uç nokta/adayı.')
            return 0
        if args.command == 'show':
            items = [i for i in catalog.list() if i['id'].startswith(args.id)]
            if len(items) != 1:
                raise ValueError('ID bulunamadı veya birden fazla kayıt eşleşti; daha uzun ID verin.')
            _print(items[0])
            return 0
        if args.command == 'stats':
            metadata = catalog.metadata()
            metadata['javascript_candidates_only'] = sum(i['sources'] == ['javascript'] for i in catalog.list())
            _print(metadata)
            return 0
        if args.command == 'export':
            _print(export_all(catalog.list(), args.output))
            return 0
        return 0
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
        # JSON/model bodies and credentials are never echoed by HTTP adapters.
        print('Hata: ' + clean_text(str(exc), 500), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Durduruldu. Tamamlanan gözlemler yerel veritabanında korundu.', file=sys.stderr)
        return 130

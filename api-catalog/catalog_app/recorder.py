"""Manual multi-site browser capture, inspired by RAE BrowserRecorder.

Only the operator navigates/clicks. HAR-shaped entries exist in memory and
are imported immediately; raw cookies/request/response values are not saved.
"""
import asyncio
import json
import queue
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .privacy import clean_text, page_context
from .discovery import script_candidates
from .service import host_matches, ingest

MAX_BODY = 500_000


def _terminal_reader(commands):
    while True:
        try:
            line = input()
        except (EOFError, OSError):
            return
        commands.put(line)
        if line.strip().lower() in ('stop', 'quit', 'exit'):
            return


def request_context(request, action):
    try:
        page = request.frame.page.url
    except Exception:
        page = ''
    return {'page': page_context(page), 'action': clean_text(action, 200), 'started': datetime.now(timezone.utc).isoformat()}


async def response_entry(request, context):
    response = await request.response()
    if response is None:
        return None
    headers = await response.all_headers()
    mime = headers.get('content-type', '').split(';', 1)[0].lower()
    is_api = request.resource_type in ('xhr', 'fetch') or 'json' in mime or any(s in urlsplit(request.url).path.lower().split('/') for s in ('api', 'rest', 'graphql', 'services'))
    if not is_api:
        return None
    try:
        length = int(headers.get('content-length', '0'))
    except ValueError:
        length = 0
    body_text = None
    if length <= MAX_BODY and ('json' in mime or request.resource_type in ('xhr', 'fetch')):
        try:
            body = await response.body()
            if len(body) <= MAX_BODY:
                body_text = body.decode('utf-8')
        except Exception:
            pass
    try:
        post_text = request.post_data
    except Exception:
        post_text = None
    req_headers = await request.all_headers()
    return {
        'startedDateTime': context['started'], '_resourceType': request.resource_type,
        '_page': context['page'], '_action': context['action'],
        'request': {'url': request.url, 'method': request.method,
                    'headers': [{'name': k, 'value': v} for k, v in req_headers.items()],
                    'postData': {'mimeType': req_headers.get('content-type', '').split(';', 1)[0], 'text': post_text}},
        'response': {'status': response.status, 'content': {'mimeType': mime, 'text': body_text}},
    }


async def record(catalog, url, data_dir, application='', action='', hosts=(), channel=None, executable_path=None, duration=None, headless=False, mode='record', scan_wait=15, scroll=False):
    if sys.version_info < (3, 10):
        raise ValueError('Canlı kayıt için Python 3.10+ ve Playwright gerekli. Python 3.9 ile import komutunu/HAR yolunu kullanabilirsiniz.')
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ValueError('Canlı kayıt için: python -m pip install -r requirements-record.txt. Kurulum yapılamıyorsa HAR import kullanın.') from None
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Kayıt başlangıç adresi HTTP/HTTPS olmalı.')
    run_id = uuid.uuid4().hex
    commands = queue.Queue()
    if mode in ('record', 'login'):
        threading.Thread(target=_terminal_reader, args=(commands,), daemon=True).start()
    current = {'action': action}
    starts, pending = {}, set()
    stats = {'captured': 0, 'accepted': 0, 'skipped': 0, 'failed': 0, 'candidates': 0, 'documented': 0}
    semaphore = asyncio.Semaphore(8)
    async with async_playwright() as playwright:
        options = {'headless': headless, 'accept_downloads': False, 'ignore_https_errors': False}
        if executable_path:
            options['executable_path'] = executable_path
        elif channel and channel != 'chromium':
            options['channel'] = channel
        profile = Path(data_dir) / 'profiles' / (channel or 'chromium')
        profile.mkdir(parents=True, exist_ok=True)
        try:
            browser = await playwright.chromium.launch_persistent_context(str(profile.resolve()), **options)
        except Exception:
            raise ValueError('Tarayıcı açılamadı. Kurulu Edge/Chrome, Playwright kurulumu ve ayrı profil kilidini kontrol edin. HAR import alternatifi README içinde.') from None

        def started(request):
            if host_matches(request.url, hosts):
                starts[id(request)] = request_context(request, current['action'])

        async def capture(request, metadata):
            async with semaphore:
                try:
                    if mode == 'scan' and request.resource_type == 'script':
                        response = await request.response()
                        if response is not None:
                            length = int((await response.all_headers()).get('content-length', '0'))
                            if length <= 2_000_000:
                                script = await response.text()
                                candidates = [i for i in script_candidates(script, request.url, metadata['page'], application) if host_matches(i['origin'], hosts)]
                                if candidates:
                                    result = catalog.import_items(candidates, run_id + ':js:' + str(stats['candidates']), application, metadata['page'], '', 'loaded-javascript', 0)
                                    stats['candidates'] += result['accepted']
                        return
                    entry = await response_entry(request, metadata)
                    if entry is None:
                        stats['skipped'] += 1
                        return
                    serial = stats['captured']
                    stats['captured'] += 1
                    result = ingest(catalog, {'log': {'entries': [entry]}}, application=application,
                                    filename='browser-session', hosts=hosts, capture_id=run_id + ':' + str(serial))
                    stats['accepted'] += result['accepted']
                    content = entry['response']['content'].get('text')
                    if content:
                        try:
                            document = json.loads(content)
                        except (ValueError, TypeError):
                            document = None
                        if isinstance(document, dict) and ('openapi' in document or 'swagger' in document) and isinstance(document.get('paths'), dict):
                            result_doc = ingest(catalog, document, application=application, filename='loaded-api-document', base_url=request.url, hosts=hosts)
                            stats['documented'] += result_doc['accepted']
                    stats['skipped'] += result['skipped']
                except Exception:
                    stats['failed'] += 1

        def finished(request):
            metadata = starts.pop(id(request), None)
            if metadata is not None:
                task = asyncio.create_task(capture(request, metadata))
                pending.add(task)
                task.add_done_callback(pending.discard)

        def failed(request):
            if starts.pop(id(request), None) is not None:
                stats['failed'] += 1

        if mode != 'login':
            browser.on('request', started)
            browser.on('requestfinished', finished)
            browser.on('requestfailed', failed)
        if mode == 'scan':
            print('URL taraması başladı: sayfa yüklenmesi ve yüklenen JavaScript dosyaları inceleniyor.', flush=True)
        elif mode == 'login':
            print('Uygulamaya giriş yapın. İşlem bitince terminale stop yazın. Oturum ayrı profilde saklanacak.', flush=True)
        else:
            print('Kayıt açık. Bu tarayıcı penceresindeki sitelerde normal şekilde gezinin.', flush=True)
            print('Terminal: mark Yeni gelen kayıtlar | mark Üzerime al | stop', flush=True)
        print('Ayrı kayıt profili kullanılıyor; ilk seferde uygulamalara giriş yapmanız gerekebilir.', flush=True)
        began = asyncio.get_running_loop().time()
        try:
            page = browser.pages[0] if browser.pages else await browser.new_page()
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=45000)
            except Exception:
                print('İlk sayfa tamamlanamadı; tarayıcıdan adresi açmayı/giriş yapmayı deneyebilirsiniz.', flush=True)
            if mode == 'scan':
                began = asyncio.get_running_loop().time()
                duration = scan_wait
            while browser.pages and (duration is None or asyncio.get_running_loop().time() - began < duration):
                try:
                    command = commands.get_nowait().strip()
                    if command.lower() in ('stop', 'quit', 'exit'):
                        break
                    if command.lower().startswith('mark '):
                        current['action'] = clean_text(command[5:], 200)
                        print('İşlem etiketi: ' + current['action'], flush=True)
                    elif command:
                        print('Komutlar: mark <işlem adı> veya stop', flush=True)
                except queue.Empty:
                    pass
                if mode == 'scan' and scroll:
                    try:
                        await page.mouse.wheel(0, 600)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(0.2)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            if mode != 'login':
                browser.remove_listener('request', started)
                browser.remove_listener('requestfinished', finished)
                browser.remove_listener('requestfailed', failed)
            if pending:
                try:
                    await asyncio.wait_for(asyncio.gather(*list(pending), return_exceptions=True), timeout=15)
                except asyncio.TimeoutError:
                    stats['failed'] += 1
            try:
                await browser.close()
            except Exception:
                pass
    return stats

"""Komut satırı; bağlantı yalnız açıkça seçilmiş gerçek analizde kurulur."""
import argparse
import json
import sys

from .errors import ConfigError,InputError,IntegrationError

def main(argv=None):
    parser=argparse.ArgumentParser(description="Risk feature incelemesi — kurum kataloğu web uygulaması")
    sub=parser.add_subparsers(dest="command",required=True)
    run=sub.add_parser("analyze",help="Excel dosyasını incele")
    run.add_argument("input")
    run.add_argument("--output",required=True)
    run.add_argument("--mode",choices=["real"],default="real")
    run.add_argument("--config")
    run.add_argument("--state-dir")
    run.add_argument("--no-resume",action="store_true")
    run.add_argument("--strategy",choices=["exact","bm25","embedding","hybrid"],default="hybrid")
    web=sub.add_parser("serve",help="Yerel web uygulamasını aç")
    web.add_argument("--port",type=int,default=8765)
    web.add_argument("--no-browser",action="store_true")
    check=sub.add_parser("check-config",help="Ayarları bağlantı yapmadan doğrula")
    check.add_argument("path")
    check.add_argument("--mode",choices=["real"],default="real")
    prepared=sub.add_parser("prepare",help="Düzenleme ve sıralamada korunacak satır kimlikli Excel kopyası")
    prepared.add_argument("input")
    prepared.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=="prepare":
            from .excelio import prepare_requests
            print(prepare_requests(args.input,args.output))
            return 0
        if args.command=="serve":
            from .web import serve
            serve(args.port,not args.no_browser)
            return 0
        if args.command=="check-config":
            from .config import load_config
            load_config(args.path,args.mode)
            print("Yapılandırma biçimi doğrulandı. Ağ bağlantısı denenmedi.")
            return 0
        from .pipeline import analyze
        result=analyze(args.input,args.output,mode=args.mode,config_path=args.config,state_dir=args.state_dir,resume=not args.no_resume,strategy=args.strategy)
        print(json.dumps({"durum":result["run_info"]["Tamamlanma"],"satır":len(result["results"]),"dosya":result["output"],"mod":result["run_info"]["Mod"]},ensure_ascii=False))
        return 0 if result["run_info"]["Tamamlanma"]=="Tamamlandı" else 2
    except (ConfigError,InputError,IntegrationError) as error:
        print(str(error),file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("İş kesildi. Aynı komutu yeniden çalıştırarak checkpoint üzerinden devam edebilirsiniz.",file=sys.stderr)
        return 130
    except Exception:
        print("İş tamamlanamadı. Dosya erişimini ve kurulum bağımlılıklarını kontrol edin. Hassas hata ayrıntıları yazdırılmadı.",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())

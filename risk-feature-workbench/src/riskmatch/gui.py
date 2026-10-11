"""İnce yerel arayüz; analiz mantığı pipeline içindedir. Ağ sunucusu yok."""
from pathlib import Path
from queue import Queue,Empty
import threading
import tkinter as tk
from tkinter import filedialog,ttk,messagebox

from .errors import ConfigError,InputError,IntegrationError

class App:
    def __init__(self,root):
        self.root=root
        root.title("Risk Feature Workbench")
        root.geometry("820x680")
        root.minsize(780,660)
        style=ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame",background="#F8FAFC")
        style.configure("TLabel",background="#F8FAFC",foreground="#142C47",font=("Segoe UI",11))
        style.configure("Title.TLabel",font=("Segoe UI",23,"bold"))
        style.configure("TButton",font=("Segoe UI",11),padding=10)
        self.input=tk.StringVar()
        self.config=tk.StringVar()
        self.mode=tk.StringVar(value="demo")
        self.status=tk.StringVar(value="Sentetik demo hazır. Başlamak için Excel dosyası seçin.")
        self.queue=Queue()
        self.busy=False
        frame=ttk.Frame(root,padding=28)
        frame.pack(fill="both",expand=True)
        frame.columnconfigure(0,weight=1)
        ttk.Label(frame,text="Risk alanı incelemesi",style="Title.TLabel").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,10))
        ttk.Label(frame,text="Talebi seçin. Kanıtları inceleyin. Uzman kararını Excel'de tamamlayın.",wraplength=700).grid(row=1,column=0,columnspan=2,sticky="w",pady=(0,22))
        ttk.Label(frame,text="Çalışma modu").grid(row=2,column=0,sticky="w")
        modes=ttk.Frame(frame)
        modes.grid(row=3,column=0,columnspan=2,sticky="w",pady=(8,16))
        self.demo_button=ttk.Radiobutton(modes,text="Demo · sentetik katalog",variable=self.mode,value="demo",command=self.mode_changed)
        self.demo_button.pack(side="left",padx=(0,24))
        self.real_button=ttk.Radiobutton(modes,text="Gerçek · kurum bağlantıları",variable=self.mode,value="real",command=self.mode_changed)
        self.real_button.pack(side="left")
        ttk.Label(frame,text="Talep Excel dosyası (.xlsx)").grid(row=4,column=0,sticky="w")
        ttk.Entry(frame,textvariable=self.input).grid(row=5,column=0,sticky="ew",pady=8)
        self.pick_button=ttk.Button(frame,text="Excel seç",command=self.pick_input)
        self.pick_button.grid(row=5,column=1,padx=(12,0))
        self.sample_button=ttk.Button(frame,text="Sentetik örneği kullan",command=self.sample)
        self.sample_button.grid(row=6,column=0,sticky="w",pady=(0,16))
        ttk.Label(frame,text="Gerçek mod yapılandırması (config.local.json)").grid(row=7,column=0,sticky="w")
        ttk.Entry(frame,textvariable=self.config).grid(row=8,column=0,sticky="ew",pady=8)
        self.config_button=ttk.Button(frame,text="Ayar dosyası seç",command=self.pick_config)
        self.config_button.grid(row=8,column=1,padx=(12,0))
        self.run_button=ttk.Button(frame,text="Analiz et ve sonucu kaydet",command=self.run)
        self.run_button.grid(row=9,column=0,columnspan=2,sticky="ew",pady=(18,12))
        self.progress=ttk.Progressbar(frame,mode="indeterminate")
        self.progress.grid(row=10,column=0,columnspan=2,sticky="ew")
        ttk.Label(frame,textvariable=self.status,wraplength=700).grid(row=11,column=0,columnspan=2,sticky="w",pady=(14,8))
        ttk.Label(frame,text="Demo banka bağlantısını doğrulamaz. Benzerlik skoru doğruluk yüzdesi değildir.",wraplength=700).grid(row=12,column=0,columnspan=2,sticky="w")
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.after(150,self.poll)

    def mode_changed(self):
        self.status.set("Demo: kurum ağına bağlanılmaz." if self.mode.get()=="demo" else "Gerçek: yalnız Analiz et düğmesi kurum bağlantılarını başlatır. Ayar dosyası gereklidir.")

    def pick_input(self):
        value=filedialog.askopenfilename(filetypes=[("Excel çalışma kitabı","*.xlsx")])
        if value:self.input.set(value)

    def pick_config(self):
        value=filedialog.askopenfilename(filetypes=[("JSON yapılandırma","*.json")])
        if value:self.config.set(value)

    def sample(self):
        self.mode.set("demo")
        self.input.set(str(Path(__file__).parent/"data"/"sentetik_talepler.xlsx"))
        self.mode_changed()

    def run(self):
        if self.busy:return
        if not self.input.get():
            self.status.set("Önce bir Excel dosyası seçin.")
            return
        if self.mode.get()=="real" and not self.config.get():
            self.status.set("Gerçek mod için yapılandırma dosyası seçin.")
            return
        output=filedialog.asksaveasfilename(defaultextension=".xlsx",initialfile="risk_inceleme_sonuclari.xlsx",filetypes=[("Excel sonuç dosyası","*.xlsx")])
        if not output:return
        self.busy=True
        for widget in (self.run_button,self.pick_button,self.config_button,self.sample_button,self.real_button,self.demo_button):
            widget.configure(state="disabled")
        self.progress.start(12)
        self.status.set("İnceleme sürüyor. Tamamlanan satırlar yerel olarak kaydediliyor.")
        args=(self.input.get(),output,self.mode.get(),self.config.get() or None)
        threading.Thread(target=self.worker,args=args,daemon=True).start()

    def worker(self,source,output,mode,config):
        try:
            from .pipeline import analyze
            result=analyze(source,output,mode=mode,config_path=config if mode=="real" else None)
            self.queue.put((True,result))
        except (ConfigError,InputError,IntegrationError) as error:
            self.queue.put((False,str(error)))
        except Exception:
            self.queue.put((False,"İş tamamlanamadı. Dosya erişimini ve kurulum ayarlarını kontrol edin."))

    def poll(self):
        try:
            ok,value=self.queue.get_nowait()
        except Empty:
            pass
        else:
            self.busy=False
            self.progress.stop()
            for widget in (self.run_button,self.pick_button,self.config_button,self.sample_button,self.real_button,self.demo_button):
                widget.configure(state="normal")
            if ok:
                self.status.set(f"{value['run_info']['Tamamlanma']}. {len(value['results'])} satır. Sonuç: {value['output']}")
            else:
                self.status.set(value)
        self.root.after(150,self.poll)

    def close(self):
        if self.busy and not messagebox.askyesno("İş sürüyor","Pencere kapatılsın mı? Aynı dosyayla yeniden başladığınızda tamamlanan satırlar üzerinden devam edilir."):
            return
        self.root.destroy()

def launch():
    try:
        root=tk.Tk()
    except tk.TclError:
        raise ConfigError("Pencere bileşeni başlatılamadı. Standart Python Windows kurulumunda Tcl/Tk bileşenini etkinleştirin; CLI demo/analyze komutları kullanılabilir.") from None
    App(root)
    root.mainloop()

# -*- coding: utf-8 -*-
"""DE1-SoC Video Player — вікно застосунку.

Обираєте відео -> застосунок робить файл для плати DE1-SoC або CPUlator,
сам його перевіряє, а за потреби відкриває CPUlator із уже завантаженим
роликом (з наперед виділеною пам'яттю для великих файлів).
"""
import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devideo import __version__                  # noqa: E402
from devideo import helper                       # noqa: E402
from devideo.make import ROOT, make              # noqa: E402
from devideo.targets import QUALITY, TARGETS     # noqa: E402

BOARD_STEPS = """ЯК ЗАПУСТИТИ НА ПЛАТІ DE1-SoC

1. Прошийте ПЛІС системою з лабораторної роботи 6 (зі звуком):
     hardware\\DE1_SoC_Computer.sof
   (Quartus -> Tools -> Programmer -> Add File -> Start).

2. Монітор — до роз'єму VGA, навушники або колонки — до зеленого гнізда LINE OUT.

3. Intel FPGA Monitor Program -> File -> Open Project ->
     firmware\\board\\player.amp -> Actions -> Load -> Continue.
   На індикаторах з'явиться «------»: програвач чекає на ролик.

4. Двічі клацніть згенерований файл ..._board_load.bat у теці output.
   Він заливає ролик у SDRAM через JTAG (близько 1 МБ/с, тобто ролик
   на 28 МБ — приблизно пів хвилини). Щойно ролик доїде, показ почнеться сам.
   Порядок кроків 3 і 4 вільний: програвач чекає, поки дані з'являться.

Керування:
   KEY0  — пауза; повторне натискання продовжує з того ж місця
   KEY1  — показ із початку
   SW0   — угорі: після останнього кадру показ починається знову
   SW2..1 — гучність: 00 повна, 01 удвічі тихіше, 10 учетверо, 11 увосьмеро
   HEX5..HEX0 — номер кадру (шістнадцятковий)
   LEDR0 — показ іде, LEDR1 — пауза, LEDR2 — звук знайдено,
   LEDR9 — кадр не вклався в 41,667 мс (гасне після KEY1)

Перед першим запуском можна прогнати firmware\\board\\testpattern.amp:
кольорові смуги й шахівниця перевіряють порядок байтів і крок рядка.
"""


class App:
    def __init__(self, root):
        self.root = root
        root.title('DE1-SoC Video Player %s' % __version__)
        root.geometry('980x720')
        root.minsize(820, 580)
        self.q = queue.Queue()
        self.busy = False
        self.result = None
        self.helper = None

        style = ttk.Style()
        try:
            style.theme_use('vista')
        except tk.TclError:
            pass
        pad = {'padx': 8, 'pady': 4}

        head = ttk.Label(root, text='Відео -> ролик для плати DE1-SoC або CPUlator',
                         font=('Segoe UI', 13, 'bold'))
        head.pack(anchor='w', padx=10, pady=(10, 4))

        f1 = ttk.LabelFrame(root, text=' 1. Відео ')
        f1.pack(fill='x', **pad)
        self.video = tk.StringVar()
        ttk.Entry(f1, textvariable=self.video, state='readonly').pack(side='left', fill='x', expand=True,
                                                                       padx=6, pady=6)
        ttk.Button(f1, text='Обрати…', command=self.pick).pack(side='left', padx=6)

        f2 = ttk.LabelFrame(root, text=' 2. Куди і як ')
        f2.pack(fill='x', **pad)
        self.target = tk.StringVar(value='cpulator')
        col = ttk.Frame(f2)
        col.grid(row=0, column=0, sticky='nw', padx=6, pady=4)
        for k, t in TARGETS.items():
            ttk.Radiobutton(col, text=t.title, value=k, variable=self.target,
                            command=self.target_changed).pack(anchor='w')
        self.audio = tk.BooleanVar(value=True)
        ttk.Checkbutton(col, text='Зі звуком', variable=self.audio).pack(anchor='w', pady=(6, 0))
        col2 = ttk.Frame(f2)
        col2.grid(row=0, column=1, sticky='nw', padx=20, pady=4)
        ttk.Label(col2, text='Якість зображення:').pack(anchor='w')
        self.quality = tk.StringVar(value=TARGETS['cpulator'].quality)
        for k, (thr, title) in QUALITY.items():
            ttk.Radiobutton(col2, text=title, value=k, variable=self.quality).pack(anchor='w')
        col3 = ttk.Frame(f2)
        col3.grid(row=0, column=2, sticky='nw', padx=20, pady=4)
        ttk.Label(col3, text='Фрагмент (с), порожньо — усе відео').grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(col3, text='з').grid(row=1, column=0, sticky='e')
        self.start = tk.StringVar(value='0')
        ttk.Entry(col3, textvariable=self.start, width=8).grid(row=1, column=1, sticky='w', pady=2)
        ttk.Label(col3, text='тривалість').grid(row=2, column=0, sticky='e')
        self.length = tk.StringVar(value='')
        ttk.Entry(col3, textvariable=self.length, width=8).grid(row=2, column=1, sticky='w', pady=2)

        f3 = ttk.LabelFrame(root, text=' 3. Дії ')
        f3.pack(fill='x', **pad)
        self.buttons = []
        for text, cmd in (('Згенерувати й перевірити', self.generate),
                          ('Перевірити в CPUlator (автоматично)', self.check_cpulator),
                          ('Відкрити в CPUlator', self.open_cpulator),
                          ('Тека з файлами', self.open_folder),
                          ('Як запустити на платі', self.board_help)):
            b = ttk.Button(f3, text=text, command=cmd)
            b.pack(side='left', padx=6, pady=6)
            self.buttons.append(b)

        self.log_box = ScrolledText(root, height=18, font=('Consolas', 10), state='disabled', wrap='word')
        self.log_box.pack(fill='both', expand=True, **pad)
        self.status = tk.StringVar(value='Оберіть відео (mp4, mkv, avi, mov, webm...).')
        ttk.Label(root, textvariable=self.status, anchor='w').pack(fill='x', padx=10, pady=(0, 8))

        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.pump)
        self.log('DE1-SoC Video Player %s. Файли зберігаються в теці %s'
                 % (__version__, os.path.join(ROOT, 'output')))

    # ------------------------------------------------------------ службове
    def log(self, text):
        self.q.put(('log', text))

    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == 'log':
                    self.log_box.configure(state='normal')
                    self.log_box.insert('end', val + '\n')
                    self.log_box.see('end')
                    self.log_box.configure(state='disabled')
                elif kind == 'status':
                    self.status.set(val)
                elif kind == 'done':
                    self.busy = False
                    for b in self.buttons:
                        b.state(['!disabled'])
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def run_bg(self, title, fn):
        if self.busy:
            return
        self.busy = True
        for b in self.buttons:
            b.state(['disabled'])
        self.q.put(('status', title + '...'))

        def work():
            try:
                fn()
            except Exception as e:
                self.log('ПОМИЛКА: %s' % e)
                self.log(traceback.format_exc(limit=3))
                self.q.put(('status', 'Помилка: %s' % e))
            finally:
                self.q.put(('done', None))
        threading.Thread(target=work, daemon=True).start()

    def need_result(self, cpulator=False):
        if not self.result:
            messagebox.showinfo('Спершу файл', 'Спершу натисніть «Згенерувати й перевірити».')
            return False
        if cpulator and self.result['target'].board:
            messagebox.showinfo('Файл для плати', 'Останній файл зроблено для плати (звук 48 кГц, заливка '
                                'через JTAG). Для CPUlator оберіть ціль «CPUlator» і згенеруйте файл ще раз.')
            return False
        return True

    # ------------------------------------------------------------ дії
    def target_changed(self):
        self.quality.set(TARGETS[self.target.get()].quality)

    def pick(self):
        p = filedialog.askopenfilename(title='Оберіть відео', filetypes=[
            ('Відео', '*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.wmv *.flv *.mpg *.mpeg *.ts *.gif'),
            ('Усі файли', '*.*')])
        if p:
            self.video.set(p)
            self.status.set('Відео обрано. Далі — «Згенерувати й перевірити».')

    def generate(self):
        video = self.video.get()
        if not video:
            messagebox.showinfo('Немає відео', 'Оберіть відео кнопкою «Обрати…».')
            return
        try:
            start = float(self.start.get() or 0)
            length = float(self.length.get()) if self.length.get().strip() else None
        except ValueError:
            messagebox.showerror('Фрагмент', 'Початок і тривалість — числа в секундах.')
            return

        # змінні вікна читаємо тут, у головному потоці: tkinter не любить фонових звертань
        target, quality, with_audio = self.target.get(), self.quality.get(), self.audio.get()

        def fn():
            self.log('')
            self.log('=== %s -> %s' % (os.path.basename(video), TARGETS[target].title))
            res = make(video, target, quality, with_audio, start, length, log=self.log)
            self.result = res
            if res['target'].board:
                tip = 'для плати: ролик заливає %s' % os.path.basename(res['files'][1])
            elif res['nums'].get('needs_bigmem'):
                tip = 'для CPUlator відкриється режим із наперед виділеною пам\'яттю'
            else:
                tip = 'вміщується у звичайний CPUlator'
            self.q.put(('status', ('Готово: %s (%s)' % (os.path.basename(res['main']), tip))
                        if res['ok'] else 'Файл зроблено, але перевірка знайшла помилки — див. журнал'))
        self.run_bg('Генерую', fn)

    def premem(self):
        size = os.path.getsize(self.result['main']) / 1048576
        return int(min(1024, 64 + 3 * size)) if self.result['nums'].get('needs_bigmem') else 0

    def open_cpulator(self):
        if not self.need_result(cpulator=True):
            return
        out = os.path.dirname(self.result['main'])
        pm = self.premem()
        if self.helper and self.helper.premem_mb != pm:
            self.helper.stop()
            self.helper = None
        if not self.helper:
            self.helper = helper.start(out, pm)
        url = self.helper.url_for(os.path.basename(self.result['main']))
        self.log('CPUlator: %s%s' % (url, ' (пам\'ять %d МБ)' % pm if pm else ''))
        self.log('Ролик завантажиться сам. Натисніть Continue МИШЕЮ — без клацання браузер не вмикає звук.')
        self.log('Якщо за 30 с нічого не сталося — оновіть сторінку кнопкою браузера.')
        webbrowser.open(url)

    def check_cpulator(self):
        if not self.need_result(cpulator=True):
            return
        from devideo import check

        def fn():
            self.log('')
            self.log('=== Автоматична перевірка в CPUlator (браузер без вікна, близько хвилини)')
            ok, lines, nums = check.run(self.result['main'], self.premem() > 0, log=self.log)
            self.log('Підсумок: %s' % ('усе працює' if ok else 'є зауваження — див. рядки «НІ»'))
            self.q.put(('status', 'Перевірку в CPUlator %s' % ('пройдено' if ok else 'не пройдено')))
        self.run_bg('Перевіряю в CPUlator', fn)

    def open_folder(self):
        d = os.path.dirname(self.result['main']) if self.result else os.path.join(ROOT, 'output')
        os.makedirs(d, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(d)
        else:
            subprocess.Popen(['xdg-open', d])

    def board_help(self):
        w = tk.Toplevel(self.root)
        w.title('Запуск на платі DE1-SoC')
        w.geometry('760x640')
        t = ScrolledText(w, font=('Consolas', 10), wrap='word')
        t.pack(fill='both', expand=True, padx=8, pady=8)
        t.insert('end', BOARD_STEPS)
        if self.result:
            t.insert('end', '\nЗгенеровані файли:\n' + '\n'.join(self.result['files']) + '\n')
        t.configure(state='disabled')

    def close(self):
        if self.helper:
            self.helper.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()

import json, time, uuid, re, shutil, sys
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QEvent, QObject
from PySide6.QtGui import QPainter, QPixmap, QColor, QPen, QFont, QCursor
from PySide6.QtWidgets import *
import keyboard
import ctypes
from rta_audio import Audio
from steam_achievements import read_achievements, discover
ROOT=Path(__file__).resolve().parent
CHART=ROOT/'chart.json'; CONFIG=ROOT/'config.json'; RECORDS=ROOT/'records'; MAPS=ROOT/'maps'
for p in (RECORDS,MAPS): p.mkdir(exist_ok=True)
def load(p,default):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
def save(p,data):
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(p)
def uid(): return uuid.uuid4().hex
def fmt(t):
    s=int(max(0,t)); return f'{s//3600:02}:{s//60%60:02}:{s%60:02}'
def step(): return dict(id=uid(),group='新規',title='新しい手順',notes='',split='',tasks=[],waves=[])
def wave(): return dict(name='WAVE',image='',points=[])
def fit_window(window, preferred=None):
    screen=window.screen() or QApplication.primaryScreen()
    if screen is None:return
    area=screen.availableGeometry()
    frame=window.frameGeometry()
    extra_w=max(16,frame.width()-window.width())
    extra_h=max(40,frame.height()-window.height())
    w,h=preferred or (window.width(),window.height())
    window.resize(min(w,max(1,area.width()-extra_w-12)),min(h,max(1,area.height()-extra_h-12)))
    frame=window.frameGeometry()
    x=max(area.left(),min(frame.left(),area.right()-frame.width()+1))
    y=max(area.top(),min(frame.top(),area.bottom()-frame.height()+1))
    window.move(x,y)

class WindowFitFilter(QObject):
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.Show and isinstance(obj,QDialog):
            QTimer.singleShot(0,lambda w=obj: self.fit_dialog(w))
        return False
    def fit_dialog(self,w):
        try:
            if w.isVisible():fit_window(w)
        except RuntimeError:pass

class MapView(QWidget):
    clicked=Signal(float,float)
    def __init__(self):
        super().__init__(); self.setMinimumSize(160,120); self.data=wave(); self.pix=QPixmap(); self.rect=QRectF()
    def show_wave(self,w):
        self.data=w or wave(); self.pix=QPixmap(str(ROOT/self.data['image'])) if self.data['image'] else QPixmap(); self.update()
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.fillRect(self.rect if False else self.contentsRect(),QColor('#20252b')); self.rect=QRectF()
        if self.pix.isNull():
            p.setPen(QColor('white')); p.drawText(self.contentsRect(),Qt.AlignmentFlag.AlignCenter,'PNGマップ未登録'); return
        scale=min(self.width()/self.pix.width(),self.height()/self.pix.height()); w=self.pix.width()*scale; h=self.pix.height()*scale
        self.rect=QRectF((self.width()-w)/2,(self.height()-h)/2,w,h); p.drawPixmap(self.rect,self.pix,QRectF(self.pix.rect()))
        for i,m in enumerate(self.data['points'],1):
            x=self.rect.x()+m['x']*w; y=self.rect.y()+m['y']*h; r=m.get('radius',16); p.setPen(QPen(QColor('#ff3030'),3)); p.setBrush(QColor(30,0,0,150)); p.drawEllipse(QRectF(x-r,y-r,r*2,r*2)); p.setPen(QColor('white')); p.setFont(QFont('Arial',max(8,int(r*.8)),QFont.Weight.Bold)); p.drawText(QRectF(x-r,y-r,r*2,r*2),Qt.AlignmentFlag.AlignCenter,str(i))
    def mousePressEvent(self,e):
        pos=e.position(); r=self.rect
        if not r.isEmpty() and r.contains(pos): self.clicked.emit((pos.x()-r.x())/r.width(),(pos.y()-r.y())/r.height())
class MapEditor(QDialog):
    def __init__(self,waves,parent):
        super().__init__(parent); self.setWindowTitle('マップ・WAVE編集'); self.resize(900,650); self.waves=json.loads(json.dumps(waves)); self.i=-1
        l=QVBoxLayout(self); row=QHBoxLayout(); l.addLayout(row); self.combo=QComboBox(); row.addWidget(self.combo,1)
        for name,fn in [('WAVE追加',self.add),('WAVE複製',self.duplicate_wave),('WAVE削除',self.remove),('PNG選択',self.image),('名称変更',self.rename)]:
            b=QPushButton(name); b.clicked.connect(fn); row.addWidget(b)
        self.map=MapView(); l.addWidget(self.map,1); self.map.clicked.connect(self.point); self.points=QListWidget(); l.addWidget(self.points)
        row=QHBoxLayout(); l.addLayout(row)
        for name,fn in [('説明編集',self.note),('赤丸サイズ',self.radius),('位置修正（選択後クリック）',self.move),('削除',self.delete),('↑',lambda:self.reorder(-1)),('↓',lambda:self.reorder(1))]:
            b=QPushButton(name); b.clicked.connect(fn); row.addWidget(b)
        self.moving=None; self.combo.currentIndexChanged.connect(self.select); buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); l.addWidget(buttons); self.refresh(0)
    def current(self): return self.waves[self.i] if 0<=self.i<len(self.waves) else None
    def refresh(self,i):
        self.combo.blockSignals(True); self.combo.clear(); self.combo.addItems([w['name'] for w in self.waves]); self.combo.setCurrentIndex(min(i,len(self.waves)-1)); self.combo.blockSignals(False); self.select(self.combo.currentIndex())
    def select(self,i): self.i=i; self.moving=None; self.redraw()
    def redraw(self):
        w=self.current(); self.map.show_wave(w); self.points.clear()
        if w: self.points.addItems([f"{i+1}: {m.get('note','')}" for i,m in enumerate(w['points'])])
    def add(self):
        w=wave(); w['name']=f'WAVE {len(self.waves)+1}'; self.waves.append(w); self.refresh(len(self.waves)-1)
    def duplicate_wave(self):
        if self.current() is None:return
        w=json.loads(json.dumps(self.current())); w['name']+='（コピー）'; i=self.i+1; self.waves.insert(i,w); self.refresh(i)
    def remove(self):
        if self.current() is not None: self.waves.pop(self.i); self.refresh(max(0,self.i-1))
    def image(self):
        if self.current() is None: self.add()
        f,_=QFileDialog.getOpenFileName(self,'PNG選択','','PNG (*.png)')
        if f:
            dest=MAPS/(uid()+'.png'); shutil.copyfile(f,dest); self.current()['image']=dest.relative_to(ROOT).as_posix(); self.redraw()
    def rename(self):
        if self.current() is None:return
        s,ok=QInputDialog.getText(self,'名称','WAVE名',text=self.current()['name'])
        if ok:self.current()['name']=s; self.refresh(self.i)
    def point(self,x,y):
        w=self.current()
        if w is None:return
        if self.moving is not None: w['points'][self.moving].update(x=x,y=y); self.moving=None
        else:w['points'].append(dict(x=x,y=y,note='',radius=16))
        self.redraw()
    def selected(self):
        w=self.current(); i=self.points.currentRow(); return w['points'][i] if w and i>=0 else None
    def note(self):
        m=self.selected()
        if m is not None:
            s,ok=QInputDialog.getMultiLineText(self,'説明','敵・対処方法',m['note'])
            if ok:m['note']=s; self.redraw()
    def radius(self):
        m=self.selected()
        if m is not None:
            n,ok=QInputDialog.getInt(self,'サイズ','半径',m['radius'],5,100)
            if ok:m['radius']=n; self.redraw()
    def move(self):
        if self.selected() is not None:self.moving=self.points.currentRow()
    def delete(self):
        if self.selected() is not None:self.current()['points'].pop(self.points.currentRow()); self.redraw()
    def reorder(self,d):
        if self.selected() is None:return
        a=self.current()['points']; i=self.points.currentRow(); j=i+d
        if 0<=j<len(a):a[i],a[j]=a[j],a[i]; self.redraw(); self.points.setCurrentRow(j)
class Editor(QDialog):
    def __init__(self,steps,parent):
        super().__init__(parent); self.setWindowTitle('チャート編集'); self.resize(1100,700); self.steps=json.loads(json.dumps(steps)); self.i=-1
        l=QVBoxLayout(self); row=QHBoxLayout(); l.addLayout(row,1); self.list=QListWidget(); row.addWidget(self.list,1); form=QFormLayout(); row.addLayout(form,2)
        self.group=QLineEdit(); self.title=QPlainTextEdit(); self.notes=QPlainTextEdit(); self.split=QLineEdit(); self.tasks=QPlainTextEdit()
        for name,w in [('周回・分類',self.group),('行動内容',self.title),('注意事項・解説',self.notes),('主要区間名（終了地点のみ）',self.split),('チェック項目（1行1項目）',self.tasks)]:form.addRow(name,w)
        self.tts=QLineEdit(); self.tts_warning=QLineEdit(); self.tts_mute=QCheckBox(); form.addRow("読み上げ専用文",self.tts); form.addRow("重要警告の読み上げ",self.tts_warning); form.addRow("この手順は読み上げない",self.tts_mute)
        b=QPushButton('マップ・WAVE編集'); b.clicked.connect(self.maps); form.addRow(b)
        b=QPushButton('別の手順から全WAVEコピー'); b.clicked.connect(self.copy_waves); form.addRow(b)
        bar=QHBoxLayout(); l.addLayout(bar)
        for name,fn in [('追加',self.add),('複製',self.duplicate),('削除',self.delete),('↑',lambda:self.move(-1)),('↓',lambda:self.move(1))]:
            b=QPushButton(name); b.clicked.connect(fn); bar.addWidget(b)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.finish); buttons.rejected.connect(self.reject); l.addWidget(buttons); self.list.currentRowChanged.connect(self.select); self.refresh(0)
    def commit(self):
        if 0<=self.i<len(self.steps):self.steps[self.i].update(group=self.group.text(),title=self.title.toPlainText(),notes=self.notes.toPlainText(),split=self.split.text(),tasks=[x for x in self.tasks.toPlainText().splitlines() if x.strip()],tts=self.tts.text(),tts_warning=self.tts_warning.text(),tts_mute=self.tts_mute.isChecked())
    def refresh(self,i):
        self.i=-1; self.list.blockSignals(True); self.list.clear(); self.list.addItems([f"{s['group']} | {s['title'][:45]}" for s in self.steps]); self.list.blockSignals(False); self.list.setCurrentRow(i)
    def select(self,i):
        old=self.i; self.commit()
        if old>=0 and old<len(self.steps):self.list.item(old).setText(f"{self.steps[old]['group']} | {self.steps[old]['title'][:45]}")
        self.i=i
        if i<0:return
        s=self.steps[i]; self.group.setText(s['group']); self.title.setPlainText(s['title']); self.notes.setPlainText(s['notes']); self.split.setText(s['split']); self.tasks.setPlainText('\n'.join(s['tasks'])); self.tts.setText(s.get('tts','')); self.tts_warning.setText(s.get('tts_warning','')); self.tts_mute.setChecked(s.get('tts_mute',False))
    def add(self):self.commit(); i=self.i+1; self.steps.insert(i,step()); self.refresh(i)
    def duplicate(self):
        if self.i<0:return
        self.commit(); s=json.loads(json.dumps(self.steps[self.i])); s['id']=uid(); i=self.i+1; self.steps.insert(i,s); self.refresh(i)
    def delete(self):
        if self.i>=0 and len(self.steps)>1:self.commit(); self.steps.pop(self.i); self.refresh(min(self.i,len(self.steps)-1))
    def move(self,d):
        self.commit(); i=self.i; j=i+d
        if i>=0 and 0<=j<len(self.steps):self.steps[i],self.steps[j]=self.steps[j],self.steps[i]; self.refresh(j)
    def copy_waves(self):
        if self.i<0:return
        self.commit()
        sources=[(i,s) for i,s in enumerate(self.steps) if i!=self.i and s.get('waves')]
        if not sources:
            QMessageBox.information(self,'全WAVEコピー','コピー可能なマップが登録されていません。'); return
        d=QDialog(self); d.setWindowTitle('コピー元の手順を選択'); d.resize(650,450); l=QVBoxLayout(d)
        search=QLineEdit(); search.setPlaceholderText('手順名・周回・WAVE名で検索'); l.addWidget(search)
        items=QListWidget(); l.addWidget(items)
        def refresh(text=''):
            items.clear()
            for i,s in sources:
                label=f"{i+1}: {s['group']} | {s['title']} ({len(s['waves'])} WAVE)"
                if text.casefold() not in (label+' '+ ' '.join(w['name'] for w in s['waves'])).casefold():continue
                item=QListWidgetItem(label.replace('\n',' ')); item.setData(Qt.ItemDataRole.UserRole,i); items.addItem(item)
            if items.count():items.setCurrentRow(0)
        search.textChanged.connect(refresh); refresh()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); l.addWidget(buttons)
        buttons.accepted.connect(lambda:d.accept() if items.currentItem() else None); buttons.rejected.connect(d.reject); items.itemDoubleClicked.connect(lambda _:d.accept())
        if not d.exec() or not items.currentItem():return
        source=self.steps[items.currentItem().data(Qt.ItemDataRole.UserRole)]
        if self.steps[self.i]['waves'] and QMessageBox.question(self,'上書き確認','現在の全WAVEをコピー元の内容で置き換えますか？')!=QMessageBox.StandardButton.Yes:return
        self.steps[self.i]['waves']=json.loads(json.dumps(source['waves']))
        QMessageBox.information(self,'全WAVEコピー',f"{len(source['waves'])} WAVEをコピーしました。チャート編集画面で保存してください。")
    def maps(self):
        if self.i<0:return
        d=MapEditor(self.steps[self.i]['waves'],self)
        if d.exec():self.steps[self.i]['waves']=d.waves
    def finish(self):self.commit(); self.accept()
class Main(QMainWindow):
    hot=Signal(str)
    def __init__(self):
        super().__init__(); self.setWindowTitle('YS8 RTA Helper'); self.resize(1200,850); self.steps=load(CHART,[step()]); self.keys=load(CONFIG,{'start':'f6','back':'f7','next':'f8','wave':'f9','prevwave':'shift+f9','pause':'f10','reset':'ctrl+f12'}); self.hooks=[]; self.last_hot={}; self.hot.connect(self.action, Qt.ConnectionType.QueuedConnection); self.clear_state()
        self.keys.setdefault("repeat","f11"); self.audio=Audio(ROOT,self)
        self.achievement_config=load(ROOT/'steam_config.json',{}); self.achievement_state=None; self.achievement_error=''
        root=QWidget(); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(root); self.setCentralWidget(scroll); l=QVBoxLayout(root); bar=QGridLayout(); l.addLayout(bar)
        for index,(name,fn) in enumerate([('開始',lambda:self.action('start')),('戻る',lambda:self.action('back')),('次へ',lambda:self.action('next')),('前WAVE',lambda:self.action('prevwave')),('次WAVE',lambda:self.action('wave')),('一時停止',lambda:self.action('pause')),('終了・リセット',lambda:self.action('reset')),('チャート編集',self.edit),('キー設定',self.settings),('記録一覧',self.history)]):
            b=QPushButton(name); b.clicked.connect(fn); bar.addWidget(b,index//5,index%5)
        self.clock=QLabel(); self.clock.setWordWrap(True); self.clock.setStyleSheet('font-size:22px;color:#82e4aa'); l.addWidget(self.clock); self.heading=QLabel(); self.heading.setWordWrap(True); l.addWidget(self.heading); self.title=QLabel(); self.title.setWordWrap(True); self.title.setStyleSheet('font-size:25px;font-weight:bold'); l.addWidget(self.title)
        split=QSplitter(); l.addWidget(split,1); left=QWidget(); a=QVBoxLayout(left); split.addWidget(left); self.notes=QPlainTextEdit(); self.notes.setReadOnly(True); self.notes.setStyleSheet('color:#ffd19a'); a.addWidget(QLabel('注意事項・攻略解説')); a.addWidget(self.notes,2); self.tasks=QListWidget(); a.addWidget(QLabel('チェックリスト')); a.addWidget(self.tasks,2); self.tasks.itemChanged.connect(self.check); self.upcoming=QPlainTextEdit(); self.upcoming.setReadOnly(True); a.addWidget(QLabel('次の手順')); a.addWidget(self.upcoming,1)
        self.right=QWidget(); b=QVBoxLayout(self.right); split.addWidget(self.right); self.wave_label=QLabel(); b.addWidget(self.wave_label); self.map=MapView(); b.addWidget(self.map,3); self.spawn=QPlainTextEdit(); self.spawn.setReadOnly(True); b.addWidget(self.spawn,1); split.setSizes([550,650]); self.splits=QPlainTextEdit(); self.splits.setReadOnly(True); self.splits.setFixedHeight(70); l.addWidget(self.splits); self.status=QLabel(); self.status.setWordWrap(True); l.addWidget(self.status)
        self.achievement_label=QLabel('Steam実績: 未設定'); self.achievement_label.setWordWrap(True); l.addWidget(self.achievement_label)
        ar=QHBoxLayout(); l.addLayout(ar)
        for name,fn in [('Steam実績設定',self.achievement_settings),('実績一覧',self.achievement_list),('音声設定',lambda:self.audio.settings(self,self.steps)),('再読み上げ',lambda:self.action('repeat'))]:
            button=QPushButton(name); button.clicked.connect(fn); ar.addWidget(button)
        self.audio_label=QLabel("音声: 待機中"); self.audio_label.setWordWrap(True); l.addWidget(self.audio_label); self.audio.status.connect(self.audio_label.setText)
        self.achievement_timer=QTimer(self); self.achievement_timer.timeout.connect(self.poll_achievements); self.achievement_timer.start(500)
        self.poll_achievements()
        self.timer=QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(100); self.key_timer=QTimer(self); self.key_timer.setTimerType(Qt.TimerType.PreciseTimer); self.key_timer.timeout.connect(self.poll_keys); self.register(); self.render()
    def clear_state(self):self.i=0; self.w=0; self.base=0.; self.anchor=None; self.started=False; self.finished=False; self.rows=[]; self.checked={}; self.run_id=uid(); self.started_at=None; self.achievement_events=[]; self.achievement_baseline=None
    def elapsed(self):return self.base+(time.perf_counter()-self.anchor if self.anchor is not None else 0)
    def freeze(self):self.base=self.elapsed(); self.anchor=None
    @staticmethod
    def parse_windows_key(key):
        parts=[p.strip().lower() for p in key.split('+')]
        mods={'ctrl':0x11,'control':0x11,'shift':0x10,'alt':0x12,'win':0x5B,'windows':0x5B}
        names={'space':32,'enter':13,'return':13,'tab':9,'esc':27,'escape':27,'backspace':8,'insert':45,'delete':46,'home':36,'end':35,'page up':33,'page down':34,'left':37,'up':38,'right':39,'down':40}
        names.update({f'f{i}':111+i for i in range(1,25)})
        names.update({c:ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'})
        if not parts or parts[-1] not in names or any(p not in mods for p in parts[:-1]):
            raise ValueError(f'Unsupported hotkey: {key}')
        return names[parts[-1]],frozenset(mods[p] for p in parts[:-1])
    def register(self):
        self.key_timer.stop()
        for h in self.hooks:keyboard.remove_hotkey(h)
        self.hooks=[]; self.key_error=''; self.last_key=''; self.bindings={}; self.key_down={}
        try:
            if sys.platform=='win32':
                self.get_key=ctypes.windll.user32.GetAsyncKeyState
                self.get_key.argtypes=[ctypes.c_int]; self.get_key.restype=ctypes.c_short
                self.bindings={a:self.parse_windows_key(k) for a,k in self.keys.items()}
                self.key_down={vk:self.is_down(vk) for vk,_ in self.bindings.values()}
                self.key_timer.start(10)
            else:
                for action,key in self.keys.items():
                    self.hooks.append(keyboard.add_hotkey(key,lambda a=action:self.hot.emit(a)))
        except Exception as e:
            self.key_error=f'キー登録失敗: {e}'
    def is_down(self,vk):
        return bool(self.get_key(vk)&0x8000)
    def poll_keys(self):
        mods=frozenset(v for v in (0x10,0x11,0x12,0x5B) if self.is_down(v) or (v==0x5B and self.is_down(0x5C)))
        pressed=set()
        for vk in self.key_down:
            down=self.is_down(vk)
            if down and not self.key_down[vk]:pressed.add(vk)
            self.key_down[vk]=down
        for action,(vk,required) in self.bindings.items():
            if vk in pressed and mods==required:
                self.last_key=self.keys[action]
                self.action(action)
    def action(self,a):
        if QApplication.activeModalWidget():return
        now=time.monotonic()
        if now-self.last_hot.get(a,-100)<.18:return
        self.last_hot[a]=now
        old=(self.i,self.w,self.started,self.finished,self.anchor is not None)
        if a=='repeat':self.audio.stop(); self.audio.speak(self.audio.step_text(self.steps[self.i]),interrupt=False); return
        if a=='start' and not self.started:
            self.poll_achievements(); self.achievement_baseline=self.achievement_state.copy() if self.achievement_state is not None and not self.achievement_error else None
            self.started=True; self.started_at=datetime.now().isoformat(); self.anchor=time.perf_counter()
        elif a=='pause' and self.started and not self.finished:
            if self.anchor is None:self.anchor=time.perf_counter()
            else:self.freeze()
        elif a=='next' and self.started and not self.finished:
            t=self.elapsed(); s=self.steps[self.i]; prev=self.rows[-1]['cumulative'] if self.rows else 0; last=next((r['cumulative'] for r in reversed(self.rows) if r['split']),0); self.rows.append(dict(id=s['id'],title=s['title'],group=s['group'],duration=t-prev,cumulative=t,split=s['split'],split_duration=t-last if s['split'] else None)); self.w=0
            if self.i==len(self.steps)-1:self.freeze(); self.finished=True; self.write_record(True)
            else:self.i+=1; self.write_record(False)
        elif a=='back' and self.started and self.rows:
            if self.finished:self.finished=False; self.anchor=time.perf_counter()
            else:self.i=max(0,self.i-1)
            self.rows.pop(); self.w=0; self.write_record(False)
        elif a in ('wave','prevwave'):
            self.w=max(0,min(len(self.steps[self.i]['waves'])-1,self.w+(1 if a=='wave' else -1)))
        elif a=='reset':
            if QMessageBox.question(self,'確認','走行を終了してリセットしますか？')!=QMessageBox.StandardButton.Yes:return
            if self.started:self.write_record(self.finished)
            self.clear_state()
        self.render()
        new=(self.i,self.w,self.started,self.finished,self.anchor is not None)
        if a=='reset':self.audio.stop()
        elif new!=old:
            if self.finished:self.audio.stop(); self.audio.sound('finish'); self.audio.speak('完走しました。',interrupt=False)
            elif a in ('start','next','back'):
                event='split' if a=='next' and self.rows and self.rows[-1]['split'] else a
                self.audio.step(self.steps[self.i],event)
                if event=='split' and self.audio.cfg['split']:self.audio.speak('主要区間、'+self.rows[-1]['split']+'。'+fmt(self.rows[-1]['split_duration']),interrupt=False)
            elif a in ('wave','prevwave'):
                self.audio.stop(); self.audio.sound('wave')
                if self.audio.cfg['wave'] and self.steps[self.i]['waves']:self.audio.speak(self.steps[self.i]['waves'][self.w]['name'],interrupt=False)
            elif a=='pause':self.audio.sound('pause')
    def check(self,item):
        self.checked[self.steps[self.i]['id']]=[j for j in range(self.tasks.count()) if self.tasks.item(j).checkState()==Qt.CheckState.Checked]
        if self.started:self.write_record(self.finished)
    def render(self):
        s=self.steps[self.i]; self.heading.setText(f"{s['group']} | {self.i+1}/{len(self.steps)}"+(' | 完走' if self.finished else '')); self.title.setText(s['title']); self.notes.setPlainText(s['notes']); self.tasks.blockSignals(True); self.tasks.clear()
        for j,t in enumerate(s['tasks']):
            item=QListWidgetItem(t); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if j in self.checked.get(s['id'],[]) else Qt.CheckState.Unchecked); self.tasks.addItem(item)
        self.tasks.blockSignals(False); self.upcoming.setPlainText('\n\n'.join(f"{j+1}: {x['title']}" for j,x in enumerate(self.steps[self.i+1:self.i+5],self.i+1))); waves=s['waves']; self.right.setVisible(bool(waves))
        if waves:
            w=waves[self.w]; self.wave_label.setText(f"{w['name']} ({self.w+1}/{len(waves)})"); self.map.show_wave(w); self.spawn.setPlainText('\n'.join(f"{j+1}. {m['note']}" for j,m in enumerate(w['points'])))
        self.splits.setPlainText('主要区間: '+' / '.join(f"{r['split']} {fmt(r['split_duration'])}" for r in self.rows if r['split'])); self.tick()
    def tick(self):
        t=self.elapsed(); prev=self.rows[-1]['cumulative'] if self.rows else 0; last=next((r['cumulative'] for r in reversed(self.rows) if r['split']),0); self.clock.setText(f'総時間 {fmt(t)}    主要区間 {fmt(t-last)}    手順 {fmt(t-prev)}'); self.status.setText('完走' if self.finished else '計測中' if self.anchor is not None else '一時停止' if self.started else '開始待ち'); self.status.setText(self.status.text()+' | '+(self.key_error or ('キー検知: '+self.last_key if self.last_key else 'キー入力待ち')))
    def write_record(self,complete):
        save(RECORDS/(self.run_id+'.json'),dict(started_at=self.started_at,complete=complete,total=self.elapsed(),steps=self.rows,checked=self.checked,current_id=self.steps[self.i]['id'],achievements=self.achievement_events))
    def achievement_settings(self):
        if self.started:
            QMessageBox.information(self,'Steam実績','走行リセット後に設定してください'); return
        d=QDialog(self); d.setWindowTitle('Steam実績設定'); l=QFormLayout(d)
        fields={}
        for key,label in [('schema','スキーマ'),('stats','ユーザー実績')]:
            row=QHBoxLayout(); field=QLineEdit(self.achievement_config.get(key,'')); fields[key]=field; row.addWidget(field)
            b=QPushButton('参照'); row.addWidget(b)
            def browse(checked=False,f=field):
                path,_=QFileDialog.getOpenFileName(d,'Steam実績ファイル',f.text(),'BIN (*.bin)')
                if path:f.setText(path)
            b.clicked.connect(browse); l.addRow(label,row)
        combo=QComboBox(); combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon); combo.setMinimumContentsLength(20); pairs=discover(); combo.addItem('自動検出候補を選択')
        for schema,stats in pairs:combo.addItem(stats)
        def choose(i):
            if i>0:
                schema,stats=pairs[i-1]; fields['schema'].setText(schema); fields['stats'].setText(stats)
        combo.currentIndexChanged.connect(choose); l.addRow('自動検出',combo)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); l.addRow(buttons)
        if d.exec():
            config={k:f.text().strip() for k,f in fields.items()}
            try:
                if any(config.values()):read_achievements(config['schema'],config['stats'])
            except Exception as e:QMessageBox.warning(self,'Steam実績',str(e)); return
            self.achievement_config=config; save(ROOT/'steam_config.json',config); self.achievement_state=None; self.poll_achievements()
    def poll_achievements(self):
        c=self.achievement_config
        if not c.get('schema') or not c.get('stats'):
            self.achievement_label.setText('Steam実績: 未設定'); return
        try: current=read_achievements(c['schema'],c['stats'])
        except Exception as e:
            self.achievement_error=str(e); self.achievement_label.setText('Steam実績: 読み取り失敗（再試行中） '+str(e)); return
        self.achievement_error=''; self.achievement_state=current
        if self.started and not self.finished:
            if self.achievement_baseline is not None:
                for key,item in current.items():
                    old=self.achievement_baseline.get(key)
                    if old and item['unlocked'] and (not old['unlocked'] or (item['timestamp'] and item['timestamp']!=old['timestamp'])):
                        self.achievement_events.append(dict(**item,elapsed=self.elapsed(),detected_at=datetime.now().isoformat(),step_id=self.steps[self.i]['id']))
                        self.audio.sound('achievement',ordered=True)
                        if self.audio.cfg['achievement']:self.audio.speak('実績、'+item['name']+'を取得。',interrupt=False)
                changed=any(current.get(k)!=v for k,v in self.achievement_baseline.items())
                self.achievement_baseline=current.copy()
                if changed:self.write_record(False)
            else:self.achievement_baseline=current.copy()
        count=sum(a['unlocked'] for a in current.values()); latest=self.achievement_events[-1] if self.achievement_events else None
        self.achievement_label.setText(f"Steam実績: {count}/{len(current)} | 今回検知: {len(self.achievement_events)}"+(f" | 最新: {latest['name']} ({fmt(latest['elapsed'])})" if latest else ''))
    def achievement_list(self):
        d=QDialog(self); d.setWindowTitle('Steam実績一覧'); d.resize(1000,650); l=QVBoxLayout(d); only=QCheckBox('未取得のみ'); l.addWidget(only); table=QTableWidget(); l.addWidget(table)
        def refresh():
            items=[a for a in (self.achievement_state or {}).values() if not only.isChecked() or not a['unlocked']]; table.setColumnCount(5); table.setHorizontalHeaderLabels(['状態','実績名','説明','Steam取得日時','今回検知タイム']); table.setRowCount(len(items)); events={a['id']:a for a in self.achievement_events}
            for i,a in enumerate(items):
                ts=a['timestamp']; event=events.get(a['id']); vals=['取得済み' if a['unlocked'] else '未取得',a['name'],a['description'],datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else '—',fmt(event['elapsed']) if event else '—']
                for j,v in enumerate(vals):table.setItem(i,j,QTableWidgetItem(v))
            table.resizeColumnsToContents()
        only.toggled.connect(refresh); timer=QTimer(d); timer.timeout.connect(refresh); timer.start(1000); refresh(); d.exec(); timer.stop()
    def edit(self):
        if self.started:QMessageBox.information(self,'編集','走行リセット後に編集してください');return
        d=Editor(self.steps,self)
        if d.exec():self.steps=d.steps; save(CHART,self.steps); self.clear_state(); self.render()
    def settings(self):
        d=QDialog(self); d.setWindowTitle('キー設定'); l=QFormLayout(d); fields={}
        for a,k in self.keys.items():fields[a]=QLineEdit(k); l.addRow(a,fields[a])
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); b.accepted.connect(d.accept); b.rejected.connect(d.reject); l.addRow(b)
        if d.exec():
            keys={a:f.text().strip() for a,f in fields.items()}
            try:
                if len(set(keys.values()))!=len(keys):raise ValueError('キーが重複しています')
                for k in keys.values():
                    if sys.platform=='win32':self.parse_windows_key(k)
                    else:keyboard.parse_hotkey(k)
                if sys.platform=='win32' and len(set(map(self.parse_windows_key,keys.values())))!=len(keys):raise ValueError('キーが重複しています')
            except Exception as e:QMessageBox.warning(self,'キー設定',str(e));return
            self.keys=keys; save(CONFIG,keys); self.register()
    def history(self):
        d=QDialog(self); d.setWindowTitle('走行記録'); d.resize(900,650); l=QVBoxLayout(d); combo=QComboBox(); l.addWidget(combo); table=QTableWidget(); l.addWidget(table); runs=[load(p,{}) for p in sorted(RECORDS.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)]; pb=min((r for r in runs if r.get('complete')),key=lambda r:r['total'],default=None); baseline={r['id']:r for r in pb['steps']} if pb else {}
        def show(i):
            if i<0:return
            rows=runs[i]['steps']; table.setColumnCount(6); table.setHorizontalHeaderLabels(['手順','所要時間','累積','PB累積差（秒）','主要区間','区間時間']); table.setRowCount(len(rows))
            for j,r in enumerate(rows):
                ref=baseline.get(r['id']); vals=[r['title'],fmt(r['duration']),fmt(r['cumulative']),f"{r['cumulative']-ref['cumulative']:+.1f}" if ref else '—',r['split'],fmt(r['split_duration']) if r['split'] else '']
                for k,v in enumerate(vals):table.setItem(j,k,QTableWidgetItem(v))
            table.resizeColumnsToContents()
        combo.currentIndexChanged.connect(show); combo.addItems([f"{r['started_at']} | {'完走' if r['complete'] else '途中'} | {fmt(r['total'])}" for r in runs]); d.exec()
    def closeEvent(self,e):
        self.audio.close()
        self.key_timer.stop()
        if self.started:self.write_record(self.finished)
        for h in self.hooks:keyboard.remove_hotkey(h)
        super().closeEvent(e)
if __name__=='__main__':
    app=QApplication(sys.argv); window_filter=WindowFitFilter(app); app.installEventFilter(window_filter); app.setStyle('Fusion'); app.setStyleSheet('QWidget {background:#20242b;color:#eeeeee;font-size:14px;} QPushButton,QLineEdit,QPlainTextEdit,QListWidget,QComboBox,QTableWidget {background:#303640;padding:5px;} QPushButton:hover {background:#465362;}'); win=Main(); screen=QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen(); win.move(screen.availableGeometry().topLeft()); win.show(); fit_window(win,(1100,760)); QTimer.singleShot(0,lambda:fit_window(win)); sys.exit(app.exec())

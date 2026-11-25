import base64, hashlib, json, math, struct, time, wave, threading, queue, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QSoundEffect
from PySide6.QtWidgets import *

EVENTS={'start':'開始','next':'次の手順','back':'前の手順','wave':'WAVE変更','split':'主要区間','achievement':'実績取得','warning':'重要警告','finish':'完走','pause':'一時停止・再開'}
DEFAULT=dict(enabled=False,se=True,user='',password='',url='http://127.0.0.1:32766',voice='ui_ja_JP',version='2.0.0',speed=1.3,volume=80,se_volume=50,fallback=True,achievement=True,wave=True,split=False, sounds={})

class Audio(QObject):
    ready=Signal(int,str)
    status=Signal(str)
    se_ready=Signal(int,str)
    def __init__(self,root,parent):
        super().__init__(parent); self.root=root; self.path=root/'audio_config.json'; self.cache=root/'audio_cache'; self.cache.mkdir(exist_ok=True)
        self.cfg=dict(DEFAULT)
        try:self.cfg.update(json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError,ValueError):pass
        self.gen=0; self.jobs=queue.Queue(); self.closed=False
        self.output=QAudioOutput(self); self.player=QMediaPlayer(self); self.player.setAudioOutput(self.output)
        self.pending=[]; self.current=None; self.ready.connect(self.play); self.se_ready.connect(self.play_se); self.player.mediaStatusChanged.connect(self.media_status); self.player.errorOccurred.connect(self.play_error)
        threading.Thread(target=self.worker,daemon=True).start()
    def stop(self, preserve_se=False):
        self.gen+=1
        keep=[x for x in self.pending if x[0]=='se'][-1:] if preserve_se else []
        if preserve_se and self.current and self.current[0]=='se':
            self.pending=keep
            return
        self.current=None; self.pending=keep; self.player.stop(); self.advance()
    def advance(self):
        if self.closed or self.current or not self.pending:return
        self.current=self.pending.pop(0)
        kind,path=self.current
        self.output.setVolume(self.cfg['se_volume' if kind=='se' else 'volume']/100)
        self.player.setSource(QUrl.fromLocalFile(path)); self.player.play()
    def media_status(self,status):
        if status==QMediaPlayer.MediaStatus.EndOfMedia and self.current:
            self.current=None; self.advance()
    def play_error(self,*args):
        self.status.emit('音声再生エラー: '+self.player.errorString())
        if self.current:
            self.current=None; self.advance()
    def close(self):
        self.closed=True; self.stop(); self.jobs.put(None)
    def sound(self,event,ordered=False):
        if not self.cfg['se']:return
        try:
            custom=self.cfg['sounds'].get(event,''); p=Path(custom) if custom else self.cache/('se_'+event+'.wav')
            if not custom and not p.exists():
                tones={'start':[660,880],'next':[880],'back':[440],'wave':[660,660],'split':[660,880,1100],'achievement':[784,988,1175],'warning':[330,330,330],'finish':[523,659,784,1047],'pause':[550]}.get(event,[660]); rate=22050
                with wave.open(str(p),'wb') as f:
                    f.setparams((1,2,rate,0,'NONE','not compressed')); f.writeframes(b''.join(struct.pack('<h',int(11000*math.sin(2*math.pi*hz*i/rate)*min(1,i/150,(2200-i)/300))) for hz in tones for i in range(2200)))
            
            if ordered:self.jobs.put((self.gen,str(p.resolve()),None,True))
            else:self.pending.append(('se',str(p.resolve()))); self.advance()
        except Exception as e:self.status.emit('SE: '+str(e))
    def speak(self,text,interrupt=True,force=False):
        if interrupt:self.stop(preserve_se=True)
        if not (self.cfg['enabled'] or force) or not text.strip():return
        self.jobs.put((self.gen,text.strip(),dict(self.cfg),True))
    def step_text(self,s):
        if s.get('tts_mute'):return ''
        text=s.get('tts','').strip() or (s['title'] if self.cfg['fallback'] else '')
        warning=s.get('tts_warning','').strip()
        return '。'.join(x for x in [ ('注意。'+warning) if warning else '',text] if x)
    def step(self,s,event):
        self.stop(); self.sound('warning' if s.get('tts_warning') and not s.get('tts_mute') else event); self.speak(self.step_text(s),interrupt=False)
    def request(self,c,path,method='GET',body=None):
        url=c['url'].rstrip('/')
        if urllib.parse.urlparse(url).hostname not in ('localhost','127.0.0.1','::1'):raise ValueError('APIはローカル接続のみ対応')
        auth=base64.b64encode((c['user']+':'+c['password']).encode()).decode(); req=urllib.request.Request(url+'/api/talk/v1/'+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Authorization':'Basic '+auth,'Content-Type':'application/json'},method=method)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=8) as r:return r.read()
    def synth(self,c,text,g):
        body=dict(text=text,destination='memory',force_enqueue=True,language='ja_JP',voice_name=c['voice'],voice_version=c['version'],global_parameters={'speed':c['speed']})
        p=self.cache/(hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()+'.wav')
        if p.exists():return p
        job=json.loads(self.request(c,'speech-syntheses','POST',body))['uuid']; path='speech-syntheses/'+urllib.parse.quote(job,safe='')
        try:
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if self.closed or g!=self.gen:return None
                state=json.loads(self.request(c,path))['state']
                if state=='succeeded':
                    data=self.request(c,path+'/wav')
                    if data[:4]!=b'RIFF' or data[8:12]!=b'WAVE':raise ValueError('WAV形式ではありません')
                    p.write_bytes(data); return p
                if state not in ('queued','running'):raise RuntimeError('合成状態: '+state)
                time.sleep(.25)
            raise TimeoutError('音声合成タイムアウト')
        finally:
            try:self.request(c,path,'DELETE')
            except Exception:pass
    def worker(self):
        while not self.closed:
            item=self.jobs.get()
            if item is None:return
            g,text,c,play=item
            if g!=self.gen:continue
            if c is None:
                self.ready.emit(g,"SE:"+text); continue
            try:
                # API limit: 500 characters. Split long instructions into separate WAVs.
                for i in range(0,len(text),450):
                    if g!=self.gen or self.closed:break
                    p=self.synth(c,text[i:i+450],g)
                    if p and play and g==self.gen:
                        self.ready.emit(g,str(p))
                if g==self.gen:self.status.emit('音声: 準備完了')
            except Exception as e:
                if g==self.gen:self.status.emit('TTSエラー: '+str(e))
    def play_se(self,g,p):
        if g==self.gen and not self.closed:self.pending.append(('se',p)); self.advance()
    def play(self,g,p):
        if g!=self.gen or self.closed:return
        self.pending.append(('se',p[3:]) if p.startswith('SE:') else ('tts',p)); self.advance()
    def settings(self,parent,steps):
        d=QDialog(parent); d.setWindowTitle('音声・SE設定'); outer=QVBoxLayout(d); scroll=QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll); w=QWidget(); scroll.setWidget(w); f=QFormLayout(w); fields={}
        for k,label in [('enabled','TTS有効'),('se','SE有効'),('fallback','専用文が空なら行動内容を読む'),('achievement','実績取得を読む'),('wave','WAVE名を読む'),('split','主要区間タイムを読む')]:
            b=QCheckBox(); b.setChecked(self.cfg[k]); fields[k]=b; f.addRow(label,b)
        for k,label in [('url','API URL'),('user','APIユーザー名'),('password','APIパスワード'),('voice','VoiceName'),('version','VoiceVersion')]:
            b=QLineEdit(self.cfg[k]); fields[k]=b; f.addRow(label,b)
            if k=='password':b.setEchoMode(QLineEdit.EchoMode.Password)
        for k,label in [('volume','TTS音量 %'),('se_volume','SE音量 %'),('speed','話速')]:
            b=QDoubleSpinBox() if k=='speed' else QSpinBox(); b.setRange(.5,2.0) if k=='speed' else b.setRange(0,100); b.setValue(self.cfg[k]); fields[k]=b; f.addRow(label,b)
        sounds={}
        for k,label in EVENTS.items():
            row=QHBoxLayout(); b=QLineEdit(self.cfg['sounds'].get(k,'')); sounds[k]=b; row.addWidget(b); pick=QPushButton('WAV選択'); row.addWidget(pick)
            def choose(checked=False,b=b):
                p,_=QFileDialog.getOpenFileName(d,'SE選択','','WAV (*.wav)')
                if p:b.setText(p)
            pick.clicked.connect(choose); f.addRow(label+' SE',row)
        def apply():
            self.stop(); self.cfg.update({k:(b.isChecked() if isinstance(b,QCheckBox) else b.text() if isinstance(b,QLineEdit) else b.value()) for k,b in fields.items()}); self.cfg['sounds']={k:b.text() for k,b in sounds.items()}; self.path.write_text(json.dumps(self.cfg,ensure_ascii=False,indent=2),encoding='utf-8')
        test=QPushButton('設定保存・雨衣テスト'); test.clicked.connect(lambda:(apply(),self.speak('雨衣です。読み上げのテストです。',force=True))); outer.addWidget(test)
        cache=QPushButton('設定保存・全手順の音声を事前生成'); outer.addWidget(cache)
        def prepare():
            apply(); g=self.gen
            for s in steps:
                text=self.step_text(s)
                if text:self.jobs.put((g,text,dict(self.cfg),False))
            self.status.emit('音声キャッシュ生成を開始')
        cache.clicked.connect(prepare); cancel=QPushButton('音声処理停止'); cancel.clicked.connect(self.stop); outer.addWidget(cancel)
        status=QLabel(); status.setWordWrap(True); self.status.connect(status.setText); outer.addWidget(status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(lambda:(apply(),d.accept())); buttons.rejected.connect(d.reject); outer.addWidget(buttons); d.resize(650,700); d.exec(); self.status.disconnect(status.setText)

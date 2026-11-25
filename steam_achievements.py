import struct
from pathlib import Path

APP_ID = '579180'

def parse_kv(data):
    pos = 0
    def string():
        nonlocal pos
        end = data.index(0, pos)
        value = data[pos:end].decode('utf-8', errors='replace')
        pos = end + 1
        return value
    def obj(depth=0):
        nonlocal pos
        if depth > 64: raise ValueError('KeyValues nesting')
        result = {}
        while pos < len(data):
            kind = data[pos]; pos += 1
            if kind == 8: return result
            key = string()
            if kind == 0: value = obj(depth+1)
            elif kind == 1: value = string()
            elif kind in (2,3,4,6,7):
                fmt = {2:'<I',3:'<f',4:'<I',6:'<I',7:'<Q'}[kind]
                value = struct.unpack_from(fmt,data,pos)[0]; pos += struct.calcsize(fmt)
            else: raise ValueError(f'Unknown KeyValues type {kind} at {pos-1}')
            result[key] = value
        raise ValueError('Incomplete KeyValues')
    result = obj()
    if pos != len(data): raise ValueError('Trailing KeyValues data')
    return result

def read_achievements(schema_path, stats_path):
    schema = parse_kv(Path(schema_path).read_bytes())[APP_ID]['stats']
    cache = parse_kv(Path(stats_path).read_bytes())['cache']
    result = {}
    for group, info in schema.items():
        if not isinstance(info,dict): continue
        for bit, item in info.get('bits',{}).items():
            if not isinstance(item,dict) or 'name' not in item: continue
            state = cache.get(group,{})
            unlocked = bool(state.get('data',0) & (1 << int(bit)))
            display = item.get('display',{})
            name = display.get('name',{}); desc = display.get('desc',{})
            result[item['name']] = dict(id=item['name'],name=name.get('japanese',name.get('english',item['name'])),description=desc.get('japanese',desc.get('english','')),unlocked=unlocked,timestamp=state.get('AchievementTimes',{}).get(bit,0) if unlocked else 0)
    if not result: raise ValueError('実績定義がありません')
    return result

def discover():
    roots=[]
    try:
        import winreg
        for hive,key in [(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam'),(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam')]:
            try:
                with winreg.OpenKey(hive,key) as k:
                    for name in ('SteamPath','InstallPath'):
                        try: roots.append(Path(winreg.QueryValueEx(k,name)[0]))
                        except OSError: pass
            except OSError: pass
    except ImportError: pass
    roots += [Path('C:/Program Files (x86)/Steam'),Path('C:/Program Files/Steam')]
    pairs=[]
    for root in roots:
        folder=root/'appcache'/'stats'; schema=folder/f'UserGameStatsSchema_{APP_ID}.bin'
        if schema.is_file():
            for stats in folder.glob(f'UserGameStats_*_{APP_ID}.bin'):
                pair=(str(schema),str(stats))
                if pair not in pairs: pairs.append(pair)
    return pairs

"""Fetch the pinned public model bundle; no audio ever leaves this PC."""
import concurrent.futures,hashlib,json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
metadata=json.loads((ROOT/'model_metadata.json').read_text(encoding='utf-8-sig'))
REVISION='e0f4c8b53f4c52fc30c5b66fd7384c0ce85884ff'
assert metadata['sha']==REVISION
DEST=ROOT/'models/v2_expert';DEST.mkdir(parents=True,exist_ok=True)
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def fetch(row):
    name=row['rfilename'];p=(DEST/name).resolve()
    assert p.is_relative_to(DEST.resolve())
    p.parent.mkdir(parents=True,exist_ok=True)
    expected=row.get('lfs',{}).get('sha256')
    def valid():return p.is_file() and p.stat().st_size==row['size'] and (not expected or sha(p)==expected)
    if not valid():
        url=f'https://huggingface.co/BierHerr/InfernoSaber/resolve/{REVISION}/{name}?download=true'
        tmp=p.with_suffix(p.suffix+'.part')
        for attempt in range(4):
            try:
                request=urllib.request.Request(url,headers={'User-Agent':'PicoSongPatcher/1.2'})
                with urllib.request.urlopen(request,timeout=180) as response,tmp.open('wb') as f:
                    for block in iter(lambda:response.read(1024*1024),b''):f.write(block)
                assert tmp.stat().st_size==row['size'],name+' size mismatch'
                if expected:assert sha(tmp)==expected,name+' hash mismatch'
                tmp.replace(p);break
            except Exception as error:
                if attempt==3:raise RuntimeError(name+': '+type(error).__name__) from None
                time.sleep(attempt+1)
    assert valid(),name
    print(name+' verified ('+str(row['size'])+' bytes)',flush=True)
    return {'file':name,'bytes':row['size'],'sha256':sha(p)}
rows=[r for r in metadata['siblings'] if r['rfilename']!='.gitattributes']
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(fetch,rows))
(ROOT/'models_lock.json').write_text(json.dumps({'repository':'BierHerr/InfernoSaber','revision':REVISION,'files':results},indent=2),encoding='utf-8')
print('MODEL BUNDLE VERIFIED',flush=True)

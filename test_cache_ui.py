import sys,tempfile,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT.parent/'beat_saber_lab/python_packages'),str(ROOT)]
import tkinter as tk
import app
from cache_view import inspect_cache

source=ROOT/'dist_v3/Pico Song Patcher 1.2/data/library.json'
original=source.read_bytes();state=json.loads(original)
work=ROOT/'test_work';work.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=work) as td:
    app.HOME=Path(td);(app.HOME/'data').mkdir()
    (app.HOME/'data/library.json').write_bytes(original)
    root=tk.Tk();root.withdraw();window=app.Window(root);root.update_idletasks()
    enabled_before=[s.get('enabled',True) for s in window.library.state['songs']]
    inv=inspect_cache(state['base_obb']);window.accept_inventory(inv)
    assert [s.get('enabled',True) for s in window.library.state['songs']]==enabled_before
    assert window.base.get()==app.DEFAULT_BASE
    rows={s['level_id']:window.tree.item(str(i))['values'] for i,s in enumerate(window.library.state['songs'])}
    old=next(s for s in state['songs'] if s['title']=='Mr. Saxobeat' and not s.get('auto_backend'))
    new=next(s for s in state['songs'] if s['title']=='Mr. Saxobeat' and s.get('auto_backend'))
    assert rows[old['level_id']][1]=='Да'
    assert rows[new['level_id']][1]=='Нет'
    assert 'AI: 0' in window.cache_summary.get()
    window.use_cache_selection(inv)
    assert sum(e.get('enabled',True) for e in window.library.state['songs'])==33
    assert not any(e.get('enabled',True) and e.get('auto_backend') for e in window.library.state['songs'])
    print(json.dumps({'status':'PASS','library_songs':len(rows),'cached_added':inv['custom'],
        'selected_before':sum(enabled_before),'selection_not_changed_by_read':True,
        'source_obb_is_original':True,'requested_height':root.winfo_reqheight(),'requested_width':root.winfo_reqwidth()},indent=2))
    root.destroy()
assert source.read_bytes()==original

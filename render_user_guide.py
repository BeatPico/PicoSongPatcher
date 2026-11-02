"""Render the repository guide to one self-contained offline HTML document."""
import base64
from pathlib import Path
import sys

def render(root, destination=None):
    import markdown
    root=Path(root)
    source=root/'docs/USER_GUIDE_RU.md'
    body=markdown.markdown(source.read_text(encoding='utf-8'),extensions=['tables','fenced_code','sane_lists'])
    image=base64.b64encode((root/'docs/window-map.svg').read_bytes()).decode('ascii')
    body=body.replace('src="window-map.svg"','src="data:image/svg+xml;base64,'+image+'"')
    html='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Pico Song Patcher — инструкция к EXE</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#eef2f8;color:#24324b;font:16px/1.65 "Segoe UI",Arial,sans-serif}main{max-width:1120px;margin:36px auto;padding:42px 56px;background:white;border-radius:20px;box-shadow:0 12px 40px #24324b0d}h1{font-size:34px;line-height:1.25;color:#18223d;margin-top:0}h2{margin-top:45px;padding-top:16px;border-top:1px solid #dfe5ef;color:#463d97;font-size:25px}h3{font-size:20px;color:#463d97}a{color:#5145bc;text-underline-offset:3px}a:hover{color:#176b5b}strong{color:#172744}li{margin:.55em 0}code{font-family:Consolas,monospace;font-size:.9em;background:#f1f4fa;border-radius:4px;padding:2px 5px;overflow-wrap:anywhere}pre{overflow-x:auto;padding:20px 24px;background:#111c35;color:#e4ecff;border-radius:12px;line-height:1.55}pre code{background:none;padding:0;color:inherit;overflow-wrap:normal}table{width:100%;border-collapse:collapse;margin:24px 0;font-size:15px}td,th{border:1px solid #dce3ee;text-align:left;vertical-align:top;padding:12px 15px}th{background:#edf0fb;color:#3b3486}tbody tr:nth-child(even){background:#f8fafc}td:first-child{width:29%}img{max-width:100%;height:auto;border-radius:12px}footer{margin-top:40px;color:#64748b;font-size:14px}@media(max-width:740px){main{margin:0;border-radius:0;padding:26px 20px}h1{font-size:27px}h2{font-size:22px}table{font-size:13px}td,th{padding:8px}pre{font-size:12px;padding:14px}}@media print{body{background:white}main{box-shadow:none;margin:0;padding:0;max-width:none}h2{break-after:avoid}tr{break-inside:avoid}a{color:inherit}pre{white-space:pre-wrap;background:#f5f5f5;color:black}}
</style></head><body><main>'''+body+'''<footer>Pico Song Patcher 1.4.1 · Справка хранится на вашем компьютере и открывается без интернета.</footer></main></body></html>'''
    target=Path(destination) if destination else root/'docs/USER_GUIDE_RU.html'
    target.write_text(html,encoding='utf-8')
    print('Offline guide: '+str(target),flush=True)
    return target

if __name__=='__main__':
    render(Path(__file__).resolve().parent,sys.argv[1] if len(sys.argv)>1 else None)

"""Offline xterm document; terminal bytes are data, never markup."""
import hashlib
import json
import secrets
from dataclasses import asdict
from pathlib import Path

_ASSETS = {
    'xterm.js': '14903579ff54664cd72f8e8699e6961a6272c21863ec1c3b118cdc8af5d4a972',
    'xterm.css': '854a7c0fb70e8b1a083c16797ab827299fb18744f5ad34f227b48337e33293c6',
    'addon-fit.js': 'ba3ea256ce0620a0992a197d6c9baea64823fc93d8da07a9e366ca9943c18527',
}


def _json(value):
    return json.dumps(value, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e')


def data_script(kind, value):
    return 'window.gentleReceive(' + _json([kind, value]) + ');'


def build_html(theme, assets_dir):
    assets = {}
    for name, digest in _ASSETS.items():
        content = (Path(assets_dir) / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('offline terminal asset integrity mismatch: ' + name)
        assets[name] = content.decode('utf-8').replace('</script', '<\\/script')
    nonce = secrets.token_hex(24)
    return (_DOCUMENT.replace('@NONCE@', nonce)
            .replace('@CSS@', assets['xterm.css'])
            .replace('@XTERM@', assets['xterm.js'])
            .replace('@FIT@', assets['addon-fit.js'])
            .replace('@THEME@', _json(asdict(theme))))


_DOCUMENT = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-@NONCE@'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>@CSS@
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden}
body{background:var(--background);color:var(--foreground);border:1px solid var(--accent);
font:13px Consolas,monospace;display:flex;flex-direction:column}
header{height:28px;flex:none;display:flex;align-items:center;user-select:none;cursor:move}
header span{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--accent)}
button{width:32px;height:26px;border:0;background:transparent;color:var(--accent);cursor:pointer}
button:hover,button:active{background:var(--foreground);color:var(--background)}
button:focus-visible{outline:1px solid var(--accent);outline-offset:-3px}
svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.5}
#terminal{flex:1;min-height:0;width:100%;padding:0 3px 3px}
.xterm-viewport{scrollbar-color:var(--accent) var(--background)}
::selection{color:var(--background);background:var(--accent)}
.grip{position:fixed;z-index:30}.mini .grip{display:none}
.grip[data-edge=n],.grip[data-edge=s]{left:6px;right:6px;height:4px;cursor:ns-resize}
.grip[data-edge=n]{top:0}.grip[data-edge=s]{bottom:0}
.grip[data-edge=w],.grip[data-edge=e]{top:6px;bottom:6px;width:4px;cursor:ew-resize}
.grip[data-edge=w]{left:0}.grip[data-edge=e]{right:0}
.grip[data-edge=nw],.grip[data-edge=ne],.grip[data-edge=sw],.grip[data-edge=se]{width:6px;height:6px}
.grip[data-edge=nw]{top:0;left:0;cursor:nwse-resize}.grip[data-edge=se]{bottom:0;right:0;cursor:nwse-resize}
.grip[data-edge=ne]{top:0;right:0;cursor:nesw-resize}.grip[data-edge=sw]{bottom:0;left:0;cursor:nesw-resize}
</style></head><body>
<header><button id="close" aria-label="Close gentle-stream" title="Close gentle-stream">
<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
<span>gentle-stream — real-time generative radio</span>
<button id="toggle" aria-label="Toggle mini window" title="Toggle mini window">
<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 7V3h4M3 3l5 5m5 1v4H9m4 0L8 8"/></svg></button></header>
<div id="terminal" role="region" aria-label="gentle-stream terminal"></div>
<script nonce="@NONCE@">
(function() {
  const report = (stage,line=0) => window.chrome.webview.postMessage({type:'startup',stage,line});
  window.addEventListener('error',event=>report('script_error',event.lineno||0));
  window.addEventListener('unhandledrejection',()=>report('promise_error'));
  document.addEventListener('securitypolicyviolation',()=>report('csp_blocked'));
  report('bootstrap');
})();
</script>
<script nonce="@NONCE@">@XTERM@</script><script nonce="@NONCE@">@FIT@</script>
<script nonce="@NONCE@">
'use strict';
const post = message => window.chrome.webview.postMessage(message);
let epoch = -1, regions = [], grid = [0,0];
const theme = @THEME@;
function applyTheme(t) {
  for (const key of ['background','foreground','accent'])
    document.documentElement.style.setProperty('--'+key,t[key]);
  terminal.options.theme = {background:t.background,foreground:t.foreground,cursor:t.accent,
    selectionBackground:t.accent};
}
const terminal = new Terminal({fontFamily:'Consolas, monospace',fontSize:13,
  scrollback:0,allowProposedApi:false,convertEol:false,disableStdin:false});
const fit = new FitAddon.FitAddon();
terminal.loadAddon(fit);
terminal.open(document.getElementById('terminal'));
applyTheme(theme);
terminal.onData(text => post({type:'input',text}));
terminal.attachCustomKeyEventHandler(e => {
  if (e.key === 'Tab') {
    if (e.type === 'keydown') post({type:'input',text:e.shiftKey?'\\x1b[Z':'\\t'});
    e.preventDefault(); return false;
  }
  return true;
});
function resized() {
  regions=[]; epoch=-1;
  fit.fit(); grid=[terminal.cols,terminal.rows];
  post({type:'resize',cols:terminal.cols,rows:terminal.rows});
}
new ResizeObserver(resized).observe(document.getElementById('terminal'));
window.gentleReceive = function(packet) {
  const [kind,data]=packet;
  if(kind==='output') terminal.write(data[1],()=>post({type:'ack',seq:data[0]}));
  else if(kind==='theme') applyTheme(data);
  else if(kind==='mode') {document.body.classList.toggle('mini',data==='mini');resized();}
  else if(kind==='regions') {
    if(data[1]===grid[0] && data[2]===grid[1]) {epoch=data[0];regions=data[3];}
  }
};
document.getElementById('close').onclick=()=>post({type:'action',action:'quit'});
document.getElementById('toggle').onclick=()=>post({type:'action',action:'toggle'});
document.querySelector('header').addEventListener('mousedown',e=>{
  if(e.button===0 && !e.target.closest('button')) {
    e.preventDefault(); post({type:'drag',header:true});
  }
});
document.getElementById('terminal').addEventListener('mousedown',e=>{
  if(e.button!==0 || epoch<0) return;
  const box=document.querySelector('.xterm-screen').getBoundingClientRect();
  const col=Math.floor((e.clientX-box.left)/box.width*grid[0]);
  const row=Math.floor((e.clientY-box.top)/box.height*grid[1]);
  if(regions.some(([x,y,w,h])=>col>=x && col<x+w && row>=y && row<y+h)) {
    e.preventDefault();e.stopImmediatePropagation();post({type:'drag',epoch,col,row});
  }
},true);
for(const edge of ['n','s','w','e','nw','ne','sw','se']) {
  const grip=document.createElement('div');grip.className='grip';grip.dataset.edge=edge;
  grip.addEventListener('mousedown',e=>{if(e.button===0){e.preventDefault();post({type:'grip',edge});}});
  document.body.append(grip);
}
document.addEventListener('contextmenu',e=>e.preventDefault());
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('drop',e=>e.preventDefault());
// Readiness is document/terminal initialization, not a compositor frame grant.
// A hidden/occluded WebView can suspend requestAnimationFrame indefinitely.
setTimeout(()=>{resized();terminal.focus();post({type:'ready'});},0);
</script></body></html>'''

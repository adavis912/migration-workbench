import sys
p=sys.argv[1]; t=open(p).read()
if 'aria-labelledby="ar-h"' in t:
    s=t.index('  <section class="panel" aria-labelledby="ar-h">'); e=t.index('  <section class="panel" aria-labelledby="pl-h">'); t=t[:s]+t[e:]
    s=t.index('// architecture tabs'); e=t.index('// workbench (shared by all apps)'); t=t[:s]+t[e:]
    import re; t=re.sub(r'\.arch-tab \{[\s\S]*?\.arch-img\[hidden\] \{ display: none; \}\n','',t)
    open(p,'w').write(t); print('main page: architecture removed')
else: print('main page: already removed')

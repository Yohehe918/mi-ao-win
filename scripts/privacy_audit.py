"""Fail a release on user paths, device identifiers, credentials or private files.

For PyInstaller, inspect decompressed resources and code objects, not only strings
visible in the compressed EXE. Results contain relative paths and rule names only.
"""
import argparse
import json
import marshal
from pathlib import Path
import re
import types
import io
import zipfile

SENSITIVE_RULES = {
    'windows-user-path': re.compile(rb'(?i)[a-z]:[\\/]Users[\\/](?!Public[\\/]|Default[\\/]|<|\{)[^\\/\s"\x00]+'),
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'api-secret': re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'non-example-email': re.compile(rb'(?i)[A-Z0-9._%+-]+@(?!example\.(?:com|org)|users\.noreply\.github\.com)[A-Z0-9.-]+\.[A-Z]{2,}'),
}
PRIVATE_NAMES = {'config.json','button-presets.json','extra-key-session.json','.env','hosts.yml'}
IGNORED_DIRS = {'.git','build','dist','__pycache__','.pytest_cache','.venv','licenses'}


def scan_bytes(data, label, hits, *, third_party=False, extra=()):
    for name, rule in SENSITIVE_RULES.items():
        # Third-party notices legitimately include their authors' public contact data.
        if third_party and name in ('windows-user-path','non-example-email'):
            continue
        if rule.search(data):
            hits.append({'file':label,'rule':name})
        try:
            text = data.decode('utf-16-le').encode('utf-8')
        except (UnicodeDecodeError,UnicodeEncodeError):
            text = b''
        if rule.search(text):
            hits.append({'file':label,'rule':name+'-utf16'})
    for value in extra:
        if value.encode() in data or value.encode('utf-16-le') in data:
            hits.append({'file':label,'rule':'private-build-identifier'})


def scan_code(code, label, hits, extra, third_party=False):
    scan_bytes(code.co_filename.encode(), label+':filename', hits, extra=extra)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            scan_code(value,label,hits,extra,third_party)
        elif isinstance(value,(str,bytes)):
            scan_bytes(value.encode() if isinstance(value,str) else value,label,hits,extra=extra,third_party=third_party)


def audit(source, artifacts=None, extra=()):
    hits = []
    checked = 0
    source = Path(source)
    for file in source.rglob('*'):
        if not file.is_file() or any(part in IGNORED_DIRS for part in file.relative_to(source).parts):
            continue
        relative = file.relative_to(source)
        if file.name in PRIVATE_NAMES or file.suffix.lower() in ('.log','.jsonl','.wav','.mp3','.pem','.key'):
            hits.append({'file':str(relative),'rule':'private-file'})
        if file.suffix.lower() in ('.py','.md','.txt','.toml','.json','.js','.spec','.iss','.isl','.yml'):
            third_party = file.name.startswith(('LICENSE','NOTICE','Frida-')) or 'assets' in relative.parts or file.suffix == '.isl'
            scan_bytes(file.read_bytes(),str(relative),hits,third_party=third_party,extra=extra)
            checked += 1
    if artifacts:
        from PyInstaller.archive.readers import CArchiveReader
        for executable in Path(artifacts).glob('*.exe'):
            scan_bytes(executable.read_bytes(),executable.name,hits,third_party=True,extra=extra)
            checked += 1
            if executable.name != '米遥.exe':
                continue
            archive = CArchiveReader(str(executable))
            for name, entry in archive.toc.items():
                if name.endswith('.pyz'):
                    pyz = archive.open_embedded_archive(name)
                    for module in pyz.toc:
                        payload = pyz.extract(module)
                        if isinstance(payload,types.CodeType):
                            scan_code(payload,module,hits,extra,not (module == 'miao' or module.startswith('miao.')))
                            checked += 1
                else:
                    payload = archive.extract(name)
                    if not isinstance(payload,bytes):
                        continue
                    if name == 'launcher_gui':
                        scan_code(marshal.loads(payload),name,hits,extra)
                    elif name == 'base_library.zip':
                        with zipfile.ZipFile(io.BytesIO(payload)) as base:
                            for module in base.namelist():
                                if module.endswith('.pyc'):
                                    scan_code(marshal.loads(base.read(module)[16:]),module,hits,extra,True)
                                    checked += 1
                    elif name.startswith('assets') and any(x in name for x in ('licenses','key-recovery','vendor')):
                        # Scan own private identifiers even within upstream binary packages.
                        scan_bytes(payload,name,hits,third_party=True,extra=extra)
                    elif name.startswith('miao'):
                        scan_bytes(payload,name,hits,extra=extra)
                    else:
                        scan_bytes(payload,name,hits,extra=extra,third_party=True)
                    checked += 1
    return {'checked_items':checked,'findings':hits,'passed':not hits}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--artifacts')
    parser.add_argument('--private-pattern',action='append',default=[])
    args = parser.parse_args()
    result = audit(args.source,args.artifacts,args.private_pattern)
    if args.artifacts:
        (Path(args.artifacts)/'privacy-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)

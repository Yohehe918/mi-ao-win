"""Prepare reproducible assets and component notices. Never reads user app data."""
import hashlib
import importlib.metadata as metadata
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from miao.readiness import CABLE_SHA256
from miao.version import VERSION
from miao.driver_probe import ARCHIVE_HASH, VERSION as FRIDA_VERSION


def download(url, path, expected):
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={'User-Agent':'mi-ao-win-build'})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('Vendor download hash mismatch: '+path.name)
        path.write_bytes(data)


def prepare():
    download('https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip',
             ROOT/'assets/vendor/VBCABLE_Driver_Pack45.zip', CABLE_SHA256)
    download(f'https://github.com/frida/frida/releases/download/{FRIDA_VERSION}/frida-gadget-{FRIDA_VERSION}-windows-x86_64.dll.xz',
             ROOT/f'assets/key-recovery/frida-gadget-{FRIDA_VERSION}-windows-x86_64.dll.xz', ARCHIVE_HASH)
    licenses = ROOT/'assets/licenses'
    if licenses.exists():
        import shutil
        if licenses.resolve() != (ROOT/'assets/licenses').absolute():
            raise RuntimeError('Refusing to replace redirected generated license folder')
        shutil.rmtree(licenses)
    licenses.mkdir(parents=True, exist_ok=True)
    from shutil import copyfile
    for name in ('Frida-COPYING', 'Frida-Gum-COPYING'):
        copyfile(ROOT/'assets/key-recovery'/name, licenses/name)
    for name in ('LICENSE','NOTICE','THIRD_PARTY_NOTICES.md'):
        copyfile(ROOT/name, licenses/name)
    copyfile(ROOT/'installer/LICENSE-InnoSetup.txt',licenses/'LICENSE-InnoSetup.txt')
    manifest = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name','unknown')
        # Only runtime and build dependencies actually used by this program.
        if not (name.lower() in {'bleak','pywin32','pystray','pillow','sounddevice','cffi','pycparser','six','pyinstaller','pyinstaller-hooks-contrib','altgraph','packaging','pefile','pywin32-ctypes'} or name.lower().startswith('winrt-')):
            continue
        manifest.append(f'{name}=={distribution.version}')
        for file in distribution.files or []:
            if any(part.lower().startswith(('license','copying','notice','copyright')) for part in file.parts):
                source = Path(distribution.locate_file(file))
                if source.is_file() and source.suffix.lower() not in ('.py','.pyc','.pyd','.dll'):
                    destination = licenses/name/Path(*file.parts[1:]) if len(file.parts)>1 else licenses/name/file.name
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    copyfile(source,destination)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():
        copyfile(python_license,licenses/'Python-LICENSE.txt')
    for path in (Path(sys.base_prefix)/'tcl').glob('*/license*'):
        if path.is_file():
            copyfile(path, licenses/(path.parent.name+'-'+path.name))
    (licenses/'COMPONENTS.txt').write_text('\n'.join(sorted(manifest))+'\n',encoding='utf-8')
    from PIL import Image, ImageDraw
    icon = Image.new('RGBA',(256,256),(20,28,38,255))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((86,30,170,226),radius=24,fill=(52,147,255,255))
    draw.ellipse((103,74,153,124),outline='white',width=8)
    draw.rounded_rectangle((120,144,136,187),radius=7,fill='white')
    icon.save(ROOT/'assets/miao.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    version = tuple(int(part) for part in VERSION.split('.'))+(0,)
    (ROOT/'assets/version-info.txt').write_text(f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers={version}, prodvers={version}, mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)),
      kids=[StringFileInfo([StringTable('080404B0',[
        StringStruct('CompanyName','mi-ao-win contributors'),
        StringStruct('FileDescription','米遥 Windows 遥控器与语音桥接'),
        StringStruct('FileVersion','{VERSION}'), StringStruct('ProductVersion','{VERSION}'),
        StringStruct('ProductName','米遥 Windows'), StringStruct('OriginalFilename','米遥.exe'),
        StringStruct('LegalCopyright','2026 mi-ao-win contributors; MIT upstream credits included')])]),
        VarFileInfo([VarStruct('Translation',[2052,1200])])])''', encoding='utf-8')
    print('Prepared pinned vendor packages, licenses, icon and version '+VERSION)


if __name__ == '__main__':
    prepare()

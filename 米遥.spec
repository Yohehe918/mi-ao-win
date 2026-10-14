# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launcher_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('miao/profiles', 'miao/profiles'), ('miao/resources', 'miao/resources'),
           ('assets/key-recovery', 'assets/key-recovery'),
           ('assets/vendor', 'assets/vendor'), ('assets/licenses', 'assets/licenses')],
    hiddenimports=['sounddevice', 'pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='米遥',
    icon='assets/miao.ico',
    version='assets/version-info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

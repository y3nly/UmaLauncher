# -*- mode: python ; coding: utf-8 -*-


block_cipher = None

hidden_imports = []

datas = [
    ("./_assets/icon/global/default.ico", "_assets/icon/global"),
    ("./_assets/icon/global/connecting.ico", "_assets/icon/global"),
    ("./_assets/icon/global/connected.ico", "_assets/icon/global"),
    ("./_assets/umasim-cli.exe", "_assets"),
    ("./_assets/skill_data.txt", "_assets"),
    ("./_assets/branch.txt", "_assets"),
    ("./_assets/commit_hash.txt", "_assets"),
    ("./_assets/build_date.txt", "_assets"),
    ("../build_staging/adblock/adblock_domains.txt", "_assets"),
    ("../build_staging/adblock/adblock_domains.json", "_assets"),
    ("./_assets/trackblazer_scheduler", "_assets/trackblazer_scheduler"),
    ("./_assets/training_helper", "_assets/training_helper"),
    ("./_assets/gm", "_assets/gm"),
    ("./_assets/uaf", "_assets/uaf"),
    ("./_assets/gl", "_assets/gl"),
    ("./_assets/gff", "_assets/gff"),
    ("./_assets/rmu", "_assets/rmu"),
    ("./_assets/dreams", "_assets/dreams"),
    ("./_assets/mant", "_assets/mant"),
    ("./umalauncher_private/assets", "umalauncher_private/assets"),
    ("./umalauncher_private/umasim/data", "umalauncher_private/umasim/data"),
    ("./ff_profile", "ff_profile"),
]

a = Analysis(
    ["threader.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pathvalidate",
        "google.protobuf",
        "pypresence",
        "PIL.AvifImagePlugin",
        "PIL.WebPImagePlugin",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


def normalized_toc_name(entry):
    return entry[0].replace("\\", "/").lower()


def keep_private_data(entry):
    name = normalized_toc_name(entry)
    if name.startswith("selenium/webdriver/common/"):
        return name.startswith("selenium/webdriver/common/windows/")
    return "/translations/" not in name


def keep_private_binary(entry):
    name = normalized_toc_name(entry)
    return not (
        name.startswith("pil/_avif")
        or name.startswith("pil/_webp")
        or name.endswith("/qwebp.dll")
    )


# The private executable has the same Windows-only browser and image support as
# the public build. Private feature data is added explicitly above.
a.datas = [entry for entry in a.datas if keep_private_data(entry)]
a.binaries = [entry for entry in a.binaries if keep_private_binary(entry)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="UmaLauncher-Private",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    uac_admin=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["./_assets/icon/global/default.ico"],
    version="build/version_private.rc",
)

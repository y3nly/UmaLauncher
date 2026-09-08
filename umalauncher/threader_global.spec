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
    ("./_assets/trackblazer_scheduler/index.html", "_assets/trackblazer_scheduler"),
    ("./_assets/trackblazer_scheduler/app.js", "_assets/trackblazer_scheduler"),
    ("./_assets/trackblazer_scheduler/solver-browser.js", "_assets/trackblazer_scheduler"),
    ("./_assets/trackblazer_scheduler/styles.css", "_assets/trackblazer_scheduler"),
    ("./_assets/trackblazer_scheduler/favicon.ico", "_assets/trackblazer_scheduler"),
    ("./_assets/trackblazer_scheduler/races.json", "_assets/trackblazer_scheduler"),
    ("./_assets/trackblazer_scheduler/epithets.json", "_assets/trackblazer_scheduler"),
    ("./_assets/training_helper", "_assets/training_helper"),
    (
        "./_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/index.js",
        "_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0",
    ),
    (
        "./_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/glpk.wasm",
        "_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0",
    ),
    (
        "./_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/LICENSE",
        "_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0",
    ),
    (
        "./_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0/PROVENANCE.md",
        "_assets/trackblazer_scheduler/vendor/glpk.js-5.0.0",
    ),
    ("./_assets/gm", "_assets/gm"),
    ("./_assets/uaf", "_assets/uaf"),
    ("./_assets/gl", "_assets/gl"),
    ("./_assets/gff", "_assets/gff"),
    ("./_assets/rmu", "_assets/rmu"),
    ("./_assets/dreams", "_assets/dreams"),
    ("./_assets/mant", "_assets/mant"),
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


def keep_global_data(entry):
    name = normalized_toc_name(entry)
    if name.startswith("selenium/webdriver/common/"):
        return name.startswith("selenium/webdriver/common/windows/")
    return "/translations/" not in name


def keep_global_binary(entry):
    name = normalized_toc_name(entry)
    return not (
        name.startswith("pil/_avif")
        or name.startswith("pil/_webp")
        or name.endswith("/qwebp.dll")
    )


# The Selenium hook collects all three platform managers and the Qt/Pillow hooks
# collect optional locale and image-codec payloads. This executable is Windows-only
# and only opens PNG/ICO assets, so discard those files after hook processing.
a.datas = [entry for entry in a.datas if keep_global_data(entry)]
a.binaries = [entry for entry in a.binaries if keep_global_binary(entry)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="UmaLauncher-Global",
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
    version="version_global.rc",
)

from pathlib import Path

import pyinstaller_versionfile

import version


def generate():
    output_file = Path("build") / "version_private.rc"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    pyinstaller_versionfile.create_versionfile(
        output_file=str(output_file),
        version=version.VERSION,
        file_description="Uma Launcher (Private)",
        internal_name="Uma Launcher (Private)",
        original_filename="UmaLauncher-Private.exe",
        product_name="Uma Launcher (Private)",
    )


if __name__ == "__main__":
    generate()

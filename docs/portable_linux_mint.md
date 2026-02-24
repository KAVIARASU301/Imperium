# Portable build guide (Linux Mint)

This guide shows how to package **Options Badger** so it can run on another Linux Mint machine without installing project dependencies manually.

## Important compatibility note

For best compatibility, build on:

- the **same Linux Mint major version** as target machines, and
- the same CPU architecture (typically `x86_64`).

Linux binaries are sensitive to glibc/system-library versions.

---

## 1) Prepare a clean build environment

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
pip install pyinstaller
```

---

## 2) Create a portable folder build (recommended)

Use `--onedir` for PySide6/Qt apps (more reliable than `--onefile` for large GUI apps):

```bash
pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name options-badger \
  --add-data "assets:assets" \
  main.py
```

Output is created in:

- `dist/options-badger/`

That folder is your portable app bundle.

### Quick run test

```bash
./dist/options-badger/options-badger
```

If the app starts, packaging is successful.

---

## 3) Distribute the portable folder

Share the entire `dist/options-badger/` directory (zip/tar.gz recommended):

```bash
tar -czf options-badger-linux-mint-portable.tar.gz -C dist options-badger
```

On target machine:

```bash
tar -xzf options-badger-linux-mint-portable.tar.gz
cd options-badger
./options-badger
```

---

## 4) (Optional) Convert to AppImage (single file)

If you want a single executable file:

1. Build the app with PyInstaller (`dist/options-badger` from step 2).
2. Use `linuxdeploy` + AppImage plugin to wrap the folder.

High-level flow:

```bash
# Example tools (download binaries from official releases)
chmod +x linuxdeploy-x86_64.AppImage
chmod +x linuxdeploy-plugin-appimage-x86_64.AppImage

# Export plugin path if needed
export LINUXDEPLOY_PLUGIN_APPIMAGE=./linuxdeploy-plugin-appimage-x86_64.AppImage

# Build AppDir/AppImage (desktop file + icon required)
./linuxdeploy-x86_64.AppImage \
  --appdir AppDir \
  --desktop-file options-badger.desktop \
  --icon-file assets/imperium_desk_icon.png \
  --output appimage
```

You will need a valid `options-badger.desktop` file for this step.

---

## 5) Practical troubleshooting

### Missing Qt platform plugin (xcb)

If launch fails with xcb/plugin errors on target machine, install common runtime libs:

```bash
sudo apt update
sudo apt install -y libxcb-cursor0 libxkbcommon-x11-0 libglu1-mesa
```

### API/browser login flow

Kite login opens in a browser. Ensure target machine has a default browser configured and outbound internet access.

### Credentials and session storage

Runtime app data is stored under:

- `~/.options_badger/`

This is expected and separate from the portable binary folder.

---

## 6) Minimal release checklist

- Build on the oldest Linux Mint version you intend to support.
- Confirm app launches from `dist/options-badger/options-badger`.
- Verify assets (icons/sounds/textures) load correctly.
- Test login, market data connection, and one paper-trade workflow.
- Ship as tar.gz (or AppImage if you complete optional step 4).

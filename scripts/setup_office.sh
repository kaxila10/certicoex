#!/usr/bin/env bash
# Rootless LibreOffice for Debian 13. apt verifies metadata and package hashes.
set -euo pipefail
cd "$(dirname "$0")/.."
repo_root="$PWD"
if command -v libreoffice >/dev/null; then libreoffice --headless --version; exit 0; fi
if [ -f .local/office/.prepared ]; then exit 0; fi
. /etc/os-release
if [ "$ID" != debian ] || [ "$VERSION_ID" != 13 ]; then
  echo 'Install LibreOffice Writer with your system package manager.' >&2; exit 1
fi
mkdir -p .local/apt/lists/partial .local/apt/archives/partial .local/office
cat > .local/apt/sources.list <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://security.debian.org/debian-security trixie-security main
EOF
apt_args=(-o Debug::NoLocking=true -o Dir::Etc::parts=- -o Dir::Etc::main=-
  -o "Dir::Etc::sourcelist=$repo_root/.local/apt/sources.list" -o Dir::Etc::sourceparts=-
  -o "Dir::State::lists=$repo_root/.local/apt/lists" -o "Dir::Cache::archives=$repo_root/.local/apt/archives")
/usr/bin/apt-get "${apt_args[@]}" update
/usr/bin/apt-get "${apt_args[@]}" --download-only --no-install-recommends -y install libreoffice-writer fonts-crosextra-carlito
for deb in .local/apt/archives/*.deb; do dpkg-deb -x "$deb" .local/office; done
python3 - <<'PY'
from pathlib import Path
from xml.sax.saxutils import escape
root = Path('.local/office').resolve()
program = root / 'usr/lib/libreoffice/program'
f = program / 'fundamentalrc'
f.write_text(f.read_text().replace('BRAND_BASE_DIR=file:///usr/lib/libreoffice', 'BRAND_BASE_DIR=' + program.parent.as_uri()))
# Debian normally creates this file in its post-install step.
main = root / 'etc/libreoffice/registry/main.xcd'
main.parent.mkdir(parents=True, exist_ok=True)
main.write_bytes((root / 'usr/lib/libreoffice/share/.registry/main.xcd').read_bytes())
for p in root.rglob('*'):
    if p.is_symlink() and p.readlink().is_absolute():
        target = root / str(p.readlink()).lstrip('/')
        if target.exists():
            p.unlink(); p.symlink_to(target)
fontdir = escape(str(root / 'usr/share/fonts'))
cache = escape(str(root.parent / 'font-cache'))
(root / 'fonts.conf').write_text(f'''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig><dir>/usr/share/fonts</dir><dir>/usr/share/texmf/fonts</dir><dir>{fontdir}</dir><cachedir>{cache}</cachedir><alias><family>Calibri</family><prefer><family>Carlito</family></prefer></alias><alias><family>Arial</family><prefer><family>Liberation Sans</family><family>DejaVu Sans</family></prefer></alias></fontconfig>''')
(root / '.prepared').touch()
PY
echo 'LibreOffice preparado en .local/office'

#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo 'Instala Python 3.12 o posterior desde python.org y vuelve a abrir este archivo.'
  read -r -p 'Pulsa Intro para salir. '
  exit 1
fi
python3 scripts/launch.py

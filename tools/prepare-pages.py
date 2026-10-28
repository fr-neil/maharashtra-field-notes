"""Stage the built public site for Pages without repository tooling or credentials."""
from pathlib import Path
import shutil

ROOT = Path.cwd()
OUTPUT = ROOT / '_site'
assert (ROOT / 'index.html').is_file(), 'Run from the built public repository root.'
assert not OUTPUT.exists(), '_site already exists; use a clean build directory.'
OUTPUT.mkdir()
for name in ['index.html', '404.html', '.nojekyll', 'favicon.svg', 'assets', 'data', 'downloads']:
    source = ROOT / name
    if source.is_dir():
        shutil.copytree(source, OUTPUT / name)
    elif source.is_file():
        shutil.copy2(source, OUTPUT / name)
assert (OUTPUT / 'data' / 'bootstrap.json').is_file(), 'Public data missing.'
print('Staged public HTML, assets, data and downloads for Pages.')

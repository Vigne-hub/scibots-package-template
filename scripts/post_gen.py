"""Post-generation task for the scibots package template.

Runs inside the target repository (copier `_tasks`):
- writes a static ``<module_dir>/_version.py`` (keeps the versioneer
  ``get_versions()`` shape so existing ``__init__.py`` files keep working)
- removes versioneer / setup.py / setup.cfg / .conda-recipe
- drops versioneer lines from MANIFEST.in
- makes sure .pixi/ is ignored
- headers / firmware flavors: patches file_handler.py so it no longer imports
  versioneer, takes SRC_DIR / PREFIX / the package name from the build
  environment, and (firmware) fails loudly when the PlatformIO build fails
"""
import argparse
import pathlib
import re
import shutil
import sys

VERSION_TEMPLATE = '''# Stamped by `cz bump` (see [tool.commitizen] in pyproject.toml). Keep the
# `__version__ = "..."` line at column 0 so the version_files regex matches.
__version__ = "{version}"


def get_versions():
    """Compatibility shim for the former versioneer interface."""
    return {{"version": __version__}}
'''

READ_VERSION = '''

def read_version() -> str:
    # Read the version without importing the package (its generated modules
    # may not exist yet while this script runs).
    import pathlib
    import re as _re
    text = pathlib.Path(__file__).parent.joinpath({module_dir!r}, '_version.py').read_text()
    return _re.search(r'^__version__ = "([^"]+)"', text, _re.M).group(1)
'''

PIO_RUN = "subprocess.run(['pio', 'run'], env=env)"
PIO_RUN_CHECKED = "subprocess.run(['pio', 'run'], env=env, check=True)"
LIB_EXTRA = "    env['PLATFORMIO_LIB_EXTRA_DIRS'] = str(pioh.conda_arduino_include_path())\n"
CORE_DIR = (
    "    if 'PLATFORMIO_CORE_DIR' not in env and os.name == 'nt' and env.get('HOMEDRIVE') and env.get('HOMEPATH'):\n"
    "        # rattler-build points HOME at its (deep) work directory; from there the\n"
    "        # toolchain include paths exceed MAX_PATH. Keep the PlatformIO core dir\n"
    "        # in the real user profile instead.\n"
    "        env['PLATFORMIO_CORE_DIR'] = os.path.join(env['HOMEDRIVE'] + env['HOMEPATH'], '.platformio')\n"
)


def unwrap_try_block(s: str) -> str:
    """Remove the `try: ... except FileNotFoundError: print('Failed to generate
    firmware')` wrapper around the firmware build so failures propagate."""
    lines = s.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'^(\s*)try:\s*$', line)
        if m:
            indent = m.group(1)
            j = i + 1
            while j < len(lines) and not re.match(
                    r'^' + re.escape(indent) + r'except FileNotFoundError:\s*$', lines[j]):
                j += 1
            if j + 1 < len(lines) and 'Failed to generate firmware' in lines[j + 1]:
                for body in lines[i + 1:j]:
                    out.append(body[4:] if body.startswith(indent + '    ') else body)
                i = j + 2
                continue
        out.append(line)
        i += 1
    return ''.join(out)


def patch_file_handler(root: pathlib.Path, module_dir: str, package_name: str,
                       module_name: str = '', lib_name: str = '') -> None:
    fh = root / 'file_handler.py'
    if not fh.exists():
        print('WARNING: file_handler.py not found; nothing to patch', file=sys.stderr)
        return
    s = fh.read_text()
    original = s
    problems = []

    # 1. versioneer -> static version
    s = re.sub(r'^import versioneer\n', '', s, count=1, flags=re.M)
    if 'versioneer.get_version()' in s:
        s = s.replace('versioneer.get_version()', 'read_version()')
        imports = list(re.finditer(r'^(?:from \S+ import .*|import \S+.*)\n', s, flags=re.M))
        if imports:
            pos = imports[-1].end()
            s = s[:pos] + READ_VERSION.format(module_dir=module_dir) + s[pos:]
        else:
            problems.append('could not place read_version()')
    if 'versioneer' in s:
        problems.append('versioneer still referenced')

    # 2. positional args default from the rattler-build environment
    if 'import os\n' not in s:
        s = s.replace('import argparse\n', 'import os\nimport argparse\n', 1)
    replacements = {
        "parser.add_argument('source_dir')":
            "parser.add_argument('source_dir', nargs='?', default=os.environ.get('SRC_DIR', '.'))",
        "parser.add_argument('prefix')":
            "parser.add_argument('prefix', nargs='?', default=os.environ.get('PREFIX'))",
        "parser.add_argument('package_name')":
            f"parser.add_argument('package_name', nargs='?', default={package_name!r})",
    }
    # Some repos (nadamq, nanopb-helpers) also take module_name / lib_name.
    optional = {
        "parser.add_argument('module_name')":
            f"parser.add_argument('module_name', nargs='?', default={module_name!r})",
        "parser.add_argument('lib_name')":
            f"parser.add_argument('lib_name', nargs='?', default={lib_name!r})",
    }
    for old, new in replacements.items():
        if old in s:
            s = s.replace(old, new, 1)
        else:
            problems.append(f'argument line not found: {old}')
    for old, new in optional.items():
        if old in s:
            s = s.replace(old, new, 1)

    # 3. firmware builds: fail loudly; short PlatformIO core dir on Windows
    if PIO_RUN in s:
        s = s.replace(PIO_RUN, PIO_RUN_CHECKED)
        s = unwrap_try_block(s)
        if LIB_EXTRA in s:
            s = s.replace(LIB_EXTRA, LIB_EXTRA + CORE_DIR, 1)
        else:
            problems.append('PLATFORMIO_LIB_EXTRA_DIRS line not found; core dir not pinned')

    if s != original:
        fh.write_text(s)
        print('patched file_handler.py')
    for p in problems:
        print(f'WARNING: file_handler.py: {p} -- fix by hand', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--flavor', default='python')
    parser.add_argument('--package-name', default='')
    parser.add_argument('--module-name', default='')
    parser.add_argument('--lib-name', default='')
    args = parser.parse_args()
    root = pathlib.Path.cwd()

    module_dir = root / args.module_dir
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / '_version.py').write_text(VERSION_TEMPLATE.format(version=args.version))
    print(f'wrote {module_dir / "_version.py"}')

    for name in ('setup.py', 'setup.cfg', 'versioneer.py', 'pavement.py'):
        p = root / name
        if p.exists():
            p.unlink()
            print(f'removed {name}')
    recipe = root / '.conda-recipe'
    if recipe.is_dir():
        shutil.rmtree(recipe)
        print('removed .conda-recipe/')

    manifest = root / 'MANIFEST.in'
    if manifest.exists():
        kept = [line for line in manifest.read_text().splitlines()
                if not any(t in line for t in ('versioneer', 'setup.py', 'RELEASE-VERSION',
                                                'include version.py'))]
        manifest.write_text('\n'.join(kept) + '\n')
        print('cleaned MANIFEST.in')

    gitignore = root / '.gitignore'
    text = gitignore.read_text() if gitignore.exists() else ''
    if '.pixi/' not in text:
        if text and not text.endswith('\n'):
            text += '\n'
        text += '# pixi\n.pixi/\n'
        gitignore.write_text(text)
        print('added .pixi/ to .gitignore')

    if args.flavor in ('headers', 'firmware'):
        patch_file_handler(root, args.module_dir, args.package_name,
                           args.module_name, args.lib_name)


if __name__ == '__main__':
    main()

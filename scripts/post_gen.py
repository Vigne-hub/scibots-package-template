"""Post-generation task for the scibots package template.

Runs inside the target repository (copier `_tasks`):
- writes a static ``<module_dir>/_version.py`` (keeps the versioneer
  ``get_versions()`` shape so existing ``__init__.py`` files keep working)
- removes versioneer / setup.py / setup.cfg / .conda-recipe
- drops versioneer lines from MANIFEST.in
- makes sure .pixi/ is ignored
"""
import argparse
import pathlib
import shutil

VERSION_TEMPLATE = '''# Stamped by `cz bump` (see [tool.commitizen] in pyproject.toml). Keep the
# `__version__ = "..."` line at column 0 so the version_files regex matches.
__version__ = "{version}"


def get_versions():
    """Compatibility shim for the former versioneer interface."""
    return {{"version": __version__}}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', required=True)
    parser.add_argument('--version', required=True)
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


if __name__ == '__main__':
    main()

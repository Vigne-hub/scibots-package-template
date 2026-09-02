"""Derive copier answers for a sci-bots repo from its conda-build meta.yaml.

usage: extract_answers.py <owner> <repo> [--ref BRANCH] [--package-name NAME] [--version V]

Prints a YAML list with one manifest entry to stdout. Review it before use:
win-only selectors are dropped, `versioneer`/`pip`/`setuptools`/`python`
are not carried over, and the version defaults to the newest tag.
"""
import argparse
import base64
import json
import re
import subprocess
import sys

import yaml

SKIP_DEPS = {'python', 'versioneer', 'pip', 'setuptools', 'pytest', 'nose'}
LICENSES = {
    'BSD': 'BSD-3-Clause', 'BSD-3': 'BSD-3-Clause', 'BSD-3-Clause': 'BSD-3-Clause',
    'MIT': 'MIT', 'GPL': 'GPL-3.0-or-later', 'GPLv2': 'GPL-2.0-or-later',
    'GPLv3': 'GPL-3.0-or-later', 'LGPL': 'LGPL-3.0-or-later',
    'LGPLv2.1': 'LGPL-2.1-or-later', 'LGPLv3': 'LGPL-3.0-or-later',
}


def gh(path):
    return json.loads(subprocess.check_output(['gh', 'api', path]))


def contents(owner, repo, path, ref):
    data = gh(f'repos/{owner}/{repo}/contents/{path}?ref={ref}')
    return base64.b64decode(data['content']).decode()


def indent_of(line):
    return len(line) - len(line.lstrip(' '))


def section(text, header, indent):
    """Body of the block introduced by `header` at exactly `indent` spaces."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == header and indent_of(line) == indent:
            out = []
            for nxt in lines[i + 1:]:
                if nxt.strip() == '' or nxt.strip().startswith('#'):
                    continue
                if indent_of(nxt) <= indent:
                    break
                out.append(nxt)
            return '\n'.join(out) + '\n'
    return ''


def dep_list(block):
    """Parse '- name spec  # [selector]' lines into {name: spec}; drop selector-only lines."""
    out = {}
    for line in block.splitlines():
        m = re.match(r'^\s*-\s+([^\s#{]+)(?:\s+([^#]*?))?\s*(#\s*\[(.*?)\])?\s*$', line)
        if not m:
            continue
        name, spec, _, selector = m.groups()
        if selector or name in SKIP_DEPS or '::' in name:
            continue
        out[name] = (spec or '').strip() or '*'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('owner'); ap.add_argument('repo')
    ap.add_argument('--ref', default=None)
    ap.add_argument('--package-name', default=None)
    ap.add_argument('--version', default=None)
    a = ap.parse_args()

    info = gh(f'repos/{a.owner}/{a.repo}')
    ref = a.ref or info['default_branch']
    meta = contents(a.owner, a.repo, '.conda-recipe/meta.yaml', ref)
    top = [x['name'] for x in gh(f'repos/{a.owner}/{a.repo}/contents?ref={ref}')]

    pkg = a.package_name
    if not pkg:
        m = re.search(r"set PKG_NAME = '([^']+)'", meta)
        if m:
            pkg = m.group(1)
        else:
            m = re.search(r'^package:\n\s+name:\s*([^\s{]+)', meta, re.M)
            pkg = m.group(1) if m else a.repo
    module_m = re.search(r"set MODULE_NAME = '([^']+)'", meta)
    module = module_m.group(1) if module_m else pkg.replace('-', '_')
    lib_m = re.search(r"set LIB_NAME = '([^']+)'", meta)
    has_dev = bool(re.search(r'- name: \{\{ PKG_NAME \}\}-dev', meta))
    flavor = 'headers' if has_dev else 'python'

    build_deps = dep_list(section(meta, 'build:', 2))
    if flavor == 'headers':
        outputs = meta[meta.index('outputs:'):]
        dev_part, _, py_part = outputs.partition('- name: {{ PKG_NAME }}\n')
        dev_run = dep_list(section(dev_part, 'run:', 6))
        run = dep_list(section(py_part, 'run:', 6))
    else:
        dev_run = {}
        run = dep_list(section(meta, 'run:', 2))
    run.pop(pkg + '-dev', None)
    run = {k: v for k, v in run.items() if not k.startswith('{{')}

    scripts = {}
    for line in section(meta, 'entry_points:', 2).splitlines():
        m = re.match(r'\s*-\s*(\S+)\s*=\s*(\S+)', line)
        if m:
            scripts[m.group(1)] = m.group(2)

    summary_m = re.search(r'^\s*summary:\s*(.+)$', meta, re.M)
    license_m = re.search(r'^\s*license:\s*(.+)$', meta, re.M)
    lic_raw = (license_m.group(1).strip().strip('"\'') if license_m else 'BSD')
    license = LICENSES.get(lic_raw.split()[0].rstrip(','), lic_raw)
    lic_file = next((f for f in top if f.upper().startswith(('LICENSE', 'COPYING'))), '')
    package_dir = 'src' if 'src' in top and module not in top else '.'

    version = a.version
    if not version:
        tags = gh(f'repos/{a.owner}/{a.repo}/tags?per_page=1')
        version = tags[0]['name'].lstrip('v') if tags else '0.0.0'

    entry = {
        'repo': a.repo,
        'source': f'{a.owner}/{a.repo}',
        'source_ref': ref,
        'answers': {
            'package_name': pkg,
            'module_name': module,
            'package_dir': package_dir,
            'version': version,
            'description': (summary_m.group(1).strip() if summary_m else pkg),
            'license': license,
            'license_file': lic_file,
            'flavor': flavor,
            'scripts': scripts,
            'run_dependencies': run,
        },
    }
    if flavor == 'headers':
        entry['answers']['host_dependencies'] = build_deps
        entry['answers']['dev_run_dependencies'] = dev_run
        entry['answers']['lib_name'] = lib_m.group(1) if lib_m else ''.join(
            w.capitalize() for w in pkg.split('-'))
    yaml.safe_dump([entry], sys.stdout, sort_keys=False, default_flow_style=False)


if __name__ == '__main__':
    main()

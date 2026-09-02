"""Apply the template to a list of sci-bots repos and seed the prefix.dev channel.

usage: bootstrap.py repos.yaml [--only NAME ...] [--fork-owner Vigne-hub]
                    [--workdir DIR] [--key-file PATH] [--no-publish] [--dry-run]

For each manifest entry:
  1. fork <source> under --fork-owner if needed; fetch <source_ref>; reset the
     fork's default branch (or `branch:`) to it and force-push
  2. apply the copier template with `answers` (fresh copy)
  3. commit ("build: ..."), tag v<version> if missing, push branch + tags
  4. set the PREFIX_API_KEY secret from --key-file
  5. dispatch publish.yml with publish_current=true and wait for it
  6. check the package on the channel

Manifest entry:
  - repo: or-event            # name under --fork-owner
    source: AlexSklav/or-event
    source_ref: master        # branch/tag in the source repo
    branch: master            # target branch in the fork (default: master)
    answers: {package_name: ..., version: ..., ...}   # copier answers
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

import yaml

TEMPLATE = 'https://github.com/Vigne-hub/scibots-package-template.git'
TEMPLATE_LOCAL = pathlib.Path(__file__).resolve().parent.parent


def run(cmd, cwd=None, check=True, capture=False, quiet=False):
    if not quiet:
        print('  $', ' '.join(map(str, cmd)), flush=True)
    r = subprocess.run(cmd, cwd=cwd, check=check, text=True,
                       capture_output=capture)
    return r.stdout.strip() if capture else None


def gh_json(path):
    return json.loads(run(['gh', 'api', path], capture=True, quiet=True))


def repo_exists(full):
    return subprocess.run(['gh', 'api', f'repos/{full}'], capture_output=True).returncode == 0


def copier_args(answers):
    args = []
    for k, v in answers.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        args += ['-d', f'{k}={v}']
    return args


def process(entry, opts):
    repo, source = entry['repo'], entry['source']
    src_ref = entry.get('source_ref', 'master')
    branch = entry.get('branch', 'master')
    fork = f'{opts.fork_owner}/{repo}'
    answers = entry['answers']
    version = answers['version']
    print(f'\n### {repo}  ({source}@{src_ref} -> {fork}:{branch}, v{version})')
    if opts.dry_run:
        print('  copier', ' '.join(copier_args(answers)))
        return

    # 1. fork + reset to source ref
    if not repo_exists(fork):
        run(['gh', 'repo', 'fork', source, '--clone=false', '--fork-name', repo])
        time.sleep(5)
    wd = opts.workdir / repo
    if not wd.exists():
        run(['gh', 'repo', 'clone', fork, str(wd), '--', '-q'])
    run(['git', 'remote', 'remove', 'upstream'], cwd=wd, check=False, capture=True)
    run(['git', 'remote', 'add', 'upstream', f'https://github.com/{source}.git'], cwd=wd)
    run(['git', 'fetch', '-q', '--tags', 'upstream', src_ref], cwd=wd)
    run(['git', 'checkout', '-q', '-B', branch, 'FETCH_HEAD'], cwd=wd)
    run(['git', 'reset', '-q', '--hard', 'FETCH_HEAD'], cwd=wd)
    run(['git', 'clean', '-fdq', '-e', '.pixi'], cwd=wd)

    # 2. template
    run(['pixi', 'exec', '--spec', 'copier>=9', '--', 'copier', 'copy', '--trust',
         '--defaults', '--overwrite', str(TEMPLATE_LOCAL), '.'] + copier_args(answers), cwd=wd)
    ans = wd / '.copier-answers.yml'
    ans.write_text(ans.read_text().replace(str(TEMPLATE_LOCAL).replace('\\', '/'), TEMPLATE)
                   .replace(str(TEMPLATE_LOCAL), TEMPLATE))

    # 3. commit, tag, push
    run(['git', 'add', '-A'], cwd=wd)
    run(['git', '-c', 'core.safecrlf=false', 'commit', '-q', '-m',
         'build: build and publish with pixi (scibots-package-template)\n\n'
         'Replace versioneer/setup.py/.conda-recipe with a pyproject.toml carrying the\n'
         'pixi package definition, a static _version.py stamped by commitizen, and\n'
         'workflows that release to https://prefix.dev/vigne-hub/scibots on fix:/feat:\n'
         'pushes.\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'], cwd=wd)
    tags = run(['git', 'tag', '--list', f'v{version}'], cwd=wd, capture=True)
    if not tags:
        run(['git', 'tag', '-a', f'v{version}', '-m', f'v{version}'], cwd=wd)
    run(['git', 'push', '-q', '--force', '-u', 'origin', branch], cwd=wd)
    run(['git', 'push', '-q', 'origin', '--tags'], cwd=wd)
    if gh_json(f'repos/{fork}')['default_branch'] != branch:
        run(['gh', 'repo', 'edit', fork, '--default-branch', branch])

    # 4. secret
    if opts.key_file:
        with open(opts.key_file) as f:
            subprocess.run(['gh', 'secret', 'set', 'PREFIX_API_KEY', '--repo', fork],
                           stdin=f, check=True)
        print('  secret PREFIX_API_KEY set')

    if opts.no_publish:
        return

    # 5. publish. The push itself triggers publish.yml: when HEAD carries
    #    release-worthy commits since the last v* tag that run bumps and
    #    publishes; otherwise it skips and we publish the current version.
    time.sleep(20)
    push_run = run(['gh', 'run', 'list', '--repo', fork, '--workflow=publish.yml',
                    '--event', 'push', '--limit', '1', '--json', 'databaseId',
                    '--jq', '.[0].databaseId'], capture=True)
    published = False
    if push_run:
        subprocess.run(['gh', 'run', 'watch', push_run, '--repo', fork, '--interval', '15'],
                       capture_output=True)
        steps = json.loads(run(['gh', 'run', 'view', push_run, '--repo', fork, '--json',
                                'conclusion,jobs'], capture=True, quiet=True))
        pub = [st for j in steps['jobs'] for st in j['steps'] if st['name'] == 'Publish']
        if steps['conclusion'] == 'success' and pub and pub[0]['conclusion'] == 'success':
            published = True
            print(f'  push-triggered run {push_run} released a new version')
        elif steps['conclusion'] != 'success':
            subprocess.run(['gh', 'run', 'view', push_run, '--repo', fork, '--log-failed'])
            raise SystemExit(f'{repo}: push-triggered publish failed (run {push_run})')
    if published:
        run_id = push_run
    else:
        run(['gh', 'workflow', 'run', 'publish.yml', '--repo', fork, '--ref', branch,
             '-f', 'publish_current=true'])
        time.sleep(25)
        run_id = run(['gh', 'run', 'list', '--repo', fork, '--workflow=publish.yml',
                      '--event', 'workflow_dispatch', '--limit', '1', '--json', 'databaseId',
                      '--jq', '.[0].databaseId'], capture=True)
    rc = subprocess.run(['gh', 'run', 'watch', run_id, '--repo', fork, '--exit-status',
                         '--interval', '15'], capture_output=True).returncode
    concl = run(['gh', 'run', 'view', run_id, '--repo', fork, '--json', 'conclusion',
                 '--jq', '.conclusion'], capture=True)
    print(f'  publish run {run_id}: {concl}')
    if rc != 0:
        subprocess.run(['gh', 'run', 'view', run_id, '--repo', fork, '--log-failed'])
        raise SystemExit(f'{repo}: publish failed (run {run_id})')

    # 6. verify
    out = subprocess.run(['pixi', 'search', '-c', 'https://prefix.dev/vigne-hub/scibots',
                          answers['package_name'], '--limit', '1'], capture_output=True, text=True)
    print('  channel:', ' '.join(l.split()[-1] for l in (out.stdout + out.stderr).splitlines()
                                 if l.startswith(('Version', 'Build'))) or (out.stdout + out.stderr).strip()[:200])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('manifest')
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--fork-owner', default='Vigne-hub')
    ap.add_argument('--workdir', type=pathlib.Path, default=pathlib.Path.home() / 'PycharmProjects')
    ap.add_argument('--key-file', default=None)
    ap.add_argument('--no-publish', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    opts = ap.parse_args()
    entries = yaml.safe_load(open(opts.manifest))
    if opts.only:
        entries = [e for e in entries if e['repo'] in opts.only]
    for e in entries:
        process(e, opts)
    print('\nall done')


if __name__ == '__main__':
    main()

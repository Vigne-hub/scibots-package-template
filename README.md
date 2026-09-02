# scibots-package-template

[Copier](https://copier.readthedocs.io/) template that gives a sci-bots
dependency package (forks of `AlexSklav/*`) the same build and release setup
as `dropbot.py`:

- `pyproject.toml` with the pixi package definition (`pixi build`), the
  conda run dependencies and the commitizen configuration
- static `_version.py` (versioneer, `setup.py`, `setup.cfg` and
  `.conda-recipe` are removed)
- `.github/workflows/publish.yml`: commitizen bump -> `pixi build` ->
  `pixi upload prefix --channel <namespace/channel>` -> push release commit
  and tag; API key secret if present, trusted publishing otherwise
- `.github/workflows/conventional-commits.yml`: PR commit-message check
- `CHANGELOG.md` baseline

## Apply to a repository

```
pixi exec --spec copier -- copier copy --trust <path to this template> <repo> \
    -d package_name=or-event -d package_dir=src -d version=0.2.3 \
    -d description="Wait on multiple threading.Event instances" \
    -d license=BSD-3-Clause -d license_file=LICENSE.md \
    -d run_dependencies='{}'
```

Answers are stored in `.copier-answers.yml`; later template changes are
applied with `copier update --trust` inside the repository.

## Seeding the channel

After the first push, run the publish workflow manually with
`publish_current` checked to upload the existing version as-is. From then on
`fix:` / `feat:` commits on the default branch release automatically.

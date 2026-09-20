"""Recipe parsing: pull the maintainer list, package name(s), and license out of meta.yaml /
recipe.yaml text."""

from __future__ import annotations

import jinja2
import yaml

_MAINTAINER_KEYS = ("recipe-maintainers", "maintainers")


class ParseError(Exception):
    """Raised when a recipe file's content can't be rendered/parsed."""


class _SilentUndefined(jinja2.Undefined):
    """A jinja2 Undefined that renders as an empty string instead of raising.

    conda-forge meta.yaml files use conda-build-only jinja helpers
    (compiler(), pin_subpackage(), cdt(), load_setup_py_data(), environ, ...)
    that we don't have implementations for here. The extra.recipe-maintainers
    list is essentially always a static list of strings though, so we render
    leniently rather than reproducing conda-build's full jinja context: any
    undefined name/call/attribute just evaluates to "nothing" and the parts
    of the file we actually care about still come through correctly.
    """

    def _fail_with_undefined_error(self, *args, **kwargs):
        return _SilentUndefined()

    def __getattr__(self, name):
        return _SilentUndefined()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __repr__(self):
        return ""


_UNDEFINED_DUNDERS = (
    "__add__",
    "__radd__",
    "__sub__",
    "__rsub__",
    "__mul__",
    "__rmul__",
    "__truediv__",
    "__rtruediv__",
    "__floordiv__",
    "__rfloordiv__",
    "__mod__",
    "__rmod__",
    "__pos__",
    "__neg__",
    "__call__",
    "__getitem__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__int__",
    "__float__",
    "__complex__",
    "__pow__",
    "__rpow__",
)
for _dunder in _UNDEFINED_DUNDERS:
    setattr(_SilentUndefined, _dunder, lambda self, *args, **kwargs: _SilentUndefined())
del _UNDEFINED_DUNDERS, _dunder


class _TolerantFilters(dict):
    """A jinja2 `Environment.filters` mapping where any filter -- known or not -- can't raise.

    conda-build/rattler-build recipes use custom jinja filters we don't implement
    (version_to_buildstring, split, ...) and real filters are sometimes applied to already
    undefined/nonsense values (e.g. `x|replace(y, "z")` where `y` came from an unimplemented
    helper). An unknown filter name would otherwise raise `TemplateAssertionError` at compile
    time, and a real filter choking on a bad argument would raise at render time -- either way
    aborting the whole parse even though the maintainer list (or package name) we actually want
    lives elsewhere in the file. Continuing the same "render leniently" philosophy as
    `_SilentUndefined`: an unknown filter passes its input through unchanged, and any filter that
    raises falls back to its unfiltered input instead of aborting the render.
    """

    def get(self, key, default=None):
        try:
            func = dict.__getitem__(self, key)
        except KeyError:
            return lambda value, *args, **kwargs: value

        def _tolerant(value, *args, **kwargs):
            try:
                return func(value, *args, **kwargs)
            except Exception:
                return value

        return _tolerant

    def __getitem__(self, key):
        return self.get(key)

    def __contains__(self, key) -> bool:
        return True


_JINJA_ENV = jinja2.Environment(undefined=_SilentUndefined)  # noqa: S701 (renders YAML, not HTML)
_JINJA_ENV.filters = _TolerantFilters(_JINJA_ENV.filters)

# recipe.yaml (rattler-build v1) uses minijinja `${{ }}` templating instead of conda-build's
# `{{ }}`. Block/comment delimiters are set to sentinels that can't occur in real recipe.yaml
# text, since that format has no jinja block syntax (it uses YAML `if:`/`then:`/`else:` lists
# instead) -- this is just a safety net against an accidental "{%"/"{#" collision.
_RECIPE_YAML_JINJA_ENV = jinja2.Environment(  # noqa: S701 (renders YAML, not HTML)
    undefined=_SilentUndefined,
    variable_start_string="${{",
    variable_end_string="}}",
    block_start_string="\x00BLOCK\x00",
    block_end_string="\x00BLOCK\x00",
    comment_start_string="\x00COMMENT\x00",
    comment_end_string="\x00COMMENT\x00",
)
_RECIPE_YAML_JINJA_ENV.filters = _TolerantFilters(_RECIPE_YAML_JINJA_ENV.filters)


def _render_meta_yaml(text: str) -> str:
    return _JINJA_ENV.from_string(text).render()


def _resolve_recipe_yaml_context(raw_context) -> dict:
    """Render a recipe.yaml `context:` block's `${{ }}` values in declaration order.

    Each entry may reference earlier entries (e.g. `cxx_name: ${{ "lib" ~ name }}` after
    `name: ...`), mirroring how `{% set %}` chains resolve top-to-bottom in meta.yaml. Falls
    back to the raw, unrendered value on a per-entry render error rather than raising, matching
    this module's lenient philosophy elsewhere.
    """
    resolved: dict = {}
    if not isinstance(raw_context, dict):
        return resolved

    for key, value in raw_context.items():
        if isinstance(value, str):
            try:
                resolved[key] = _RECIPE_YAML_JINJA_ENV.from_string(value).render(**resolved)
            except Exception:
                resolved[key] = value
        else:
            resolved[key] = value
    return resolved


def _render_recipe_yaml(text: str, context: dict) -> str:
    return _RECIPE_YAML_JINJA_ENV.from_string(text).render(**context)


def _extract_from_data(data) -> list | None:
    if not isinstance(data, dict):
        return None
    extra = data.get("extra")
    if not isinstance(extra, dict):
        return None

    value = None
    for key in _MAINTAINER_KEYS:
        if key in extra:
            value = extra[key]
            break
    if not isinstance(value, list):
        return None

    maintainers = []
    for entry in value:
        if isinstance(entry, str):
            maintainers.append(entry)
        elif isinstance(entry, dict):
            maintainers.append(entry.get("github") or entry.get("name") or str(entry))
        else:
            maintainers.append(str(entry))
    return maintainers


def _extract_license_from_data(data) -> str | None:
    """Return a recipe's top-level `about.license`, or `None` if absent/malformed.

    Only the top-level `about` block is read, never a per-output one -- conda-forge convention
    puts a single license declaration there even for multi-output feedstocks (mirrors how
    `extra.recipe-maintainers` is read the same way in `_extract_from_data`), and it's what
    applies to every package name the feedstock produces.
    """
    if not isinstance(data, dict):
        return None
    about = data.get("about")
    if not isinstance(about, dict):
        return None
    license_ = about.get("license")
    if not isinstance(license_, str):
        return None
    license_ = license_.strip()
    return license_ or None


def _extract_package_names_from_data(data) -> list[str]:
    """Return the real, installable conda package name(s) declared by a parsed recipe.

    If `outputs` is a non-empty list, the recipe is multi-output and ONLY the output names are
    used -- conda-forge convention gives multi-output recipes' top-level `package`/`recipe` name
    a throwaway suffix (e.g. "boost-split", "mamba-split", "openscm-units-suite") that is never
    itself an installable package, so it must never be returned alongside or instead of the real
    output names. Each output's name may be a flat `name` key (meta.yaml-style) or a nested
    `package.name` key (recipe.yaml-style).

    Otherwise (no `outputs`), the top-level `package.name` (meta.yaml/recipe.yaml single-output)
    or `recipe.name` (recipe.yaml single-output using the `recipe:` key) is the package name.

    A name that still contains "{{" (unresolved templating, e.g. a recipe.yaml `${{ }}` that
    couldn't be rendered) is dropped rather than returned as a garbage package name.
    """
    if not isinstance(data, dict):
        return []

    def _clean(name) -> str | None:
        if not isinstance(name, str):
            return None
        name = name.strip()
        return name if name and "{{" not in name else None

    outputs = data.get("outputs")
    if isinstance(outputs, list) and outputs:
        names = []
        for output in outputs:
            if not isinstance(output, dict):
                continue
            name = output.get("name")
            if name is None and isinstance(output.get("package"), dict):
                name = output["package"].get("name")
            name = _clean(name)
            if name is not None:
                names.append(name)
        return names

    for top_key in ("package", "recipe"):
        top = data.get(top_key)
        if isinstance(top, dict):
            name = _clean(top.get("name"))
            if name is not None:
                return [name]
    return []


def _parse_meta_yaml(text: str) -> dict:
    try:
        rendered = _render_meta_yaml(text)
    except Exception as exc:
        raise ParseError(f"failed to render meta.yaml: {exc}") from exc
    try:
        data = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        raise ParseError(f"failed to parse meta.yaml: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _parse_recipe_yaml_raw(text: str) -> dict:
    """Parse recipe.yaml WITHOUT rendering `${{ }}`.

    `${{ }}` doesn't disturb YAML syntax, so a recipe.yaml can always be parsed this way even
    though templated fields (like the package name) come through as literal, unresolved
    "${{ ... }}" strings. That's fine for maintainers, which are essentially always static
    strings never worth templating, and this path is more robust than rendering: an undefined
    helper collapsing to an empty string can turn an innocuous scalar like
    "${{ compiler(...) }} >=1.23" into "  >=1.23", which PyYAML's scanner misreads as an invalid
    block-scalar header -- losing the whole file's data, maintainers included.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParseError(f"failed to parse recipe.yaml: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _parse_recipe_yaml_rendered(text: str, raw: dict) -> dict:
    """Best-effort rendered parse of recipe.yaml, for resolving templated package names.

    Falls back to the unrendered `raw` data (never raises) if rendering breaks the file's YAML
    structure -- losing a templated package name for one feedstock is preferable to losing its
    maintainers too, which `parse_recipe`/`extract_maintainers_from_text` get from `raw` instead.
    """
    context = _resolve_recipe_yaml_context(raw.get("context") if isinstance(raw, dict) else None)
    try:
        rendered = _render_recipe_yaml(text, context)
        data = yaml.safe_load(rendered)
    except Exception:
        return raw
    return data if isinstance(data, dict) else raw


def extract_maintainers_from_text(filename: str, text: str) -> list:
    """Return the maintainer list found in a recipe.yaml/meta.yaml's raw text."""
    data = _parse_recipe_yaml_raw(text) if filename == "recipe.yaml" else _parse_meta_yaml(text)
    return _extract_from_data(data) or []


def extract_package_names_from_text(filename: str, text: str) -> list[str]:
    """Return the real, installable conda package name(s) found in a recipe.yaml/meta.yaml."""
    if filename == "recipe.yaml":
        data = _parse_recipe_yaml_rendered(text, _parse_recipe_yaml_raw(text))
    else:
        data = _parse_meta_yaml(text)
    return _extract_package_names_from_data(data)


def extract_license_from_text(filename: str, text: str) -> str | None:
    """Return the top-level `about.license` found in a recipe.yaml/meta.yaml, or `None`."""
    data = _parse_recipe_yaml_raw(text) if filename == "recipe.yaml" else _parse_meta_yaml(text)
    return _extract_license_from_data(data)


def parse_recipe(filename: str, text: str) -> tuple[list, list[str], str | None]:
    """Return (maintainers, package_names, license) for a recipe.yaml/meta.yaml, parsed as needed.

    For meta.yaml this parses once (a single jinja render already resolves both). For recipe.yaml
    this parses the unrendered text for maintainers and license (robust) and separately,
    best-effort, the rendered text for package names (see `_parse_recipe_yaml_rendered`) -- so a
    rendering issue that only affects a templated package name never costs the feedstock its
    maintainer list or license too.
    """
    if filename == "recipe.yaml":
        raw = _parse_recipe_yaml_raw(text)
        maintainers = _extract_from_data(raw) or []
        license_ = _extract_license_from_data(raw)
        package_names = _extract_package_names_from_data(_parse_recipe_yaml_rendered(text, raw))
    else:
        data = _parse_meta_yaml(text)
        maintainers = _extract_from_data(data) or []
        package_names = _extract_package_names_from_data(data)
        license_ = _extract_license_from_data(data)
    return maintainers, package_names, license_

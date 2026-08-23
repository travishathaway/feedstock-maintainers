"""Recipe parsing: pull the maintainer list out of meta.yaml / recipe.yaml text."""

from __future__ import annotations

from typing import Optional

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

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = \
        __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = \
        __mod__ = __rmod__ = __pos__ = __neg__ = __call__ = \
        __getitem__ = __lt__ = __le__ = __gt__ = __ge__ = \
        __int__ = __float__ = __complex__ = __pow__ = __rpow__ = \
        lambda self, *args, **kwargs: _SilentUndefined()

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


_JINJA_ENV = jinja2.Environment(undefined=_SilentUndefined)


def _render_meta_yaml(text: str) -> str:
    return _JINJA_ENV.from_string(text).render()


def _extract_from_data(data) -> Optional[list]:
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


def extract_maintainers_from_text(filename: str, text: str) -> list:
    """Return the maintainer list found in a recipe.yaml/meta.yaml's raw text.

    recipe.yaml uses `${{ }}` (minijinja) templating, which YAML doesn't treat
    specially, so it can be parsed directly. meta.yaml uses conda-build's
    `{{ }}` jinja templating and needs rendering first.
    """
    if filename == "recipe.yaml":
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ParseError(f"failed to parse {filename}: {exc}") from exc
    else:
        try:
            rendered = _render_meta_yaml(text)
        except Exception as exc:
            raise ParseError(f"failed to render {filename}: {exc}") from exc
        try:
            data = yaml.safe_load(rendered)
        except yaml.YAMLError as exc:
            raise ParseError(f"failed to parse {filename}: {exc}") from exc

    return _extract_from_data(data) or []

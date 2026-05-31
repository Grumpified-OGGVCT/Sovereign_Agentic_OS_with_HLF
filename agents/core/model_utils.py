"""
Model utility helpers — single source of truth for cloud-model detection
and model-name normalisation.

These functions were previously duplicated as ``_is_cloud()`` in
``agents/gateway/router.py`` and as inline logic in ``agents/core/main.py``.
Centralising them here means any change to the detection rules propagates
everywhere automatically.

Usage::

    from agents.core.model_utils import is_cloud_model, strip_cloud_suffix

    if is_cloud_model(model_id):
        bare = strip_cloud_suffix(model_id)
"""

from __future__ import annotations


def is_cloud_model(model: str) -> bool:
    """Return ``True`` when *model* is a cloud-routed model.

    Cloud model IDs follow two conventions used by the Sovereign OS:

    * ``{root}:cloud`` — bare-tag cloud models (e.g. ``kimi-k2.5:cloud``).
    * ``{root}:{size}-cloud`` — size-qualified cloud variants
      (e.g. ``qwen3-vl:32b-cloud``).

    Both forms are detected so that callers do not need to duplicate the
    ``endswith(":cloud") or endswith("-cloud")`` idiom.
    """
    return model.endswith(":cloud") or model.endswith("-cloud")


def strip_cloud_suffix(model: str) -> str:
    """Remove the cloud routing suffix from a model ID.

    Returns the bare model name suitable for passing to the Ollama or
    OpenRouter API endpoint.

    Examples::

        >>> strip_cloud_suffix("kimi-k2.5:cloud")
        'kimi-k2.5'
        >>> strip_cloud_suffix("qwen3-vl:32b-cloud")
        'qwen3-vl:32b'
        >>> strip_cloud_suffix("qwen:7b")
        'qwen:7b'
    """
    return model.removesuffix(":cloud").removesuffix("-cloud")

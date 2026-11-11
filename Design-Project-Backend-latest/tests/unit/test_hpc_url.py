"""The endpoint is published in two forms; both must reach the same path.

`HPC_BASE_URL` is commonly given as either `https://host:8443` or
`https://host:8443/v1`. Paths in `app/llm/hpc.py` carry their own `/v1`, so
without normalisation the second form produces `/v1/v1/chat/completions` and
every request 404s.
"""

from __future__ import annotations

import pytest

from app.llm.hpc import CHAT_PATH, MODELS_PATH, normalise_base_url

HOST = "https://llm.example.sg:8443"


@pytest.mark.parametrize(
    "configured",
    [HOST, f"{HOST}/", f"{HOST}/v1", f"{HOST}/v1/", f"  {HOST}/v1  "],
)
def test_both_published_forms_collapse_to_one_root(configured):
    assert normalise_base_url(configured) == HOST


@pytest.mark.parametrize("configured", [HOST, f"{HOST}/v1"])
def test_the_resulting_request_url_has_exactly_one_v1(configured):
    for path in (CHAT_PATH, MODELS_PATH):
        url = normalise_base_url(configured) + path
        assert url.count("/v1") == 1, url


def test_a_v1_inside_a_longer_path_is_preserved():
    """Only a trailing `/v1` is the API-version marker; a mounted prefix is not."""
    assert normalise_base_url("https://host/api/v1") == "https://host/api"
    assert normalise_base_url("https://host/v1/gateway") == "https://host/v1/gateway"

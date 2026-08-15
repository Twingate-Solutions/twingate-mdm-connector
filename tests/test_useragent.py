"""Tests for the Twingate User-Agent builder."""

import httpx

from src import __version__
from src.twingate.useragent import build_user_agent


def test_list_op_has_no_via() -> None:
    ua = build_user_agent(
        providers=["jumpcloud", "fleetdm"], mode="any", dry_run=False, op="list"
    )
    assert ua == (
        f"twingate-mdm-connector/{__version__} "
        f"(providers=fleetdm,jumpcloud; mode=any; dry_run=false; op=list) "
        f"python-httpx/{httpx.__version__}"
    )
    assert "via=" not in ua


def test_trust_op_includes_via() -> None:
    ua = build_user_agent(
        providers=["fleetdm", "jumpcloud"],
        mode="all",
        dry_run=True,
        op="trust",
        via=["jumpcloud"],
    )
    assert "op=trust" in ua
    assert "via=jumpcloud" in ua
    assert "dry_run=true" in ua
    assert "mode=all" in ua


def test_providers_sorted_and_deduped() -> None:
    ua = build_user_agent(
        providers=["jumpcloud", "fleetdm", "jumpcloud"],
        mode="any",
        dry_run=False,
        op="list",
    )
    assert "providers=fleetdm,jumpcloud" in ua


def test_no_providers_reported_as_none() -> None:
    ua = build_user_agent(providers=[], mode="any", dry_run=False, op="list")
    assert "providers=none" in ua

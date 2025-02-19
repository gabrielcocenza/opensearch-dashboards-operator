#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import subprocess

import pytest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


DEFAULT_LXD_NETWORK = "lxdbr0"
RAW_DNSMASQ = """dhcp-option=3
dhcp-option=6"""


def _lxd_network(name: str, subnet: str, external: bool = True):
    """Helper function"""

    # Don't create the network if it already exists
    try:
        subprocess.run(
            ["sudo", "lxc", "network", "show", name],
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        logger.info(f"LXD network {name} already exists")
        return
    except subprocess.CalledProcessError:
        # If we can't list the network, let's try to create it
        pass

    try:
        output = subprocess.run(
            [
                "sudo",
                "lxc",
                "network",
                "create",
                name,
                "--type=bridge",
                f"ipv4.address={subnet}",
                f"ipv4.nat={external}".lower(),
                "ipv6.address=none",
                "dns.mode=none",
            ],
            capture_output=True,
            check=True,
            encoding="utf-8",
        ).stdout
        logger.info(f"LXD network created: {output}")
        output = subprocess.run(
            ["sudo", "lxc", "network", "show", name],
            capture_output=True,
            check=True,
            encoding="utf-8",
        ).stdout
        logger.debug(f"LXD network status: {output}")

        if not external:
            subprocess.run(
                ["sudo", "lxc", "network", "set", name, "raw.dnsmasq", RAW_DNSMASQ], check=True
            )

        subprocess.run(f"sudo ip link set up dev {name}".split(), check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error creating LXD network {name} with: {e.returncode} {e.stderr}")
        raise


@pytest.fixture(scope="module")
def lxd():
    try:
        # Set all networks' dns.mode=none
        # We want to avoid check:
        # https://github.com/canonical/lxd/blob/
        #     762f7dc5c3dc4dbd0863a796898212d8fbe3f7c3/lxd/device/nic_bridged.go#L403
        # As described on:
        # https://discuss.linuxcontainers.org/t/
        #     error-failed-start-validation-for-device-enp3s0f0-instance
        #     -dns-name-net17-nicole-munoz-marketing-already-used-on-network/15586/22?page=2
        subprocess.run(
            [
                "sudo",
                "lxc",
                "network",
                "set",
                DEFAULT_LXD_NETWORK,
                "dns.mode=none",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Error creating LXD network {DEFAULT_LXD_NETWORK} with: {e.returncode} {e.stderr}"
        )
        raise
    _lxd_network("client", "10.0.0.1/24", True)
    _lxd_network("cluster", "10.10.10.1/24", False)
    _lxd_network("backup", "10.20.20.1/24", False)


@pytest.fixture(scope="module")
def lxd_spaces(ops_test: OpsTest, lxd):
    subprocess.run(
        [
            "juju",
            "reload-spaces",
            f"--model={ops_test.model.name}",
        ],
    )
    spaces = [("client", "10.0.0.0/24"), ("cluster", "10.10.10.0/24"), ("backup", "10.20.20.0/24")]
    for space in spaces:
        subprocess.run(
            f"juju add-space --model={ops_test.model.name} {space[0]} {space[1]}".split(),
            check=True,
        )


@pytest.hookimpl()
def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("CI", "true").lower() == "true":
        # Nothing to do, as this is a temp runner only
        return

    def __exec(cmd):
        try:
            subprocess.run(cmd.split(), check=True)
        except subprocess.CalledProcessError as e:
            # Log and try to delete the next network
            logger.warning(f"Error deleting LXD network with: {e.returncode} {e.stderr}")

    for network in ["client", "cluster", "backup"]:
        __exec(f"sudo lxc network delete {network}")

    __exec(f"sudo lxc network unset {DEFAULT_LXD_NETWORK} dns.mode")

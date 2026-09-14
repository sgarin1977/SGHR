from ipaddress import (
    IPv4Address,
    IPv6Address,
    ip_address,
    ip_network,
)
from typing import Iterable


IpAddress = IPv4Address | IPv6Address


def resolve_client_ip(
    *,
    peer_ip: str,
    forwarded_for: str | None,
    trusted_proxy_networks: Iterable[str],
) -> str:
    try:
        peer = ip_address(
            str(peer_ip).strip()
        )
    except ValueError as exc:
        raise ValueError(
            "Request peer IP is invalid."
        ) from exc

    networks = []

    for value in trusted_proxy_networks:
        normalized = str(value).strip()
        if not normalized:
            continue

        try:
            networks.append(
                ip_network(
                    normalized,
                    strict=False,
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Trusted proxy network is invalid."
            ) from exc

    def is_trusted(address: IpAddress) -> bool:
        return any(
            (
                address.version == network.version
                and address in network
            )
            for network in networks
        )

    if not is_trusted(peer):
        return str(peer)

    if not isinstance(forwarded_for, str):
        return str(peer)

    values = [
        value.strip()
        for value in forwarded_for.split(",")
        if value.strip()
    ]

    if not values:
        return str(peer)

    try:
        forwarded_chain = [
            ip_address(value)
            for value in values
        ]
    except ValueError:
        # A malformed trusted-proxy header cannot
        # create a client-controlled rate-limit key.
        return str(peer)

    for candidate in reversed(
        forwarded_chain + [peer]
    ):
        if not is_trusted(candidate):
            return str(candidate)

    return str(forwarded_chain[0])

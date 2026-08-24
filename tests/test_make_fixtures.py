"""Tests für den synthetischen Fixture-Generator."""

from __future__ import annotations

from tests.make_fixtures import Pseudonymizer, clean_resource, pick_resources


def test_fixture_generator_preserves_nested_disk_parent_chain() -> None:
    resources = [
        {
            "id": "agent-real",
            "type": "agent",
            "name": "real-host",
            "displayName": "real-host",
            "canonicalIdentity": {"primaryId": "agent-canon", "aliases": []},
        },
        {
            "id": "member-real",
            "type": "storage",
            "parentId": "agent-real",
            "name": "real-member",
            "displayName": "real-member",
            "canonicalIdentity": {"primaryId": "member-canon", "aliases": []},
            "storage": {"type": "unraid-cache-pool"},
            "tags": ["none"],
            "disk": {"current": 95, "used": 950, "total": 1000, "free": 50},
        },
        {
            "id": "disk-real",
            "type": "physical_disk",
            "parentId": "member-real",
            "name": "real-disk",
            "displayName": "real-disk",
            "canonicalIdentity": {"primaryId": "disk-canon", "aliases": []},
            "physicalDisk": {"temperature": 37},
        },
    ]

    p = Pseudonymizer()
    cleaned = [clean_resource(resource, p) for resource in pick_resources(resources)]

    by_type = {resource["type"]: resource for resource in cleaned}

    assert set(by_type) == {"agent", "storage", "physical_disk"}
    assert by_type["physical_disk"]["physicalDisk"] == {"temperature": 37}
    assert by_type["physical_disk"]["parentId"] == by_type["storage"]["id"]
    assert by_type["storage"]["parentId"] == by_type["agent"]["id"]
    assert by_type["storage"]["storage"] == {"type": "unraid-cache-pool"}
    assert by_type["storage"]["tags"] == ["none"]
    assert "real" not in repr(cleaned)

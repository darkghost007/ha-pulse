"""Tests für den synthetischen Fixture-Generator."""

from __future__ import annotations

from tests.make_fixtures import Pseudonymizer, clean_alert, clean_resource, pick_resources


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
            "physicalDisk": {
                "temperature": 37,
                "health": "PASSED",
                "storageState": "online",
                "spunDown": False,
                "wearout": 73,
                "serial": "real-serial",
                "model": "real-model",
                "smart": {"powerOnHours": 1234},
            },
        },
    ]

    p = Pseudonymizer()
    cleaned = [clean_resource(resource, p) for resource in pick_resources(resources)]

    by_type = {resource["type"]: resource for resource in cleaned}

    assert set(by_type) == {"agent", "storage", "physical_disk"}
    assert by_type["physical_disk"]["physicalDisk"] == {
        "health": "PASSED",
        "smart": {"powerOnHours": 1234},
        "spunDown": False,
        "storageState": "online",
        "temperature": 37,
        "wearout": 73,
    }
    assert by_type["physical_disk"]["parentId"] == by_type["storage"]["id"]
    assert by_type["storage"]["parentId"] == by_type["agent"]["id"]
    assert by_type["storage"]["storage"] == {"type": "unraid-cache-pool"}
    assert by_type["storage"]["tags"] == ["none"]
    assert "real" not in repr(cleaned)
    assert "serial" not in repr(cleaned)
    assert "model" not in repr(cleaned)


def test_fixture_generator_keeps_docker_alert_shape_without_real_values() -> None:
    p = Pseudonymizer()
    resource = clean_resource(
        {
            "id": "container-resource",
            "type": "app-container",
            "name": "real-container",
            "displayName": "real-container",
            "canonicalIdentity": {"primaryId": "app-container:real-container-hash", "aliases": []},
            "docker": {
                "agentId": "real-agent-id",
                "containerId": "real-container-id",
                "health": "unhealthy",
            },
            "metricsTarget": {"resourceId": "real-metrics-id"},
        },
        p,
    )
    alert = clean_alert(
        {
            "id": "real-alert-id",
            "level": "critical",
            "type": "docker-container-health",
            "resourceId": "docker:real-agent-id/real-container-id",
            "resourceName": "real-container",
            "message": "private alert text",
            "startTime": "2026-08-24T10:00:00Z",
            "acknowledged": False,
        },
        p,
    )

    assert resource["docker"]["agentId"] in alert["resourceId"]
    assert resource["docker"]["containerId"] in alert["resourceId"]
    assert alert["resourceId"].startswith("docker:")
    assert alert["startTime"] == "2026-08-24T10:00:00Z"
    assert "real" not in repr([resource, alert])
    assert "private alert text" not in repr(alert)

"""Tests für Pulse-Normalisierung."""

from __future__ import annotations

import pytest

from custom_components.pulse.coordinator import normalize_memory_percent, normalize_percent, normalize_state, parse_pulse_time


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1, None),
        (-0.5, None),
        (0, 0.0),
        (0.1, 0.1),
        (100, 100.0),
        (None, None),
        ("n/a", None),
    ],
)
def test_disk_percent_sentinel(value, expected) -> None:
    assert normalize_percent(value) == expected


def test_fixture_resources_are_categorized_or_explicitly_ignored(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    categorized = (
        len(data.hosts)
        + len(data.guests)
        + len(data.containers)
        + len(data.storages)
        + len(data.physical_disks)
    )
    ignored = sum(data.ignored_types.values())

    assert categorized == 13
    assert ignored == 3
    assert categorized + ignored == len(fixture_state["resources"])
    assert set(data.ignored_types) == {"docker-image", "docker-network", "docker-volume"}


def test_fixture_counts_follow_live_schema(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)

    assert len(data.hosts) == 4
    assert len(data.guests) == 2
    assert len(data.containers) == 3
    assert len(data.storages) == 2
    assert len(data.physical_disks) == 2
    assert len(data.alerts) == 4
    # `degraded` ist erreichbar und zählt daher als online.
    assert data.summary.hosts_online == 4
    assert data.summary.hosts_offline == 0
    assert data.summary.vms_running == 2
    assert data.summary.containers_running == 2
    assert data.summary.containers_stopped == 1


def test_canonical_primary_id_is_identity_even_with_same_source_id() -> None:
    payload = {
        "resources": [
            {
                "id": "same-short-id",
                "type": "agent",
                "sourceType": "agent",
                "status": "online",
                "canonicalIdentity": {"primaryId": "source-a:same", "aliases": []},
            },
            {
                "id": "same-short-id",
                "type": "agent",
                "sourceType": "docker",
                "status": "online",
                "canonicalIdentity": {"primaryId": "source-b:same", "aliases": []},
            },
        ],
        "activeAlerts": [],
    }

    data = normalize_state(payload)

    assert set(data.hosts) == {"source-a:same", "source-b:same"}


def test_last_update_accepts_epoch_millis_and_iso() -> None:
    assert parse_pulse_time(1787520028513).isoformat() == "2026-08-23T21:20:28.513000+00:00"
    assert parse_pulse_time("2026-08-23T14:40:28Z").isoformat() == "2026-08-23T14:40:28+00:00"


def test_stopped_guest_metrics_are_unknown() -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "vm-1",
                    "type": "vm",
                    "status": "stopped",
                    "canonicalIdentity": {"primaryId": "vm:1", "aliases": []},
                    "cpu": {"current": 0.011},
                    "memory": {"current": 42, "used": 10, "total": 100, "free": 90},
                    "disk": {"current": 12},
                }
            ],
            "activeAlerts": [],
        }
    )

    guest = data.guests["vm:1"]
    assert guest.cpu_usage is None
    assert guest.memory_usage is None
    assert guest.storage_usage is None


def test_libvirt_memory_full_without_free_space_is_unknown() -> None:
    assert normalize_memory_percent({"current": 100, "used": 4096, "total": 4096, "free": 0}) is None
    assert normalize_memory_percent({"current": 75, "used": 3072, "total": 4096, "free": 1024}) == 75.0


def test_missing_active_alerts_is_stale_not_empty(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload.pop("activeAlerts")

    data = normalize_state(payload)

    assert data.alerts == []
    assert "alerts" in data.stale


def test_missing_primary_id_does_not_create_resource() -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "unstable-short-id",
                    "type": "agent",
                    "status": "online",
                    "canonicalIdentity": {"aliases": ["old"]},
                }
            ],
            "activeAlerts": [],
        }
    )

    assert data.hosts == {}
    assert data.ignored_types == {"agent": 1}
    assert "resources" in data.stale


def test_physical_disks_are_not_storage_capacity_resources(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)

    assert len(data.physical_disks) == 2
    assert all(disk.storage_usage is None for disk in data.physical_disks.values())
    assert all(disk.storage_used is None for disk in data.physical_disks.values())
    assert all(disk.storage_total is None for disk in data.physical_disks.values())
    assert set(data.physical_disks) <= data.removed_resource_ids


def test_unraid_storage_pools_are_detected_by_tag_not_usage() -> None:
    data = normalize_state(
        {
            "resources": [
                _storage("pool:zfs-empty", "res-zfs-empty", 0, 1000, "unraid-cache-pool", ["zfs"]),
                _storage("member:full", "res-member-full", 95, 1000, "unraid-cache-pool", ["none"]),
                _storage("pool:zero-total", "res-zero-total", 50, 0, "unraid-cache-pool", ["zfs"]),
                _storage("pool:generic", "res-generic", 10, 1000, "generic", ["none"]),
            ],
            "activeAlerts": [],
        }
    )

    assert set(data.storages) == {"pool:zfs-empty", "pool:generic"}
    assert data.ignored_types == {"storage": 2}
    assert data.removed_resource_ids == {"member:full", "pool:zero-total"}
    assert "resources" not in data.stale


def test_host_canonical_id_follows_skipped_intermediate_resources() -> None:
    data = normalize_state(
        {
            "resources": [
                _host("host:1", "res-host"),
                _storage("member:1", "res-member", 95, 1000, "unraid-cache-pool", ["none"])
                | {"parentId": "res-host"},
                {
                    "id": "res-disk",
                    "type": "physical_disk",
                    "parentId": "res-member",
                    "status": "online",
                    "canonicalIdentity": {"primaryId": "disk:1", "aliases": []},
                    "physicalDisk": {"temperature": 37},
                },
            ],
            "activeAlerts": [],
        }
    )

    assert data.physical_disks["disk:1"].parent_canonical_id == "member:1"
    assert data.physical_disks["disk:1"].host_canonical_id == "host:1"
    assert data.removed_resource_ids == {"member:1", "disk:1"}
    assert "resources" not in data.stale


def test_host_canonical_id_cycle_is_bounded() -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "res-a",
                    "type": "physical_disk",
                    "parentId": "res-b",
                    "status": "online",
                    "canonicalIdentity": {"primaryId": "disk:a", "aliases": []},
                },
                {
                    "id": "res-b",
                    "type": "storage",
                    "parentId": "res-a",
                    "status": "online",
                    "canonicalIdentity": {"primaryId": "storage:b", "aliases": []},
                    "disk": {"current": 1, "used": 1, "total": 100, "free": 99},
                },
            ],
            "activeAlerts": [],
        }
    )

    assert data.physical_disks["disk:a"].host_canonical_id is None
    assert data.storages["storage:b"].host_canonical_id is None
    assert "resources" not in data.stale


def test_unparseable_entity_resource_inside_valid_list_marks_resources_stale() -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "res-1",
                    "type": "agent",
                    "status": "online",
                    "canonicalIdentity": {"primaryId": "host:1", "aliases": []},
                },
                {
                    "id": "res-2",
                    "type": "agent",
                    "status": "offline",
                    "canonicalIdentity": {"aliases": []},
                },
            ],
            "activeAlerts": [],
        }
    )

    assert set(data.hosts) == {"host:1"}
    assert "resources" in data.stale


def test_ignored_docker_noise_does_not_mark_resources_stale() -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "res-1",
                    "type": "docker-volume",
                    "status": "online",
                }
            ],
            "activeAlerts": [],
        }
    )

    assert data.ignored_types == {"docker-volume": 1}
    assert "resources" not in data.stale


def test_unparseable_alert_inside_valid_list_marks_alerts_stale() -> None:
    data = normalize_state(
        {
            "resources": [],
            "activeAlerts": [
                {"id": "alert-1", "level": "warning"},
                "not-an-alert",
            ],
        }
    )

    assert len(data.alerts) == 1
    assert "alerts" in data.stale


def test_degraded_host_is_online_but_not_healthy() -> None:
    """`degraded` bedeutet erreichbar mit Warnung — nicht offline.

    Ein connectivity-Sensor, der einen erreichbaren Host als offline meldet,
    ist schlicht falsch; die Warnung gehört in den Gesundheits-Aggregatsensor.
    """

    def _host(status: str) -> dict:
        return {
            "id": f"agent-{status}",
            "type": "agent",
            "name": status,
            "status": status,
            "canonicalIdentity": {"primaryId": f"agent:{status}", "aliases": []},
        }

    data = normalize_state({"resources": [_host("degraded"), _host("offline"), _host("online")]})

    degraded = data.hosts["agent:degraded"]
    assert degraded.is_host_online is True
    assert degraded.is_host_healthy is False

    assert data.hosts["agent:offline"].is_host_online is False
    assert data.hosts["agent:online"].is_host_healthy is True
    assert data.summary.hosts_online == 2
    assert data.summary.hosts_offline == 1


def _storage(
    primary_id: str,
    resource_id: str,
    current: float,
    total: int,
    storage_type: str,
    tags: list[str],
) -> dict:
    return {
        "id": resource_id,
        "type": "storage",
        "status": "online",
        "storage": {"type": storage_type},
        "tags": tags,
        "canonicalIdentity": {"primaryId": primary_id, "aliases": []},
        "disk": {"current": current, "used": 1, "total": total, "free": max(total - 1, 0)},
    }


def _host(primary_id: str, resource_id: str) -> dict:
    return {
        "id": resource_id,
        "type": "agent",
        "status": "online",
        "canonicalIdentity": {"primaryId": primary_id, "aliases": []},
    }

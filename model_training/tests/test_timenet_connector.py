from robot_observability.timenet_connector import CONNECTOR


def test_connector_metadata_matches_exported_id() -> None:
    connector = CONNECTOR()
    metadata = connector.metadata()
    assert metadata.dataset_id == "ysamet/robot-collision-observability"
    assert str(metadata.dataset_version) == "1.0.0"

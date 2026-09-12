"""Part II identity for the shared KUKA raw-run TimeF connector."""

from ..kuka_collision.connector import KukaPartConnector


class KukaContactPart2Connector(KukaPartConnector):
    """Convert local KUKA Part II intentional-contact runs into TimeF records."""

    ROOT_ENV = "KUKA_PART2_ROOT"
    PART_LABEL = "Part II"
    RECORD_PREFIX = "kuka-part2"
    EVENT_KEY = "intentional_contact"
    EVENT_LABEL = "intentional_contact"
    EXPERIMENT_NAME = "KUKA LWR4+ intentional-contact experiment"


CONNECTOR = KukaContactPart2Connector

"""Read-only hardware coverage for the native local-backup stream."""
import base64

import pytest


@pytest.mark.verifies("create_local_backup")
def test_local_backup_is_a_complete_portable_document(qc, record_property):
    document = qc.create_local_backup()

    assert document["type"] == "backup"
    assert document["creator"] == "quad"
    assert isinstance(document["name"], str) and document["name"]
    assert len(document["payload_hash"]) == 64
    assert all(c in "0123456789abcdefABCDEF" for c in document["payload_hash"])
    payload = base64.b64decode(document["payload"], validate=True)
    assert payload

    record_property("local_backup_payload_bytes", len(payload))

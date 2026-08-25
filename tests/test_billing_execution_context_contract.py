import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_billing.schemas import BillingExecutionContextV1, DebitPayload


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "billing_execution_context_v1.json"
)


def test_cross_contract_fixture_matches_findesk_canonical_wire_shape():
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)

    assert data["actor_user_id"] == data["billing_execution_context"][
        "actor_user_id"
    ]
    assert data["organization_id"] == data["billing_execution_context"][
        "fop_organization_id"
    ]
    assert set(data["billing_execution_context"]) == {
        "version",
        "actor_user_id",
        "fop_organization_id",
        "source",
        "revision",
        "context_id",
    }

    payload = DebitPayload.model_validate_json(raw)
    context = payload.billing_execution_context
    assert context is not None
    assert context.version == 1
    assert context.source == "fop_limits_schedule"
    assert context.context_id == "fop-limits-schedule:organization:42"


@pytest.mark.parametrize(
    "source",
    [
        "documents_upload",
        "documents_reprocess",
        "documents_refine",
        "staff_doc_http",
        "staff_doc_processing",
        "staff_doc_websocket",
    ],
)
def test_findesk_document_sources_are_part_of_canonical_v1_contract(source):
    context = BillingExecutionContextV1(
        actor_user_id=17,
        fop_organization_id=42,
        source=source,
        revision=1,
        context_id=f"{source}:42:17",
    )

    assert context.source == source


def test_unknown_findesk_source_is_rejected():
    with pytest.raises(ValidationError):
        BillingExecutionContextV1(
            actor_user_id=17,
            fop_organization_id=42,
            source="client_supplied_source",
            revision=1,
            context_id="client:42:17",
        )

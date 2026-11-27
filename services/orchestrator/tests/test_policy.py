"""Policy engine and approval service (Python layer above the SQL ledger)."""
import pytest

from lanekeeper import auth, db
from lanekeeper.policy import approvals
from lanekeeper.policy.engine import PolicyDenied, SideEffect, check, invalidate_matrix_cache

from conftest import HARBOR, NYC, USERS

pytestmark = pytest.mark.db

PAYLOAD = {"robot_id": "LK-01-A", "route_cell_ids": ["MN-1AV-0040", "MN-1AV-0041"], "simulation_mode": True}


def P(key):
    return auth.Principal(user_id=USERS[key], memberships=auth.load_memberships(USERS[key]))


@pytest.fixture(autouse=True)
def _clean(fresh_db):
    invalidate_matrix_cache()
    db.one("update municipal_memberships set status='active', revoked_at=null, valid_until=null returning 1")
    yield
    db.one("update municipal_memberships set status='active', revoked_at=null, valid_until=null returning 1")


def new_request(**kw):
    with db.tx() as cur:
        return approvals.request(cur, jurisdiction_id=NYC, action="dispatch_simulated_mission", target_kind="mission_proposal",
                                 target_id="p-test", payload=kw.get("payload", PAYLOAD), requested_by="agent:test",
                                 requested_by_user=kw.get("requested_by_user"))


def test_permissions_follow_membership_matrix():
    assert check(P("nyc_supervisor"), NYC, "mission.approve").allowed
    assert not check(P("nyc_operator"), NYC, "mission.approve").allowed
    assert not check(P("nyc_viewer"), NYC, "evidence.view").allowed
    assert not check(P("harbor_supervisor"), NYC, "ticket.view").allowed
    assert not check(auth.ANONYMOUS, NYC, "ticket.view").allowed
    assert check(auth.ANONYMOUS, None, "report.create").allowed


def test_physical_side_effect_always_denied_even_for_admins():
    for who in ("nyc_admin", "nyc_supervisor"):
        assert not check(P(who), NYC, "fleet.stop", SideEffect.PHYSICAL).allowed
        assert not check(P(who), NYC, "robot.command_real").allowed


def test_dev_token_carries_identity_not_roles():
    tok = auth.mint_dev_token(USERS["rider"])
    p = auth.principal_from_token(tok)
    assert p.user_id == USERS["rider"] and p.memberships == ()


def test_forged_or_tampered_token_rejected():
    tok = auth.mint_dev_token(USERS["nyc_supervisor"])
    with pytest.raises(auth.AuthError):
        auth.principal_from_token(tok[:-2] + ("AA" if tok[-2:] != "AA" else "BB"))
    import jwt
    forged = jwt.encode({"sub": USERS["nyc_supervisor"], "aud": "authenticated", "iss": "lanekeeper-dev"}, "wrong-secret")
    with pytest.raises(auth.AuthError):
        auth.principal_from_token(forged)


def test_full_approve_revalidate_consume_cycle():
    ref = new_request()
    approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "constraints reviewed")
    a = approvals.revalidate(ref.approval_id, expected_hash=ref.payload_hash, jurisdiction_id=NYC,
                             action="dispatch_simulated_mission")
    assert a["status"] == "approved"
    with db.tx() as cur:
        approvals.consume(cur, ref.approval_id, operation_id=ref.operation_id, expected_hash=ref.payload_hash,
                          jurisdiction_id=NYC, action="dispatch_simulated_mission", invocation_id=None)
    with pytest.raises(approvals.ApprovalError, match="consumed"):
        approvals.revalidate(ref.approval_id, expected_hash=ref.payload_hash, jurisdiction_id=NYC,
                             action="dispatch_simulated_mission")


def test_operator_cannot_approve_and_attempt_is_audited():
    ref = new_request()
    with pytest.raises(approvals.ApprovalError):
        approvals.decide(P("nyc_operator"), ref.approval_id, True, "I want to")
    row = db.one("select outcome from audit_log where target = %s order by id desc limit 1", (ref.approval_id,))
    assert row["outcome"] == "denied"


def test_other_city_supervisor_cannot_approve():
    ref = new_request()
    with pytest.raises(approvals.ApprovalError, match="no active membership"):
        approvals.decide(P("harbor_supervisor"), ref.approval_id, True, "cross-tenant attempt")


def test_requester_cannot_approve_own_request():
    ref = new_request(requested_by_user=USERS["nyc_supervisor"])
    with pytest.raises(approvals.ApprovalError, match="separation of duties"):
        approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "self approve")
    approvals.decide(P("nyc_supervisor2"), ref.approval_id, True, "second supervisor approves")



def test_expired_approval_is_marked_expired_on_decide():
    ref = new_request()
    db.one("update approval_requests set expires_at = now() - interval '1 minute' where id=%s returning 1", (ref.approval_id,))
    with pytest.raises(approvals.ApprovalError, match="expired"):
        approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "too late")
    assert db.one("select status from approval_requests where id=%s", (ref.approval_id,))["status"] == "expired"

def test_denied_approval_never_revalidates():
    ref = new_request()
    approvals.decide(P("nyc_supervisor"), ref.approval_id, False, "route crosses closure")
    with pytest.raises(approvals.ApprovalError, match="denied"):
        approvals.revalidate(ref.approval_id, expected_hash=ref.payload_hash, jurisdiction_id=NYC,
                             action="dispatch_simulated_mission")


def test_modified_payload_fails_revalidation():
    ref = new_request()
    approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "constraints reviewed")
    from lanekeeper.hashing import payload_hash
    changed = payload_hash({**PAYLOAD, "robot_id": "LK-01-B"})
    with pytest.raises(approvals.ApprovalError, match="payload changed"):
        approvals.revalidate(ref.approval_id, expected_hash=changed, jurisdiction_id=NYC, action="dispatch_simulated_mission")


def test_revoked_approver_fails_revalidation_before_any_effect():
    ref = new_request()
    approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "constraints reviewed")
    db.one("update municipal_memberships set status='revoked' where user_id=%s returning 1", (USERS["nyc_supervisor"],))
    with pytest.raises(approvals.ApprovalError, match="no longer holds"):
        approvals.revalidate(ref.approval_id, expected_hash=ref.payload_hash, jurisdiction_id=NYC,
                             action="dispatch_simulated_mission")


def test_tampered_signature_fails_revalidation():
    ref = new_request()
    approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "constraints reviewed")
    db.one("update approval_requests set signature = repeat('0', 64) where id=%s returning 1", (ref.approval_id,))
    with pytest.raises(approvals.ApprovalError, match="signature"):
        approvals.revalidate(ref.approval_id, expected_hash=ref.payload_hash, jurisdiction_id=NYC,
                             action="dispatch_simulated_mission")


def test_real_dispatch_cannot_even_be_requested():
    with pytest.raises(approvals.ApprovalError, match="simulation"):
        new_request(payload={**PAYLOAD, "simulation_mode": False})


def test_reason_required():
    ref = new_request()
    with pytest.raises(approvals.ApprovalError, match="reason"):
        approvals.decide(P("nyc_supervisor"), ref.approval_id, True, "")


def test_cross_tenant_policy_denied_raises():
    with pytest.raises(PolicyDenied):
        check(P("harbor_admin"), NYC, "ticket.view").raise_if_denied()
    assert check(P("harbor_admin"), HARBOR, "ticket.view").allowed

"""Row-level security: tenant isolation, role scoping, forged claims, revocation. Runs against real Postgres."""
import pytest

from conftest import HARBOR, NYC, USERS, as_user

pytestmark = pytest.mark.db


@pytest.fixture
def two_tenant_data(admin):
    with admin.cursor() as cur:
        cur.execute("""
          insert into hazard_reports (id, jurisdiction_id, source_kind, idempotency_key, raw_location, accuracy_m, hazard_claimed, cell_id)
          values ('aaaaaaaa-0000-4000-8000-000000000001', %s, 'rider_app', 'rls-test-nyc-1',
                  extensions.st_geogfromtext('POINT(-73.978 40.737)'), 8, 'glass', 'MN-1AV-0040'),
                 ('aaaaaaaa-0000-4000-8000-000000000002', %s, 'rider_app', 'rls-test-hc-1',
                  extensions.st_geogfromtext('POINT(-74.085 40.615)'), 8, 'glass', 'HC-HAV-0030')""", (NYC, HARBOR))
        cur.execute("""
          insert into tickets (id, jurisdiction_id, hazard_type, severity, priority, created_by)
          values ('bbbbbbbb-0000-4000-8000-000000000001', %s, 'glass', 'high', 2, 'test'),
                 ('bbbbbbbb-0000-4000-8000-000000000002', %s, 'glass', 'high', 2, 'test')""", (NYC, HARBOR))
    return admin


def count(cur, sql, *args):
    cur.execute(sql, args)
    return cur.fetchone()[0]


def test_anon_cannot_read_tickets_reports_or_consent(two_tenant_data):
    with as_user(two_tenant_data, role="anon") as cur:
        assert count(cur, "select count(*) from tickets") == 0
        assert count(cur, "select count(*) from hazard_reports") == 0
        with pytest.raises(Exception, match="permission denied"):
            cur.execute("select * from report_consent")


def test_anon_reads_only_generalized_public_map(two_tenant_data):
    with as_user(two_tenant_data, role="anon") as cur:
        cur.execute("select * from public_hazard_cells limit 1")
        cols = {d.name for d in cur.description}
        assert cols == {"cell_id", "jurisdiction_id", "condition", "updated_day", "centroid", "provenance"}
        assert count(cur, "select count(*) from public_hazard_cells") > 0


def test_supervisor_sees_only_own_city(two_tenant_data):
    with as_user(two_tenant_data, "nyc_supervisor") as cur:
        cur.execute("select jurisdiction_id::text from tickets")
        assert {r[0] for r in cur.fetchall()} == {NYC}
        cur.execute("select jurisdiction_id::text from hazard_reports")
        assert {r[0] for r in cur.fetchall()} == {NYC}
    with as_user(two_tenant_data, "harbor_supervisor") as cur:
        cur.execute("select jurisdiction_id::text from tickets")
        assert {r[0] for r in cur.fetchall()} == {HARBOR}


def test_cross_tenant_admin_cannot_read_other_city_evidence_or_knowledge(two_tenant_data):
    with as_user(two_tenant_data, "harbor_admin") as cur:
        assert count(cur, "select count(*) from hazard_reports where jurisdiction_id = %s", NYC) == 0
        assert count(cur, "select count(*) from knowledge_chunks where jurisdiction_id = %s", NYC) == 0
        assert count(cur, "select count(*) from robot_status where jurisdiction_id = %s", NYC) == 0
    with as_user(two_tenant_data, "nyc_admin") as cur:
        assert count(cur, "select count(*) from knowledge_chunks where jurisdiction_id = %s", HARBOR) == 0


def test_forged_role_and_tenant_claims_grant_nothing(two_tenant_data):
    forged = {"app_role": "city_admin", "tenant_id": NYC, "city_id": NYC, "user_role": "platform_admin"}
    with as_user(two_tenant_data, "rider", claims_extra=forged) as cur:
        assert count(cur, "select count(*) from tickets") == 0
        assert count(cur, "select count(*) from hazard_reports") == 0
    # a JWT claiming service_role while connected as authenticated still gets no rows
    with as_user(two_tenant_data, "rider", claims_extra={"role": "service_role"}) as cur:
        assert count(cur, "select count(*) from tickets") == 0


def test_viewer_cannot_see_evidence_but_can_see_tickets(two_tenant_data):
    with as_user(two_tenant_data, "nyc_viewer") as cur:
        assert count(cur, "select count(*) from tickets") == 1
        assert count(cur, "select count(*) from hazard_reports") == 0


def test_platform_support_cannot_read_tenant_evidence(two_tenant_data):
    with as_user(two_tenant_data, "platform") as cur:
        assert count(cur, "select count(*) from hazard_reports") == 0
        assert count(cur, "select count(*) from tickets") == 0


def test_revoked_membership_loses_access_immediately(two_tenant_data):
    with two_tenant_data.cursor() as cur:
        cur.execute("update municipal_memberships set status='revoked', revoked_at=now() where user_id=%s",
                    (USERS["nyc_supervisor"],))
    with as_user(two_tenant_data, "nyc_supervisor") as cur:
        assert count(cur, "select count(*) from tickets") == 0


def test_expired_membership_loses_access(two_tenant_data):
    with two_tenant_data.cursor() as cur:
        cur.execute("update municipal_memberships set valid_until=now() - interval '1 minute' where user_id=%s",
                    (USERS["nyc_operator"],))
    with as_user(two_tenant_data, "nyc_operator") as cur:
        assert count(cur, "select count(*) from tickets") == 0


def test_clients_cannot_write_tickets_or_approvals(two_tenant_data):
    with as_user(two_tenant_data, "nyc_supervisor") as cur:
        cur.execute("update tickets set status='dismissed'")
        assert cur.rowcount == 0  # RLS: no update policy -> zero rows
        cur.execute("update approval_requests set status='approved'")
        assert cur.rowcount == 0


def test_reporter_reads_own_reports_only(two_tenant_data):
    with two_tenant_data.cursor() as cur:
        cur.execute("update hazard_reports set reporter_user_id=%s where id='aaaaaaaa-0000-4000-8000-000000000001'",
                    (USERS["rider"],))
    with as_user(two_tenant_data, "rider") as cur:
        cur.execute("select id::text from hazard_reports")
        assert [r[0] for r in cur.fetchall()] == ["aaaaaaaa-0000-4000-8000-000000000001"]


def test_unapproved_and_other_tenant_docs_excluded_from_hybrid_search(two_tenant_data):
    with two_tenant_data.cursor() as cur:
        cur.execute("select title from lk.hybrid_search_knowledge(%s, 'approve mission escalation', null, 20)", (NYC,))
        titles = [r[0] for r in cur.fetchall()]
    assert not any("Harbor" in t for t in titles)
    assert not any("NOT APPROVED" in t for t in titles)


def test_client_roles_cannot_call_privileged_functions(two_tenant_data):
    with as_user(two_tenant_data, "nyc_supervisor") as cur:
        with pytest.raises(Exception, match="permission denied"):
            cur.execute("select lk.consume_approval(gen_random_uuid(), gen_random_uuid(), 'x', %s, 'x', null)", (NYC,))
    with as_user(two_tenant_data, "nyc_supervisor") as cur:
        with pytest.raises(Exception, match="permission denied"):
            cur.execute("select * from lk.lease_jobs('w')")


def test_checkpoint_schema_unreachable_from_clients(two_tenant_data):
    with as_user(two_tenant_data, "nyc_admin") as cur:
        cur.execute("select has_schema_privilege('lk_checkpoints', 'usage')")
        assert cur.fetchone()[0] is False

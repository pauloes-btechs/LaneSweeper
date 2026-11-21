"""SQL functions: spatial matching, job leasing/retry/dead-letter, hybrid search ranking contributions."""
import pytest

from conftest import NYC

pytestmark = pytest.mark.db


def test_point_on_first_ave_matches_nearest_cell(admin):
    with admin.cursor() as cur:
        cur.execute("""select cell_id, distance_m, search_radius_m from lk.match_lane_cells(40.7449, -73.9727, 8, %s)""", (NYC,))
        rows = cur.fetchall()
    assert rows, "expected a cell match on 1st Ave"
    assert rows[0][1] < 5
    assert rows[0][2] == 30  # base radius used when GPS accuracy is better than it


def test_point_far_from_lanes_matches_nothing(admin):
    with admin.cursor() as cur:
        cur.execute("select count(*) from lk.match_lane_cells(40.7580, -73.9855, 10, %s)", (NYC,))  # Times Square
        assert cur.fetchone()[0] == 0


def test_poor_accuracy_widens_radius_but_is_capped(admin):
    with admin.cursor() as cur:
        cur.execute("select max(search_radius_m) from lk.match_lane_cells(40.7449, -73.9727, 500, %s)", (NYC,))
        assert cur.fetchone()[0] == 75


def test_match_is_tenant_scoped(admin):
    with admin.cursor() as cur:
        cur.execute("select count(*) from lk.match_lane_cells(40.7449, -73.9727, 8, '22222222-2222-4222-8222-222222222222')")
        assert cur.fetchone()[0] == 0


def test_job_lease_retry_and_dead_letter(admin):
    with admin.cursor() as cur:
        cur.execute("insert into jobs (kind, dedupe_key, max_attempts) values ('t', 'job-1', 2) returning id")
        jid = cur.fetchone()[0]
        cur.execute("select id from lk.lease_jobs('w1', 5, 60, '{t}')")
        assert [r[0] for r in cur.fetchall()] == [jid]
        cur.execute("insert into job_attempts (job_id, attempt, worker) values (%s, 1, 'w1')", (jid,))
        cur.execute("select lk.finish_job(%s, 'w1', false, 'boom')", (jid,))
        assert cur.fetchone()[0] == "retry_wait"
        cur.execute("update jobs set run_after = now() where id = %s", (jid,))
        cur.execute("select id from lk.lease_jobs('w2', 5, 60, '{t}')")
        assert cur.fetchall()
        cur.execute("select lk.finish_job(%s, 'w2', false, 'boom again')", (jid,))
        assert cur.fetchone()[0] == "dead_letter"


def test_expired_lease_is_reclaimed_not_lost(admin):
    with admin.cursor() as cur:
        cur.execute("insert into jobs (kind, dedupe_key) values ('t', 'job-2') returning id")
        jid = cur.fetchone()[0]
        cur.execute("select id from lk.lease_jobs('dead-worker', 1, 60, '{t}')")
        cur.execute("update jobs set lease_expires_at = now() - interval '1 second' where id = %s", (jid,))
        cur.execute("select id, leased_by from lk.lease_jobs('w3', 1, 60, '{t}')")
        assert cur.fetchone() == (jid, "w3")


def test_duplicate_business_event_cannot_enqueue_twice(admin):
    import psycopg
    with admin.cursor() as cur:
        cur.execute("insert into jobs (kind, dedupe_key) values ('t', 'evt-123')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("insert into jobs (kind, dedupe_key) values ('t', 'evt-123')")


def test_hybrid_search_reports_both_rank_contributions(admin):
    with admin.cursor() as cur:
        vec = "[" + ",".join(["0.01"] * 768) + "]"
        cur.execute("update knowledge_chunks set embedding = %s::extensions.vector, embed_model='test', embed_dim=768, embed_version='t'", (vec,))
        cur.execute("""select title, text_rank, vector_rank, fused from lk.hybrid_search_knowledge(%s, 'robot stops cyclist', %s::extensions.vector, 5)""",
                    (NYC, vec))
        rows = cur.fetchall()
    assert rows
    top = rows[0]
    assert "stop" in top[0].lower()
    assert top[1] is not None and top[2] is not None and top[3] > 0

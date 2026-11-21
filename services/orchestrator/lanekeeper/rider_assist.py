"""Read-only help for the rider's device agent before it asks the rider to approve a drafted report.

The agent on the phone/glasses drafts a report (snapshots, location, hazard) on its own. Before prompting the rider it
calls `preview` to learn where the report would land and whether the hazard is already reported, so the prompt can
say "Possible glass on 1st Ave — already reported as LK-101; add your confirmation?". Nothing is written here.
"""
from __future__ import annotations

from . import db

NEARBY_M = 40.0


def preview(lat: float, lng: float, accuracy_m: float | None, hazard: str | None) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("location out of range")
    if accuracy_m is not None and not 0 <= accuracy_m <= 5000:
        raise ValueError("accuracy out of range")
    cell = db.one("""select m.cell_id, m.distance_m, m.search_radius_m, m.jurisdiction_id::text jid,
                            s.street, s.facility, s.borough, j.name jurisdiction
                       from lk.match_lane_cells(%s, %s, %s) m
                       join lane_cells c on c.id = m.cell_id
                       left join lane_segments s on s.id = c.segment_id
                       join jurisdictions j on j.id = m.jurisdiction_id
                      where m.rank = 1""", (lat, lng, accuracy_m))
    if not cell:
        return {"in_network": False, "cell_id": None, "place": None, "nearby_open": None,
                "advice": "Not on a mapped protected lane here. You can still submit; a city operator will review the location."}
    place = ", ".join(x for x in [f"{cell['street']} ({cell['facility']} lane)" if cell["street"] else None, cell["borough"]] if x)
    near = db.one("""select t.number, i.hazard_type, i.report_count,
                            round(extensions.st_distance(c.geom,
                                  extensions.st_setsrid(extensions.st_makepoint(%s, %s), 4326)::extensions.geography)::numeric, 0)::float8 distance_m
                       from hazard_incidents i
                       join lane_cells c on c.id = i.cell_id
                       join tickets t on t.incident_id = i.id
                      where i.jurisdiction_id = %s::uuid and i.status in ('open', 'monitoring')
                        and (%s::text is null or i.hazard_type = %s)
                        and extensions.st_dwithin(c.geom, extensions.st_setsrid(extensions.st_makepoint(%s, %s), 4326)::extensions.geography, %s)
                      order by distance_m limit 1""",
               (lng, lat, cell["jid"], hazard, hazard, lng, lat, NEARBY_M))
    advice = (f"Already reported as {near['number']} ({near['report_count']} report{'s' if near['report_count'] != 1 else ''}). "
              "Submitting adds your confirmation to that ticket." if near
              else "No open report here yet. Submitting creates a new report for city review.")
    return {"in_network": True, "cell_id": cell["cell_id"], "distance_m": round(cell["distance_m"], 1), "place": place,
            "jurisdiction": cell["jurisdiction"],
            "nearby_open": ({"ticket_number": near["number"], "hazard_type": near["hazard_type"],
                             "report_count": near["report_count"], "distance_m": near["distance_m"]} if near else None),
            "advice": advice}

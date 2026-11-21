# Market fit: LaneKeeper OS

**Date of research: 2026-11-21.** Web sources were checked on that date. Statements are labelled **[sourced]** (with a link), **[assumption]** (ours, unverified), or **[demo]** (measured on synthetic data in this repo). Nothing here comes from interviews with NYC staff or riders. That research has not been done yet and is the first pilot task.

## 1. NYC today: who keeps bike lanes clean

| Fact | Source |
|---|---|
| DSNY's mechanical brooms sweep along curbs on thousands of street miles, and DSNY states this includes narrow protected bike lanes. **[sourced]** | DSNY "Street Cleaning" page on nyc.gov (exact URL not captured; search result only) |
| In April 2022 the city committed $11M for year-round street cleaning, including equipment and personnel to sweep protected bike lanes and other narrow infrastructure. **[sourced]** | [NYC Mayor's Office, 2022-04-18](https://www.nyc.gov/mayors-office/news/2022/04/mayor-adams-sanitation-commissioner-tisch-11-million-commitment-new-street-cleaning) |
| In July 2022 DSNY put six compact sweepers into service to cover all 133.5 miles of protected lanes once a week, with $6.7M for equipment and $2.8M for staff. **[sourced]** Four more units were planned. **[sourced]** | [amNY](https://www.amny.com/news/smaller-street-sweepers-to-clear-bike-lanes), [Time Out](https://www.timeout.com/newyork/news/these-pint-sized-street-sweepers-will-clean-nycs-bike-lanes-weekly-070722) |
| DOT maintains most of the 1,550+ lane-mile cycling network. **[sourced]** DOT lists 555 protected bike lanes in the network. **[sourced]** | [DOT MMR 2025](https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/dot.pdf), [DOT bike stats](https://www.nyc.gov/html/dot/html/bicyclists/bikestats.shtml) |
| The city installed 18.2 protected bike lane miles in 2025 against a legal benchmark of 50. **[sourced]** | [NYC Streets Plan update](https://www.nyc.gov/html/dot/downloads/pdf/nyc-streets-plan-update.pdf) |
| Riders can report trash or debris in a driving or **biking** lane through 311, including broken glass. **[sourced]** | [NYC311 KA-02356](https://portal.311.nyc.gov/article/?kanumber=KA-02356) |

**Reading:** NYC already funds and runs a dedicated, weekly program for protected lanes. LaneKeeper must not pitch "nobody cleans bike lanes." The defensible gap is between weekly scheduled sweeps and hazards that appear *between* them, such as broken glass, plus the lack of **verifiable closure** that a specific hazard was removed. Whether that gap is large enough to fund is unknown (see §8).

## 2. How the problem shows up for riders

- **[sourced, anecdotal]** Cycling coverage has documented debris and glass building up in protected lanes, including volunteer cleanups and Council members urging DSNY to buy narrow sweepers ([BK Reader 2021](https://bkreader.com/2021/03/10/during-bike-lane-cleanup-street-riders-nyc-ask-where-are-city-officials/)). Glass has also been deliberately scattered on new Village lanes ([CBS New York](https://www.cbsnews.com/newyork/news/bicycle-lane-backlash-shards-of-glass-west-village-east-village)).
- **[sourced, anecdotal, outside NYC]** Riders in other US cities describe repeated flats and swerving into traffic to avoid glass and debris ([Boston.com readers](https://www.boston.com/community/readers-say/how-readers-feel-about-bike-safety-in-boston/), [Bend Bulletin](https://bendbulletin.com/?p=669706)).
- **[not yet measured]** We found no published NYC data on flats per rider-hour, glass-specific 311 volume in bike lanes, or time from report to removal. The 311 open dataset should allow a debris-in-bike-lane extract. This is pilot week-1 work.

Observed versus proposed: the anecdotes show the problem exists. They don't show its frequency, and they don't show that riders would report it more often if reporting were easier. Both are hypotheses.

## 3. Competitors and substitutes

| Category | Examples | What they do | Relation to LaneKeeper |
|---|---|---|---|
| Human crews and mini-sweepers | DSNY bike-lane sweepers | Scheduled weekly sweeps **[sourced]** | The incumbent. LaneKeeper should route work *to* them, not replace them. |
| Autonomous sweepers | Trombia Free (Finland); WeRide Robosweeper (China, Abu Dhabi); Bucher CityCat 2020ev | Porvoo, Finland buys a monthly **sweeping service** from Trombia rather than owning the robot **[sourced]** ([Kurrant](https://kurrant.com/?p=47542)). WeRide runs public-road robosweeper pilots **[sourced]** ([NVIDIA blog](https://blogs.nvidia.com/blog/weride-autonomous-street-sweepers)). Abu Dhabi deployed them on the Corniche in Oct 2025 **[sourced]** ([dubizzle](https://www.dubizzle.com/blog/cars/autonomous-cleaning-vehicles-abu-dhabi/)). The Port Authority of NY & NJ trialled an autonomous sweeper in 2023 **[sourced]** ([National Academies](https://www.nationalacademies.org/read/27903/chapter/15)). | Hardware exists elsewhere. LaneKeeper's robot layer should be **vendor-neutral**, not a homemade LK-01. |
| Machine-vision road inspection | vialytics, Cyvl, RoadBotics (Michelin) | Smartphone or vehicle cameras grade pavement and assets. vialytics reports 1,000+ municipalities and an OMNIA Partners cooperative-purchasing contract **[sourced]** ([PR Newswire via Webull](https://www.webull.com/news/15911162662416384)). Lane-marking damage detection has been added **[sourced]** ([vialytics](https://www.mynewsdesk.com/de/vialytics/pressreleases/vialytics-launches-ai-detection-for-damaged-lane-markings-to-help-cities-prioritize-repainting-3435363)). | The closest software substitute. They're strong on pavement and assets, but we found no evidence of transient debris detection or rider-sourced reports. They could also be **partners**. |
| 311 / CRM platforms | NYC311, CivicPlus 311 CRM (formerly SeeClickFix), Salesforce, Accela, Esri | Intake, routing, status **[sourced]** ([CivicPlus](https://seeclickfix.com/bb)) | LaneKeeper must **integrate**, not compete. NYC311 is the system of record. |
| GIS | Esri ArcGIS, LION / CSCL | Street centrelines and assets | Source of truth for lane geometry. LaneKeeper's lane cells must be derived from it. |

## 4. First buyer, ranked

| Rank | Buyer | Budget owner | Sales cycle | Permit barrier | Measurable pilot value | Why |
|---|---|---|---|---|---|---|
| 1 | **Business Improvement District** with protected lanes | BID sanitation budget. NYC BIDs invested more than $216M in FY2025 **[sourced]** ([SBS](https://www.nyc.gov/site/sbs/about/pr20260518-bidday2026.page)), and sanitation is a core BID service | Months, not years | Low for software. BIDs already run supplemental sanitation crews | Time-to-removal inside the district | Short cycle, existing crews, visible outcomes |
| 2 | Campus or private right-of-way (university, park conservancy, Hudson River Park-style operator) | Facilities | Months | Private paths avoid some public-roadway rules **[assumption]** | Verified-clear rate | Controlled site. The only realistic place for an early robot trial **[assumption]** |
| 3 | Bike-share operator (Citi Bike / Lyft) | Operations | 6–12 months **[assumption]** | None for software | Flats and damage per 1,000 trips near hazards | Fleet damage cost is a direct pain **[assumption]** |
| 4 | NYC DOT / DSNY | City budget | 12–24+ months, procurement **[assumption]** | High (POST-style surveillance scrutiny, records, unions) | Citywide time-to-resolution | Biggest prize, slowest path. Needs a BID or campus reference first. |

## 5. Software first, before any robot

Yes, as a hypothesis worth testing. The value chain *report → verify → dedupe → route to an existing crew → verify removal → publish* works with **human crews only**. The robot branch is an optional executor. A pilot can prove or disprove value with these data, each with a named denominator:

1. Hazards reported per rider-hour or per lane-mile-week (opt-in pilot riders).
2. Share confirmed by a crew on arrival (detection precision in the field).
3. Median time from report to crew removal, against the 311 baseline for the same blocks.
4. Share of closures with after-evidence (verifiable closure).
5. Duplicate-link accuracy against a crew-labelled sample.

## 6. Legal, privacy and right-of-way

- **Robots on NYC streets are not authorised by simulating them.** In 2019 NYC ordered FedEx's delivery robot off the city's streets and sidewalks, citing vehicle and traffic law. **[sourced]** ([TechSpot](https://www.techspot.com/news/82943-new-york-tells-fedex-get-robot-off-city.html)) A 2025–26 New York State Senate bill (S9950) proposes a permit regime for "personal delivery devices". **[sourced]** ([NYSenate PDF](https://legislation.nysenate.gov/pdf/bills/2025/S9950)) We have not confirmed whether it passed, and a sweeper is not obviously a delivery device. **Treat any public-roadway robot operation in NYC as unauthorised until counsel says otherwise.**
- **Camera data.** The NYC POST Act, amended by Local Law 56 of 2025, imposes impact-and-use disclosures on *NYPD* surveillance technology. **[sourced]** ([NYPD IUP](https://www.nyc.gov/assets/nypd/downloads/pdf/public_information/post-final/License-Plate-Readers-NYPD-Impact-and-Use-Policy-2.4.26-FINAL.pdf)) It doesn't apply to a BID directly, but it shows the scrutiny a city camera programme would face. **[assumption]** LaneKeeper's design already crops to the road band, strips EXIF, keeps evidence private and retains it briefly. A privacy impact assessment is still required.
- **Rider wearables.** Riders' glasses or phones capture other people. This needs opt-in only, on-device detection, uploads only for user-confirmed reports, and no face or plate processing (already built).

## 7. Cost structure (baseline model is assumptions)

The original `analysis/cost_model.py` is kept unchanged as an **assumption-based baseline**:

| Scenario | Today $/lane-mi | Robot $/lane-mi | Ratio |
|---|---|---|---|
| Low | $29.25 | $11.31 | 2.59× |
| Base | $44.34 | $18.28 | 2.42× |
| High | $78.51 | $44.93 | 1.75× |

**What's wrong with treating that as savings:**

- Its capex assumption spreads $6.7M over **45** DSNY units ($148,889 each). Public reporting ties the $6.7M to a fleet of 6 units plus 4 planned. **[sourced]** The money may also cover other equipment, such as brushes fitted to plow vehicles. **[sourced]** ([Streetsblog](https://nyc.streetsblog.org/2022/11/23/snow-problem-sanitation-has-bigger-fleet-of-bike-lane-plows-but-same-old-car-first-cleanup-plan)) Per-unit cost is therefore **unknown**, somewhere between about $150k and $670k, and it moves the ratio substantially.
- Robot capex ($40k–$100k), supervisor ratios (1:5–1:15) and nightly km are all **[assumption]**, with no vendor quote or field data behind them.
- The model omits permitting, insurance beyond $3k per unit per year, teleoperation staffing, vandalism, charging infrastructure, cybersecurity, footage retention and accessibility compliance.
- Program-level scale for reference: one weekly pass over 133.5 protected miles is about **6,940 lane-mile passes a year** **[sourced count × 52]**.

**Metric to use instead:** cost per **verified** lane-mile (or per verified hazard removed). That needs field verification data, which doesn't exist yet.

## 8. Ideal customer, positioning, pilot

- **Ideal customer:** a NYC BID or campus operator with at least 2 miles of protected or off-street bike paths, an existing sanitation crew, and a stakeholder who is accountable for cyclist complaints.
- **Buyer vs user:** the buyer is the BID executive director or facilities director. The users are crew supervisors (dispatch), crews (field), riders (reporting) and the public (read-only map).
- **Positioning:** "Verified hazard closure for bike lanes. Riders report in seconds, your existing crews get the right job, and nothing is marked clean without proof." The robot is a roadmap item, not the pitch.
- **Differentiation:** (1) evidence-gated closure (no "cleared" without after-evidence), (2) privacy-minimised rider capture with on-device detection, (3) an auditable approval ledger for any automated executor, (4) vendor-neutral executors (human crew today, any certified robot later).

### 90-day NYC pilot hypothesis (software only, human crews)

| Weeks | Do | Measure |
|---|---|---|
| 1–2 | Extract the 311 debris-in-bike-lane baseline for the district; recruit 30–50 opt-in riders; derive lane cells from city GIS | Baseline time-to-resolution and complaint volume |
| 3–10 | Riders report through the PWA (glasses optional); the BID crew receives LaneKeeper tickets; the crew photographs after-state | KPIs below, weekly |
| 11–12 | Compare with baseline; rider survey; crew interviews | Go / no-go |

### Pilot KPIs (definitions, with denominators)

| KPI | Definition | Status today |
|---|---|---|
| Detection precision / recall | vs independently labelled frames | **[demo]** 0.71 / 1.00 on 16 synthetic frames; **no field data** |
| False-warning rate | warnings per rider-hour with no hazard on review | not measured |
| Geo-match accuracy | reports matched to the correct lane cell / labelled reports | **[demo]** tested on synthetic points only |
| Duplicate-link accuracy | correct links / crew-labelled pairs | not measured |
| Time to triage | report → ticket with an owner | **[demo]** seconds, synthetic |
| Work-order approval time | request → decision | not measured (human) |
| Mission completion / safety-stop rate | robot only | **[demo]** twin only |
| Verified removal rate | closures with after-evidence / closures | not measured |
| Time to resolution | report → verified removal | not measured; key KPI |
| Operator workload | minutes per ticket | not measured |
| Cost per verified lane-mile | total cost / verified lane-miles | not measurable until the pilot |
| Rider satisfaction | survey (n, response rate stated) | not measured |

### Falsification criteria (stop or pivot if)

- Median time to verified removal doesn't improve by at least 30 % over the 311 baseline for the same blocks **[assumption threshold]**.
- Fewer than 1 confirmed hazard per 10 rider-hours, so the problem is too rare to justify the system.
- Crew-confirmed precision below 60 %, so riders and crews lose trust.
- The BID won't commit budget for a paid phase 2 at an explicit price.

## 9. Key unknowns

Real hazard frequency between DSNY sweeps · whether riders will report while riding · whether BIDs see bike lanes as theirs to clean (jurisdiction lines with DSNY) · integration access to NYC311 · per-unit cost of the existing DSNY bike-lane fleet · any lawful path for robot trials on NYC public right-of-way · Microlumin hardware performance outdoors (untested).

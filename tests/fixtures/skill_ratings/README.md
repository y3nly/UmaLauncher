# Recorded veteran rating regression

These 31 records retain unchanged rating-relevant fields from the actual Global
veteran response `2026-09-28 09-48-31.244359_packet_in.json`. The JSON includes
the source SHA-256. Account identifiers, race histories, and other unrelated
fields are omitted; stats, skills, aptitudes, unique levels, and server scores
are unchanged. No fabricated catalog, candidate set, or mocked planner is used.

The records include 19 careers affected by terrain aptitude scaling, 11 recent
careers 7413–7423 (7418 overlaps), and two Murmur controls (3653 and 3811).
Terrain skills occur at different aptitudes and in combination, while the
distance and running-style bonuses still contribute to the recorded totals.
Career 7418 has an observed score of 17,479. Incorrectly applying the 1.1 Dirt
aptitude multiplier to Got the Spirit! changes its base 217 rating to 239,
incorrectly predicting an SS score of 17,501.

The functional test uses `SkillDataSnapshot.refresh` on the actual installed
Global master, then compares `skill_planner.current_rating` with each veteran's
server score. With application dependencies installed, run from the repo root:

```powershell
$env:UMALAUNCHER_TEST_MASTER_DB = 'C:\path\to\master.mdb'
python -m unittest discover -s tests -p test_skill_rating_records.py -v
```

Without an explicit master path, the test skips rather than substituting data.
The master database itself is not bundled. No game action is submitted.

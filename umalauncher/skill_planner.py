"""Packet/master-based ratings and advisory SS purchase planning.

The aptitude, learned-group and grouped-knapsack rules follow Umapyoi's
core/actions/packet_skills.py. No game transactions or simulator calls live here.
"""
import math
import threading
import time
import traceback
import uuid
from concurrent.futures import Future
from loguru import logger
import mdb
import skill_simulation as sim
from uma_rating import stat_rating
from bashin_data import BashinJsonCache

SS_TARGET = 17500
RANKS = ((300, 'G'), (600, 'G+'), (900, 'F'), (1300, 'F+'), (1800, 'E'),
         (2300, 'E+'), (2900, 'D'), (3500, 'D+'), (4900, 'C'), (6500, 'C+'),
         (8200, 'B'), (10000, 'B+'), (12100, 'A'), (14500, 'A+'), (15900, 'S'),
         (17500, 'S+'), (19200, 'SS'), (19600, 'SS+'))
HIGH_RANKS = ((19600, 400, 23900, 'UG'), (23900, 500, 28800, 'UF'),
              (28800, 560, 34400, 'UE'), (34400, 630, 40700, 'UD'),
              (40700, 700, 47600, 'UC'), (47600, 760, 55200, 'UB'),
              (55200, 800, math.inf, 'UA'))
APTITUDES = tuple((f'{prefix}=={i}', field) for prefix, fields in (
    ('distance_type', ('proper_distance_short', 'proper_distance_mile', 'proper_distance_middle', 'proper_distance_long')),
    ('ground_type', ('proper_ground_turf', 'proper_ground_dirt')),
    ('running_style', tuple(sim.STYLE_APTITUDES.values())),
) for i, field in enumerate(fields, 1))


def rank_name(score):
    for threshold, name in RANKS:
        if score < threshold:
            return name
    for start, step, end, name in HIGH_RANKS:
        if score < end:
            suffix = min(9, (score - start) // step)
            return name + (str(suffix) if suffix else '')


def catalog_row(catalog, sid):
    return catalog.get(sid) or catalog.get(sid - 800000 if 900000 <= sid < 1000000 else sid)


def skill_rating(row, chara):
    multiplier = 1.0
    for token, field in APTITUDES:
        if token in str(row.get('condition_1') or ''):
            aptitude = chara[field]
            multiplier = 1.1 if aptitude >= 7 else .9 if aptitude >= 5 else .8 if aptitude >= 2 else .7
            break
    return round(row['grade_value'] * multiplier)


def current_rating(chara, catalog):
    groups, inherited, unique = {}, 0, None
    for learned in chara['skill_array']:
        sid = learned['skill_id']
        if str(sid).startswith('1'):
            unique = learned
            continue
        row = catalog_row(catalog, sid)
        if row is None:
            raise ValueError(f'Rating data missing for acquired skill {sid}')
        if row['skill_category'] == 5 or 900000 <= sid < 1000000:
            inherited += skill_rating(row, chara)
            continue
        group = row['group_id'] or sid
        prior = groups.get(group)
        if prior is None or (row['group_rate'], sid) > (prior['group_rate'], prior['id']):
            groups[group] = row
    stats = sum(stat_rating(chara[field]) for field in ('speed', 'stamina', 'power', 'guts', 'wiz'))
    skills = inherited + sum(skill_rating(row, chara) for row in groups.values())
    if unique:
        skills += unique['level'] * (170 if chara['talent_level'] >= 3 else 120)
    return dict(score=stats + skills, rank=rank_name(stats + skills), statScore=stats,
                skillScore=skills), groups


def trainee_key(chara, available):
    fields = {k: v for k, v in chara.items() if k.startswith('proper_') or k in (
        'start_time', 'card_id', 'talent_level', 'speed', 'stamina', 'power', 'guts',
        'wiz', 'skill_point', 'race_running_style')}
    fields['skills'] = sorted((s['skill_id'], s['level']) for s in chara['skill_array'])
    fields['hints'] = sorted((s['group_id'], s['rarity'], s.get('level', 0)) for s in chara['skill_tips_array'])
    return sim.fingerprint([fields, available])


def expand_purchases(available):
    """White upgrades become reachable through their currently offered lower rank."""
    expanded = {sid: dict(info) for sid, info in available.items()}
    for sid, info in list(expanded.items()):
        while True:
            sid = mdb.get_next_skill_id_in_chain(sid)
            if sid is None or mdb.get_skill_rarity(sid) != 1:
                break
            expanded.setdefault(sid, dict(info, is_acquired=False,
                base_cost=mdb.get_skill_costs_dict().get(str(sid), 0), rarity=1))
    return expanded


def rating_rows(chara, available, rows, catalog, published=None, style=None):
    rating, acquired_groups = current_rating(chara, catalog)
    learned = {s['skill_id'] for s in chara['skill_array']}
    hint_levels = {(tip['group_id'], tip['rarity']): tip.get('level', 0)
                   for tip in chara['skill_tips_array']}
    metrics = {int(s['id']): s.get('metrics', {}).get(style) for s in (published or {}).get('skills', [])}
    base_rows = {}
    for row in catalog.values():
        if row['group_rate'] <= 0:
            continue
        group = row['group_id'] or row['id']
        prior = base_rows.get(group)
        if prior is None or (row['group_rate'], row['id']) < (prior['group_rate'], prior['id']):
            base_rows[group] = row
    def cm_value(sid):
        value = (metrics.get(sid) or {}).get('mean')
        return float(value) if value is not None and math.isfinite(float(value)) else None
    options = []
    for skill in rows:
        sid = int(skill['id'])
        row = catalog_row(catalog, sid)
        if row is None:
            raise ValueError(f'Rating data missing for skill {sid}')
        group = row['group_id'] or sid
        # Inherited uniques are independent purchases, even when a unique shares a group.
        inherited = skill['skillKind'] == 'inheritedUnique' or 900000 <= sid < 1000000
        current = None if inherited else acquired_groups.get(group)
        prior_score = skill_rating(current, chara) if current else 0
        gain = skill_rating(row, chara) - prior_score
        chain = []
        for rank in [*mdb.get_prerequisite_skill_ids(sid), sid]:
            info = available.get(rank, {})
            if rank in learned or info.get('is_acquired'):
                continue
            record = catalog_row(catalog, rank)
            if record is None:
                raise ValueError(f'Rating data missing for prerequisite {rank}')
            # A white hint discounts both circle ranks; gold has its own hint.
            hint = hint_levels.get((record['group_id'], record['rarity']), info.get('hint_level', 0))
            hint = max(0, min(int(hint), 5))
            cost = int(record['baseCost'] * (100 - sim.DISCOUNTS[hint]) / 100)
            chain.append(dict(id=str(rank), name=record['name'], cost=cost))
        cost = sum(step['cost'] for step in chain)
        skill.update(baseCost=cost, ratingGain=gain, pointsPerSp=gain / cost if cost > 0 else None,
                     groupRate=row['group_rate'], purchaseChain=chain)
        endpoint_cm = cm_value(sid)
        prior_cm = cm_value(current['id']) if current else 0.0
        incremental = endpoint_cm - prior_cm if endpoint_cm is not None and prior_cm is not None else None
        base = base_rows.get(group, row)
        current_rate = current['group_rate'] if current else 0
        base_cm = cm_value(base['id'])
        adds_base = current_rate < base['group_rate']
        base_gain = skill_rating(base, chara) - prior_score if adds_base else 0
        base_value = max(0, base_cm - (prior_cm or 0)) if adds_base and base_cm is not None else 0
        upgrade_value = max(0, endpoint_cm - (base_cm if adds_base else prior_cm)) if (
            row['group_rate'] > max(base['group_rate'], current_rate) and endpoint_cm is not None
            and (base_cm if adds_base else prior_cm) is not None) else 0
        if inherited:
            # Parent planning values inherited uniques only for reaching SS.
            incremental = 0.0
            base_gain, base_value, upgrade_value = gain, 0.0, 0.0
        options.append(dict(id=str(sid), name=skill['name'], group=str(sid) if inherited else str(group),
            cost=cost, gain=gain, baseGain=base_gain, cm=incremental, baseCm=base_value,
            upgradeCm=upgrade_value, ranked=not inherited and row['group_rate'] > base['group_rate'],
            chain=chain))
    return rating, options


def purchase_plan(rating, options, budget, choices):
    """Grouped DP: CM first, SS feasibility, and minimum necessary ranked padding."""
    by_id = {o['id']: o for o in options}
    fixed, rejected, spent = {}, [], 0
    # Recheck saved choices against current SP/costs, preserving selection order.
    for sid in dict.fromkeys(map(str, choices.get('required', []))):
        option = by_id.get(sid)
        if option is None:
            continue
        previous = fixed.get(option['group'])
        cost = spent - (previous['cost'] if previous else 0) + option['cost']
        if cost > budget:
            rejected.append(sid)
            continue
        fixed[option['group']] = option
        spent = cost
    required = {o['id'] for o in fixed.values()}
    excluded = set(map(str, choices.get('excluded', [])))
    groups = {}
    conflict = None
    for option in options:
        sid, group = option['id'], option['group']
        blocked = sid in excluded or any(step['id'] in excluded for step in option['chain'])
        if sid in required:
            if blocked:
                conflict = 'A selected purchase needs an excluded prerequisite.'
        if not blocked and option['cost'] > 0:
            groups.setdefault(group, []).append(option)
    fixed_options = list(fixed.values())
    gain = sum(o['gain'] for o in fixed_options)
    result = dict(target=SS_TARGET, current=rating, availableSp=budget, required=sorted(required),
                  excluded=sorted(excluded), rejected=rejected, conflict=conflict)
    if conflict:
        return dict(result, status='conflict', purchases=fixed_options, plannedCost=spent,
                    leftoverSp=budget-spent, projectedScore=rating['score']+gain,
                    projectedRank=rank_name(rating['score']+gain))
    groups = {g: opts for g, opts in groups.items() if g not in fixed}
    remaining = budget - spent
    needed = max(0, SS_TARGET - rating['score'] - gain)
    def max_gain(include_ranked):
        dp = [-1] * (remaining + 1)
        dp[0] = 0
        for opts in groups.values():
            following = dp.copy()
            for o in opts:
                if o['ranked'] and not include_ranked:
                    continue
                for used in range(remaining - o['cost'] + 1):
                    if dp[used] >= 0:
                        following[used+o['cost']] = max(following[used+o['cost']], dp[used]+o['gain'])
            dp = following
        return max(dp)
    max_rating_gain = max_gain(True)
    reachable = max_rating_gain >= needed
    base_reachable = max_gain(False) >= needed
    cm_only = needed == 0 or not reachable
    # State: spend, gain, base gain, base CM, upgrade CM, ranked count, options, tie IDs.
    tie_ids = {o['id']: -int(o['id']) for o in options}
    zero = (0, 0, 0, 0.0, 0.0, 0, (), ())
    states = {(0, 0): zero}
    def preference(s):
        ids = s[7]
        if not reachable:
            return (s[3], s[1], -s[5], -s[0], ids)
        if cm_only:
            return (s[3]+s[4], -s[5], -s[0], ids)
        if base_reachable:
            return (s[3], s[4], -s[5], -s[0], ids)
        return (-s[5], s[3], s[4], -s[0], ids)
    for group in sorted(groups):
        following = dict(states)
        for s in states.values():
            for o in groups[group]:
                if s[0]+o['cost'] > remaining or (cm_only and (
                        o['cm'] is None or o['cm'] < 0 or (reachable and o['cm'] == 0))):
                    continue
                # Without a reachable SS target, preserve actual CM value first
                # and use tied CM outcomes to maximize rating with spare SP.
                candidate = (s[0]+o['cost'], s[1]+o['gain'], s[2]+o['baseGain'],
                             s[3]+(o['baseCm'] if reachable else o['cm']),
                             s[4]+o['upgradeCm'], s[5]+int(o['ranked']),
                             s[6]+(o,), s[7]+(tie_ids[o['id']],))
                capped = 0 if cm_only else min(needed, candidate[2] if base_reachable else candidate[1])
                key = (candidate[0], capped)
                if key not in following or preference(candidate) > preference(following[key]):
                    following[key] = candidate
        # Keep only rating/CM Pareto improvements at each spend, as in Umapyoi.
        states = {}
        best_by_spend = {}
        for key in sorted(following, key=lambda k: (k[0], -k[1])):
            p = preference(following[key])
            if key[0] not in best_by_spend or p > best_by_spend[key[0]]:
                states[key] = following[key]
                best_by_spend[key[0]] = p
    candidates = [s for (_, capped), s in states.items() if cm_only or capped >= needed]
    best = max(candidates, key=preference)
    selected = fixed_options + list(best[6])
    projected = rating['score'] + gain + best[1]
    selected = [dict(o, reason='Selected' if o['id'] in required else
                'Reaching SS' if not cm_only and projected-o['gain'] < SS_TARGET else
                'Spare SP rating' if not reachable and o['cm'] == 0 else
                'CM value' if o['cm'] is not None and o['cm'] > 0 else 'Reaching SS') for o in selected]
    selected.sort(key=lambda o: (o['ranked'], -(o['cm'] or 0), int(o['id'])))
    return dict(result, status='ready' if reachable else 'unreachable', purchases=selected,
                maximumScore=rating['score']+gain+max_rating_gain, projectedScore=projected,
                projectedRank=rank_name(projected), plannedCost=spent+best[0], leftoverSp=remaining-best[0],
                pointsToSs=max(0, SS_TARGET-projected),
                missingPerformance=[o['id'] for o in options if o['cm'] is None])


class SkillWindowData:
    """Owned by the preparation worker; file/HTTP work never runs in the UI thread."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.bashin = BashinJsonCache(snapshot.cache_dir / 'bashin')
        self.page_id = None
        self.configs = self.courses = None
        self.published = {}
        self.planning_context = None

    def prepare(self, request):
        self.planning_context = None
        selection, chara = request['selection'], request['chara']
        cm_id, style, mode = selection['cmId'], selection['style'], selection['mode']
        page_id = selection.get('pageId')
        if page_id != self.page_id:
            self.page_id = page_id
            self.configs = self.courses = None
            self.published.clear()
        if self.configs is None:
            self.configs = sim.load_cm_configs(self.bashin.load_json)
        self.snapshot.refresh(mdb.get_db_path())
        available = expand_purchases(request['available'])
        cm = self.configs[cm_id]
        if cm.get('kind') not in ('tt', 'cm_pool') and self.courses is None:
            self.courses = sim.load_course_data(self.bashin.load_json)
        course = (cm['racePool']['scenarios'][0]['track'] if cm.get('kind') == 'cm_pool' else
                  cm['racePool']['courses'][0] if cm.get('kind') == 'tt' else self.courses['courses'][str(cm['course'])])
        payload, rows, benchmarks = sim.build_evaluation(chara, available, cm, course, style,
                                                       self.snapshot.metadata, mdb.get_prerequisite_skill_ids)
        published = None
        if mode != 'ace':
            if cm_id not in self.published:
                value = self.bashin.load_json(sim.BASHIN_DATA_ROOT + cm['file'])
                if not value.get('skills') or str(value.get('meta', {}).get('cmId')) != str(cm_id):
                    raise ValueError('Published CM evaluation is missing or invalid')
                self.published[cm_id] = value
            published = self.published[cm_id]
        rating, options = rating_rows(chara, available, rows, self.snapshot.catalog, published, style)
        choices = selection.get('choices', {})
        plan = purchase_plan(rating, options, chara['skill_point'], choices) if mode == 'rating' else None
        snapshot = dict(careerId=sim.career_id(chara), source='ace' if mode == 'ace' else 'published',
            mode=mode, selectionVersion=selection['version'],
            meta=dict(cm['meta']),
            baseSetting=payload['baseSetting'], courseData=self.courses, skills=rows,
            benchmarkDefinitions=benchmarks, rating=dict(current=rating, availableSp=chara['skill_point'],
                plan=plan, learnedIds=[str(s['skill_id']) for s in chara['skill_array']]))
        if mode == 'ace':
            snapshot['meta']['effectivenessThresholdSeconds'] = payload['effectivenessThresholdSeconds']
            if 'racePool' in payload:
                snapshot['racePool'] = payload['racePool']
        if published:
            metrics = {str(s['id']): s['metrics'] for s in published['skills']}
            for row in rows:
                row['metrics'] = metrics.get(row['id'], {})
            snapshot['publishedBenchmarks'] = published['benchmarks']
            snapshot['meta']['locationFiles'] = published['meta'].get('locationFiles', {})
            snapshot['baseSetting'] = dict(payload['baseSetting'], umaStatus=published['benchmarks'][style]['baseStats'])
            if published['benchmarks'][style].get('staminaByCourse'):
                snapshot['staminaByCourse'] = published['benchmarks'][style]['staminaByCourse']
            context_id = uuid.uuid4().hex
            self.planning_context = (context_id, rating, options, chara['skill_point'])
            snapshot['rating'].update(contextId=context_id, options=[
                dict(id=o['id'], group=o['group'], cost=o['cost'], gain=o['gain'],
                     prerequisites=[step['id'] for step in o['chain']]) for o in options
            ])
        return dict(snapshot=snapshot, payload=payload, skillDataPath=str(self.snapshot.path))

    def replan(self, request):
        """Reprice choices against the last prepared inputs, without loading any data."""
        if not self.planning_context or request.get('contextId') != self.planning_context[0]:
            return None
        version, choices = request.get('version'), request.get('choices')
        if type(version) is not int or version < 0 or not isinstance(choices, dict):
            raise ValueError('Invalid planner selection')
        if any(not isinstance(choices.get(key), list) or len(choices[key]) > 1000
               or any(type(sid) not in (str, int) for sid in choices[key])
               for key in ('required', 'excluded')):
            raise ValueError('Invalid planner choices')
        context_id, rating, options, budget = self.planning_context
        return dict(contextId=context_id, selectionVersion=version,
                    plan=purchase_plan(rating, options, budget, choices))


class PreparationWorker:
    """One active calculation and one latest pending request, independent of Ace."""
    def __init__(self, prepare, replan=None):
        self.prepare = prepare
        self.replan = replan
        self.condition = threading.Condition()
        self.revision = 0
        self.pending = self.completion = None
        self.plan_pending = self.plan_reply = None
        self.context_id = None
        self.plan_version = -1
        self.stopped = False
        self.thread = threading.Thread(target=self._run, name='skill-planner-worker', daemon=True)

    def submit(self, request):
        with self.condition:
            if self.stopped:
                return None
            if not self.thread.is_alive():
                self.thread.start()
            self.revision += 1
            self.pending = (self.revision, request)
            self.completion = None
            self.condition.notify()
            return self.revision

    def take(self):
        with self.condition:
            result, self.completion = self.completion, None
            return result

    def submit_plan(self, request):
        """Reply directly to the browser; keep only its latest pending choices."""
        reply = Future()
        with self.condition:
            version = request.get('version')
            if (self.stopped or self.replan is None or type(version) is not int
                    or not self.context_id or request.get('contextId') != self.context_id
                    or version < self.plan_version):
                reply.set_result(None)
                return reply
            self.plan_version = version
            if self.plan_pending:
                self.plan_pending[1].set_result(None)
            if self.plan_reply and not self.plan_reply.done():
                self.plan_reply.set_result(None)
            self.plan_pending = (request, reply)
            self.condition.notify()
        return reply

    def stop(self):
        with self.condition:
            self.stopped = True
            self.pending = None
            if self.plan_pending:
                self.plan_pending[1].set_result(None)
                self.plan_pending = None
            if self.plan_reply and not self.plan_reply.done():
                self.plan_reply.set_result(None)
            self.condition.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=1)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopped or self.pending is not None or self.plan_pending is not None)
                if self.stopped:
                    return
                if self.pending is not None:
                    revision, request = self.pending
                    self.pending = None
                    reply = None
                else:
                    request, reply = self.plan_pending
                    self.plan_pending = None
                    self.plan_reply = reply
            start = time.monotonic()
            try:
                result, error = (self.replan(request) if reply else self.prepare(request)), None
                operation = 'choices' if reply else request['selection']['mode']
                logger.debug(f"Skill window {operation} preparation: {time.monotonic()-start:.3f}s")
            except Exception as exc:
                logger.error(f'Skill window preparation failed:\n{traceback.format_exc()}')
                result, error = None, str(exc)
            with self.condition:
                if reply:
                    self.plan_reply = None
                    if not reply.done():
                        if error:
                            reply.set_exception(ValueError(error))
                        else:
                            reply.set_result(result)
                elif not self.stopped and revision == self.revision:
                    self.context_id = result['snapshot']['rating'].get('contextId') if result else None
                    self.plan_version = -1
                    self.completion = (revision, result, error)

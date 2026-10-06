"""Packet/master-based ratings and advisory SS purchase planning.

The aptitude, learned-group and grouped-knapsack rules follow Umapyoi's
core/actions/packet_skills.py. The pure optimizer is identical in both projects.
No game transactions or simulator calls live here.
"""
import math
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import CancelledError, Future
from loguru import logger
import mdb
import skill_simulation as sim
from uma_rating import stat_rating
from bashin_data import BashinJsonCache
from skill_activation import skill_can_activate
from skill_purchase_optimizer import optimize_purchases, purchase_order, purchase_tier

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


STYLE_NAMES = dict(NIGE='front', SEN='pace', SASI='late', OI='end')
STYLE_BASELINES = dict(NIGE=(201252, 201242), SEN=(201322, 201312),
                       SASI=(201392, 201382), OI=(201462, 201452))


def rating_rows(chara, available, rows, catalog, published=None, style=None):
    """Price eligible endpoints and classify their CM/activation priorities."""
    rating, acquired_groups = current_rating(chara, catalog)
    learned = {s['skill_id'] for s in chara['skill_array']}
    hint_levels = {(tip['group_id'], tip['rarity']): tip.get('level', 0)
                   for tip in chara['skill_tips_array']}
    metrics = {int(s['id']): s.get('metrics', {}).get(style) for s in (published or {}).get('skills', [])}

    def cm_value(sid):
        value = (metrics.get(sid) or {}).get('mean')
        return float(value) if value is not None and math.isfinite(float(value)) else None

    def rank_cost(sid):
        record = catalog_row(catalog, sid)
        # White hints discount both circle ranks; gold hints remain separate.
        hint = hint_levels.get((record['group_id'], record['rarity']),
                               available.get(sid, {}).get('hint_level', 0))
        return int(record['baseCost'] * (100 - sim.DISCOUNTS[max(0, min(int(hint), 5))]) / 100)

    efficiencies = [(cm_value(sid) or 0) / catalog[sid]['baseCost']
                    for sid in STYLE_BASELINES.get(style, ())
                    if sid in catalog and catalog[sid]['baseCost'] > 0 and (cm_value(sid) or 0) > 0]
    threshold = .5 * min(efficiencies, default=0)
    families = {}
    for record in catalog.values():
        if record['group_rate'] > 0 and record['skill_category'] != 5:
            families.setdefault(record['group_id'] or record['id'], []).append(record)
    good_rates = {}
    for group, family in families.items():
        family.sort(key=lambda row: (row['group_rate'], row['id']))
        full_cost = 0
        for rate in sorted({record['group_rate'] for record in family}):
            ranks = [record for record in family if record['group_rate'] == rate]
            record = next((record for record in ranks if record['id'] in available), ranks[0])
            full_cost += rank_cost(record['id'])
            value = cm_value(record['id']) or 0
            if value > 0 and full_cost > 0 and value / full_cost >= threshold:
                good_rates[group] = record['group_rate']
                break
    gold_groups = {catalog[sid]['group_id'] for sid in available
                   if sid in catalog and catalog[sid]['rarity'] > 1
                   and catalog[sid]['skill_category'] != 5}
    options = []
    for current in acquired_groups.values():
        if current['group_rate'] >= 0 or current['baseCost'] <= 0:
            continue
        sid = current['id']
        options.append(dict(
            id=str(sid), name=current['name'], group=str(current['group_id']),
            skill_id=sid, group_id=current['group_id'], cost=current['baseCost'],
            gain=-skill_rating(current, chara), cm=0.0, cm_value=0.0, ranked=False,
            rate=current['group_rate'],
            chain=[dict(id=str(sid), name=current['name'], cost=current['baseCost'])],
        ))
    for skill in rows:
        sid = int(skill['id'])
        row = catalog_row(catalog, sid)
        if row is None:
            raise ValueError(f'Rating data missing for skill {sid}')
        inherited = skill['skillKind'] == 'inheritedUnique' or 900000 <= sid < 1000000
        if sid in learned:
            continue
        if row['skill_category'] == 5 and not inherited:
            continue
        group = row['group_id'] or sid
        current = None if inherited else acquired_groups.get(group)
        current_rate = max(0, current['group_rate']) if current else 0
        if not inherited and row['group_rate'] <= current_rate:
            continue
        base = families[group][0] if not inherited else row
        if group in gold_groups and row['rarity'] == 1 and row['group_rate'] > base['group_rate']:
            continue
        prior_score = skill_rating(current, chara) if current else 0
        gain = skill_rating(row, chara) - prior_score
        chain = []
        if current and current['group_rate'] < 0:
            chain.append(dict(id=str(current['id']), name=current['name'], cost=current['baseCost']))
        for rank in [*mdb.get_prerequisite_skill_ids(sid), sid]:
            info = available.get(rank, {})
            if rank in learned or info.get('is_acquired'):
                continue
            record = catalog_row(catalog, rank)
            if record is None:
                raise ValueError(f'Rating data missing for prerequisite {rank}')
            chain.append(dict(id=str(rank), name=record['name'], cost=rank_cost(rank)))
        cost = sum(step['cost'] for step in chain)
        skill.update(baseCost=cost, ratingGain=gain, pointsPerSp=gain / cost if cost > 0 else None,
                     groupRate=row['group_rate'], purchaseChain=chain)
        endpoint_cm = None if inherited else cm_value(sid)
        prior_cm = (cm_value(current['id']) or 0) if current and current_rate else 0.0
        incremental = (endpoint_cm or 0) - prior_cm
        base_cm = cm_value(base['id']) or 0
        adds_base = current_rate < base['group_rate']
        base_gain = skill_rating(base, chara) - prior_score if adds_base else 0
        base_value = max(0, base_cm - prior_cm) if adds_base else 0
        upgrade_value = max(0, (endpoint_cm or 0) - (base_cm if adds_base else prior_cm)) if (
            row['group_rate'] > max(base['group_rate'], current_rate)) else 0
        good_rate = good_rates.get(group)
        cm_id = (published or {}).get('meta', {}).get('cmId')
        activates = not inherited and skill_can_activate(
            row, cm_id, STYLE_NAMES.get(style), chara,
            {f'{style}_rate': (metrics.get(sid) or {}).get('effectiveRate', 0)},
        )
        if inherited:
            incremental, base_value, upgrade_value = 0.0, 0.0, 0.0
        options.append(dict(
            id=str(sid), name=skill['name'], group=str(sid) if inherited else str(group),
            skill_id=sid, group_id=sid if inherited else group,
            cost=cost, gain=gain, baseGain=base_gain, cm=endpoint_cm,
            baseCm=base_value, upgradeCm=upgrade_value, cm_value=incremental,
            rate=row['group_rate'], ranked=not inherited and row['group_rate'] > base['group_rate'],
            good=not inherited and good_rate is not None and row['group_rate'] >= good_rate,
            new_good=not inherited and good_rate is not None and current_rate < good_rate <= row['group_rate'],
            new_family=current_rate == 0, rank_gain=row['group_rate'] - max(base['group_rate'], current_rate),
            green=row['skill_category'] == 0, activates=activates, ss_only=inherited,
            chain=chain,
        ))
    return rating, options


def purchase_plan(rating, options, budget, choices, cancelled=None):
    """Use the same final-purchase priorities and SS constraint as Umapyoi."""
    by_id = {option['id']: option for option in options}
    fixed, rejected, spent = {}, [], 0
    # Required selections reserve a minimum rank, allowing a later upgrade.
    for sid in dict.fromkeys(map(str, choices.get('required', []))):
        option = by_id.get(sid)
        if option is None or option.get('ss_only'):
            continue
        previous = fixed.get(option['group_id'])
        if previous and previous['rate'] >= option['rate']:
            continue
        cost = spent - (previous['cost'] if previous else 0) + option['cost']
        if cost > budget:
            rejected.append(sid)
            continue
        fixed[option['group_id']] = option
        spent = cost
    required = {option['id'] for option in fixed.values()}
    excluded = set(map(str, choices.get('excluded', [])))
    # Explicit UI exclusions are absolute. They do not become SS exceptions.
    groups = {}
    conflict = None
    for option in options:
        blocked = option['id'] in excluded or any(step['id'] in excluded for step in option['chain'])
        if option['id'] in required and blocked:
            conflict = 'A selected purchase needs an excluded prerequisite.'
        if not blocked and option['cost'] > 0:
            if option['group_id'] in fixed:
                option = dict(option, good=True)
            groups.setdefault(option['group_id'], []).append(option)
    result = dict(target=SS_TARGET, current=rating, availableSp=budget, required=sorted(required),
                  excluded=sorted(excluded), rejected=rejected, conflict=conflict)
    if conflict:
        gain = sum(option['gain'] for option in fixed.values())
        return dict(result, status='conflict', purchases=list(fixed.values()), plannedCost=spent,
                    leftoverSp=budget-spent, projectedScore=rating['score']+gain,
                    projectedRank=rank_name(rating['score']+gain))
    optimized = optimize_purchases(groups, budget, fixed,
                                   max(0, SS_TARGET - rating['score']), cancelled=cancelled)
    reachable = optimized.ss_without_exceptions or optimized.ss_exception
    projected = rating['score'] + optimized.rating_gain
    selected = [dict(option, tier=purchase_tier(option, fixed)) for option in optimized.choices]
    required_order = {group: index for index, group in enumerate(fixed)}
    selected.sort(key=lambda option: purchase_order(option, required_order))
    for option in selected:
        reason = 'Selected' if option['group_id'] in fixed else option['tier']
        if reachable and projected-option['gain'] < SS_TARGET:
            reason += '; Reaching SS'
        option['reason'] = reason
    return dict(result, status='ready' if reachable else 'unreachable', purchases=selected,
                maximumScore=rating['score']+optimized.maximum_rating_gain, projectedScore=projected,
                projectedRank=rank_name(projected), plannedCost=optimized.cost, leftoverSp=budget-optimized.cost,
                pointsToSs=max(0, SS_TARGET-projected),
                missingPerformance=[option['id'] for option in options if option['cm'] is None and not option.get('ss_only')])


class SkillWindowData:
    """Owned by the preparation worker; file/HTTP work never runs in the UI thread."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.bashin = BashinJsonCache(snapshot.cache_dir / 'bashin')
        self.page_id = None
        self.configs = self.courses = None
        self.published = {}
        self.planning_context = None
        self.plans = OrderedDict()

    def _plan(self, rating, options, budget, choices, cancelled):
        # Selection order matters for ranks and for pruning unaffordable choices.
        key = (tuple(map(str, choices.get('required', []))),
               tuple(sorted(set(map(str, choices.get('excluded', []))))))
        if cancelled and cancelled():
            raise CancelledError()
        if key in self.plans:
            self.plans.move_to_end(key)
            return self.plans[key]
        plan = purchase_plan(rating, options, budget, choices, cancelled)
        self.plans[key] = plan
        if len(self.plans) > 32:
            self.plans.popitem(last=False)
        return plan

    def prepare(self, request):
        self.planning_context = None
        self.plans.clear()
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
        plan = self._plan(rating, options, chara['skill_point'], choices, request.get('_cancelled')) if mode == 'rating' else None
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
                    plan=self._plan(rating, options, budget, choices, request.get('_cancelled')))


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
        self.active_cancel = None
        self.stopped = False
        self.thread = threading.Thread(target=self._run, name='skill-planner-worker', daemon=True)

    def submit(self, request):
        with self.condition:
            if self.stopped:
                return None
            if not self.thread.is_alive():
                self.thread.start()
            self.revision += 1
            if self.active_cancel:
                self.active_cancel.set()
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
            if self.plan_reply and self.active_cancel:
                self.active_cancel.set()
            self.plan_pending = (request, reply)
            self.condition.notify()
        return reply

    def cancel(self):
        """Stop preparation/recommendations without shutting down the reusable worker."""
        with self.condition:
            self.revision += 1
            self.pending = None
            self.completion = None
            if self.active_cancel:
                self.active_cancel.set()
            if self.plan_pending:
                self.plan_pending[1].set_result(None)
                self.plan_pending = None
            if self.plan_reply and not self.plan_reply.done():
                self.plan_reply.set_result(None)
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.stopped = True
            self.cancel()
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
                self.active_cancel = threading.Event()
                request = dict(request, _cancelled=self.active_cancel.is_set)
            start = time.monotonic()
            try:
                result, error = (self.replan(request) if reply else self.prepare(request)), None
                operation = 'choices' if reply else request['selection']['mode']
                logger.debug(f"Skill window {operation} preparation: {time.monotonic()-start:.3f}s")
            except CancelledError:
                result, error = None, None
            except Exception as exc:
                logger.error(f'Skill window preparation failed:\n{traceback.format_exc()}')
                result, error = None, str(exc)
            with self.condition:
                self.active_cancel = None
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

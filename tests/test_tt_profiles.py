from pathlib import Path
import copy
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'umalauncher'))
import skill_simulation as sim
import skill_planner as planner


def profile(category, distance, surface):
    return dict(cmId='tt_' + category, name='TT ' + category.title(), kind='tt',
        file='cm_data_tt_' + category + '.json', courseCount=1, weightingPolicy='equal-courses-v1',
        racePool=dict(courses=[dict(location=10001, course=10101, condition='GOOD', gateCount=12,
                                   distanceType=distance, surface=surface)],
            conditions=[dict(weather=1, condition='GOOD', weight=1)],
            seasonWeights={str(s):.2 for s in range(1,6)}, time=2))


def cm_all_profile():
    return dict(cmId='cm_all', kind='cm_pool', name='CM All', file='cm_data_cm_all.json',
            weightingPolicy='equal-cm-entries-1-48', racePool={'scenarios':[
                dict(id=str(i), track=dict(location=10006,course=10606,condition='GOOD',gateCount=9,
                    distanceType=(i % 4)+1,surface=(i % 2)+1), season=(i % 5)+1,weather=(i % 4)+1,time=2)
                for i in range(1,49)]})


class TeamTrialsTests(unittest.TestCase):
    def setUp(self):
        self.entries = [profile(*args) for args in [('sprint',1,1),('mile',2,1),('medium',3,1),('long',4,1),('dirt',2,2)]]
        self.configs = sim.load_cm_configs(lambda _:dict(profiles=self.entries))
        self.chara = dict(speed=1234,stamina=999,power=1100,guts=550,wiz=950,start_time=1,card_id=1001,
            skill_array=[dict(skill_id=200012,level=1)], skill_tips_array=[], skill_point=500,
            proper_distance_short=8,proper_distance_mile=7,proper_distance_middle=6,proper_distance_long=5,
            proper_ground_turf=7,proper_ground_dirt=6, **{field:7 for field in sim.STYLE_APTITUDES.values()})
        self.metadata = {sid:dict(id=str(sid),name=str(sid),skillKind='normal',order=sid,baseCost=100,metrics={})
                         for sid in (200012,200172)}
        self.available = {200012:dict(is_acquired=True),200172:dict(is_acquired=False,hint_level=1)}

    def test_all_pools_use_actual_aptitudes_skills_and_balanced_budget(self):
        for key, cm in self.configs.items():
            payload, rows, _ = sim.build_evaluation(self.chara,self.available,cm,cm['racePool']['courses'][0],
                                                   'SEN',self.metadata,lambda _:[])
            self.assertEqual(payload['acquiredSkillIds'],[200012])
            self.assertEqual(payload['unacquiredSkillIds'],[200172])
            self.assertEqual(rows[0]['baseCost'],90)
            self.assertEqual(payload['iterations'],2000)
            self.assertFalse(payload['collectLocationTelemetry'])
            status = payload['baseSetting']['umaStatus']
            self.assertEqual(status['speed'],1234)
            self.assertEqual(status['surfaceFit'],'B' if key=='tt_dirt' else 'A')
            self.assertEqual(status['distanceFit'],dict(tt_sprint='S',tt_mile='A',tt_medium='B',tt_long='C',tt_dirt='A')[key])
            changed = copy.deepcopy(payload)
            changed['racePool']['conditions'][0]['weather']=2
            self.assertNotEqual(sim.fingerprint(payload),sim.fingerprint(changed))
            changed = copy.deepcopy(payload)
            changed['poolWeightingPolicy']='new-policy'
            self.assertNotEqual(sim.fingerprint(payload),sim.fingerprint(changed))

    def test_parent_preparation_consumes_published_pool_metrics(self):
        catalog = {sid:dict(id=sid,group_id=sid,group_rate=1,skill_category=1,grade_value=100,
                           rarity=1,baseCost=100,name=str(sid)) for sid in self.metadata}
        with tempfile.TemporaryDirectory() as directory, patch.object(planner.mdb,'get_next_skill_id_in_chain',return_value=None), \
             patch.object(planner.mdb,'get_prerequisite_skill_ids',return_value=[]), patch.object(planner.mdb,'get_db_path',return_value='unused'):
            snapshot = types.SimpleNamespace(cache_dir=Path(directory),metadata=self.metadata,catalog=catalog,
                                             path=Path(directory)/'skills.json',refresh=lambda _:None)
            worker = planner.SkillWindowData(snapshot)
            for entry in [*self.entries, cm_all_profile()]:
                metrics = {'SEN':dict(mean=1.25,effectiveRate=.4,simulatedEffectiveRate=1,seasonalAvailability=.4)}
                published = dict(meta=entry,skills=[dict(id='200172',metrics=metrics)],
                                 benchmarks={'SEN':dict(baseStats={'style':'SEN','speed':1600},
                                     staminaByCourse={'10101':{'GOOD':500}},staminaRange=[500,550])})
                worker.bashin.load_json = lambda url: (dict(profiles=[*self.entries, cm_all_profile()]) if url==sim.CM_INDEX_URL else
                    dict(courses={'unused':{}}) if url.endswith('course_data.json') else published)
                request = dict(selection=dict(cmId=entry['cmId'],style='SEN',mode='rating',pageId='p',version=3),
                               chara=self.chara,available=self.available)
                output = worker.prepare(request)
                self.assertEqual(output['snapshot']['meta']['cmId'],entry['cmId'])
                self.assertNotIn('courseId',output['snapshot']['meta'])
                self.assertEqual(output['snapshot']['skills'][0]['metrics'],metrics)
                self.assertEqual(output['snapshot']['publishedBenchmarks'],published['benchmarks'])
                self.assertEqual(output['snapshot']['baseSetting']['umaStatus']['speed'],1600)
                self.assertEqual(output['snapshot']['staminaByCourse'],{'10101':{'GOOD':500}})
                self.assertEqual(output['payload']['baseSetting']['umaStatus']['stamina'],999)
                self.assertNotIn('staminaByCourse',output['payload'])
                self.assertLessEqual(output['snapshot']['rating']['plan']['plannedCost'],500)
                self.assertIsNone(worker.replan(dict(contextId='stale',version=4,choices={})))

    def test_invalid_pool_and_duplicate_ids_are_rejected(self):
        bad = copy.deepcopy(self.entries)
        bad[0]['racePool']['conditions'][0]['weight']=float('nan')
        with self.assertRaises(ValueError): sim.load_cm_configs(lambda _:dict(profiles=bad))
        with self.assertRaises(ValueError): sim.load_cm_configs(lambda _:dict(profiles=self.entries*2))

    def test_cm_all_mixed_aptitudes_and_benchmarks(self):
        entry = cm_all_profile()
        cm = sim.load_cm_configs(lambda _:dict(profiles=[entry]))['cm_all']
        payload, rows, definitions = sim.build_evaluation(self.chara,self.available,cm,
            cm['racePool']['scenarios'][0]['track'],'SEN',self.metadata,lambda _:[])
        self.assertEqual(payload['iterations'],2000)
        self.assertEqual(payload['effectivenessThresholdSeconds'],.001)
        self.assertFalse(payload['collectLocationTelemetry'])
        self.assertEqual(payload['acquiredSkillIds'],[200012])
        self.assertEqual(rows[0]['baseCost'],90)
        for scenario in payload['racePool']['scenarios']:
            track = scenario['track']
            self.assertEqual(scenario['distanceFit'],{1:'S',2:'A',3:'B',4:'C'}[track['distanceType']])
            self.assertEqual(scenario['surfaceFit'],{1:'A',2:'B'}[track['surface']])
        self.assertNotIn('distanceFit',entry['racePool']['scenarios'][0])
        self.assertIn({'id':'distance_up','candidate':{'distanceFitSteps':1}},payload['evaluationComparisons'])
        changed=copy.deepcopy(payload)
        changed['racePool']['scenarios'][0]['season']=4
        self.assertNotEqual(sim.fingerprint(payload),sim.fingerprint(changed))
        bad=copy.deepcopy(entry)
        bad['racePool']['scenarios'][1]['id']='1'
        with self.assertRaises(ValueError): sim.load_cm_configs(lambda _:dict(profiles=[bad]))

    def test_latest_preparation_wins_after_profile_change(self):
        started, release = threading.Event(), threading.Event()
        def prepare(request):
            if request['selection']['cmId']=='old': started.set(); release.wait(2)
            return {'snapshot':{'rating':{'contextId':request['selection']['cmId']}}}
        worker = planner.PreparationWorker(prepare)
        try:
            worker.submit({'selection':{'cmId':'old','mode':'ace'}})
            self.assertTrue(started.wait(2))
            worker.submit({'selection':{'cmId':'new','mode':'ace'}})
            release.set()
            with worker.condition:
                worker.condition.wait_for(lambda:worker.completion is not None,timeout=2)
            completion=worker.take()
            self.assertIsNotNone(completion)
            self.assertEqual(completion[1]['snapshot']['rating']['contextId'],'new')
            self.assertIsNone(completion[2])
        finally:
            release.set(); worker.stop()


if __name__=='__main__': unittest.main()

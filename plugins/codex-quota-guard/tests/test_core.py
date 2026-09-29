import copy, json, pathlib, sys, tempfile, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from core import *

class PolicyTests(unittest.TestCase):
    def quota(self,a=50,b=50):
        return {'checked_at':1000,'windows':{'5h':{'remaining':a,'resets_at':2000},'week':{'remaining':b,'resets_at':3000}}}
    def test_pause_at_5h_threshold_or_week_exhausted(self):
        for a,b in [(1,80),(80,0),(0,0)]: self.assertEqual(quota_decision(self.quota(a,b),DEFAULTS,1001),'pause')
    def test_resume_uses_5h_strict_threshold_and_week_not_exhausted(self):
        for a,b in [(9.99,90),(10,90),(90,0)]: self.assertNotEqual(quota_decision(self.quota(a,b),DEFAULTS,1001),'resume')
        for a,b in [(10.01,20.1),(20,90),(80,10),(80,1),(80,0.1)]:
            self.assertEqual(quota_decision(self.quota(a,b),DEFAULTS,1001),'resume')
        saved=validate_settings({'resume_5h':20})
        self.assertEqual(saved['resume_5h'],20)
        self.assertNotEqual(quota_decision(self.quota(15,90),saved,1001),'resume')
    def test_low_week_does_not_trigger_checkpoint(self):
        self.assertEqual(quota_decision(self.quota(10,1),DEFAULTS,1001),'hold')
        self.assertEqual(quota_decision(self.quota(5,50),DEFAULTS,1001),'checkpoint')
    def test_missing_week_never_resumes(self):
        q=self.quota(); del q['windows']['week']
        self.assertEqual(quota_decision(q,DEFAULTS,1001),'unknown')
        q['windows']['5h']['remaining']=0
        self.assertEqual(quota_decision(q,DEFAULTS,1001),'pause')
    def test_stale_or_expired_never_resumes(self):
        self.assertEqual(quota_decision(self.quota(),DEFAULTS,1361),'unknown')
        self.assertEqual(quota_decision(self.quota(),dict(DEFAULTS,dynamic_poll=False),1201),'unknown')
        q=self.quota(); q['windows']['week']['resets_at']=900
        self.assertEqual(quota_decision(q,DEFAULTS,1001),'unknown')
    def test_dynamic_polling_exact_boundaries(self):
        settings=dict(DEFAULTS,pause_5h=5)
        for remaining,expected in [(80,180),(20,180),(19.99,60),(10,60),(9.99,30),(5,30),(0,30)]:
            self.assertEqual(poll_interval(self.quota(remaining),settings,1001),expected)
        self.assertEqual(quota_decision(self.quota(5),settings,1001),'pause')
    def test_unknown_expired_or_exhausted_week_uses_short_interval(self):
        self.assertEqual(poll_interval(None,DEFAULTS,1001),30)
        self.assertEqual(poll_interval(self.quota(80,0),DEFAULTS,1001),30)
        self.assertEqual(poll_interval(self.quota(),DEFAULTS,3001),30)
    def test_custom_dynamic_policy_and_fixed_fallback(self):
        settings=validate_settings(dict(DEFAULTS,poll_mid_below=40,poll_low_below=15,
                                      poll_high_seconds=240,poll_mid_seconds=90,poll_low_seconds=20))
        for remaining,expected in [(80,240),(39,90),(14,20)]:self.assertEqual(poll_interval(self.quota(remaining),settings,1001),expected)
        settings.update(dynamic_poll=False,poll_seconds=75)
        for remaining in (80,14,0):self.assertEqual(poll_interval(self.quota(remaining),settings,1001),75)
    def test_invalid_dynamic_policy_rejected(self):
        for change in [dict(poll_low_below=20),dict(poll_mid_below=0),dict(poll_low_seconds=61),dict(poll_high_seconds=59),dict(poll_mid_seconds=1),dict(dynamic_poll='yes')]:
            with self.assertRaises(ValueError):validate_settings(dict(DEFAULTS,**change))
    def test_window_order_and_bucket_id(self):
        r={'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':14,'windowDurationMins':10080,'resetsAt':3000},
                                            'secondary':{'usedPercent':69,'windowDurationMins':300,'resetsAt':2000}},
                                      'other':{'primary':{'usedPercent':100,'windowDurationMins':300,'resetsAt':2000}}}}
        q=normalize_quota(r,now=1000)
        self.assertEqual(q['windows']['5h']['remaining'],31)
        self.assertEqual(q['windows']['week']['remaining'],86)
    def test_bad_quota_rejected(self):
        for used in [float('nan'),float('inf'),-1,101,'30',True]:
            with self.assertRaises(ValueError): normalize_quota({'rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':used,'resetsAt':2000}}},now=1000)
    def test_settings_validation(self):
        for overrides in [{'resume_5h':1},{'pause_5h':25},{'resume_5h':100},{'pause_5h':float('nan')},{'poll_seconds':1},{'enabled':'yes'},{'oops':1}]:
            with self.assertRaises(ValueError): validate_settings(dict(DEFAULTS,**overrides))
        self.assertEqual(validate_settings({'pause_5h':2})['pause_5h'],2)
    def test_old_week_settings_are_ignored_other_preferences_preserved(self):
        settings=validate_settings({'pause_week':1,'resume_week':20,'pause_5h':2,'resume_5h':30,'auto_resume':False})
        self.assertNotIn('pause_week',settings); self.assertNotIn('resume_week',settings)
        self.assertFalse(settings['auto_resume']); self.assertEqual(settings['pause_5h'],2)
        self.assertEqual(quota_decision(self.quota(31,1),settings,1001),'resume')
        self.assertEqual(quota_decision(self.quota(30,1),settings,1001),'hold')
    def test_waiting_and_children_excluded(self):
        base={'status':'inProgress','turn_id':'t','runtime_status':'active'}
        self.assertTrue(is_active(base))
        for k in ('waiting','is_child','ephemeral'): self.assertFalse(is_active(dict(base,**{k:True})))
    def test_only_owned_pause_can_resume(self):
        r={'phase':'paused','reason':'quota','turn_id':'old'}
        t={'status':'interrupted','turn_id':'old','runtime_status':'idle','owner':'desktop'}
        self.assertTrue(resume_eligible(r,t))
        for override in [{'turn_id':'new'},{'status':'completed'},{'waiting':True},{'owner':None},{'runtime_status':'active'}]:
            self.assertFalse(resume_eligible(r,dict(t,**override)))
        for phase in ('cancelled','resumed','resume_pending','needs_review'):
            self.assertFalse(resume_eligible(dict(r,phase=phase),t))
    def test_crash_does_not_repeat_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'tasks.json'; j=Journal(p)
            t={'id':'a','turn_id':'b','title':'test'}
            j.intent(t,'pause','one'); j.mark('a','paused'); j.intent(t,'resume','two')
            recovered=Journal(p); recovered.recover_uncertain()
            self.assertEqual(recovered.data['tasks']['a']['phase'],'needs_review')
    def test_atomic_config_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'settings.json'; atomic_json(p,{'标题':'守护'})
            self.assertEqual(read_json(p)['标题'],'守护')
            self.assertEqual(len(list(pathlib.Path(d).glob('*.tmp'))),0)

if __name__=='__main__': unittest.main()

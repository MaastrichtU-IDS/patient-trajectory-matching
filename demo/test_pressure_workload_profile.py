"""Checks for isolated process sampling, bounded fixed execution and safe aggregates."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import benchmark_configured_pressure as workload
import profile_pressure_workload as profiler

ROOT = profiler.ROOT
EXAMPLE = ROOT/'examples/configured-pressure-service'


def inputs():
    return workload.load_inputs(EXAMPLE/'config.json',EXAMPLE/'workload.json',1)


def stat(parent=1, session=9, rss=10, start=25):
    fields = ['S',str(parent),'9',str(session)] + ['0']*18
    fields[19] = str(start); fields[21] = str(rss)
    return '9 (worker (odd) name) '+' '.join(fields)


class MemoryProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)

    def test_linux_stat_comm_does_not_shift_fields(self):
        self.assertEqual(profiler.proc_record(stat()),{'parent':1,'session':9,'start':25,'rss_pages':10})
        self.assertEqual(profiler.proc_record(stat(rss=-1))['rss_pages'],0)

    def test_sample_tracks_descendants_and_reparented_same_session(self):
        for pid,parent,session,rss in [(9,1,9,10),(10,9,10,20),(11,10,10,30),(12,1,9,40),(13,1,13,100)]:
            (self.temp/str(pid)).mkdir(); (self.temp/str(pid)/'stat').write_text(stat(parent,session,rss,pid*2))
        (self.temp/'14').mkdir()  # Exited between enumeration and stat read.
        with patch.object(profiler.os,'sysconf',return_value=4096): result = profiler.sample_tree(9,self.temp)
        self.assertEqual(result['rss_bytes'],100*4096)
        self.assertEqual(result['processes'],4)
        self.assertEqual(result['identities'],{(9,18),(10,20),(11,22),(12,24)})
        self.assertEqual(result['unreadable_or_exited_process_records'],1)

    def test_limits_reject_nonfinite_intervals_and_timeout(self):
        for interval in (0,.001,1.01,float('inf'),float('nan')):
            with self.assertRaises(ValueError): profiler.options(interval,10)
        for timeout in (0,.9,86401,float('inf'),float('nan')):
            with self.assertRaises(ValueError): profiler.options(.02,timeout)

    def test_unsupported_platform_does_not_start_child(self):
        with patch.object(profiler.platform,'system',return_value='Darwin'),patch.object(profiler.subprocess,'Popen') as launch:
            result = profiler.run(inputs())
        launch.assert_not_called(); self.assertEqual(result['failure_code'],'LINUX_PROC_REQUIRED')

    def test_output_cannot_replace_inputs_or_follow_symlink_to_review(self):
        current = inputs()
        for path in (EXAMPLE/'config.json',EXAMPLE/'workload.json',EXAMPLE/'source-review.json',
                     EXAMPLE/'mapping/new-report.json',ROOT/'examples/prepared-measurement-session/new-report.json'):
            with self.assertRaises(ValueError): profiler.protected_output(path,current,workload)
        link = self.temp/'report.json'; link.symlink_to(EXAMPLE/'source-review.json')
        with self.assertRaises(ValueError): profiler.protected_output(link,current,workload)

    def test_child_context_and_report_size_are_checked(self):
        report = self.temp/'report.json'
        report.write_text(json.dumps({'profile':workload.PROFILE,'status':'VERIFIED','context_id':'wrong'}))
        with self.assertRaisesRegex(ValueError,'CHILD_CONTEXT_MISMATCH'): profiler.child_summary(report,'expected')
        report.write_bytes(b' '*(profiler.MAX_REPORT_BYTES+1))
        with self.assertRaisesRegex(ValueError,'CHILD_REPORT_TOO_LARGE'): profiler.child_summary(report,'expected')

    def test_failure_report_does_not_export_child_details(self):
        report = self.temp/'report.json'
        report.write_text(json.dumps({'profile':workload.PROFILE,'status':'FAILED','context_id':'expected',
            'failure':{'code':'secret patient 123','stage':'private/path'}}))
        summary = profiler.child_summary(report,'expected')
        self.assertEqual(set(summary),{'status','report_sha256'})
        self.assertNotIn('secret',json.dumps(summary))

    @unittest.skipUnless(platform.system()=='Linux','/proc sampler requires Linux')
    def test_timeout_kills_isolated_child_and_suppresses_raw_output(self):
        # Test subprocess is local test scaffolding, never a public command option.
        child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],start_new_session=True,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        result = profiler.monitor(child,.01,.05)
        self.assertTrue(result['timed_out']); self.assertIsNotNone(child.poll())
        self.assertGreater(result['memory']['samples'],0)
        self.assertLess(result['elapsed_seconds'],5)

    @unittest.skipUnless(platform.system()=='Linux','/proc sampler requires Linux')
    def test_proc_sampling_failure_cleans_up_child(self):
        child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],start_new_session=True)
        with patch.object(profiler,'sample_tree',side_effect=OSError('private/path')):
            result = profiler.monitor(child,.01,1)
        self.assertEqual(result['sampling_failure'],'PROC_SAMPLING_FAILED')
        self.assertIsNotNone(child.poll()); self.assertNotIn('private/path',json.dumps(result))

    @unittest.skipUnless(platform.system()=='Linux','/proc sampler requires Linux')
    def test_short_lived_child_can_have_no_resident_samples(self):
        child = subprocess.Popen([sys.executable,'-c','pass'],start_new_session=True); child.wait()
        result = profiler.monitor(child,.01,1)
        self.assertEqual(result['memory']['samples_with_resident_memory'],0)
        self.assertIsNone(result['memory']['maximum_sampled_aggregate_rss_bytes'])
        self.assertFalse(result['memory']['coverage_complete'])

    def test_fixed_runner_only_and_spawn_failure_is_sanitized(self):
        with patch.object(profiler.subprocess,'Popen',side_effect=OSError('sensitive path')) as launch:
            report = profiler.run(inputs())
        command = launch.call_args.args[0]
        self.assertEqual(command[:2],[sys.executable,str(profiler.RUNNER)])
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertEqual(launch.call_args.kwargs['stderr'],subprocess.DEVNULL)
        self.assertEqual(report['failure_code'],'PROFILE_EXECUTION_FAILED')
        self.assertNotIn('sensitive path',json.dumps(report))

    @unittest.skipUnless(platform.system()=='Linux','/proc sampler requires Linux')
    def test_real_authored_workload_runs_and_exports_only_aggregates(self):
        output = self.temp/'memory.json'; capture = io.StringIO()
        with redirect_stdout(capture):
            result = profiler.main(['--config',str(EXAMPLE/'config.json'),'--workload',str(EXAMPLE/'workload.json'),
                '--repetitions','1','--interval','.01','--output',str(output)])
        report = json.loads(output.read_text())
        self.assertEqual(result,0,report)
        self.assertEqual(report['status'],'MEASURED')
        self.assertEqual(report['child_verification']['verified_trials'],2)
        self.assertEqual(report['child_verification']['http_inspections_equal'],12)
        self.assertGreater(report['memory']['maximum_sampled_aggregate_rss_bytes'],0)
        self.assertGreater(report['memory']['samples_with_root'],0)
        self.assertEqual(report['child_exit_code'],0)
        self.assertFalse(report['representative_clinical_scale_established'])
        serialized = json.dumps(report)
        for secret in (str(ROOT),str(self.temp),'patient_id','episode_id','reviewer','Authored supplied records'):
            self.assertNotIn(secret,serialized)
        self.assertFalse(report['child_raw_output_included'])
        self.assertEqual(json.loads(capture.getvalue())['status'],'MEASURED')


if __name__=='__main__': unittest.main()

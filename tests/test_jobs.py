"""jobs：同名任务同时只跑一个；状态、出错、结束后的样子。"""
import _env  # noqa: F401
import threading
import unittest

import jobs


class Jobs(unittest.TestCase):
    def test_second_start_while_running_is_refused(self):
        gate, ran = threading.Event(), []

        def work(job):
            ran.append(1)
            gate.wait(2)
        self.assertTrue(jobs.start('t-busy', work))
        self.assertFalse(jobs.start('t-busy', work))         # 以前「先看再开」没锁，这里会开出第二个
        self.assertIsNone(jobs.run('t-busy', work))
        self.assertTrue(jobs.running('t-busy'))
        gate.set()
        for t in threading.enumerate():
            if t.name == 't-busy':
                t.join(2)
        self.assertEqual(ran, [1])
        self.assertFalse(jobs.running('t-busy'))

    def test_progress_and_end_state(self):
        def work(job, n):
            job.update(stage='数', total=n)
            for i in range(n):
                job['done'] = i + 1
            return 'ok'
        self.assertEqual(jobs.run('t-prog', work, 3, done=0), 'ok')
        st = jobs.get('t-prog')
        self.assertEqual((st['running'], st['done'], st['total'], st['stage'], st['error']), (False, 3, 3, None, None))
        self.assertTrue(st['finished'])

    def test_fresh_resets_but_keeps_other_fields(self):
        jobs.run('t-keep', lambda job: job.update(last='上次结果', done=5))
        jobs.run('t-keep', lambda job: None, done=0)
        st = jobs.get('t-keep')
        self.assertEqual((st['last'], st['done']), ('上次结果', 0))

    def test_error_is_recorded_and_run_reraises(self):
        def boom(job):
            raise ValueError('坏了')
        with self.assertRaises(ValueError):
            jobs.run('t-err', boom)
        self.assertEqual(jobs.get('t-err')['error'], 'ValueError: 坏了')
        self.assertFalse(jobs.running('t-err'))
        jobs.run('t-err', lambda job: None)                   # 再跑一次成功，错误清掉
        self.assertIsNone(jobs.get('t-err')['error'])

    def test_idle_defaults(self):
        self.assertEqual(jobs.get('t-never', total=0),
                         {'total': 0, 'running': False, 'error': None, 'started': None, 'finished': None})


if __name__ == '__main__':
    unittest.main()

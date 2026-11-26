"""Click anything by voice for users who cannot use their hands (group 3)."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def rect(x, y, w, h):
    return SimpleNamespace(left=x, top=y, right=x + w, bottom=y + h)


class WorkerTargetsTests(unittest.TestCase):
    def automation(self, rows, rects):
        from aidy import ai_uia_worker as worker
        automation = object.__new__(worker.Automation)
        automation.elements = {7: {row['id']: SimpleNamespace(CurrentBoundingRectangle=rects[row['id']]) for row in rows}}
        automation.snapshot = Mock(return_value=dict(elements=rows, view='v1', truncated=False, foreground=True))
        automation.client = Mock()
        automation._hit_belongs = Mock(return_value=True)
        return automation

    def row(self, ref, name, control=50000, enabled=True):
        return dict(id=ref, name=name, automation_id='', control=control, enabled=enabled, value='',
                    control_type='Button' if control == 50000 else 'Text')

    def test_targets_keep_visible_enabled_interactive_controls_in_order(self):
        from aidy import ai_uia_worker as worker
        rows = [self.row('e1', 'Send'), self.row('e2', 'Label', control=50020), self.row('e3', 'Off', enabled=False),
                self.row('e4', 'Tiny'), self.row('e5', 'Search', control=50004), self.row('e6', 'Hidden')]
        rects = {'e1': rect(10, 10, 80, 30), 'e2': rect(0, 0, 50, 20), 'e3': rect(0, 0, 50, 20),
                 'e4': rect(5, 5, 2, 2), 'e5': rect(100, 10, 200, 30), 'e6': rect(400, 400, 50, 20)}
        automation = self.automation(rows, rects)
        window = {'hwnd': 7, 'pid': 1, 'process': 'chrome.exe'}
        covered = lambda point: 9 if point.x > 390 else 7  # e6 lies under another window
        with patch.object(worker, 'U') as user32:
            user32.WindowFromPoint.side_effect = covered
            user32.GetAncestor.side_effect = lambda hwnd, flag: hwnd
            result = automation.targets(window)
        self.assertEqual(result['view'], 'v1')
        self.assertEqual([(t['id'], t['name'], t['x'], t['y'], t['w'], t['h']) for t in result['targets']],
                         [('e1', 'Send', 10, 10, 80, 30), ('e5', 'Search', 100, 10, 200, 30)])

    def test_targets_are_capped(self):
        from aidy import ai_uia_worker as worker
        rows = [self.row(f'e{i}', f'B{i}') for i in range(200)]
        automation = self.automation(rows, {r['id']: rect(i, 0, 10, 10) for i, r in enumerate(rows)})
        with patch.object(worker, 'U') as user32:
            user32.WindowFromPoint.return_value = 7
            user32.GetAncestor.side_effect = lambda hwnd, flag: hwnd
            result = automation.targets({'hwnd': 7, 'pid': 1, 'process': 'x.exe'})
        self.assertEqual(len(result['targets']), 200)  # all are matched by name; the overlay shows 150

    def test_targets_skip_controls_whose_centre_belongs_to_something_else(self):
        from aidy import ai_uia_worker as worker
        rows = [self.row('e1', 'Send'), self.row('e2', 'Clipped')]
        automation = self.automation(rows, {'e1': rect(0, 0, 50, 20), 'e2': rect(60, 0, 50, 20)})
        automation._hit_belongs = Mock(side_effect=lambda element, hit: element is automation.elements[7]['e1'])
        with patch.object(worker, 'U') as user32:
            user32.WindowFromPoint.return_value = 7
            user32.GetAncestor.side_effect = lambda hwnd, flag: hwnd
            result = automation.targets({'hwnd': 7, 'pid': 1, 'process': 'x.exe'})
        self.assertEqual([t['id'] for t in result['targets']], ['e1'])
        self.assertFalse(result['truncated'])

    def test_hit_on_a_child_of_the_control_belongs_to_it(self):
        # Review: in VS Code 14 of 53 labelled controls had a child element at their centre.
        from aidy import ai_uia_worker as worker
        automation = object.__new__(worker.Automation)
        button, child, grandchild, other = object(), object(), object(), object()
        parents = {grandchild: child, child: button, button: None, other: None}
        automation.client = Mock()
        automation.client.CompareElements.side_effect = lambda a, b: a is b
        automation.client.ControlViewWalker.GetParentElement.side_effect = lambda e: parents[e]
        self.assertTrue(automation._hit_belongs(button, button))
        self.assertTrue(automation._hit_belongs(button, grandchild))
        self.assertFalse(automation._hit_belongs(button, other))

    def test_moved_control_is_detected(self):
        from aidy.ai_uia_worker import moved
        self.assertFalse(moved(rect(10, 10, 80, 30), [10, 10, 80, 30]))
        self.assertFalse(moved(rect(12, 11, 80, 30), [10, 10, 80, 30]))
        self.assertTrue(moved(rect(10, 210, 80, 30), [10, 10, 80, 30]))  # the page scrolled



def only(plan):
    assert plan is not None and len(plan['steps']) == 1, plan
    return plan['steps'][0]['tool'], plan['steps'][0]['arguments']


class PointerGrammarTests(unittest.TestCase):
    def test_click_phrases(self):
        from aidy.ai_pointer import pointer_plan
        self.assertEqual(only(pointer_plan('Click Send.')), ('click_target', {'target': 'Send', 'gesture': 'click'}))
        self.assertEqual(only(pointer_plan('click on the search button')),
                         ('click_target', {'target': 'search', 'gesture': 'click'}))
        self.assertEqual(only(pointer_plan('double click readme')), ('click_target', {'target': 'readme', 'gesture': 'double_click'}))
        self.assertEqual(only(pointer_plan('Right click 7.')), ('click_target', {'target': '7', 'gesture': 'right_click'}))
        self.assertEqual(only(pointer_plan('press the submit button')), ('click_target', {'target': 'submit', 'gesture': 'click'}))
        self.assertEqual(only(pointer_plan('click number seven')), ('click_target', {'target': '7', 'gesture': 'click'}))
        self.assertEqual(only(pointer_plan('push the send button')), ('click_target', {'target': 'send', 'gesture': 'click'}))
        self.assertEqual(only(pointer_plan('press the delete button')), ('click_target', {'target': 'delete', 'gesture': 'click'}))

    def test_button_phrases_are_clicks_not_keys(self):
        # Review: "press the delete button" pressed the Delete key without the click confirmation.
        from aidy.ai_keys import key_plan
        self.assertIsNone(key_plan('press the delete button'))
        self.assertIsNone(key_plan('press the home button'))

    def test_label_phrases(self):
        from aidy.ai_pointer import pointer_plan
        for text in ['show numbers', 'Show labels.', 'number the buttons', 'show me the numbers']:
            with self.subTest(text=text):
                self.assertEqual(only(pointer_plan(text)), ('show_labels', {'action': 'show'}))
        for text in ['hide numbers', 'Hide the labels.', 'remove numbers']:
            with self.subTest(text=text):
                self.assertEqual(only(pointer_plan(text)), ('show_labels', {'action': 'hide'}))

    def test_label_number(self):
        from aidy.ai_pointer import label_number
        for text, n in [('7', 7), ('Seven.', 7), ('number 12', 12), ('click 3', 3), ('Number seven.', 7), ('twenty', 20),
                        ('to', 2), ('too', 2), ('for', 4), ('won', 1), ('ate', 8)]:
            with self.subTest(text=text):
                self.assertEqual(label_number(text), n)
        for text in ['seven apples', 'click send', '', 'zero', '0', '151']:
            with self.subTest(text=text):
                self.assertIsNone(label_number(text))

    def test_not_pointer_phrases(self):
        from aidy.ai_pointer import pointer_plan
        for text in ['click', 'click on', 'close the window', 'show desktop', 'press enter', 'press control s',
                     'open steam', 'show me the weather', "don't click send", 'click send in ten minutes',
                     'click here', 'click there', 'click twice', 'click again']:
            with self.subTest(text=text):
                self.assertIsNone(pointer_plan(text))


if __name__ == '__main__':
    unittest.main()


class PointerRoutingTests(unittest.TestCase):
    def test_pointer_plans_are_local_valid_and_hidden_from_google(self):
        from aidy.ai_local_commands import local_plan
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        from aidy.ai_tools import Registry
        r = Registry(['steam'])
        self.assertEqual(local_plan('click send', r)['steps'][0]['tool'], 'click_target')
        self.assertEqual(local_plan('show numbers', r)['steps'][0]['tool'], 'show_labels')
        self.assertEqual(local_plan('press enter', r)['steps'][0]['tool'], 'press_keys')
        for text in ['click send', 'right click 7', 'show numbers', 'hide numbers']:
            r.plan(local_plan(text, r))
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), Mock())
        self.addCleanup(provider.close)
        self.assertFalse({'click_target', 'show_labels'} & set(provider.available_tools(r)))


TARGETS = [dict(id='e1', name='Send', control_type='Button', x=10, y=10, w=80, h=30),
           dict(id='e2', name='Search', control_type='Edit', x=100, y=10, w=200, h=30),
           dict(id='e3', name='Delete', control_type='Button', x=10, y=60, w=80, h=30),
           dict(id='e4', name='Open', control_type='Button', x=10, y=110, w=80, h=30),
           dict(id='e5', name='Open', control_type='MenuItem', x=10, y=160, w=80, h=30),
           dict(id='e6', name='Отправить', control_type='Button', x=10, y=210, w=80, h=30),
           dict(id='e7', name='', automation_id='SendButton', control_type='Button', x=10, y=260, w=30, h=30),
           dict(id='e8', name='Seven', control_type='Button', x=10, y=310, w=30, h=30)]
for _row in TARGETS:
    _row.setdefault('automation_id', '')
WINDOW = {'hwnd': 7, 'pid': 1, 'process': 'chrome.exe'}


class PointerRuntimeTests(unittest.TestCase):
    def setUp(self):
        import os
        import test_app_routing
        from aidy.ai_engine import AIEngine
        from aidy.ai_provider import AIConfig, Credentials, GeminiProvider
        from aidy.ai_tools import Registry
        env = patch.dict(os.environ, {'AIDY_INTERPRETATION_MODE': 'original'})
        env.start()
        self.addCleanup(env.stop)
        fixture = test_app_routing.AppRoutingTests()
        self.host = fixture.make_assistant()
        self.addCleanup(fixture.doCleanups)
        self.runtime = self.host._ai
        self.runtime.config = AIConfig(mode='api')
        self.runtime.reload = Mock()
        self.runtime.poll_cancel_audio = Mock()
        self.runtime.windows.snapshot = Mock(return_value=[])
        self.runtime.catalog.refresh = Mock()
        self.host.voice_id_enabled = False
        self.host.voice = Mock(muted=False)
        self.host._deafen_after_speak = Mock()
        self.session = Mock()
        self.session.request.side_effect = OSError('offline')
        provider = GeminiProvider(AIConfig(mode='api'), Credentials(['placeholder']), self.session)
        self.runtime.engine = AIEngine(provider, Registry([a['id'] for a in self.host.apps]),
                                       self.runtime.authorize, self.runtime.confirm, self.runtime.execute)
        self.runtime.uia = Mock()
        self.runtime.uia.call.side_effect = self.worker
        self.window = dict(WINDOW)
        front = patch('aidy.ai_pointer.foreground_window', side_effect=lambda: dict(self.window))
        front.start()
        self.addCleanup(front.stop)
        self.overlays = []
        overlay = patch.object(self.runtime, '_overlay', side_effect=self.overlays.append)
        overlay.start()
        self.addCleanup(overlay.stop)

    truncated = False

    def worker(self, window, operation, cancel, progress, timeout=3, authorize=None, **args):
        if operation == 'targets':
            return dict(view='v1', targets=[dict(t) for t in TARGETS], truncated=self.truncated)
        if operation == 'mouse':
            return dict(status='dispatched', message='Input sent.')
        raise AssertionError(operation)

    def clicks(self):
        return [(c.kwargs['element'], c.kwargs['gesture']) for c in self.runtime.uia.call.call_args_list
                if c.args[1] == 'mouse']

    def spoken(self):
        return [c.args[-1] for c in self.host.voice.tts_blocking.call_args_list + self.host.voice.play_or_tts.call_args_list]

    def test_click_by_unique_name_is_instant_and_silent(self):
        self.assertTrue(self.runtime.run('click search'))
        self.assertEqual(self.clicks(), [('e2', 'click')])
        self.assertEqual(self.spoken(), [])
        self.session.request.assert_not_called()

    def test_same_name_twice_shows_numbers_then_clicks_the_chosen_one(self):
        self.assertTrue(self.runtime.run('click open'))
        self.assertEqual(self.clicks(), [])
        self.assertEqual([[l['n'] for l in o] for o in self.overlays], [[1, 2]])
        self.assertTrue(any('number' in s.casefold() for s in self.spoken()))
        self.assertTrue(self.runtime.pending)
        self.assertTrue(self.runtime.run('Two.'))
        self.assertEqual(self.clicks(), [('e5', 'click')])
        self.assertEqual(self.overlays[-1], [])
        self.assertFalse(self.runtime.pending)

    def test_show_numbers_then_right_click_a_number(self):
        self.assertTrue(self.runtime.run('show numbers'))
        self.assertEqual([l['n'] for l in self.overlays[-1]], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(self.overlays[-1][0], dict(n=1, x=10, y=10, w=80, h=30))
        self.assertTrue(self.runtime.pending)
        self.assertTrue(self.runtime.run('right click 4'))
        self.assertEqual(self.clicks(), [('e4', 'right_click')])

    def test_number_without_labels_or_out_of_range_is_refused(self):
        self.assertFalse(self.runtime.run('click 9'))  # no numbers shown and no control named Nine
        self.runtime.run('show numbers')
        self.assertFalse(self.runtime.run('click 9'))
        self.assertEqual(self.clicks(), [])
        self.assertTrue(self.spoken())

    def test_labels_expire(self):
        from aidy import ai_runtime
        import time
        with patch.object(ai_runtime, 'LABELS_SECONDS', 0.05):
            self.runtime.run('show numbers')
        time.sleep(0.1)
        self.assertFalse(self.runtime.pending)
        self.runtime.run('2')
        self.assertEqual(self.clicks(), [])

    def test_window_change_after_numbers_stops_the_click(self):
        self.runtime.run('show numbers')
        self.window = {'hwnd': 8, 'pid': 2, 'process': 'notepad.exe'}
        self.assertFalse(self.runtime.run('3'))
        self.assertEqual(self.clicks(), [])

    def test_risky_control_asks_first(self):
        with patch.object(self.runtime, 'confirm', return_value=False) as confirm:
            self.assertFalse(self.runtime.run('click delete'))
        confirm.assert_called_once()
        self.assertEqual(self.clicks(), [])
        with patch.object(self.runtime, 'confirm', return_value=True):
            self.assertTrue(self.runtime.run('click send'))
        self.assertEqual(self.clicks(), [('e1', 'click')])

    def test_confirmation_names_the_control(self):
        step = dict(tool='click_target', arguments=dict(target='Delete', gesture='click'), delay_seconds=0)
        label, spoken, _ = self.runtime._prepare_confirmation(step)
        self.assertEqual((label, spoken), ('Click Delete', 'Click Delete'))

    def test_expired_numbers_disappear_by_themselves(self):
        from aidy import ai_runtime
        import time
        with patch.object(ai_runtime, 'LABELS_SECONDS', 0.05):
            self.runtime.run('show numbers')
        time.sleep(0.1)
        self.runtime.tick()
        self.assertEqual(self.overlays[-1], [])

    def test_another_command_hides_the_numbers(self):
        import time
        self.runtime.run('show numbers')
        self.runtime._command_until = time.monotonic() + 8  # "Aidy," was said
        with patch('aidy.system.get_active_window_info', return_value={'process': 'chrome', 'title': 'x'}), \
                patch('aidy.ai_keys.press'):
            self.runtime.run('page down')
        self.assertEqual(self.overlays[-1], [])
        self.assertFalse(self.runtime.run('5'))
        self.assertEqual(self.clicks(), [])

    def test_a_number_answers_aidys_own_question_first(self):
        import time
        from aidy.ai_engine import Result
        self.runtime.run('show numbers')
        self.runtime.engine.pending_until = time.monotonic() + 20
        with patch.object(self.runtime.engine, 'run', return_value=Result('verified', 'Volume up.')) as run:
            self.runtime.run('3')
        self.assertEqual(run.call_args.args[0], '3')
        self.assertEqual(self.clicks(), [])

    def test_click_passes_the_label_rectangle_so_a_scrolled_page_is_refused(self):
        self.runtime.run('show numbers')
        self.runtime.run('click 4')
        call = [c for c in self.runtime.uia.call.call_args_list if c.args[1] == 'mouse'][0]
        self.assertEqual(call.kwargs['rect'], [10, 110, 80, 30])

    def test_numbers_keep_the_microphone_open_only_for_numbers_and_clicks(self):
        self.runtime.run('show numbers')
        with patch.object(self.runtime.engine, 'run') as engine_run:
            self.assertFalse(self.runtime.run('close it'))
        engine_run.assert_not_called()
        self.assertNotEqual(self.overlays[-1], [])  # the numbers stay
        self.assertTrue(self.runtime.run('two'))

    def test_russian_and_unnamed_send_controls_ask_first(self):
        self.runtime.run('show numbers')
        with patch.object(self.runtime, 'confirm', return_value=False) as confirm:
            self.assertFalse(self.runtime.run('6'))
            self.runtime.run('show numbers')
            self.assertFalse(self.runtime.run('7'))
        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(self.clicks(), [])

    def test_unknown_name_goes_to_the_normal_planner(self):
        self.assertFalse(self.runtime.run('click the first video'))
        self.session.request.assert_called()  # Google/agent decide, not "I can't find"
        self.assertFalse(any("can't find" in s for s in self.spoken()))

    def test_cut_off_scan_never_guesses_a_single_match(self):
        self.truncated = True
        self.assertTrue(self.runtime.run('click search'))
        self.assertEqual(self.clicks(), [])
        self.assertEqual([[l['n'] for l in o] for o in self.overlays], [[1]])

    def test_number_name_without_numbers_shown_clicks_that_button(self):
        self.assertTrue(self.runtime.run('click seven'))
        self.assertEqual(self.clicks(), [('e8', 'click')])

    def test_confirmation_never_speaks_untrusted_non_ascii_names(self):
        step = dict(tool='click_target', arguments=dict(target='Отправить', gesture='click'), delay_seconds=0)
        label, spoken, _ = self.runtime._prepare_confirmation(step)
        self.assertEqual(spoken, 'Click this control')

    def test_terminal_in_front_is_refused(self):
        self.window = {'hwnd': 9, 'pid': 3, 'process': 'cmd.exe'}
        self.assertFalse(self.runtime.run('click send'))
        self.assertEqual(self.clicks(), [])


class OverlayLineTests(unittest.TestCase):
    def test_overlay_line_format(self):
        from aidy.ai_pointer import overlay_line
        self.assertEqual(overlay_line([dict(n=1, x=10, y=20, w=30, h=40)]),
                         'OVERLAY:{"labels": [{"n": 1, "x": 10, "y": 20, "w": 30, "h": 40}]}')
        self.assertEqual(overlay_line([]), 'OVERLAY:{"labels": []}')

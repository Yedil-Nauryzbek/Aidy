"""Stage 1 of the assistant core (2026-11-25): short ordinary speech, important speech whole."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aidy.ai_engine import Result
from aidy.ai_provider import AIConfig
from aidy.ai_runtime import AIRuntime, spoken_text


class SpokenTextTests(unittest.TestCase):
    def test_ordinary_keeps_whole_sentences_up_to_12_words(self):
        self.assertEqual(spoken_text('Done. The file was saved to your Documents folder as notes.txt. You can open it now.'),
                         'Done. The file was saved to your Documents folder as notes.txt.')

    def test_ordinary_long_first_sentence_is_cut_at_a_clause(self):
        spoken = spoken_text('Tabs: ' + ', '.join(f'Page {i} title' for i in range(30)) + '.')
        self.assertLessEqual(len(spoken.split()), 24)
        self.assertTrue(spoken.startswith('Tabs: Page 0 title,'))
        self.assertTrue(spoken.endswith('title.'))

    def test_important_is_spoken_whole_up_to_150_words(self):
        failure = ("I couldn't open the report because the file is locked by another program. "
                   'Close Excel and try again. Nothing was changed on your computer.')
        self.assertEqual(spoken_text(failure, important=True), failure)
        self.assertLessEqual(len(spoken_text('Word. ' * 200, important=True).split()), 150)
        self.assertLessEqual(len(spoken_text(' '.join(['word'] * 200), important=True).split()), 150)

    def test_progress_summary_is_kept(self):
        self.assertEqual(spoken_text('2 of 3 goals completed. Could not open Steam: Steam is not installed.', important=True),
                         'Done 2 of 3. Steam is not installed.')
        self.assertEqual(spoken_text('3 of 3 goals completed.'), 'Done.')
        self.assertEqual(spoken_text('0 of 2 goals completed. Could not open Steam: Steam is not installed.', important=True),
                         "I couldn't do that. Steam is not installed.")

    def test_cleanup_keeps_decimals_and_drops_links_markup_emoji(self):
        spoken = spoken_text('Sure! See https://example.com/x **now** 😀. ' + 'Word. ' * 100, important=True)
        for unwanted in ('http', '**', '😀'):
            self.assertNotIn(unwanted, spoken)
        self.assertTrue(spoken.startswith('Sure! See now.'))
        text = 'The rate is 512.37 tenge at 3 p.m. per the U.S. feed on xe.com. I like C# ⭐.'
        whole = spoken_text(text, important=True)
        for kept in ('512.37', 'p.m.', 'U.S.', 'xe.com', 'C#'):
            self.assertIn(kept, whole)
        self.assertNotIn('⭐', whole)
        # Abbreviations are not sentence ends: an ordinary reply keeps the whole first sentence.
        self.assertEqual(spoken_text(text), 'The rate is 512.37 tenge at 3 p.m. per the U.S. feed on xe.com.')

    def test_nothing_speakable_is_empty(self):
        self.assertEqual(spoken_text('https://example.com 😀'), '')

    # Review 25.11: an opener sentence plus a 12-word reply was spoken as the opener alone.
    def test_opener_is_never_spoken_alone(self):
        text = 'Certainly. Your report is in the Downloads folder and ready for you now.'
        self.assertEqual(spoken_text(text), text)
        self.assertEqual(spoken_text('Ah. Your report is in the Downloads folder and ready for you now.'),
                         'Ah. Your report is in the Downloads folder and ready for you now.')

    def test_title_abbreviations_are_not_sentence_ends(self):
        for text in ('St. Petersburg is the second largest city in Russia, founded by Peter the Great.',
                     "I've booked your appointment with Dr. Brown for next Monday morning at nine o'clock."):
            with self.subTest(text=text[:20]):
                self.assertEqual(spoken_text(text), text)

    def test_action_count_failure_keeps_its_reason_whole(self):
        self.assertEqual(spoken_text('1 of 2 actions sent. File C:\\x.txt not found.', important=True),
                         'Done 1 of 2. File C:\\x.txt not found.')


class ReportLengthTests(unittest.TestCase):
    def runtime(self):
        runtime = AIRuntime.__new__(AIRuntime)
        runtime.engine = None
        runtime.config = AIConfig(mode='api')
        runtime.host = SimpleNamespace(voice=Mock(muted=False), _stop_event=threading.Event(), _deafen_after_speak=Mock())
        return runtime

    def test_failure_is_spoken_whole(self):
        runtime = self.runtime()
        text = "I couldn't save the file. The folder is read only. Choose another folder and try again."
        runtime.report(Result('failed', text))
        runtime.host.voice.tts_blocking.assert_called_once_with(text)

    def test_success_is_spoken_short(self):
        runtime = self.runtime()
        # 5 + 6 words fit the 12-word limit (the old rule cut at 10); the third sentence does not.
        runtime.report(Result('verified', 'The list has eight items. Two of them are new today. One of them was renamed by you.'))
        runtime.host.voice.tts_blocking.assert_called_once_with('The list has eight items. Two of them are new today.')

    def test_reply_ending_with_a_question_is_spoken_whole(self):
        # Aidy keeps listening after a reply; the user must hear the question it waits for.
        for reply in ('Of course, I can set that up for you right away. Which folder should I use?',
                      'What do you think? Should I continue with the essay or stop here now?'):
            with self.subTest(reply=reply[:20]):
                runtime = self.runtime()
                runtime.report(Result('answered', reply, data={'tone': 'neutral', 'sources': [], 'detail': False}))
                runtime.host.voice.tts_blocking.assert_called_once_with(reply, tone='neutral')

    def test_reply_length_follows_detail(self):
        story = ('Once upon a time a small robot wanted to see the sea. It walked for days across the hills. '
                 'At last it found the shore and listened to the waves.')
        for detail, expected in ((True, story), (False, 'Once upon a time a small robot wanted to see the sea.')):
            with self.subTest(detail=detail):
                runtime = self.runtime()
                runtime.report(Result('answered', story, data={'tone': 'warm', 'sources': [], 'detail': detail}))
                runtime.host.voice.tts_blocking.assert_called_once_with(expected, tone='warm')


class ChunkTests(unittest.TestCase):
    def speaker(self, clips=None):
        from collections import OrderedDict
        from aidy.voice_style import NeuralSpeaker
        speaker = object.__new__(NeuralSpeaker)
        if clips is not None:
            speaker.clips = clips
            speaker.cache, speaker.cache_bytes = OrderedDict(), 0
            speaker.engine = Mock()
        return speaker

    def pieces(self, text):
        speaker = self.speaker()
        speaker.render = lambda text, tone='neutral': text
        return list(speaker.chunks(text))

    def test_no_punctuation_splits_before_a_conjunction(self):
        self.assertEqual(self.pieces('You have ten new messages and two of them look important.'),
                         ['You have ten new messages', 'and two of them look important.'])

    def test_abbreviation_is_not_a_chunk_boundary(self):
        self.assertEqual(self.pieces('At 3 p.m. today.'), ['At 3 p.m. today.'])

    def test_chunks_grow_within_budget_and_keep_all_text(self):
        text = ' '.join(['word'] * 80) + '.'
        pieces = self.pieces(text)
        self.assertLessEqual(len(pieces[0]), 24)
        for previous, piece in zip(pieces, pieces[1:]):
            self.assertLessEqual(len(piece), min(160, max(20, int(1.3 * len(previous)) + 4)) + 8)
        self.assertEqual(' '.join(pieces), text)
        self.assertGreater(len(pieces[-1]), len(pieces[0]))

    def test_prerecorded_chunk_plays_its_clip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / 'certainly.wav'
            clip.write_bytes(b'RIFF-clip')
            speaker = self.speaker({'Certainly.': str(clip)})
            self.assertEqual(next(speaker.chunks('Certainly. Your report is ready.')), b'RIFF-clip')
            speaker.engine.create.assert_not_called()

    # Review 25.11: chunks shrank to single words ("folder.") and paths were cut inside a word.
    def clipped_pieces(self, text, clips=('Certainly.', 'Of course.', 'Well,')):
        speaker = self.speaker()
        speaker.clips = dict.fromkeys(clips, 'clip.wav')
        speaker.render = lambda text, tone='neutral': text
        return list(speaker.chunks(text))

    def test_chunks_after_a_clip_are_not_fragments(self):
        self.assertEqual(self.clipped_pieces('Certainly. Your report is in the Downloads folder.'),
                         ['Certainly.', 'Your report is', 'in the Downloads folder.'])
        for text in ('Of course. Why did the robot go on holiday? It needed to recharge its batteries.',
                     "Well, it depends on what you need it for, but I'd start with the smaller model.",
                     'You have ten new messages and two of them look important.'):
            with self.subTest(text=text[:20]):
                pieces = self.clipped_pieces(text)
                self.assertEqual(' '.join(pieces), text)
                for piece in pieces[:-1]:
                    self.assertTrue(piece in ('Certainly.', 'Of course.', 'Well,') or len(piece) >= 12, piece)

    def test_a_word_is_never_cut(self):
        text = 'Saved C:/Users/Rami/Documents/Quarterly_report_final_version.docx to your folder.'
        pieces = self.pieces(text)
        self.assertEqual(' '.join(pieces), text)
        self.assertTrue(all(word in text.split() for piece in pieces for word in piece.split()))

    def test_title_abbreviation_is_not_a_chunk_boundary(self):
        self.assertTrue(self.pieces('Dr. Brown will see you at nine tomorrow morning.')[0].startswith('Dr. Brown'))

    def test_without_clip_dir_there_are_no_clips(self):
        from aidy.voice_style import clip_paths
        profile = {'phrases': {'Done.': 'a' * 24 + '.wav'}}
        self.assertEqual(clip_paths(profile, None), {})
        self.assertEqual(clip_paths(profile, 'C:\\pack'), {'Done.': 'C:\\pack\\' + 'a' * 24 + '.wav'})


class VoicePackTests(unittest.TestCase):
    def test_voice_pack_includes_openers_and_fixed_replies(self):
        scripts = Path(__file__).resolve().parents[3] / 'scripts'
        sys.path.insert(0, str(scripts))
        try:
            from prepare_jarvis_voice import static_phrases
        finally:
            sys.path.remove(str(scripts))
        _, phrases = static_phrases()
        for phrase in ('Right,', 'Right.', 'Certainly.', 'Good question.', 'Mouse input sent.',
                       "I couldn't do that.", 'Windows Settings page requested.'):
            self.assertTrue(phrase in phrases, phrase)


class SpeechTimingTests(unittest.TestCase):
    def test_neural_speech_logs_first_chunk_and_gaps(self):
        import contextlib
        import io
        from unittest.mock import patch
        from aidy.voice import Voice
        voice = Voice.__new__(Voice)
        voice.current_volume = 100
        speaker = SimpleNamespace(chunks=lambda text, tone: iter([b'one', b'two']))
        out = io.StringIO()
        with patch('aidy.voice.winsound.PlaySound') as play, contextlib.redirect_stdout(out):
            voice._speak_neural(speaker, 'One. Two.')
        self.assertEqual(play.call_count, 2)
        line = out.getvalue().strip()
        self.assertRegex(line, r'^VOICE TIMING: chunks=2 first_ms=\d+ max_gap_ms=\d+ total_ms=\d+$')


class PersonaTests(unittest.TestCase):
    def test_persona_sets_short_default_and_detail_rule(self):
        from aidy.ai_provider import OPENERS, PERSONA, converse_declaration
        for phrase in ('12 words', '150 words', 'detail'):
            self.assertIn(phrase, PERSONA)
        for opener in OPENERS:
            self.assertIn(opener, PERSONA)
        self.assertEqual(converse_declaration(False)['parametersJsonSchema']['properties']['detail'], {'type': 'boolean'})


if __name__ == '__main__':
    unittest.main()

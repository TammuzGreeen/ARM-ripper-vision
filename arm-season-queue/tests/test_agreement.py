import unittest

from app.agreement import MODEL_ORDER, evaluate
from test_core import master


class AgreementGateTests(unittest.TestCase):
    def transcript(self, *, disc=1, season=2, episodes='1-4', edition='Teil 1'):
        titles = '\n'.join(episode.title for episode in master().discs[disc - 1].episodes)
        return (f'Star Trek Deep Space Nine\nSeason {season}\nDisc {disc}\n{edition}\n'
                f'Episodes {episodes}\n{titles}')

    def result(self, first, second=None, approved=None):
        return evaluate({MODEL_ORDER[0]: first, MODEL_ORDER[1]: second or first},
                        {tag: {'usable': True} for tag in MODEL_ORDER},
                        [approved or master()])

    def test_full_priority_agreement_and_unique_approved_disc_match_pass(self):
        result = self.result(self.transcript())
        self.assertTrue(result['accepted'])
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['matches'][0]['disc'], 'disc-1')

    def test_both_models_agree_on_wrong_disc_number_and_wrong_season_reject(self):
        for text in (self.transcript(disc=2), self.transcript(season=3)):
            with self.subTest(text=text):
                result = self.result(text)
                self.assertFalse(result['accepted'])
                self.assertEqual(result['matches'], [])

    def test_model_disagreement_rejects_even_with_a_correct_side(self):
        result = self.result(self.transcript(), self.transcript(disc=2))
        self.assertFalse(result['accepted'])
        self.assertTrue(result['field_disagreements'])

    def test_ambiguous_approved_editions_reject(self):
        other = master().model_copy(update={'id': 'duplicate-edition'})
        result = evaluate({tag: self.transcript() for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [master(), other])
        self.assertFalse(result['accepted'])
        self.assertIn('conflicting_or_ambiguous_edition_identifiers', result['reasons'])

    def test_no_approved_masterlist_is_not_a_match(self):
        result = evaluate({tag: self.transcript() for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['matches'], [])

    def test_conflicting_explicit_edition_tokens_reject_even_when_content_is_unique(self):
        original = master()
        other = original.model_copy(update={
            'id': 'alternate-edition', 'edition': 'de-dvd-s02-alt',
            'edition_name': 'Alternate German DVD', 'edition_tokens': ['Teil 2']})
        text = self.transcript() + '\nTeil 2'
        result = evaluate({tag: text for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [original, other])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['matches'], [])

    def test_conflicting_printed_disc_identifiers_reject(self):
        original = master()
        original_disc = original.discs[0].model_copy(update={'printed_identifiers': ['LABEL ONE']})
        original = original.model_copy(update={'discs': [original_disc, *original.discs[1:]]})
        other_disc = original_disc.model_copy(update={'printed_identifiers': ['LABEL TWO']})
        other = original.model_copy(update={'id': 'alternate-label',
                                            'edition': 'de-dvd-s02-alt',
                                            'edition_name': 'Alternate German DVD',
                                            'discs': [other_disc, *original.discs[1:]]})
        text = self.transcript() + '\nLABEL ONE\nLABEL TWO'
        result = evaluate({tag: text for tag in MODEL_ORDER},
                          {tag: {'usable': True} for tag in MODEL_ORDER}, [original, other])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['matches'], [])


if __name__ == '__main__':
    unittest.main()
